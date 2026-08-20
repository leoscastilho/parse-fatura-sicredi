"""Períodos de viagem.

O que estes testes fixam, em ordem de importância:

  1. **A data que conta é a da COMPRA.** A fixture tem uma parcela comprada em
     21/08/2024 numa fatura que vence em 10/08/2026. Uma viagem em agosto de
     2026 não pode pegá-la, e uma viagem em agosto de 2024 tem que pegar. É o
     único teste que distingue esta implementação de uma que olha a coluna
     Data — e as duas passariam em qualquer outro cenário da suíte.
  2. **A categoria real sobrevive.** Ela vai para dentro da descrição, entre
     parênteses, logo antes do `{Em 15/Jul}`. Sem isso a viagem apagaria a
     informação de no que o dinheiro foi gasto.
  3. **A viagem é aplicada por último.** A categoria anexada é a FINAL, depois
     do marketplace e das correções manuais — não a que a regra chutou.
  4. **Desmarcar reverte de verdade.** Categoria original de volta, descrição
     sem parêntese nenhum.
"""

from __future__ import annotations

import io
from datetime import date

import pytest

from core import ClassifiedLine, LineState, Ruleset, classify_sources
from core.travel import (
    TRAVEL_CATEGORY, TravelError, TravelRange, annotate, apply_travel,
    desanotar, mark_travel, purchase_dates, purchase_range, validate_ranges,
)

from .conftest import _sicredi_workbook, csv_de_saida_texto


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _linha(purchase_date: str, categoria: str = "Alimentação",
           descricao: str = "", line_id: str = "0:0",
           parcela: str | None = None) -> ClassifiedLine:
    return ClassifiedLine(
        line_id=line_id, statement="x.xls", data="08/10/2026",
        purchase_date=purchase_date, merchant_raw="X", merchant="X",
        descricao=descricao or f"[Cartão] X {{Em 15/Jul}}", valor=10.0,
        pago="", categoria=categoria, state=LineState.AUTO, parcela=parcela,
    )


def _periodo(inicio: str, fim: str, rotulo: str = "") -> TravelRange:
    return TravelRange.from_dict({"inicio": inicio, "fim": fim, "rotulo": rotulo})


def _fatura(client, tmp_path, **kwargs):
    caminho = _sicredi_workbook(tmp_path / "extrato.xlsx", **kwargs)
    with caminho.open("rb") as fh:
        resposta = client.post("/upload", files={"files": ("extrato.xlsx", fh)})
    assert resposta.status_code == 200, resposta.text
    return resposta.json()


def _baseline(client, tid) -> dict[str, dict]:
    """Como o lote sai SEM viagem nenhuma, indexado pela descrição.

    Existe para as asserções não dependerem do `categories.yml` de quem roda a
    suíte. Fixar "Oggi é Lazer" transformaria uma edição de palavra-chave em
    build vermelho — e o que estes testes precisam garantir é a TRANSFORMAÇÃO
    (a categoria real vai para o parêntese), não qual categoria a regra deu.
    """
    linhas = client.post("/preview", json={
        "transaction_id": tid, "assignments": [], "travel_rejected": [],
    }).json()["rows"]
    return {l["descricao"]: l for l in linhas}


def _anotada(descricao: str, categoria: str, rotulo: str = "") -> str:
    """`(Categoria) {Rótulo}` antes do `{Em ...}` — reimplementado de propósito.

    Se o teste chamasse `annotate`, estaria comparando a função com ela mesma.
    """
    marcas = " ".join(m for m in (f"({categoria})" if categoria else "",
                                  f"{{{rotulo}}}" if rotulo else "") if m)
    if not marcas:
        return descricao
    base, marcador, sufixo = descricao.partition(" {Em ")
    return f"{base} {marcas}{marcador}{sufixo}" if marcador else f"{base} {marcas}"


def _linhas_da_fixture(tmp_path) -> list[ClassifiedLine]:
    from pathlib import Path
    caminho = _sicredi_workbook(tmp_path / "probe.xlsx")
    raiz = Path(__file__).resolve().parent.parent
    rules = Ruleset.from_text((raiz / "config" / "categories.yml").read_text("utf-8"))
    linhas, _, _ = classify_sources(
        [("probe.xlsx", io.BytesIO(caminho.read_bytes()))], rules)
    return linhas


# ---------------------------------------------------------------------------
# TravelRange
# ---------------------------------------------------------------------------

def test_periodo_invertido_e_recusado():
    with pytest.raises(TravelError, match="invertido"):
        TravelRange.from_dict({"inicio": "2026-07-20", "fim": "2026-07-14"})


def test_data_ilegivel_e_recusada():
    with pytest.raises(TravelError, match="inválido"):
        TravelRange.from_dict({"inicio": "14/07/2026", "fim": "2026-07-20"})


def test_limites_sao_inclusivos():
    """Uma viagem de 1 a 2 de maio inclui o dia 1 e o dia 2 — são dois dias."""
    periodo = _periodo("2026-05-01", "2026-05-02")
    assert periodo.contains(date(2026, 5, 1))
    assert periodo.contains(date(2026, 5, 2))
    assert not periodo.contains(date(2026, 4, 30))
    assert not periodo.contains(date(2026, 5, 3))
    assert periodo.dias == 2


def test_viagem_de_um_dia_so():
    periodo = _periodo("2026-05-01", "2026-05-01")
    assert periodo.dias == 1
    assert periodo.contains(date(2026, 5, 1))


