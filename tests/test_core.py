"""Motor de classificação: normalização, precedência, leitura e ordenação.

Estes são os testes que protegem o comportamento em que confio para colar na
planilha. Se algum quebrar, o CSV está errado — não é questão de estilo.
"""

from __future__ import annotations

import io
from datetime import date, datetime
from pathlib import Path

import pytest

from core import (
    ConfigSet, LineState, Ruleset, classify_sources, classify_statement,
    lines_to_csv, merchant_key, normalize, output_name, read_statement, sort_lines,
)
from core.profiles import OutputSchema, ProfileError
from core.statement import Entry
from core.text import compact, purchase_date_of

from .conftest import CONFIG, real_statements


# ---------------------------------------------------------------------------
# Normalização
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cru, esperado", [
    ("Prudent*APOL00188798", "PRUDENT APOL00188798"),
    ("OggiSantaRita", "OGGI SANTA RITA"),          # CamelCase separado
    ("DM          *Nintend", "DM NINTEND"),        # espaços colapsados
    ("Alimentação", "ALIMENTACAO"),                # acentos removidos
    ("GrelhaGrill", "GRELHA GRILL"),
])
def test_normalize(cru, esperado):
    assert normalize(cru) == esperado


@pytest.mark.parametrize("cru, esperado", [
    ("UNITED01624563906420", "UNITED"),     # nº de transação some
    ("UNITED01624563906431", "UNITED"),     # …e os dois viram o mesmo comerciante
    ("BEST BUY 000026", "BEST BUY"),
    ("199 RIACHUELO M", "199 RIACHUELO M"),   # 3 dígitos ficam
    # `normalize` separa dígito→maiúscula, então 11PRODUTOS vira "11 PRODUTOS".
    ("MERCADO 11PRODUTOS", "MERCADO 11 PRODUTOS"),
])
def test_merchant_key_remove_numero_de_transacao(cru, esperado):
    assert merchant_key(cru) == esperado


def test_separacao_de_digitos_nao_quebra_o_casamento():
    """A palavra-chave passa pela MESMA normalização, então continua casando."""
    rules = Ruleset.from_text(
        "configuracao: {categoria_padrao: ''}\n"
        "palavras:\n  Alimentação:\n    - MERCADO 11PRODUTOS\n")
    assert rules.classify("MERCADO 11PRODUTOS").categoria == "Alimentação"


def test_merchant_key_agrupa_transacoes_do_mesmo_lugar():
    """Sem isto, uma palavra-chave gravada nunca casaria com a compra seguinte."""
    assert merchant_key("UNITED01624563906420") == merchant_key("UNITED01624563906431")


def test_compact_casa_com_e_sem_espaco():
    assert compact(normalize("GRELHA GRILL")) == compact(normalize("GrelhaGrill"))


# ---------------------------------------------------------------------------
# Precedência das regras
# ---------------------------------------------------------------------------

RULES_YAML = """
configuracao:
  categoria_padrao: ""
  categorias: [Alimentação, Casa, Educação, Imposto, Outros]

excluir:
  - PAGAMENTO DEBITO

regras:
  - padrao: '^IOF'
    categoria: Imposto
  - padrao: 'MERCADOLIVRE (LIVROS|LEITURA)'
    categoria: Educação

palavras:
  Alimentação:
    - SUPERMERCADO
    - SUPERMERCADOS ALVORA
  Casa:
    - MOVEIS

marketplaces:
  - MERCADOLIVRE
  - AMAZON

desconhecidos:
  - FULANO DE TAL
"""


@pytest.fixture
def rules() -> Ruleset:
    return Ruleset.from_text(RULES_YAML)


def test_regra_ordenada_ganha_de_marketplace(rules):
    """Sub-loja identificável tem que vencer o genérico."""
    resultado = rules.classify("MERCADOLIVRE LIVROSNOSSOS")
    assert (resultado.categoria, resultado.state) == ("Educação", LineState.AUTO)


def test_marketplace_ganha_de_palavra_chave(rules):
    resultado = rules.classify("MERCADOLIVRE MERCADO")
    assert resultado.categoria == ""
    assert resultado.state is LineState.MARKETPLACE


def test_trecho_mais_longo_vence(rules):
    assert rules.classify("SUPERMERCADOS ALVORA 12").categoria == "Alimentação"
    assert rules.classify("SUPERMERCADO LICO").matched == "SUPERMERCADO"
    assert rules.classify("SUPERMERCADOS ALVORA").matched == "SUPERMERCADOS ALVORA"


def test_desconhecido_sai_vazio_e_nao_pergunta(rules):
    resultado = rules.classify("Fulano de Tal")
    assert resultado.categoria == ""
    assert resultado.state is LineState.IGNORED


def test_sem_regra_fica_vazio_e_pergunta(rules):
    resultado = rules.classify("LOJA NUNCA VISTA")
    assert resultado.categoria == ""
    assert resultado.state is LineState.UNMAPPED
    assert resultado.matched is None


