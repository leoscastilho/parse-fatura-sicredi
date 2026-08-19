"""Recategorizar um CSV que já saiu daqui.

O compromisso desta função cabe numa frase: **só a coluna Categoria muda**.
Todo teste aqui existe para impedir que ela deixe de ser verdade — mesma
quantidade de linhas, mesma ordem, mesmos valores, mesmas descrições.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

from core import (
    ConfigSet, LineState, Ruleset, classify_sources, lines_to_csv,
    lines_to_csv_preserving_order, read_output_csv, recategorize,
)
from core.recategorize import RecategorizeError

from .conftest import _sicredi_workbook, com_um_chute, csv_de_saida_texto


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def csv_de_saida(tmp_path, config_dir) -> Path:
    """Um CSV gerado pelo próprio portal, que é o que se recategoriza."""
    cfg = ConfigSet.load(config_dir)
    rules = Ruleset.from_text(cfg.categories_text)
    extrato = _sicredi_workbook(tmp_path / "extrato.xlsx")
    linhas, _, _ = classify_sources([("extrato.xlsx", extrato)], rules,
                                    profile=cfg.bank("sicredi"), schema=cfg.output)
    destino = tmp_path / "saida.csv"
    destino.write_bytes(lines_to_csv(linhas, schema=cfg.output))
    return destino


def _tabela(blob: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(blob.decode("utf-8"))))


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------

def test_le_o_proprio_formato_de_saida(csv_de_saida, config_dir):
    cfg = ConfigSet.load(config_dir)
    linhas = read_output_csv(csv_de_saida, name="saida.csv", schema=cfg.output)

    assert len(linhas) == len(_tabela(csv_de_saida.read_bytes())) - 1
    alvora = next(l for l in linhas if "Alvora" in l.descricao)
    # O estabelecimento sai de dentro da descrição, sem coluna extra nenhuma.
    assert alvora.merchant == "SUPERMERCADOS ALVORA"
    assert alvora.purchase_date == "2026-07-02"
    assert alvora.categoria_anterior == "Alimentação"
    assert alvora.categoria == "", "a categoria só é preenchida pela regra"


def test_recusa_arquivo_sem_as_colunas_do_formato(tmp_path, config_dir):
    ruim = tmp_path / "outro.csv"
    ruim.write_text("Data,Item,Valor\n01/01/2026,X,1.0\n", encoding="utf-8")
    cfg = ConfigSet.load(config_dir)
    with pytest.raises(RecategorizeError, match="faltam as colunas"):
        read_output_csv(ruim, name="outro.csv", schema=cfg.output)


def test_recusa_valor_ilegivel(tmp_path, config_dir):
    ruim = tmp_path / "x.csv"
    cfg = ConfigSet.load(config_dir)
    ruim.write_text(csv_de_saida_texto(cfg.output, [
        {"data": "08/10/2026", "categoria": "Casa",
         "descricao": "[Cartão] X {Em 1/Jul}", "valor": "1.234,56"}]),
        encoding="utf-8")
    with pytest.raises(RecategorizeError, match="valor ilegível"):
        read_output_csv(ruim, name="x.csv", schema=cfg.output)


def test_aceita_bom_do_excel(tmp_path, config_dir, csv_de_saida):
    com_bom = tmp_path / "bom.csv"
    com_bom.write_bytes(b"\xef\xbb\xbf" + csv_de_saida.read_bytes())
    cfg = ConfigSet.load(config_dir)
    assert read_output_csv(com_bom, name="bom.csv", schema=cfg.output)


# ---------------------------------------------------------------------------
# Reclassificação
# ---------------------------------------------------------------------------

def test_regra_nova_recategoriza_e_registra_a_mudanca(csv_de_saida, config_dir):
    cfg = ConfigSet.load(config_dir)
    linhas = read_output_csv(csv_de_saida, name="s.csv", schema=cfg.output)

    texto = cfg.categories_text.replace("  Casa:\n", "  Casa:\n    - LOJA XPTO\n", 1)
    novas, mudancas = recategorize(linhas, Ruleset.from_text(texto))

    assert len(mudancas) == 1
    assert mudancas[0].de == ""              # antes estava sem categoria
    assert mudancas[0].para == "Casa"
    assert next(l for l in novas if "Loja Xpto" in l.descricao).categoria == "Casa"


def test_marketplace_mantem_a_categoria_que_ja_estava(csv_de_saida, config_dir, tmp_path):
    """Zerar marketplace descartaria anos de decisão manual."""
    cfg = ConfigSet.load(config_dir)

    # Simula um CSV em que a Amazon já tinha sido classificada à mão.
    tabela = _tabela(csv_de_saida.read_bytes())
    cabecalho, corpo = tabela[0], tabela[1:]
    coluna_cat = cabecalho.index(cfg.output.coluna("categoria"))
    coluna_desc = cabecalho.index(cfg.output.coluna("descricao"))
    for linha in corpo:
        if "Amazon" in linha[coluna_desc]:
            linha[coluna_cat] = "Hobby"

    editado = tmp_path / "editado.csv"
    with editado.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(cabecalho)
        w.writerows(corpo)

    linhas = read_output_csv(editado, name="e.csv", schema=cfg.output)
    novas, mudancas = recategorize(linhas, Ruleset.from_text(cfg.categories_text))

    amazon = next(l for l in novas if "Amazon" in l.descricao)
    assert amazon.state is LineState.MARKETPLACE, "continua sendo marketplace"
    assert amazon.categoria == "Hobby", "a decisão manual tem que sobreviver"
    assert not any(m.line_id == amazon.line_id for m in mudancas)


def test_desconhecido_mantem_a_categoria_do_arquivo(tmp_path, config_dir):
    cfg = ConfigSet.load(config_dir)
    arquivo = tmp_path / "d.csv"
    arquivo.write_text(csv_de_saida_texto(cfg.output, [
        {"data": "08/10/2026", "categoria": "Outros",
         "descricao": "[Cartão] Renata Ribeiro Pinto {Em 7/Feb}", "valor": "160.6"}]),
        encoding="utf-8")
    linhas = read_output_csv(arquivo, name="d.csv", schema=cfg.output)
    novas, mudancas = recategorize(linhas, Ruleset.from_text(cfg.categories_text))
    assert novas[0].categoria == "Outros"
    assert not mudancas


def test_reprocessar_duas_vezes_nao_muda_nada(csv_de_saida, config_dir):
    """Idempotência: a segunda passada com as mesmas regras é um no-op."""
    cfg = ConfigSet.load(config_dir)
    rules = Ruleset.from_text(cfg.categories_text)

    primeira, _ = recategorize(
        read_output_csv(csv_de_saida, name="s.csv", schema=cfg.output), rules)
    blob = lines_to_csv_preserving_order(primeira, schema=cfg.output)

    segunda, mudancas = recategorize(
        read_output_csv(io.BytesIO(blob), name="s2.csv", schema=cfg.output), rules)
    assert not mudancas
    assert lines_to_csv_preserving_order(segunda, schema=cfg.output) == blob


# ---------------------------------------------------------------------------
# A GARANTIA
# ---------------------------------------------------------------------------

def test_so_a_coluna_categoria_muda(csv_de_saida, config_dir):
    cfg = ConfigSet.load(config_dir)
    texto = cfg.categories_text.replace("  Casa:\n", "  Casa:\n    - LOJA XPTO\n", 1)

    linhas = read_output_csv(csv_de_saida, name="s.csv", schema=cfg.output)
    novas, _ = recategorize(linhas, Ruleset.from_text(texto))
    refeito = lines_to_csv_preserving_order(novas, schema=cfg.output)

    antes, depois = _tabela(csv_de_saida.read_bytes()), _tabela(refeito)

    assert antes[0] == depois[0], "o cabeçalho mudou"
    assert len(antes) == len(depois), "o número de linhas mudou"

    coluna_categoria = antes[0].index("Categoria")
    for numero, (a, b) in enumerate(zip(antes[1:], depois[1:]), start=2):
        diferentes = [i for i in range(len(a)) if a[i] != b[i]]
        assert diferentes in ([], [coluna_categoria]), (
            f"linha {numero}: mudaram as colunas "
            f"{[antes[0][i] for i in diferentes]}, só Categoria era permitido"
        )


def test_ordem_das_linhas_e_preservada(csv_de_saida, config_dir):
    """Reordenar quebraria 'só a categoria mudou' para quem compara lado a lado."""
    cfg = ConfigSet.load(config_dir)
    linhas = read_output_csv(csv_de_saida, name="s.csv", schema=cfg.output)
    novas, _ = recategorize(linhas, Ruleset.from_text(cfg.categories_text))
    refeito = lines_to_csv_preserving_order(novas, schema=cfg.output)

    coluna = _tabela(csv_de_saida.read_bytes())[0].index(cfg.output.coluna("descricao"))
    assert ([l[coluna] for l in _tabela(csv_de_saida.read_bytes())[1:]]
            == [l[coluna] for l in _tabela(refeito)[1:]])


def test_soma_dos_valores_nao_muda(csv_de_saida, config_dir):
    cfg = ConfigSet.load(config_dir)
    linhas = read_output_csv(csv_de_saida, name="s.csv", schema=cfg.output)
    novas, _ = recategorize(linhas, Ruleset.from_text(cfg.categories_text))
    assert round(sum(l.valor for l in linhas), 2) == round(sum(l.valor for l in novas), 2)


# ---------------------------------------------------------------------------
# Pela API
# ---------------------------------------------------------------------------

def test_endpoint_recategoriza_e_exporta(client, csv_de_saida, output_schema):
    original = csv_de_saida.read_bytes()

    client.post("/rules/edit", json={"operations": [
        {"op": "add", "block": "palavras", "categoria": "Casa", "value": "LOJA XPTO"}]})

    resposta = client.post("/recategorize",
                           files=[("files", ("s.csv", original, "text/csv"))]).json()
    assert resposta["modo"] == "recategorizacao"
    assert resposta["source_files"][0]["rows"] == len(_tabela(original)) - 1
    assert len(resposta["changes"]) == 1
    assert resposta["changes"][0]["para"] == "Casa"
    assert resposta["unchanged"] == resposta["source_files"][0]["rows"] - 1

    exportado = client.post("/export", json={"transaction_id": resposta["transaction_id"],
                                             "assignments": [], "commit_mapping": False})
    assert exportado.status_code == 200
    assert "recategorizado_s.csv" in exportado.headers["content-disposition"]

    antes, depois = _tabela(original), _tabela(exportado.content)
    coluna = antes[0].index(output_schema.coluna("categoria"))
    for a, b in zip(antes, depois):
        assert [i for i in range(len(a)) if a[i] != b[i]] in ([], [coluna])


def test_endpoint_recusa_xls(client, sicredi_xlsx):
    resposta = client.post("/recategorize", files=[
        ("files", ("e.xlsx", sicredi_xlsx.read_bytes(), "application/octet-stream"))])
    assert resposta.status_code == 415


def test_endpoint_recusa_csv_de_outro_formato(client, nubank_csv):
    resposta = client.post("/recategorize", files=[
        ("files", ("nu.csv", nubank_csv.read_bytes(), "text/csv"))])
    assert resposta.status_code == 422
    assert "faltam as colunas" in resposta.json()["detail"]


def test_recategorizacao_aceita_atribuicao_manual(client, csv_de_saida):
    """A revisão é a mesma do fluxo de fatura: dá para sobrepor a regra."""
    resposta = client.post("/recategorize", files=[
        ("files", ("s.csv", csv_de_saida.read_bytes(), "text/csv"))]).json()
    tx = resposta["transaction_id"]
    linha = resposta["marketplace_items"][0]

    preview = client.post("/preview", json={"transaction_id": tx, "assignments": [
        {"scope": "line", "target": linha["line_id"], "categoria": "Hobby"}]}).json()
    alterada = next(r for r in preview["rows"] if r["line_id"] == linha["line_id"])
    assert alterada["categoria"] == "Hobby"


def test_preview_da_recategorizacao_nao_reordena(client, csv_de_saida, output_schema):
    original = csv_de_saida.read_bytes()
    resposta = client.post("/recategorize", files=[
        ("files", ("s.csv", original, "text/csv"))]).json()
    preview = client.post("/preview", json={"transaction_id": resposta["transaction_id"],
                                            "assignments": []}).json()
    coluna = _tabela(original)[0].index(output_schema.coluna("descricao"))
    assert ([r["descricao"] for r in preview["rows"]]
            == [l[coluna] for l in _tabela(original)[1:]])


def test_varios_csvs_de_uma_vez(client, csv_de_saida):
    blob = csv_de_saida.read_bytes()
    resposta = client.post("/recategorize", files=[
        ("files", ("jan.csv", blob, "text/csv")),
        ("files", ("fev.csv", blob, "text/csv")),
    ]).json()
    assert len(resposta["source_files"]) == 2
    total = sum(f["rows"] for f in resposta["source_files"])
    preview = client.post("/preview", json={"transaction_id": resposta["transaction_id"],
                                            "assignments": []}).json()
    assert len(preview["rows"]) == total
    assert "recategorizado_2_arquivos" in preview["filename"]


# ---------------------------------------------------------------------------
# Formatos antigos: sem [Cartão], sem {Em ...}, com colunas a mais
# ---------------------------------------------------------------------------

# Uma exportação ANTIGA da planilha: sem `[Cartão]`, sem `{Em 15/Jul}`, com
# espaços sobrando e com colunas que o portal não conhece. Os nomes das colunas
# saem do schema — o que se está testando é o formato legado da DESCRIÇÃO, não
# como as colunas se chamam hoje.
LEGADO_LINHAS = [
    {"data": "02/15/2019", "categoria": "Alimentação",
     "descricao": "Supermercado Alvorada", "valor": "270.50", "Mês": "2", "Ano": "2019"},
    {"data": "02/16/2019", "categoria": "",
     "descricao": "Renner  ", "valor": "79.960", "Mês": "2", "Ano": "2019"},
    {"data": "02/17/2019", "categoria": "Outros",
     "descricao": "  Padaria Brasil", "valor": "3.00", "Mês": "2", "Ano": "2019"},
    {"data": "02/18/2019", "categoria": "",
     "descricao": "Loja Que Ninguem Conhece", "valor": "12.34", "Mês": "2", "Ano": "2019"},
]
LEGADO_EXTRAS = ("Mês", "Ano")


@pytest.fixture
def legado_texto(output_schema) -> str:
    return csv_de_saida_texto(output_schema, LEGADO_LINHAS, LEGADO_EXTRAS)


@pytest.fixture
def csv_legado(tmp_path, legado_texto) -> Path:
    destino = tmp_path / "legado.csv"
    destino.write_text(legado_texto, encoding="utf-8")
    return destino


def test_classifica_sem_prefixo_e_sem_data_na_descricao(csv_legado, config_dir):
    """Exportações antigas não têm `[Cartão]` nem `{Em 15/Jul}`. Não importa."""
    cfg = ConfigSet.load(config_dir)
    linhas = read_output_csv(csv_legado, name="legado.csv", schema=cfg.output)
    novas, _ = recategorize(linhas, Ruleset.from_text(cfg.categories_text))

    por_descricao = {l.descricao.strip(): l.categoria for l in novas}
    assert por_descricao["Supermercado Alvorada"] == "Alimentação"
    assert por_descricao["Renner"] == "Vestuário"
    assert por_descricao["Padaria Brasil"] == "Alimentação"
    # Sem regra e sem categoria na origem: continua vazia, sem inventar nada.
    assert por_descricao["Loja Que Ninguem Conhece"] == ""


def test_sem_data_de_compra_nao_atrapalha(csv_legado, config_dir):
    cfg = ConfigSet.load(config_dir)
    linhas = read_output_csv(csv_legado, name="legado.csv", schema=cfg.output)
    assert all(l.purchase_date == "0001-01-01" for l in linhas), "não há {Em ...}"
    # E mesmo assim tudo processa e exporta.
    novas, _ = recategorize(linhas, Ruleset.from_text(cfg.categories_text))
    assert lines_to_csv_preserving_order(novas, schema=cfg.output)


def test_descricao_volta_intacta(csv_legado, config_dir):
    """Espaços à esquerda e à direita fazem parte do dado, não são sujeira."""
    cfg = ConfigSet.load(config_dir)
    linhas = read_output_csv(csv_legado, name="legado.csv", schema=cfg.output)
    novas, _ = recategorize(linhas, Ruleset.from_text(cfg.categories_text))
    saida = lines_to_csv_preserving_order(novas, schema=cfg.output).decode()

    assert "Renner  ," in saida, "os espaços à direita sumiram"
    assert ",  Padaria Brasil," in saida, "os espaços à esquerda sumiram"


def test_formatacao_do_valor_nao_e_reescrita(csv_legado, config_dir):
    """`270.50` não pode virar `270.5` — seria a coluna Valor mudando."""
    cfg = ConfigSet.load(config_dir)
    linhas = read_output_csv(csv_legado, name="legado.csv", schema=cfg.output)
    novas, _ = recategorize(linhas, Ruleset.from_text(cfg.categories_text))
    saida = lines_to_csv_preserving_order(novas, schema=cfg.output).decode()

    for valor in ("270.50", "79.960", "3.00", "12.34"):
        assert f",{valor}," in saida, f"{valor} foi reescrito"


def test_colunas_desconhecidas_sobrevivem(csv_legado, config_dir):
    """`Mês` e `Ano` vêm da planilha e o portal não pode descartá-las."""
    cfg = ConfigSet.load(config_dir)
    linhas = read_output_csv(csv_legado, name="legado.csv", schema=cfg.output)
    novas, _ = recategorize(linhas, Ruleset.from_text(cfg.categories_text))
    tabela = _tabela(lines_to_csv_preserving_order(novas, schema=cfg.output))

    assert tabela[0] == list(cfg.output.colunas) + list(LEGADO_EXTRAS)
    assert tabela[1][-2:] == ["2", "2019"]


def test_legado_so_muda_categoria(csv_legado, legado_texto, config_dir):
    """A mesma garantia, agora num arquivo que este portal nunca gerou."""
    cfg = ConfigSet.load(config_dir)
    linhas = read_output_csv(csv_legado, name="legado.csv", schema=cfg.output)
    novas, _ = recategorize(linhas, Ruleset.from_text(cfg.categories_text))

    antes = _tabela(legado_texto.encode("utf-8"))
    depois = _tabela(lines_to_csv_preserving_order(novas, schema=cfg.output))
    coluna = antes[0].index(cfg.output.coluna("categoria"))

    assert antes[0] == depois[0]
    assert len(antes) == len(depois)
    for numero, (a, b) in enumerate(zip(antes[1:], depois[1:]), start=2):
        diferentes = [i for i in range(len(a)) if a[i] != b[i]]
        assert diferentes in ([], [coluna]), (
            f"linha {numero}: mudaram {[antes[0][i] for i in diferentes]}")


def test_ordem_das_colunas_e_preservada(tmp_path, config_dir):
    """Se o arquivo antigo tem as colunas noutra ordem, ela volta igual."""
    cfg = ConfigSet.load(config_dir)
    ordem = list(reversed(cfg.output.colunas))
    valores = {cfg.output.coluna("pago"): "x", cfg.output.coluna("valor"): "10.00",
               cfg.output.coluna("descricao"): "Padaria Brasil",
               cfg.output.coluna("categoria"): "", cfg.output.coluna("data"): "03/01/2020"}
    invertido = tmp_path / "inv.csv"
    invertido.write_text(",".join(ordem) + "\n"
                         + ",".join(valores[c] for c in ordem) + "\n",
                         encoding="utf-8")
    linhas = read_output_csv(invertido, name="inv.csv", schema=cfg.output)
    novas, _ = recategorize(linhas, Ruleset.from_text(cfg.categories_text))
    tabela = _tabela(lines_to_csv_preserving_order(novas, schema=cfg.output))

    assert tabela[0] == ordem
    valores[cfg.output.coluna("categoria")] = "Alimentação"
    assert tabela[1] == [valores[c] for c in ordem]


def test_endpoint_aceita_formato_legado(client, csv_legado):
    resposta = client.post("/recategorize", files=[
        ("files", ("legado.csv", csv_legado.read_bytes(), "text/csv"))])
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["source_files"][0]["rows"] == 4

    exportado = client.post("/export", json={"transaction_id": corpo["transaction_id"],
                                             "assignments": [], "commit_mapping": False})
    assert "Mês,Ano" in exportado.content.decode().splitlines()[0]
    assert "270.50" in exportado.content.decode()


# ---------------------------------------------------------------------------
# Recusar uma mudança
# ---------------------------------------------------------------------------

def _muda_uma_regra(client, csv_de_saida, config_dir):
    """Faz o portal enxergar UMA mudança: LOJA XPTO passa a ser Casa."""
    alvo = config_dir / "categories.yml"
    alvo.write_text(alvo.read_text(encoding="utf-8")
                    .replace("  Casa:\n", "  Casa:\n    - LOJA XPTO\n", 1),
                    encoding="utf-8")
    corpo = client.post("/recategorize", files=[
        ("files", ("saida.csv", csv_de_saida.read_bytes(), "text/csv"))]).json()
    return corpo


def test_mudanca_recusada_volta_para_a_categoria_do_arquivo(
        client, csv_de_saida, config_dir):
    """Recusar não é limpar.

    A linha tem que voltar para a categoria que estava NO ARQUIVO — o que o
    front faz mandando uma atribuição de linha com `de`. Se recusar zerasse a
    categoria, um clique errado apagaria a decisão manual da planilha.
    """
    corpo = _muda_uma_regra(client, csv_de_saida, config_dir)
    mudanca = next(m for m in corpo["changes"] if "Loja Xpto" in m["descricao"])
    assert mudanca["para"] == "Casa"

    exportado = client.post("/export", json={
        "transaction_id": corpo["transaction_id"],
        "assignments": [{"scope": "line", "target": mudanca["line_id"],
                         "categoria": mudanca["de"]}],
        "commit_mapping": False,
    }).content.decode("utf-8")

    linha = next(l for l in exportado.splitlines() if "Loja Xpto" in l)
    assert ",Casa," not in linha
    assert linha.split(",")[1] == mudanca["de"]


def test_mudanca_aceita_e_a_recusada_convivem(client, csv_de_saida, config_dir):
    """Recusar uma não pode arrastar as outras junto."""
    corpo = _muda_uma_regra(client, csv_de_saida, config_dir)
    exportado = client.post("/export", json={
        "transaction_id": corpo["transaction_id"],
        "assignments": [], "commit_mapping": False,
    }).content.decode("utf-8")
    linha = next(l for l in exportado.splitlines() if "Loja Xpto" in l)
    assert linha.split(",")[1] == "Casa", "sem recusa, a regra vence"


def test_categoria_anterior_chega_ao_preview(client, csv_de_saida, config_dir):
    """É o `categoria_anterior` de cada linha que alimenta o "de -> para" e o
    filtro "só o que mudou" na tela de conferência."""
    corpo = _muda_uma_regra(client, csv_de_saida, config_dir)
    linhas = client.post("/preview", json={
        "transaction_id": corpo["transaction_id"], "assignments": [],
    }).json()["rows"]

    xpto = next(l for l in linhas if "Loja Xpto" in l["descricao"])
    assert xpto["categoria_anterior"] == ""
    assert xpto["categoria"] == "Casa"

    # As que não mudaram trazem anterior == atual: é o que o filtro descarta.
    inalteradas = [l for l in linhas if l["categoria_anterior"] == l["categoria"]]
    assert len(inalteradas) == len(linhas) - 1


# ---------------------------------------------------------------------------
# Renomear colunas no formato de saída
# ---------------------------------------------------------------------------
#
# O portal deixa trocar os nomes das colunas, e trocar quebrava TUDO: o writer
# montava a linha com `Descrição`/`Valor (R$)` fixos e o csv.DictWriter recusava
# ("dict contains fields not in fieldnames"). Estes testes rodam com nomes
# propositalmente diferentes dos padrões, então valem independentemente de como
# o categories.yml/output.yml de quem está rodando estiver configurado.

RENOMEADO = """
colunas: [Quando, Classe, Lançamento, Quanto, Quitado]