# ---------------------------------------------------------------------------
# mark_travel — a data da COMPRA, não a do vencimento
# ---------------------------------------------------------------------------

def test_marca_pela_data_da_compra_e_nao_pelo_vencimento(tmp_path):
    """O teste que sustenta a feature inteira.

    A parcela do Mercado Livre foi comprada em 21/08/2024 e cai numa fatura que
    vence em 10/08/2026. Uma implementação que olhasse a coluna Data marcaria a
    fatura inteira em agosto de 2026 — inclusive esta parcela de dois anos
    atrás, que não tem nada a ver com viagem nenhuma.
    """
    linhas = _linhas_da_fixture(tmp_path)

    # Janela sobre o VENCIMENTO (10/08/2026): não pode pegar nada.
    no_vencimento = mark_travel(linhas, [_periodo("2026-08-01", "2026-08-31")])
    assert [l.descricao for l in no_vencimento if l.viagem] == []

    # Janela sobre a COMPRA da parcela (21/08/2024): pega só ela.
    na_compra = mark_travel(linhas, [_periodo("2024-08-01", "2024-08-31")])
    pegas = [l for l in na_compra if l.viagem]
    assert len(pegas) == 1
    assert pegas[0].purchase_date == "2024-08-21"


def test_marca_janela_de_julho(tmp_path):
    linhas = _linhas_da_fixture(tmp_path)
    marcadas = mark_travel(linhas, [_periodo("2026-07-02", "2026-07-05")])
    pegas = sorted(l.purchase_date for l in marcadas if l.viagem)
    assert pegas == ["2026-07-02", "2026-07-03", "2026-07-04", "2026-07-05"]


def test_marcar_nao_muda_categoria_nem_descricao(tmp_path):
    """`viagem` é candidatura. Quem converte é a confirmação, mais tarde."""
    linhas = _linhas_da_fixture(tmp_path)
    marcadas = mark_travel(linhas, [_periodo("2026-07-02", "2026-07-05")])
    for antes, depois in zip(linhas, marcadas):
        assert depois.categoria == antes.categoria
        assert depois.descricao == antes.descricao


def test_sem_periodos_nada_e_marcado(tmp_path):
    linhas = _linhas_da_fixture(tmp_path)
    assert not any(l.viagem for l in mark_travel(linhas, []))


def test_periodos_multiplos_somam(tmp_path):
    linhas = _linhas_da_fixture(tmp_path)
    marcadas = mark_travel(linhas, [
        _periodo("2026-07-02", "2026-07-02"),
        _periodo("2026-07-07", "2026-07-08"),
    ])
    assert sorted(l.purchase_date for l in marcadas if l.viagem) == [
        "2026-07-02", "2026-07-07", "2026-07-08"]


def test_linha_sem_data_de_compra_nunca_e_marcada():
    """Exportação antiga sem `{Em 15/Jul}` não vira viagem por acidente."""
    sem_data = _linha(date.min.isoformat())
    ilegivel = ClassifiedLine(**{**_linha("2026-07-15").to_dict(),
                                 "purchase_date": "sei lá"})
    marcadas = mark_travel([sem_data, ilegivel],
                           [_periodo("0001-01-01", "2030-12-31")])
    assert [l.viagem for l in marcadas] == [False, False]


# ---------------------------------------------------------------------------
# purchase_range / validate_ranges
# ---------------------------------------------------------------------------

def test_purchase_range_ignora_parcelas_antigas(tmp_path):
    """A fatura cobre UM mês, mas traz parcelas de compras de anos atrás.

    A fixture tem uma parcela 08/10 comprada em 21/08/2024. Contando-a, o
    seletor de viagem oferecia dois anos para marcar uma viagem que só poderia
    ter caído nas semanas desta fatura. No arquivo real dele o efeito era o
    mesmo: a fatura de julho ia de 03/01 a 28/06, quando as compras dela vão de
    26/05 a 28/06.
    """
    linhas = _linhas_da_fixture(tmp_path)
    assert purchase_range(linhas) == (date(2026, 6, 26), date(2026, 7, 8))
    # A data antiga continua existindo na linha — o que mudou é o que o seletor
    # OFERECE, não o que o lote contém.
    assert date(2024, 8, 21) in purchase_dates(linhas)


def test_a_primeira_parcela_e_compra_deste_ciclo():
    """`01/10` foi comprada agora; `02/10` em diante carrega a data original."""
    linhas = [
        _linha("2026-07-02", parcela="01/10"),
        _linha("2024-08-21", parcela="08/10", line_id="0:1"),
    ]
    assert purchase_range(linhas) == (date(2026, 7, 2), date(2026, 7, 2))


def test_parcela_em_formato_estranho_conta_como_compra():
    """Na dúvida, intervalo mais largo: oferecer data a mais só dá opção,
    esconder data esconderia a viagem que de fato aconteceu."""
    linhas = [_linha("2024-08-21", parcela="Parcela 8 de 10")]
    assert purchase_range(linhas) == (date(2024, 8, 21), date(2024, 8, 21))


def test_fatura_so_de_parcelas_antigas_ainda_tem_intervalo():
    """Sem isto o seletor ficaria desabilitado num mês sem compra nova."""
    linhas = [
        _linha("2024-08-21", parcela="08/10"),
        _linha("2024-09-21", parcela="09/10", line_id="0:1"),
    ]
    assert purchase_range(linhas) == (date(2024, 8, 21), date(2024, 9, 21))