def test_exclusao_aceita_texto_cru_ou_normalizado(rules):
    """Regressão: 'Pag Fat Deb Cc' escapava porque a comparação era case-sensitive."""
    assert rules.is_excluded("PAGAMENTO DEBITO EM CONTA")
    assert rules.is_excluded("Pagamento Debito em conta")


def test_regex_ancorado_respeita_a_ancora(rules):
    assert rules.classify("IOF COMPRA INTERNACIONAL").categoria == "Imposto"
    assert rules.classify("TAXA DE IOF").categoria != "Imposto"


# ---------------------------------------------------------------------------
# Leitura de extratos
# ---------------------------------------------------------------------------

def test_le_extrato_sicredi(sicredi_xlsx):
    statement = read_statement(sicredi_xlsx, name="teste.xlsx")
    assert statement.due_date == datetime(2026, 8, 10)
    assert len(statement.entries) == 9
    assert statement.reconciles(), "a soma lida não bate com o total declarado"


# --- o SEGUNDO formato do mesmo banco: o CSV do aplicativo -----------------
#
# O Sicredi exporta de dois jeitos que não se parecem — planilha em seções pelo
# site, CSV com preâmbulo pelo app. O portal escolhe pela extensão e não
# pergunta nada: quem baixou o arquivo já sabe de onde ele veio, e ter que
# contar isso à tela seria transferir para o usuário uma distinção que o nome
# do arquivo resolve sozinho.

def test_le_a_fatura_exportada_pelo_app(sicredi_app_csv, config_dir):
    cfg = ConfigSet.load(config_dir)
    statement = read_statement(sicredi_app_csv, name="fatura-app.csv",
                               profile=cfg.bank("sicredi"))
    assert len(statement.entries) == 4

    # Os DECLARADOS conferidos um a um, e não só `reconciles()`: quando nenhum
    # rótulo do resumo é encontrado, os declarados ficam zerados e
    # `reconciles()` devolve True por não ter o que comparar — passaria verde
    # sem ter lido nada. Um teste de mutação mostrou exatamente isso.
    assert statement.declared_debits == 741.10, "Brasil + exterior, somados"
    assert statement.declared_credits == 400.00, "em módulo, não com o sinal do app"
    assert statement.declared_balance == 341.10
    assert statement.debits == 741.10
    assert statement.credits == 400.00
    assert statement.reconciles()


def test_o_vencimento_do_app_vem_de_dentro_do_arquivo(sicredi_app_csv, config_dir):
    """Nada a perguntar: o preâmbulo traz a data, como o `.xls` do site."""
    cfg = ConfigSet.load(config_dir)
    statement = read_statement(sicredi_app_csv, name="f.csv",
                               profile=cfg.bank("sicredi"))
    assert statement.due_date == datetime(2025, 9, 10)
    assert cfg.bank("sicredi").pede_vencimento is False


def test_basta_um_formato_pedir_para_a_tela_perguntar():
    """A tela monta o campo antes de saber qual arquivo virá.

    Nenhum banco de hoje tem formatos que discordam nisso — Sicredi não pede em
    nenhum dos dois, Nubank pede no único que tem —, então o `any` só se
    distingue do `all` num perfil misto. Este é o perfil misto.
    """
    from core.profiles import BankProfile
    misto = BankProfile(id="x", nome="X", leitura={"formatos": [
        {"extensoes": [".xls"], "vencimento": {"rotulo": "Vencimento"}},
        {"extensoes": [".csv"], "vencimento": {"perguntar": True}},
    ]})
    assert misto.pede_vencimento is True


def test_parcela_do_app_perde_os_parenteses(sicredi_app_csv, config_dir):
    """`(01/02)` vira `01/02` — a forma que o resto do portal entende.

    Deixá-la passar faria a mesma compra sair como "(Parcela (01/02))" num
    arquivo e "(Parcela 01/02)" no outro, e `parcela_seguinte` pararia de
    reconhecer a parcela antiga na hora de propor o intervalo de viagem.
    """
    cfg = ConfigSet.load(config_dir)
    statement = read_statement(sicredi_app_csv, name="f.csv",
                               profile=cfg.bank("sicredi"))
    parcelas = [e.installment for e in statement.entries if e.installment]
    assert parcelas == ["01/02"]


def test_a_compra_em_dolar_do_app_e_marcada_mas_soma_em_reais(sicredi_app_csv,
                                                              config_dir):
    """O app já converte: o dólar fica numa coluna à parte, só para marcar."""
    cfg = ConfigSet.load(config_dir)
    statement = read_statement(sicredi_app_csv, name="f.csv",
                               profile=cfg.bank("sicredi"))
    peru = next(e for e in statement.entries if "PERU" in e.description)
    assert peru.international is True
    assert peru.amount == 320.59