data:
  origem: vencimento
  formato: "%m/%d/%Y"

Lançamento:
  modelo: "[Cartão] {descricao}{parcela}{sufixo_data}"
  parcela: " (Parcela {parcela})"
  sufixo_data: " {{Em {dia}/{mes}}}"

pago: x
arquivo:
  encoding: utf-8
  um_extrato: "fatura_{periodo}.csv"
"""


@pytest.fixture
def config_renomeada(config_dir) -> Path:
    (config_dir / "output.yml").write_text(RENOMEADO, encoding="utf-8")
    return config_dir


def test_papeis_saem_da_posicao_das_colunas(config_renomeada):
    schema = ConfigSet.load(config_renomeada).output
    assert schema.coluna("data") == "Quando"
    assert schema.coluna("categoria") == "Classe"
    assert schema.coluna("descricao") == "Lançamento"
    assert schema.coluna("valor") == "Quanto"
    assert schema.coluna("pago") == "Quitado"


def test_bloco_de_descricao_aceita_o_nome_da_coluna(config_renomeada):
    """Quem renomeia a coluna renomeia o bloco junto — e é o que se espera.

    Antes o parser só conhecia a chave `descricao:`; renomear o bloco para
    `Item:` fazia o modelo cair no default em silêncio.
    """
    schema = ConfigSet.load(config_renomeada).output
    assert schema.modelo == "[Cartão] {descricao}{parcela}{sufixo_data}"
    assert schema.sufixo_data == " {{Em {dia}/{mes}}}"


def test_exporta_com_as_colunas_renomeadas(config_renomeada, tmp_path):
    cfg = ConfigSet.load(config_renomeada)
    extrato = _sicredi_workbook(tmp_path / "extrato.xlsx")
    linhas, _, _ = classify_sources([("extrato.xlsx", extrato)],
                                    Ruleset.from_text(cfg.categories_text),
                                    profile=cfg.bank("sicredi"), schema=cfg.output)
    tabela = _tabela(lines_to_csv(linhas, schema=cfg.output))

    assert tabela[0] == ["Quando", "Classe", "Lançamento", "Quanto", "Quitado"]
    alvora = next(l for l in tabela[1:] if "Alvora" in l[2])
    assert alvora[2].startswith("[Cartão] Supermercados Alvora")
    assert "{Em " in alvora[2], "o sufixo de data continua sendo aplicado"


def test_recategoriza_arquivo_com_colunas_renomeadas(config_renomeada, tmp_path):
    """Ida e volta completa: exporta com nomes novos e reprocessa esse arquivo."""
    cfg = ConfigSet.load(config_renomeada)
    extrato = _sicredi_workbook(tmp_path / "extrato.xlsx")
    linhas, _, _ = classify_sources([("extrato.xlsx", extrato)],
                                    Ruleset.from_text(cfg.categories_text),
                                    profile=cfg.bank("sicredi"), schema=cfg.output)
    saida = tmp_path / "saida.csv"
    saida.write_bytes(lines_to_csv(linhas, schema=cfg.output))

    relidas = read_output_csv(saida, name="saida.csv", schema=cfg.output)
    assert len(relidas) == len(linhas)
    novas, _ = recategorize(relidas, Ruleset.from_text(cfg.categories_text))

    antes = _tabela(saida.read_bytes())
    depois = _tabela(lines_to_csv_preserving_order(novas, schema=cfg.output))
    assert antes[0] == depois[0] == ["Quando", "Classe", "Lançamento", "Quanto", "Quitado"]
    coluna_cat = antes[0].index("Classe")
    for a, b in zip(antes[1:], depois[1:]):
        assert [i for i in range(len(a)) if a[i] != b[i]] in ([], [coluna_cat])