def test_parcela_antiga_dentro_da_janela_continua_virando_viagem():
    """O filtro é do SELETOR, não da marcação. Uma parcela cuja compra original
    caiu na viagem foi comprada na viagem — e tem que entrar."""
    linhas = [_linha("2026-07-03", parcela="03/05")]
    assert mark_travel(linhas, [_periodo("2026-07-02", "2026-07-05")])[0].viagem


def test_purchase_range_vazio_quando_nenhuma_data_e_legivel():
    assert purchase_range([_linha(date.min.isoformat())]) == (None, None)
    assert purchase_dates([_linha(date.min.isoformat())]) == []


def test_aviso_periodo_fora_do_lote(tmp_path):
    linhas = _linhas_da_fixture(tmp_path)
    avisos = validate_ranges([_periodo("2030-01-01", "2030-01-05")], linhas)
    assert len(avisos) == 1
    assert "fora das compras" in avisos[0]


def test_aviso_periodo_sem_compras(tmp_path):
    """Dentro do lote, mas caiu num buraco entre as compras.

    O buraco fica entre 26/06 e 02/07 — dentro do intervalo oferecido, que
    agora é o ciclo desta fatura e não os dois anos que a parcela 08/10
    esticava.
    """
    linhas = _linhas_da_fixture(tmp_path)
    avisos = validate_ranges([_periodo("2026-06-27", "2026-06-30")], linhas)
    assert len(avisos) == 1
    assert "não tem nenhuma compra" in avisos[0]


def test_aviso_periodos_sobrepostos(tmp_path):
    linhas = _linhas_da_fixture(tmp_path)
    avisos = validate_ranges([
        _periodo("2026-07-02", "2026-07-05"),
        _periodo("2026-07-04", "2026-07-08"),
    ], linhas)
    assert any("se sobrep" in a for a in avisos)


def test_periodo_valido_nao_gera_aviso(tmp_path):
    linhas = _linhas_da_fixture(tmp_path)
    assert validate_ranges([_periodo("2026-07-02", "2026-07-05")], linhas) == []


# ---------------------------------------------------------------------------
# annotate — a categoria real dentro da descrição
# ---------------------------------------------------------------------------

def test_categoria_entra_antes_da_data_da_compra():
    assert annotate("[Cartão] B91 Supremo Pizzaria {Em 15/Jul}", "Alimentação") == (
        "[Cartão] B91 Supremo Pizzaria (Alimentação) {Em 15/Jul}")


def test_nome_da_viagem_entra_entre_a_categoria_e_a_data():
    """O nome do período existia só na tela e não chegava à planilha — depois de
    exportar, nada dizia de qual viagem a linha era."""
    assert annotate("[Cartão] Campo Belo Country C {Em 23/Mar}",
                    "Lazer", "Campo Belo") == (
        "[Cartão] Campo Belo Country C (Lazer) {Campo Belo} {Em 23/Mar}")


def test_viagem_sem_nome_nao_ganha_chave_vazia():
    """O nome é opcional no editor de períodos; vazio não pode virar `{}`."""
    assert annotate("[Cartão] Hotel Serra {Em 15/Jul}", "Lazer") == (
        "[Cartão] Hotel Serra (Lazer) {Em 15/Jul}")
    assert annotate("[Cartão] Hotel Serra {Em 15/Jul}", "Lazer", "   ") == (
        "[Cartão] Hotel Serra (Lazer) {Em 15/Jul}")


def test_nome_da_viagem_sozinho_quando_nao_ha_categoria():
    """As duas marcas são independentes: sem categoria real ainda dá para saber
    de qual viagem a linha veio."""
    assert annotate("[Cartão] Amazon Br {Em 7/Jul}", "", "Campo Belo") == (
        "[Cartão] Amazon Br {Campo Belo} {Em 7/Jul}")


def test_anotar_com_nome_e_idempotente():
    """Refazer o /preview não empilha nem parênteses nem chaves."""
    uma_vez = annotate("[Cartão] X {Em 15/Jul}", "Hobby", "Gramado")
    assert annotate(uma_vez, "Hobby", "Gramado") == uma_vez
    assert uma_vez.count("{Gramado}") == 1


def test_anotar_e_idempotente():
    """Refazer o /preview não empilha parênteses."""
    uma_vez = annotate("[Cartão] X {Em 15/Jul}", "Hobby")
    assert annotate(uma_vez, "Hobby") == uma_vez
    assert uma_vez.count("(Hobby)") == 1


def test_anotar_preserva_a_parcela():
    assert annotate("[Cartão] Renner (Parcela 03/05) {Em 10/May}", "Vestuário") == (
        "[Cartão] Renner (Parcela 03/05) (Vestuário) {Em 10/May}")


def test_sem_data_no_fim_a_categoria_vai_no_fim():
    """Formato antigo, sem `{Em ...}`: anexa no fim em vez de não anexar."""
    assert annotate("Compra no Renner", "Vestuário") == "Compra no Renner (Vestuário)"


def test_categoria_vazia_nao_inventa_rotulo():
    original = "[Cartão] Amazon Br {Em 7/Jul}"
    assert annotate(original, "") == original
    assert annotate(original, "   ") == original


def test_linha_que_ja_era_viagem_nao_ganha_parentese_de_viagem():
    """`(Viagem)` seria a marca comendo a si mesma e não diria nada.

    A categoria real responde "o que essa compra seria se não fosse viagem".
    Numa linha que JÁ está em Viagem a coluna não tem essa resposta, e copiá-la
    para o parêntese produz uma marca que repete a coluna — e ocupa o lugar da
    marca de verdade, que é o que este caso protege.
    """
    linha = _linha("2026-07-15", categoria="Viagem",
                   descricao="[Cartão] Hotel Serra {Em 15/Jul}")
    categoria, descricao = apply_travel(linha, linha.categoria)
    assert categoria == TRAVEL_CATEGORY
    assert descricao == "[Cartão] Hotel Serra {Em 15/Jul}"