def test_o_mesmo_perfil_le_os_dois_formatos(sicredi_xlsx, sicredi_app_csv, config_dir):
    """Um banco, dois formatos, zero pergunta — a escolha sai da extensão."""
    perfil = ConfigSet.load(config_dir).bank("sicredi")
    assert perfil.extensoes == (".xls", ".xlsx", ".csv")
    assert perfil.formato_de("x.XLS")["estrategia"] == "excel_secoes"
    assert perfil.formato_de("y.csv")["estrategia"] == "csv_com_preambulo"
    assert perfil.formato_de("z.pdf") is None

    do_site = read_statement(sicredi_xlsx, name="s.xlsx", profile=perfil)
    do_app = read_statement(sicredi_app_csv, name="a.csv", profile=perfil)
    assert do_site.entries and do_app.entries
    assert do_site.bank_id == do_app.bank_id == "sicredi"


def test_arquivo_de_extensao_estranha_diz_o_que_o_banco_aceita(config_dir):
    perfil = ConfigSet.load(config_dir).bank("sicredi")
    with pytest.raises(ProfileError, match=r"\.xls.*\.csv"):
        read_statement(io.BytesIO(b"x"), name="fatura.pdf", profile=perfil)


def test_csv_sem_o_cabecalho_esperado_e_recusado_com_explicacao(tmp_path, config_dir):
    """Um CSV qualquer com `.csv` no nome não é a fatura do app."""
    arquivo = tmp_path / "qualquer.csv"
    arquivo.write_text("a;b;c\n1;2;3\n", encoding="utf-8")
    perfil = ConfigSet.load(config_dir).bank("sicredi")
    with pytest.raises(ProfileError, match="cabeçalho"):
        read_statement(arquivo, name="qualquer.csv", profile=perfil)


# --- conta conjunta: de quem foi a compra --------------------------------

def _app_com_dois(tmp_path, config_dir):
    from .conftest import _sicredi_app_csv
    caminho = _sicredi_app_csv(tmp_path / "conjunta.csv", rows=[
        ("20/08/2025", "LOJA DELE", "", "R$ 100,00", ""),
        ("21/08/2025", "LOJA DELA", "", "R$ 50,00", ""),
    ], nomes=["Leonardo S Castilho", "Rhyesla Siqueira"])
    return ConfigSet.load(config_dir), caminho


def test_o_nome_do_outro_vai_para_o_fim_da_descricao(tmp_path, config_dir):
    """O que a conta conjunta precisa: separar a compra dela da compra dele.

    A marca fica no FIM, depois da data, porque é a informação menos usada das
    três — e porque pô-la antes moveria o `{Em 3/Jan}` de lugar em toda linha já
    exportada, quebrando o diff de quem reprocessa arquivos antigos.
    """
    cfg, caminho = _app_com_dois(tmp_path, config_dir)
    linhas, _, _ = classify_sources(
        [("c.csv", caminho)], Ruleset.from_text(cfg.categories_text),
        profile=cfg.bank("sicredi"), schema=cfg.output,
        apelidos={"Leonardo S Castilho": "", "Rhyesla Siqueira": "Rhyesla"})

    dele = next(l for l in linhas if "Dele" in l.descricao)
    dela = next(l for l in linhas if "Dela" in l.descricao)
    assert dele.descricao == "[Cartão] Loja Dele {Em 20/Aug}", "sou eu: sem marca"
    assert dela.descricao == "[Cartão] Loja Dela {Em 21/Aug} <Rhyesla>"


def test_sem_o_mapa_nada_e_marcado(tmp_path, config_dir):
    """O `.xls` do site não diz quem passou o cartão, e o cartão de uma pessoa
    não tem o que separar. Nos dois casos a descrição sai como sempre saiu."""
    cfg, caminho = _app_com_dois(tmp_path, config_dir)
    linhas, _, _ = classify_sources(
        [("c.csv", caminho)], Ruleset.from_text(cfg.categories_text),
        profile=cfg.bank("sicredi"), schema=cfg.output)
    assert not any(l.descricao.endswith(">") for l in linhas)


def test_o_extrato_sugere_quem_e_o_dono_da_conta(tmp_path, config_dir):
    """O "Associado" impresso na fatura — confirmar é mais rápido que procurar
    o próprio nome numa lista de titulares."""
    cfg, caminho = _app_com_dois(tmp_path, config_dir)
    statement = read_statement(caminho, name="c.csv", profile=cfg.bank("sicredi"))
    assert statement.titular == "Leonardo S Castilho"
    assert statement.cardholders == ["Leonardo S Castilho", "Rhyesla Siqueira"]


def test_um_titular_so_nao_vira_pergunta(sicredi_app_csv, config_dir):
    """A fixture padrão tem uma pessoa: `cardholders` traz um nome, e é isso que
    a tela usa para não perguntar nada."""
    cfg = ConfigSet.load(config_dir)
    statement = read_statement(sicredi_app_csv, name="f.csv",
                               profile=cfg.bank("sicredi"))
    assert len(statement.cardholders) == 1


