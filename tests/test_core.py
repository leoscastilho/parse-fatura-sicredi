"""Motor de classificação: normalização, precedência, leitura e ordenação.

Estes são os testes que protegem o comportamento em que confio para colar na
planilha. Se algum quebrar, o CSV está errado — não é questão de estilo.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from core import (
    ConfigSet, LineState, Ruleset, classify_sources, classify_statement,
    lines_to_csv, merchant_key, normalize, output_name, read_statement, sort_lines,
)
from core.profiles import OutputSchema
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
    assert cfg.banks["nubank"].validado is False, "placeholder não pode se dizer validado"


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


def test_perfil_recusa_extensao_de_outro_banco(config_dir):
    cfg = ConfigSet.load(config_dir)
    assert cfg.bank("sicredi").accepts("extrato.xls")
    assert not cfg.bank("sicredi").accepts("extrato.csv")
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