def test_marca_existente_sobrevive_a_um_periodo_novo():
    """Marcar de novo uma linha já anotada preserva a categoria real dela."""
    linha = _linha("2026-07-15", categoria="Viagem",
                   descricao="[Cartão] Hotel Serra (Hospedagem) {Em 15/Jul}")
    _, descricao = apply_travel(linha, linha.categoria, "Gramado",
                                ["Hospedagem", "Lazer"])
    assert descricao == "[Cartão] Hotel Serra (Hospedagem) {Gramado} {Em 15/Jul}"


def test_marca_e_substituida_e_nao_empilhada():
    """Reprocessar com regra nova troca o parêntese; não escreve os dois."""
    uma = annotate("[Cartão] Hotel Serra {Em 15/Jul}", "Lazer", "Gramado")
    duas = annotate(uma, "Hospedagem", "", ["Lazer", "Hospedagem"])
    assert duas == "[Cartão] Hotel Serra (Hospedagem) {Gramado} {Em 15/Jul}"
    # O rótulo não veio na segunda chamada e sobreviveu: quem reprocessa um
    # arquivo antigo não sabe o nome da viagem, e apagá-lo seria perder a única
    # coisa que dizia QUAL viagem foi.
    assert "{Gramado}" in duas


def test_parcela_nunca_e_confundida_com_a_marca():
    """`(Parcela 03/05)` tem a forma da marca e não é marca.

    A diferença é o conteúdo ser nome de categoria conhecido. Sem essa régua,
    reprocessar comeria a parcela — e é a parcela que distingue uma compra de
    400 reais de uma de 2.000 em cinco vezes.
    """
    original = "[Cartão] Renner (Parcela 03/05) (Lazer) {Em 10/May}"
    assert annotate(original, "Vestuário", "", ["Lazer", "Vestuário"]) == (
        "[Cartão] Renner (Parcela 03/05) (Vestuário) {Em 10/May}")


def test_nome_de_estabelecimento_com_parenteses_fica_inteiro():
    """`Padaria (Matriz)` não é marca de nada — não está na lista."""
    original = "[Cartão] Padaria (Matriz) {Em 10/May}"
    assert annotate(original, "Alimentação", "", ["Lazer", "Alimentação"]) == (
        "[Cartão] Padaria (Matriz) (Alimentação) {Em 10/May}")


def test_viagem_chamada_em_alguma_coisa_nao_vira_data():
    """`{Em Paris}` só PARECE `{Em 15/Jul}`; a data tem forma exigida.

    Com um padrão frouxo (`{Em qualquer coisa}`) o nome da viagem passaria por
    marca de data, `desanotar` não o encontraria como rótulo, e renomear a
    viagem escreveria a chave nova ao lado da velha em vez de no lugar dela.
    """
    marcada = annotate("[Cartão] Bistrô", "Alimentação", "Em Paris")
    assert marcada == "[Cartão] Bistrô (Alimentação) {Em Paris}"
    assert annotate(marcada, "Alimentação", "Em Roma", ["Alimentação"]) == (
        "[Cartão] Bistrô (Alimentação) {Em Roma}")


def test_desanotar_devolve_as_marcas_separadas():
    base, categoria, rotulo = desanotar(
        "[Cartão] Campo Belo C (Lazer) {Campo Belo} {Em 23/Mar}", ["Lazer"])
    assert base == "[Cartão] Campo Belo C {Em 23/Mar}"
    assert (categoria, rotulo) == ("Lazer", "Campo Belo")


def test_desanotar_sem_lista_nao_toca_parenteses():
    """Sem saber quais são categorias, o seguro é não mexer em parêntese."""
    original = "[Cartão] Campo Belo C (Lazer) {Campo Belo} {Em 23/Mar}"
    base, categoria, rotulo = desanotar(original)
    assert base == "[Cartão] Campo Belo C (Lazer) {Em 23/Mar}"
    assert (categoria, rotulo) == ("", "Campo Belo")


# ---------------------------------------------------------------------------
# API — POST /travel
# ---------------------------------------------------------------------------

def test_upload_devolve_o_intervalo_de_compras(client, tmp_path):
    """O ciclo desta fatura, não os dois anos que a parcela 08/10 esticava."""
    dados = _fatura(client, tmp_path)
    assert dados["purchase_range"] == {"inicio": "2026-06-26", "fim": "2026-07-08"}


def test_travel_marca_e_soma(client, tmp_path):
    dados = _fatura(client, tmp_path)
    resposta = client.post("/travel", json={
        "transaction_id": dados["transaction_id"],
        "ranges": [{"inicio": "2026-07-02", "fim": "2026-07-05", "rotulo": "Gramado"}],
    })
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["count"] == 4
    assert corpo["warnings"] == []
    assert all(item["viagem"] for item in corpo["items"])
    assert corpo["total"] == round(sum(i["valor"] for i in corpo["items"]), 2)