def test_secao_internacional_nao_e_perdida(sicredi_xlsx_intl):
    """Bug histórico: o parser parava no primeiro 'Valor Total'."""
    statement = read_statement(sicredi_xlsx_intl, name="intl.xlsx")
    descricoes = [e.description for e in statement.entries]
    assert "CLOUDFLARE" in descricoes
    assert any(e.international for e in statement.entries)


def test_pagamento_da_fatura_e_descartado(sicredi_xlsx, categories_text):
    rules = Ruleset.from_text(categories_text)
    statement = read_statement(sicredi_xlsx, name="t.xlsx")
    lines, dropped = classify_statement(statement, rules)
    assert [d.descricao for d in dropped] == ["Pag Fat Deb Cc"]
    assert all("Pag Fat" not in l.descricao for l in lines)


def test_estorno_continua_no_csv_como_ajuste(sicredi_xlsx, categories_text):
    rules = Ruleset.from_text(categories_text)
    lines, _ = classify_statement(read_statement(sicredi_xlsx, name="t.xlsx"), rules)
    estorno = next(l for l in lines if "Credito Anuidade" in l.descricao)
    assert estorno.categoria == "Ajuste"
    assert estorno.valor < 0


def test_sem_vencimento_falha_com_mensagem_util(tmp_path, categories_text):
    from .conftest import _sicredi_workbook
    path = _sicredi_workbook(tmp_path / "sem-venc.xlsx", vencimento="")
    statement = read_statement(path, name="sem-venc.xlsx")
    with pytest.raises(ValueError, match="vencimento"):
        classify_statement(statement, Ruleset.from_text(categories_text))


# ---------------------------------------------------------------------------
# Descrição e data
# ---------------------------------------------------------------------------

def test_descricao_preserva_formato_historico(sicredi_xlsx, categories_text):
    lines, _ = classify_statement(
        read_statement(sicredi_xlsx, name="t.xlsx"), Ruleset.from_text(categories_text))
    alvora = next(l for l in lines if "Alvora" in l.descricao)
    assert alvora.descricao == "[Cartão] Supermercados Alvora {Em 2/Jul}"

    parcelado = next(l for l in lines if "Esplan" in l.descricao)
    assert parcelado.descricao == "[Cartão] Mercadolivre*Esplan (Parcela 08/10) {Em 21/Aug}"


def test_mes_em_ingles_independe_de_locale(sicredi_xlsx, categories_text):
    """`strftime('%b')` mudaria com o locale e quebraria 6.700 linhas."""
    lines, _ = classify_statement(
        read_statement(sicredi_xlsx, name="t.xlsx"), Ruleset.from_text(categories_text))
    assert all(
        any(m in l.descricao for m in
            ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
             "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"))
        for l in lines
    )


@pytest.mark.parametrize("vencimento, descricao, esperado", [
    ("08/10/2026", "[Cartão] X {Em 26/Jun}", date(2026, 6, 26)),
    # Parcela antiga: agosto ANTERIOR ao vencimento.
    ("04/10/2025", "[Cartão] X {Em 21/Aug}", date(2024, 8, 21)),
    ("01/10/2026", "[Cartão] X {Em 15/Dec}", date(2025, 12, 15)),  # vira o ano
])
def test_ano_da_compra_reconstruido(vencimento, descricao, esperado):
    assert purchase_date_of(vencimento, descricao) == esperado


# ---------------------------------------------------------------------------
# Ordenação
# ---------------------------------------------------------------------------

def test_ordenacao_categoria_alfabetica_vazias_no_fim(sicredi_xlsx, categories_text):
    lines, _ = classify_statement(
        read_statement(sicredi_xlsx, name="t.xlsx"), Ruleset.from_text(categories_text))
    ordenadas = sort_lines(lines)
    categorias = [l.categoria for l in ordenadas]
    preenchidas = [c for c in categorias if c]

    assert preenchidas == sorted(preenchidas, key=normalize)
    primeira_vazia = next((i for i, c in enumerate(categorias) if not c), len(categorias))
    assert all(not c for c in categorias[primeira_vazia:])


def test_cada_fatura_vira_um_bloco_contiguo(sicredi_xlsx, tmp_path, categories_text):
    from .conftest import _sicredi_workbook
    outro = _sicredi_workbook(tmp_path / "julho.xlsx", vencimento="10/07/2026")
    rules = Ruleset.from_text(categories_text)
    lines, _, _ = classify_sources(
        [("agosto.xlsx", sicredi_xlsx), ("julho.xlsx", outro)], rules)

    datas = [l.data for l in sort_lines(lines)]
    blocos = [d for i, d in enumerate(datas) if i == 0 or d != datas[i - 1]]
    assert len(blocos) == len(set(blocos)), "as faturas se misturaram"
    assert blocos == ["07/10/2026", "08/10/2026"]


# ---------------------------------------------------------------------------
# Schema de saída
# ---------------------------------------------------------------------------