def test_travel_e_substitutivo(client, tmp_path):
    """Mandar a lista sem um período é como removê-lo — sem endpoint de delete."""
    dados = _fatura(client, tmp_path)
    tid = dados["transaction_id"]

    client.post("/travel", json={"transaction_id": tid, "ranges": [
        {"inicio": "2026-07-02", "fim": "2026-07-05"},
        {"inicio": "2026-07-07", "fim": "2026-07-08"},
    ]})
    depois = client.post("/travel", json={"transaction_id": tid, "ranges": [
        {"inicio": "2026-07-07", "fim": "2026-07-08"},
    ]}).json()
    assert depois["count"] == 2
    assert sorted(i["purchase_date"] for i in depois["items"]) == [
        "2026-07-07", "2026-07-08"]

    zerado = client.post("/travel", json={"transaction_id": tid, "ranges": []}).json()
    assert zerado["count"] == 0


def test_travel_recusa_periodo_invertido(client, tmp_path):
    dados = _fatura(client, tmp_path)
    resposta = client.post("/travel", json={
        "transaction_id": dados["transaction_id"],
        "ranges": [{"inicio": "2026-07-20", "fim": "2026-07-14"}],
    })
    assert resposta.status_code == 422
    assert "invertido" in resposta.json()["detail"]


def test_travel_avisa_periodo_fora_do_lote(client, tmp_path):
    dados = _fatura(client, tmp_path)
    corpo = client.post("/travel", json={
        "transaction_id": dados["transaction_id"],
        "ranges": [{"inicio": "2030-01-01", "fim": "2030-01-05"}],
    }).json()
    assert corpo["count"] == 0
    assert len(corpo["warnings"]) == 1


def test_travel_404_em_transacao_desconhecida(client):
    resposta = client.post("/travel", json={"transaction_id": "nada", "ranges": []})
    assert resposta.status_code == 404


def _recategorizar(client, output_schema, linhas, nome="saida.csv"):
    blob = csv_de_saida_texto(output_schema, linhas).encode("utf-8")
    return client.post("/recategorize",
                       files={"files": (nome, blob, "text/csv")}).json()


def test_travel_vale_na_recategorizacao(client, output_schema):
    """Viagem também no reprocessamento — era um 409 e virou um caso de uso.

    A viagem de 2019 só é lembrada quando o histórico inteiro está na tela; se
    a única chance de marcá-la fosse o mês em que a fatura chegou, ela nunca
    seria marcada.
    """
    dados = _recategorizar(client, output_schema, [
        {"data": "07/10/2026", "categoria": "Alimentação",
         "descricao": "[Cartão] Supermercados Alvora {Em 2/Jul}", "valor": "270.51"},
    ])
    resposta = client.post("/travel", json={
        "transaction_id": dados["transaction_id"],
        "ranges": [{"inicio": "2026-07-01", "fim": "2026-07-31"}],
    })
    assert resposta.status_code == 200
    assert resposta.json()["count"] == 1


def test_recategorizacao_marca_e_exporta_a_viagem(client, output_schema):
    """A ida: a linha vira Viagem e a categoria real entra na descrição."""
    dados = _recategorizar(client, output_schema, [
        {"data": "07/10/2026", "categoria": "Alimentação",
         "descricao": "[Cartão] Supermercados Alvora {Em 2/Jul}", "valor": "270.51"},
    ])
    tid = dados["transaction_id"]
    client.post("/travel", json={"transaction_id": tid, "ranges": [
        {"inicio": "2026-07-01", "fim": "2026-07-31", "rotulo": "Gramado"}]})

    linha = client.post("/preview", json={"transaction_id": tid,
                                          "assignments": []}).json()["rows"][0]
    assert linha["categoria"] == "Viagem"
    assert linha["descricao"] == (
        "[Cartão] Supermercados Alvora (Alimentação) {Gramado} {Em 2/Jul}")


def test_reprocessar_arquivo_de_viagem_nao_empilha(client, output_schema):
    """A volta, que é o pedido: rodar o arquivo de novo não duplica nada.

    Um arquivo que já saiu daqui com viagem marcada volta pela recategorização
    quando as regras melhoram. A coluna continua `Viagem`, o `{Gramado}`
    continua lá, e o parêntese é REESCRITO — nunca escrito duas vezes.
    """
    ja_marcada = "[Cartão] Supermercados Alvora (Alimentação) {Gramado} {Em 2/Jul}"
    dados = _recategorizar(client, output_schema, [
        {"data": "07/10/2026", "categoria": "Viagem",
         "descricao": ja_marcada, "valor": "270.51"},
    ])
    linha = client.post("/preview", json={
        "transaction_id": dados["transaction_id"], "assignments": [],
    }).json()["rows"][0]

    assert linha["categoria"] == "Viagem"
    assert linha["descricao"].count("(") == 1
    assert linha["descricao"].count("{") == 2
    # As regras da fixture continuam dizendo Alimentação para o Alvorada, então
    # a marca é reescrita idêntica — e o arquivo volta byte a byte igual.
    assert linha["descricao"] == ja_marcada


def test_marca_de_viagem_acompanha_a_regra_nova(client, output_schema, config_dir):
    """`(Alimentação)` vira `(Casa)` quando a regra passa a dizer Casa.

    É o motivo de reprocessar: a marca guarda a resposta das regras para "o que
    isso seria se não fosse viagem", e essa resposta envelhece igual às outras.
    A COLUNA não se mexe — a linha continua em Viagem.
    """
    caminho = config_dir / "categories.yml"
    caminho.write_text(
        caminho.read_text(encoding="utf-8").replace(
            "  Casa:\n", "  Casa:\n    - SUPERMERCADOS ALVORA\n", 1),
        encoding="utf-8")

    dados = _recategorizar(client, output_schema, [
        {"data": "07/10/2026", "categoria": "Viagem",
         "descricao": "[Cartão] Supermercados Alvora (Alimentação) {Gramado} {Em 2/Jul}",
         "valor": "270.51"},
    ])

    # A troca é anunciada, e FORA de `changes`: recusar uma mudança de coluna
    # fixa a categoria antiga na coluna, e fazer isso aqui gravaria
    # "Alimentação" numa linha que precisa continuar "Viagem".
    assert dados["changes"] == []
    assert [(m["de"], m["para"]) for m in dados["travel_marks"]] == [
        ("Alimentação", "Casa")]

    tid = dados["transaction_id"]
    linha = client.post("/preview", json={"transaction_id": tid,
                                          "assignments": []}).json()["rows"][0]
    assert linha["categoria"] == "Viagem"
    assert linha["descricao"] == (
        "[Cartão] Supermercados Alvora (Casa) {Gramado} {Em 2/Jul}")

    # E chega no ARQUIVO. A recategorização reescreve a linha original célula a
    # célula; enquanto a descrição não estivesse nessa lista, a marca nova
    # apareceria na tela e sumiria no download.
    baixado = client.post("/export", json={"transaction_id": tid,
                                           "commit_mapping": False}).content
    assert b"(Casa) {Gramado}" in baixado
    assert b"(Alimenta" not in baixado


def test_periodo_novo_sobre_linha_ja_marcada_nao_empilha(client, output_schema):
    """Marcar de novo o que já era viagem reescreve a marca, não soma outra.

    Acontece de verdade: o histórico volta pela recategorização e o usuário
    remarca a viagem de julho porque não lembra se já tinha marcado. As linhas
    daquela viagem já têm `(Alimentação) {Gramado}`.
    """
    dados = _recategorizar(client, output_schema, [
        {"data": "07/10/2026", "categoria": "Viagem",
         "descricao": "[Cartão] Supermercados Alvora (Alimentação) {Gramado} {Em 2/Jul}",
         "valor": "270.51"},
    ])
    tid = dados["transaction_id"]
    client.post("/travel", json={"transaction_id": tid, "ranges": [
        {"inicio": "2026-07-01", "fim": "2026-07-31", "rotulo": "Gramado de novo"}]})

    linha = client.post("/preview", json={"transaction_id": tid,
                                          "assignments": []}).json()["rows"][0]
    assert linha["descricao"] == (
        "[Cartão] Supermercados Alvora (Alimentação) {Gramado de novo} {Em 2/Jul}")
    assert linha["descricao"].count("(") == 1


def test_na_fatura_o_parentese_do_estabelecimento_e_intocavel(client, tmp_path):
    """Na IMPORTAÇÃO nenhuma marca antiga é procurada, e é de propósito.

    Ali a descrição é construída do zero a cada /preview e nunca tem marca de
    processamento anterior — não há o que substituir. O que existe é um
    parêntese que veio no nome do estabelecimento, e procurar marca ali só
    poderia comer o nome dele.
    """
    dados = _fatura(client, tmp_path, rows=[
        ("02/07/2026", "PADARIA (LAZER) LTDA", None, "270,51"),
    ])
    tid = dados["transaction_id"]
    client.post("/travel", json={"transaction_id": tid, "ranges": [
        {"inicio": "2026-07-01", "fim": "2026-07-31"}]})

    linha = client.post("/preview", json={"transaction_id": tid,
                                          "assignments": [
                                              {"scope": "merchant",
                                               "target": "PADARIA LAZER LTDA",
                                               "categoria": "Alimentação"}],
                                          }).json()["rows"][0]
    assert "(Lazer)" in linha["descricao"] or "(LAZER)" in linha["descricao"]
    assert "(Alimentação)" in linha["descricao"]


def test_marca_orfa_e_trocada_e_nao_empilhada(client, output_schema):
    """Categoria normal + marca velha na descrição: o parêntese é REESCRITO.

    Acontece com quem desfaz a viagem na planilha mexendo só na coluna: a linha
    volta para `Alimentação` e o `(Lazer)` fica órfão dentro da descrição.
    Marcá-la de novo tem de trocar o parêntese — escrever o segundo é
    exatamente o empilhamento que este trabalho existe para não deixar
    acontecer.
    """
    dados = _recategorizar(client, output_schema, [
        {"data": "07/10/2026", "categoria": "Alimentação",
         "descricao": "[Cartão] Supermercados Alvora (Lazer) {Gramado} {Em 2/Jul}",
         "valor": "270.51"},
    ])
    tid = dados["transaction_id"]
    client.post("/travel", json={"transaction_id": tid, "ranges": [
        {"inicio": "2026-07-01", "fim": "2026-07-31"}]})

    linha = client.post("/preview", json={"transaction_id": tid,
                                          "assignments": []}).json()["rows"][0]
    assert linha["categoria"] == "Viagem"
    assert linha["descricao"].count("(") == 1
    assert linha["descricao"] == (
        "[Cartão] Supermercados Alvora (Alimentação) {Gramado} {Em 2/Jul}")


def test_recategorizacao_devolve_o_intervalo_de_compras(client, output_schema):
    """Sem ele os seletores da etapa Viagem ficam soltos na recategorização."""
    dados = _recategorizar(client, output_schema, [
        {"data": "07/10/2026", "categoria": "Alimentação",
         "descricao": "[Cartão] Supermercados Alvora {Em 2/Jul}", "valor": "270.51"},
        {"data": "07/10/2026", "categoria": "Casa",
         "descricao": "[Cartão] Braseiro {Em 28/Jun}", "valor": "31.00"},
    ])
    assert dados["purchase_range"] == {"inicio": "2026-06-28", "fim": "2026-07-02"}