def test_schema_muda_a_descricao_sem_mexer_em_codigo():
    entry = Entry(purchase_date=datetime(2026, 7, 15),
                  description="SUPERMERCADO X", installment="03/05", amount=10.0)
    from core.pipeline import build_description

    padrao = build_description(entry, OutputSchema())
    assert padrao == "[Cartão] Supermercado X (Parcela 03/05) {Em 15/Jul}"

    custom = OutputSchema(modelo="{descricao}{parcela}", titlecase=False,
                          parcela_modelo=" [{parcela}]")
    assert build_description(entry, custom) == "SUPERMERCADO X [03/05]"


def test_nome_do_arquivo_por_periodo(sicredi_xlsx, tmp_path, categories_text):
    from .conftest import _sicredi_workbook
    outro = _sicredi_workbook(tmp_path / "j.xlsx", vencimento="10/07/2026")
    rules = Ruleset.from_text(categories_text)

    _, _, um = classify_sources([("a.xlsx", sicredi_xlsx)], rules)
    assert output_name(um) == "fatura_2026-08.csv"

    _, _, dois = classify_sources([("a.xlsx", sicredi_xlsx), ("b.xlsx", outro)], rules)
    assert output_name(dois) == "faturas_2026-07_a_2026-08.csv"


def test_csv_tem_o_cabecalho_da_planilha(sicredi_xlsx, categories_text):
    lines, _ = classify_statement(
        read_statement(sicredi_xlsx, name="t.xlsx"), Ruleset.from_text(categories_text))
    texto = lines_to_csv(lines).decode("utf-8")
    assert texto.splitlines()[0] == "Data,Categoria,Descrição,Valor (R$),Pago"


# ---------------------------------------------------------------------------
# Multi-banco
# ---------------------------------------------------------------------------

def test_config_carrega_os_dois_bancos(config_dir):
    cfg = ConfigSet.load(config_dir)
    assert {"sicredi", "nubank"} <= set(cfg.banks)
    assert cfg.banks["sicredi"].validado is True
    assert cfg.banks["nubank"].validado is True, "validado contra um export real"


def test_nubank_usa_as_mesmas_regras_de_categoria(config_dir, nubank_csv):
    cfg = ConfigSet.load(config_dir)
    rules = Ruleset.from_text(cfg.categories_text)
    lines, _, _ = classify_sources(
        [("nu.csv", nubank_csv)], rules, profile=cfg.bank("nubank"),
        schema=cfg.output, due_date=datetime(2026, 8, 10))

    por_descricao = {l.descricao: l for l in lines}
    assert por_descricao["[Cartão] Renner {Em 11/Jul}"].categoria == "Vestuário"
    assert por_descricao["[Cartão] Supermercados Alvorada {Em 3/Jul}"].categoria == "Alimentação"
    # Marketplace continua em branco, venha de qual banco vier.
    assert por_descricao["[Cartão] Amazon Br {Em 8/Jul}"].categoria == ""


def test_cada_perfil_aceita_o_que_sabe_ler(config_dir):
    """`.csv` deixou de ser exclusividade do Nubank.

    Este teste dizia `not sicredi.accepts(".csv")`, e estava certo enquanto o
    Sicredi só exportava planilha. Com o formato do aplicativo, a extensão
    passou a valer para os dois — o que separa os bancos é o perfil escolhido
    na tela, não o sufixo do arquivo.
    """
    cfg = ConfigSet.load(config_dir)
    assert cfg.bank("sicredi").accepts("sicredi_extrato_export_site.xls")
    assert cfg.bank("sicredi").accepts("fatura.csv")
    assert not cfg.bank("sicredi").accepts("fatura.pdf")
    assert cfg.bank("nubank").accepts("fatura.csv")
    assert not cfg.bank("nubank").accepts("fatura.xls")


def test_banco_desconhecido_falha():
    from core.profiles import ProfileError
    cfg = ConfigSet.load(CONFIG)
    with pytest.raises(ProfileError, match="desconhecido"):
        cfg.bank("banco-que-nao-existe")


# ---------------------------------------------------------------------------
# Extratos reais (opcional)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not real_statements(), reason="input/*.xls não está presente")
def test_extratos_reais_conferem_com_a_fatura():
    cfg = ConfigSet.load(CONFIG)
    rules = Ruleset.from_text(cfg.categories_text)
    _, _, statements = classify_sources(
        [(p.name, p) for p in real_statements()], rules,
        profile=cfg.bank("sicredi"), schema=cfg.output)
    divergentes = [s.name for s in statements if not s.reconciles()]
    assert not divergentes, f"faturas que não fecharam: {divergentes}"


# ---------------------------------------------------------------------------
# Casamento por substring: o que ganhamos e o que pagamos
# ---------------------------------------------------------------------------

def test_substring_dentro_de_palavra_e_intencional(categories_text):
    """O casamento NÃO respeita limite de palavra — e isso é load-bearing.

    Sem ele, `UNITED01624563906420` não casaria com a palavra-chave `UNITED`,
    nem `helphbomaxcom` com `HELPHBOMAX`. Nos extratos reais são 4 linhas que
    dependem disso.
    """
    rules = Ruleset.from_text(categories_text)
    assert rules.classify("UNITED01624563906420").categoria
    assert rules.classify("DM helphbomaxcom").categoria