def test_viagem_sem_marca_nao_ganha_uma(client, output_schema):
    """Linha marcada à mão na planilha não é anotada pelas costas.

    Sem parêntese, este portal não escreveu nada ali — e um arquivo cujo
    contrato é mudar o mínimo possível não é lugar para acrescentar informação
    que ninguém pediu.
    """
    original = "[Cartão] Supermercados Alvora {Em 2/Jul}"
    dados = _recategorizar(client, output_schema, [
        {"data": "07/10/2026", "categoria": "Viagem",
         "descricao": original, "valor": "270.51"},
    ])
    assert dados["travel_marks"] == []
    linha = client.post("/preview", json={
        "transaction_id": dados["transaction_id"], "assignments": [],
    }).json()["rows"][0]
    assert linha["descricao"] == original


# ---------------------------------------------------------------------------
# API — a conversão no /preview e no /export
# ---------------------------------------------------------------------------

def test_preview_converte_confirmadas_em_viagem(client, tmp_path):
    dados = _fatura(client, tmp_path)
    tid = dados["transaction_id"]
    antes = _baseline(client, tid)

    marcadas = client.post("/travel", json={"transaction_id": tid, "ranges": [
        {"inicio": "2026-07-02", "fim": "2026-07-03"}]}).json()["items"]
    assert marcadas, "a janela precisa pegar alguma compra para o teste valer"

    linhas = {l["descricao"]: l for l in client.post("/preview", json={
        "transaction_id": tid, "assignments": [], "travel_rejected": [],
    }).json()["rows"]}

    # Dentro da janela: categoria vira Viagem e a REAL vai para o parêntese,
    # qualquer que seja ela neste categories.yml.
    for item in marcadas:
        original = antes[item["descricao"]]
        esperada = _anotada(original["descricao"], original["categoria"])
        assert esperada in linhas, f"{esperada!r} não saiu no preview"
        assert linhas[esperada]["categoria"] == "Viagem"

    # Fora da janela: byte a byte como estava.
    posto = next(d for d in antes if "Posto" in d)
    assert linhas[posto]["categoria"] == antes[posto]["categoria"]
    assert linhas[posto]["descricao"] == posto


def test_preview_escreve_o_nome_do_periodo_na_descricao(client, tmp_path):
    """O nome da viagem existia só na tela do portal. Depois de exportar, nada
    na planilha dizia de qual viagem a linha era — e é justamente a pergunta
    que se faz um ano depois."""
    dados = _fatura(client, tmp_path)
    tid = dados["transaction_id"]
    antes = _baseline(client, tid)

    marcadas = client.post("/travel", json={"transaction_id": tid, "ranges": [
        {"inicio": "2026-07-02", "fim": "2026-07-03", "rotulo": "Campo Belo"}]
    }).json()["items"]
    assert marcadas, "a janela precisa pegar alguma compra para o teste valer"

    linhas = {l["descricao"]: l for l in client.post("/preview", json={
        "transaction_id": tid, "assignments": [], "travel_rejected": [],
    }).json()["rows"]}

    for item in marcadas:
        original = antes[item["descricao"]]
        esperada = _anotada(original["descricao"], original["categoria"],
                            "Campo Belo")
        assert esperada in linhas, f"{esperada!r} não saiu no preview"
        assert "{Campo Belo} {Em " in esperada, "a chave vai ANTES da data"


def test_desmarcar_reverte_categoria_e_descricao(client, tmp_path):
    dados = _fatura(client, tmp_path)
    tid = dados["transaction_id"]
    antes = _baseline(client, tid)

    marcadas = client.post("/travel", json={"transaction_id": tid, "ranges": [
        {"inicio": "2026-07-02", "fim": "2026-07-03"}]}).json()["items"]
    alvora = next(i for i in marcadas if "Alvora" in i["descricao"])
    outras = [i for i in marcadas if i["line_id"] != alvora["line_id"]]
    assert outras, "precisa de outra linha na janela para provar o isolamento"

    linhas = {l["line_id"]: l for l in client.post("/preview", json={
        "transaction_id": tid, "assignments": [],
        "travel_rejected": [alvora["line_id"]],
    }).json()["rows"]}

    # A recusada volta INTEIRA: categoria original e descrição sem parêntese.
    original = antes[alvora["descricao"]]
    recusada = linhas[alvora["line_id"]]
    assert recusada["categoria"] == original["categoria"]
    assert recusada["descricao"] == original["descricao"]

    # As outras da mesma janela seguem viagem — recusar uma não recusa o resto.
    for item in outras:
        assert linhas[item["line_id"]]["categoria"] == "Viagem"


def test_viagem_usa_a_categoria_final_e_nao_a_da_regra(client, tmp_path):
    """A ordem importa: viagem roda DEPOIS das decisões do usuário.

    A Amazon sai em branco (marketplace). Se a viagem fosse aplicada antes da
    atribuição, o parêntese ficaria vazio e a escolha "Hobby" se perderia.
    """
    dados = _fatura(client, tmp_path)
    tid = dados["transaction_id"]
    amazon = next(i for i in dados["marketplace_items"] if "Amazon" in i["descricao"])
    client.post("/travel", json={"transaction_id": tid, "ranges": [
        {"inicio": "2026-07-07", "fim": "2026-07-07"}]})

    linhas = client.post("/preview", json={
        "transaction_id": tid,
        "assignments": [{"scope": "line", "target": amazon["line_id"],
                         "categoria": "Hobby"}],
        "travel_rejected": [],
    }).json()["rows"]

    resolvida = next(l for l in linhas if "Amazon" in l["descricao"])
    assert resolvida["categoria"] == "Viagem"
    assert resolvida["descricao"] == "[Cartão] Amazon Br (Hobby) {Em 7/Jul}"


def test_viagem_sem_categoria_real_nao_ganha_parentese(client, tmp_path):
    dados = _fatura(client, tmp_path)
    tid = dados["transaction_id"]
    client.post("/travel", json={"transaction_id": tid, "ranges": [
        {"inicio": "2026-07-07", "fim": "2026-07-07"}]})

    linhas = client.post("/preview", json={
        "transaction_id": tid, "assignments": [], "travel_rejected": [],
    }).json()["rows"]
    amazon = next(l for l in linhas if "Amazon" in l["descricao"])
    assert amazon["categoria"] == "Viagem"
    assert amazon["descricao"] == "[Cartão] Amazon Br {Em 7/Jul}"


def test_export_leva_a_anotacao_para_o_csv(client, tmp_path):
    dados = _fatura(client, tmp_path)
    tid = dados["transaction_id"]
    antes = _baseline(client, tid)

    marcadas = client.post("/travel", json={"transaction_id": tid, "ranges": [
        {"inicio": "2026-07-02", "fim": "2026-07-03"}]}).json()["items"]
    client.post("/preview", json={
        "transaction_id": tid, "assignments": [], "travel_rejected": []})

    csv_texto = client.post("/export", json={
        "transaction_id": tid, "commit_mapping": False,
    }).content.decode("utf-8")

    for item in marcadas:
        original = antes[item["descricao"]]
        esperada = _anotada(original["descricao"], original["categoria"])
        assert f"Viagem,{esperada}," in csv_texto, f"faltou {esperada!r} no CSV"

    # Fora da janela: linha idêntica à de sempre.
    posto = next(d for d in antes if "Posto" in d)
    assert f"{antes[posto]['categoria']},{posto}," in csv_texto


def test_export_respeita_o_travel_rejected_guardado_no_preview(client, tmp_path):
    """Omitir o campo no /export = "usa o que ficou do /preview"."""
    dados = _fatura(client, tmp_path)
    tid = dados["transaction_id"]
    antes = _baseline(client, tid)

    marcadas = client.post("/travel", json={"transaction_id": tid, "ranges": [
        {"inicio": "2026-07-02", "fim": "2026-07-03"}]}).json()["items"]
    alvora = next(i for i in marcadas if "Alvora" in i["descricao"])

    client.post("/preview", json={
        "transaction_id": tid, "assignments": [],
        "travel_rejected": [alvora["line_id"]]})

    csv_texto = client.post("/export", json={
        "transaction_id": tid, "commit_mapping": False,
    }).content.decode("utf-8")

    # A recusada sai como sempre saiu...
    original = antes[alvora["descricao"]]
    assert f"{original['categoria']},{original['descricao']}," in csv_texto
    # ...e as outras da janela seguem viagem.
    for item in marcadas:
        if item["line_id"] == alvora["line_id"]:
            continue
        outra = antes[item["descricao"]]
        assert f"Viagem,{_anotada(outra['descricao'], outra['categoria'])}," in csv_texto


def test_sem_viagem_o_export_e_o_de_sempre(client, tmp_path):
    """Regressão: quem não usa a feature não vê diferença nenhuma."""
    dados = _fatura(client, tmp_path)
    tid = dados["transaction_id"]
    client.post("/preview", json={"transaction_id": tid, "assignments": []})
    csv_texto = client.post("/export", json={
        "transaction_id": tid, "commit_mapping": False,
    }).content.decode("utf-8")

    assert "Viagem" not in csv_texto
    assert "[Cartão] Supermercados Alvora {Em 2/Jul}" in csv_texto


def test_encolher_periodo_esquece_rejeicoes_orfas(client, tmp_path):
    """Se a linha deixou de ser candidata, a rejeição dela não pode ressuscitar.

    Sem a limpeza, remover e recriar o período traria de volta uma decisão que
    o usuário não lembra de ter tomado.
    """
    dados = _fatura(client, tmp_path)
    tid = dados["transaction_id"]
    antes = _baseline(client, tid)

    marcadas = client.post("/travel", json={"transaction_id": tid, "ranges": [
        {"inicio": "2026-07-02", "fim": "2026-07-05"}]}).json()["items"]
    alvora = next(i for i in marcadas if "Alvora" in i["descricao"])

    client.post("/preview", json={"transaction_id": tid, "assignments": [],
                                  "travel_rejected": [alvora["line_id"]]})

    # A janela sai de cima da Alvora e volta.
    client.post("/travel", json={"transaction_id": tid, "ranges": [
        {"inicio": "2026-07-05", "fim": "2026-07-05"}]})
    client.post("/travel", json={"transaction_id": tid, "ranges": [
        {"inicio": "2026-07-02", "fim": "2026-07-05"}]})

    csv_texto = client.post("/export", json={
        "transaction_id": tid, "commit_mapping": False,
    }).content.decode("utf-8")
    original = antes[alvora["descricao"]]
    esperada = _anotada(original["descricao"], original["categoria"])
    assert f"Viagem,{esperada}," in csv_texto