def test_substring_tem_falso_positivo_conhecido():
    """O preço da escolha acima: `CIMENTO` casa dentro de `ESTABELECIMENTO`.

    Este teste existe para o comportamento ser uma decisão registrada, e não uma
    surpresa. Se um dia virar casamento por limite de palavra, ele quebra e
    obriga a revisar o teste de cima junto.
    """
    rules = Ruleset.from_text(
        "configuracao: {categoria_padrao: ''}\n"
        "palavras:\n  Construção:\n    - CIMENTO\n")
    assert rules.classify("ESTABELECIMENTO XPTO").categoria == "Construção"


# ---------------------------------------------------------------------------
# A configuração REAL do repositório
# ---------------------------------------------------------------------------
#
# A suíte roda contra uma fotografia congelada (tests/fixtures/config) para que
# editar regras no portal não derrube o build. Mas a configuração de verdade
# não pode ficar sem cobertura nenhuma: se ela parar de carregar, o app sobe
# quebrado. Estes testes verificam SANIDADE, nunca conteúdo — não exigem que
# exista nenhuma palavra-chave, categoria ou marketplace em particular, porque
# é exatamente isso que muda com o uso.

def test_config_real_carrega():
    from tests.conftest import CONFIG_REAL
    cfg = ConfigSet.load(CONFIG_REAL)
    assert cfg.output.colunas, "o formato de saída precisa ter colunas"
    assert cfg.banks, "precisa existir ao menos um banco"
    # Cada banco declara o que reconhece: sem isso, dois perfis com a mesma
    # extensão empatariam e o upload viraria erro.
    assert all(b.reconhece_algo for b in cfg.banks.values())


def test_config_real_tem_papeis_coerentes():
    """Todo papel tem que apontar para uma coluna que existe no cabeçalho."""
    from tests.conftest import CONFIG_REAL
    from core.profiles import PAPEIS
    schema = ConfigSet.load(CONFIG_REAL).output
    for papel in PAPEIS:
        assert schema.coluna(papel) in schema.colunas, (
            f"o papel {papel!r} aponta para {schema.coluna(papel)!r}, "
            f"que não está em {schema.colunas}")
    assert len(set(schema.colunas)) == len(schema.colunas), "colunas repetidas"


def test_config_real_tem_regex_validos():
    """Um regex quebrado no `regras:` derruba a classificação inteira.

    `Ruleset.from_text` compila cada padrão na carga, então chegar aqui sem
    exceção já é a prova. Os asserts existem para a falha ficar legível se um
    dia o carregamento virar preguiçoso.
    """
    from tests.conftest import CONFIG_REAL
    rules = Ruleset.from_text(ConfigSet.load(CONFIG_REAL).categories_text)
    for padrao, categoria in rules.ordered_rules:
        assert padrao.pattern, "regra sem padrão"
        assert isinstance(categoria, str)


def test_config_real_classifica_sem_estourar():
    """Vale para qualquer ruleset: classificar nunca pode levantar exceção."""
    from tests.conftest import CONFIG_REAL
    rules = Ruleset.from_text(ConfigSet.load(CONFIG_REAL).categories_text)
    for amostra in ["SUPERMERCADO X", "", "   ", "123456", "ÁÇÃO Ñ",
                    "AMAZON BR 0123", "a" * 300]:
        resultado = rules.classify(amostra)
        assert resultado.categoria == "" or isinstance(resultado.categoria, str)


# ---------------------------------------------------------------------------
# BTG Pactual — container cifrado e planilha em tabelas empilhadas
# ---------------------------------------------------------------------------

def _perfil_btg():
    from tests.conftest import CONFIG
    return ConfigSet.load(CONFIG).bank("btg")


def test_btg_le_as_duas_tabelas(btg_xlsx):
    """As compras E o pagamento da fatura, que moram em tabelas separadas.

    A de pagamentos é mais estreita — não tem "Tipo de compra" nem "Final
    Cartão" —, e ler só a de compras faria o crédito sumir da conferência.
    """
    from core.statement import read_statement
    extrato = read_statement(btg_xlsx, profile=_perfil_btg())

    assert len(extrato.entries) == 5, "4 compras + 1 pagamento"
    pagamento = [e for e in extrato.entries if e.amount < 0]
    assert len(pagamento) == 1
    assert pagamento[0].amount == -5104.93
    assert pagamento[0].cardholder == "", "a tabela de pagamentos não tem cartão"


def test_btg_nao_estraga_o_numero_da_celula(btg_xlsx):
    """`132.0` é um float na planilha, não "132,00" em pt-BR.

    Passar a célula pelo parser de texto apagaria o ponto de milhar e 132.0
    viraria 1320 — cem vezes mais caro, e sem nenhum erro visível.
    """
    from core.statement import read_statement
    extrato = read_statement(btg_xlsx, profile=_perfil_btg())
    valores = {e.description: e.amount for e in extrato.entries}
    assert valores["Petz"] == 132.00
    assert valores["Supermercado Confianca"] == 348.78


def test_btg_desgruda_a_parcela_do_nome(btg_xlsx):
    """"Petz (3/3)" -> estabelecimento "Petz", parcela "03/03".

    Zerada à esquerda porque os dois formatos do Sicredi já saem assim, e a
    coluna Descrição da planilha é lida por uma pessoa. Deixar a parcela no
    nome faria "Petz (3/3)" e "Petz (1/2)" virarem dois estabelecimentos
    diferentes, e nenhuma regra casaria com os dois.
    """
    from core.statement import read_statement
    extrato = read_statement(btg_xlsx, profile=_perfil_btg())
    petz = next(e for e in extrato.entries if e.description == "Petz")
    assert petz.installment == "03/03"
    assert "(" not in petz.description


def test_btg_marca_internacional_pela_coluna_de_tipo(btg_xlsx):
    """No Sicredi a compra no exterior está numa SEÇÃO; aqui, numa COLUNA."""
    from core.statement import read_statement
    extrato = read_statement(btg_xlsx, profile=_perfil_btg())
    fora = [e.description for e in extrato.entries if e.international]
    assert fora == ["Steamgames"]


def test_btg_usa_o_final_do_cartao_como_titular(btg_xlsx):
    """O BTG não imprime nome: quem separa as compras é o final do cartão."""
    from core.statement import read_statement
    extrato = read_statement(btg_xlsx, profile=_perfil_btg())
    assert extrato.cardholders == ["4108", "8134"]


def test_btg_tira_o_ano_do_mes_de_referencia(btg_xlsx):
    """O vencimento é "01/06" e o ano só existe no título, "Junho/2026"."""
    from core.statement import read_statement
    extrato = read_statement(btg_xlsx, profile=_perfil_btg())
    assert extrato.due_date == datetime(2026, 6, 1)


@pytest.mark.parametrize("referencia,vencimento,esperado", [
    ("Junho/2026", "01/06", datetime(2026, 6, 1)),
    ("Janeiro/2027", "01/01", datetime(2027, 1, 1)),
    # A virada do ano: a fatura é de Janeiro/2027 e vence ainda em dezembro.
    # Colar o ano da referência daria 30/12/2027 — onze meses no futuro.
    ("Janeiro/2027", "30/12", datetime(2026, 12, 30)),
    # E o contrário: fatura de Dezembro/2026 que vence já em janeiro.
    ("Dezembro/2026", "02/01", datetime(2027, 1, 2)),
])
def test_btg_vencimento_na_virada_do_ano(tmp_path, referencia, vencimento, esperado):
    from tests.conftest import _btg_workbook
    from core.statement import read_statement
    caminho = _btg_workbook(tmp_path / "virada.xlsx",
                            referencia=referencia, vencimento=vencimento)
    assert read_statement(caminho, profile=_perfil_btg()).due_date == esperado


def test_btg_confere_contra_os_lancamentos_e_nao_contra_o_total(btg_xlsx):
    """O "Total da Fatura" do BTG inclui o que não virou linha nenhuma.

    Na fatura real são R$ 20,00 de "Outros valores" — anuidade, encargos — que
    entram no total sem aparecer na lista. Comparar a soma lida com o total
    acusaria uma diferença que não é erro de leitura, e o aviso apareceria todo
    mês dizendo à pessoa para conferir uma coisa que está certa.
    """
    from core.statement import read_statement
    extrato = read_statement(btg_xlsx, profile=_perfil_btg())

    assert extrato.reconciles(), "os lançamentos fecham com o que foi declarado"
    assert extrato.declared_debits == extrato.debits
    assert extrato.declared_balance == round(extrato.debits + 20.0, 2), (
        "o total da fatura continua guardado, e é 20 maior que os lançamentos")


def test_btg_recusa_planilha_com_lancamentos_em_varias_abas(tmp_path):
    """Ler só a primeira aba descartaria compras SEM DIZER NADA.

    É o pior erro possível aqui — a fatura fecharia com um valor a menos e nada
    na tela apontaria para a causa. Reclamar deixa o problema visível.
    """
    from tests.conftest import _btg_workbook
    from core.statement import read_statement
    caminho = _btg_workbook(tmp_path / "duas-abas.xlsx", abas_extras=1)
    with pytest.raises(ProfileError, match="mais de uma aba"):
        read_statement(caminho, profile=_perfil_btg())


# ---------------------------------------------------------------------------
# core.arquivo — o invólucro, antes de qualquer banco
# ---------------------------------------------------------------------------

def test_esta_protegido_separa_cifrado_de_ole2_comum(btg_xlsx, btg_xlsx_cifrado,
                                                     sicredi_xls, sicredi_xlsx,
                                                     nubank_csv):
    """O `.xls` do Sicredi mora no MESMO container de um `.xlsx` cifrado.

    Os dois começam com `D0CF11E0`, então a mágica de 8 bytes não decide nada —
    e decidir por ela faria o portal pedir senha para toda fatura do Sicredi
    baixada do site. `sicredi_xls` é o único arquivo do projeto que faz esta
    pergunta valer: um `.xlsx` começa com `PK` e nem chega perto do impasse.
    """
    from core.arquivo import esta_protegido
    bruto = sicredi_xls.read_bytes()
    assert bruto.startswith(b"\xd0\xcf\x11\xe0"), "tem que ser OLE2 de verdade"

    assert esta_protegido(btg_xlsx_cifrado.read_bytes()) is True
    assert esta_protegido(bruto) is False
    assert esta_protegido(btg_xlsx.read_bytes()) is False
    assert esta_protegido(sicredi_xlsx.read_bytes()) is False
    assert esta_protegido(nubank_csv.read_bytes()) is False


def test_le_o_xls_antigo_do_site_do_sicredi(sicredi_xls):
    """O formato BIFF continua sendo lido — é o que o site do banco entrega.

    O `xlrd` está nos requisitos por causa dele, e até aqui nenhum teste passava
    por esse caminho: todas as fixtures de planilha eram `.xlsx`.
    """
    from tests.conftest import CONFIG
    from core.statement import read_statement
    perfil = ConfigSet.load(CONFIG).bank("sicredi")
    extrato = read_statement(sicredi_xls, profile=perfil)
    assert extrato.due_date == datetime(2026, 8, 10)
    assert extrato.reconciles()
    assert any(e.description == "SUPERMERCADOS ALVORA" for e in extrato.entries)


def test_amostra_le_o_texto_do_sharedstrings(tmp_path):
    """O Excel guarda cada string UMA vez, num arquivo à parte do zip.

    O openpyxl não faz isso — grava tudo inline na planilha —, então as
    fixtures sozinhas nunca exercitam esse caminho. Sem ele a detecção
    funcionaria nos testes e falharia na fatura de verdade, que veio do Excel.
    """
    import zipfile
    from core.arquivo import amostra_de_texto

    caminho = tmp_path / "so-sharedstrings.xlsx"
    with zipfile.ZipFile(caminho, "w") as z:
        z.writestr("xl/sharedStrings.xml",
                   '<?xml version="1.0" encoding="UTF-8"?><sst><si><t>Final '
                   'Cart\u00e3o</t></si><si><t>Per\u00edodo de Compras</t></si></sst>')
        z.writestr("xl/worksheets/sheet1.xml", "<worksheet><sheetData/></worksheet>")

    texto = amostra_de_texto(caminho.read_bytes())
    assert "Final Cartão" in texto
    assert "Período de Compras" in texto


def test_abrir_protegido_distingue_falta_de_senha_de_senha_errada(btg_xlsx_cifrado):
    """São dois estados, e a tela precisa dizer coisas diferentes em cada um."""
    from core.arquivo import PrecisaDeSenha, SenhaIncorreta, abrir_protegido
    from tests.conftest import SENHA_BTG
    bruto = btg_xlsx_cifrado.read_bytes()

    with pytest.raises(PrecisaDeSenha):
        abrir_protegido(bruto, "")
    with pytest.raises(SenhaIncorreta):
        abrir_protegido(bruto, "nao-e-essa")
    assert abrir_protegido(bruto, SENHA_BTG).startswith(b"PK\x03\x04")


def test_amostra_de_texto_enxerga_dentro_do_xlsx(btg_xlsx, nubank_csv):
    """Um `.xlsx` é um ZIP: os 8 KB crus não têm uma letra legível.

    Enquanto só o Sicredi lia planilha isso nunca apareceu — a extensão
    decidia sozinha. Com o BTG são dois `.xlsx`, e sem abrir o zip a detecção
    empataria para sempre.
    """
    from core.arquivo import amostra_de_texto
    bruto = btg_xlsx.read_bytes()
    assert b"Final Cart" not in bruto[:8192], "o zip não entrega texto de graça"

    texto = amostra_de_texto(bruto)
    assert "Final Cartão" in texto
    assert "Período de Compras" in texto
    # Arquivo que não é zip continua saindo como sempre saiu.
    assert "date,title,amount" in amostra_de_texto(nubank_csv.read_bytes())


def test_deteccao_separa_os_dois_xlsx(btg_xlsx, sicredi_xlsx):
    """Sicredi e BTG disputam o `.xlsx`; quem desempata é o conteúdo."""
    from tests.conftest import CONFIG
    from core.arquivo import amostra_de_texto
    cfg = ConfigSet.load(CONFIG)

    achado = cfg.detectar("extrato.xlsx", amostra_de_texto(btg_xlsx.read_bytes()))
    assert achado.id == "btg"
    achado = cfg.detectar("extrato.xlsx", amostra_de_texto(sicredi_xlsx.read_bytes()))
    assert achado.id == "sicredi"
