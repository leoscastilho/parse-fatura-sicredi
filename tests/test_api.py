"""API: o fluxo de 6 passos, multi-banco e o pacote de configuração.

O teste mais importante do arquivo é `test_api_e_cli_produzem_o_mesmo_csv`: se
o portal classificar diferente do script, tudo o mais é irrelevante.
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime

import pytest

from core import ConfigSet, Ruleset, classify_sources, lines_to_csv

from .conftest import com_um_chute, com_uma_redundancia


def _upload(client, path, banco="", vencimento="", nome=None):
    return client.post(
        "/upload",
        data={"banco": banco, "vencimento": vencimento},
        files=[("files", (nome or path.name, path.read_bytes(), "application/octet-stream"))],
    )


# ---------------------------------------------------------------------------
# Fluxo principal
# ---------------------------------------------------------------------------

def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_categories_expoe_as_regras(client):
    data = client.get("/categories").json()
    assert "Alimentação" in data["categories"]
    assert data["default_category"] == "", "o padrão tem que ser categoria VAZIA"
    assert "AMAZON" in data["marketplaces"]


def test_upload_separa_os_quatro_baldes(client, sicredi_xlsx):
    data = _upload(client, sicredi_xlsx).json()
    assert data["transaction_id"]
    assert data["statements"][0]["reconciles"]

    baldes = {
        "auto": {g["merchant"] for g in data["auto_classified_items"]},
        "unmapped": {g["merchant"] for g in data["unmapped_items"]},
        "marketplace": {l["merchant"] for l in data["marketplace_items"]},
    }
    assert "SUPERMERCADOS ALVORA" in baldes["auto"]
    assert "LOJA XPTO" in baldes["unmapped"]
    assert "AMAZON BR" in baldes["marketplace"]
    # O pagamento da fatura não vira lançamento.
    assert data["dropped"][0]["descricao"] == "Pag Fat Deb Cc"


def test_upload_agrupa_por_estabelecimento(client, sicredi_xlsx):
    """23 linhas do Alvorada têm que virar UMA decisão, não 23."""
    data = _upload(client, sicredi_xlsx).json()
    for grupo in data["auto_classified_items"]:
        assert grupo["count"] >= 1
        assert len(grupo["line_ids"]) == grupo["count"]


def test_validate_avisa_quando_a_palavra_chave_rouba_linhas(client, sicredi_xlsx):
    tx = _upload(client, sicredi_xlsx).json()["transaction_id"]
    resposta = client.post("/validate", json={"transaction_id": tx, "assignments": [
        {"scope": "merchant", "target": "LOJA XPTO", "categoria": "Casa",
         "persist_keyword": "S"},   # uma letra: casa com quase tudo
    ]}).json()
    impacto = resposta["impacts"][0]
    assert impacto["lines_affected"] > 1
    assert impacto["reclassified_away"], "deveria listar o que seria roubado"
    assert any(i["severity"] == "warning" for i in resposta["issues"])


def test_validate_recusa_palavra_chave_em_marketplace(client, sicredi_xlsx):
    """Marketplace muda de categoria a cada compra: gravar keyword seria errado."""
    data = _upload(client, sicredi_xlsx).json()
    tx = data["transaction_id"]
    alvo = data["marketplace_items"][0]["merchant"]
    resposta = client.post("/validate", json={"transaction_id": tx, "assignments": [
        {"scope": "merchant", "target": alvo, "categoria": "Casa", "persist_keyword": alvo},
    ]}).json()
    assert resposta["ok"] is False


def test_validate_rejeita_alvo_de_outra_transacao(client, sicredi_xlsx):
    tx = _upload(client, sicredi_xlsx).json()["transaction_id"]
    resposta = client.post("/validate", json={"transaction_id": tx, "assignments": [
        {"scope": "merchant", "target": "NAO EXISTE", "categoria": "Casa"},
    ]}).json()
    assert resposta["ok"] is False


def test_preview_e_idempotente(client, sicredi_xlsx):
    tx = _upload(client, sicredi_xlsx).json()["transaction_id"]
    corpo = {"transaction_id": tx, "assignments": [
        {"scope": "merchant", "target": "LOJA XPTO", "categoria": "Outros"}]}
    primeira = client.post("/preview", json=corpo).json()
    segunda = client.post("/preview", json=corpo).json()
    assert primeira["rows"] == segunda["rows"]


def test_preview_precedencia_linha_sobre_estabelecimento(client, sicredi_xlsx):
    data = _upload(client, sicredi_xlsx).json()
    tx = data["transaction_id"]
    linha = data["marketplace_items"][0]
    resposta = client.post("/preview", json={"transaction_id": tx, "assignments": [
        {"scope": "merchant", "target": linha["merchant"], "categoria": "Casa"},
        {"scope": "line", "target": linha["line_id"], "categoria": "Hobby"},
    ]}).json()
    resolvida = next(r for r in resposta["rows"] if r["line_id"] == linha["line_id"])
    assert resolvida["categoria"] == "Hobby", "a decisão de linha tem que vencer"


def test_export_devolve_csv_para_download(client, sicredi_xlsx, output_schema):
    tx = _upload(client, sicredi_xlsx).json()["transaction_id"]
    resposta = client.post("/export", json={"transaction_id": tx, "assignments": [],
                                            "commit_mapping": False})
    assert resposta.status_code == 200
    assert "attachment" in resposta.headers["content-disposition"]
    # Os nomes das colunas são configuráveis; o que o teste fixa é que o
    # cabeçalho exportado é EXATAMENTE o do formato de saída em vigor.
    assert resposta.content.decode().splitlines()[0] == ",".join(output_schema.colunas)


def test_export_lista_vazia_difere_de_campo_omitido(client, sicredi_xlsx):
    """`[]` = sem atribuição. Campo omitido = usa o que ficou do /preview."""
    tx = _upload(client, sicredi_xlsx).json()["transaction_id"]
    client.post("/preview", json={"transaction_id": tx, "assignments": [
        {"scope": "merchant", "target": "LOJA XPTO", "categoria": "Outros"}]})

    crua = client.post("/export", json={"transaction_id": tx, "assignments": [],
                                        "commit_mapping": False}).content.decode()
    guardada = client.post("/export", json={"transaction_id": tx,
                                            "commit_mapping": False}).content.decode()
    assert ",Outros," not in crua
    assert ",Outros," in guardada


def test_transacao_desconhecida_da_404(client):
    assert client.post("/preview", json={"transaction_id": "nao-existe",
                                         "assignments": []}).status_code == 404


def test_extensao_errada_da_415(client, nubank_csv):
    """`.pdf` nem chega a ser lido — banco nenhum exporta assim."""
    resposta = _upload(client, nubank_csv, nome="fatura.pdf")
    assert resposta.status_code == 415
    # E a mensagem diz o que É aceito, em vez de só recusar.
    assert ".csv" in resposta.json()["detail"]


def test_csv_que_nao_e_de_banco_nenhum_explica_em_vez_de_estourar(client, tmp_path):
    """Dois bancos exportam `.csv`, então a extensão não decide mais nada.

    Um CSV que não é fatura de nenhum dos dois não pode virar erro de parsing
    do banco que a ordem alfabética escolheu: o usuário leria "não achei o
    cabeçalho" sem entender por que o portal achava que aquilo era Nubank.
    """
    qualquer = tmp_path / "planilha.csv"
    qualquer.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    resposta = _upload(client, qualquer)
    assert resposta.status_code == 415
    detalhe = resposta.json()["detail"]
    assert "Sicredi" in detalhe and "Nubank" in detalhe


def test_o_banco_sai_do_arquivo_e_nao_de_uma_pergunta(client, nubank_csv, sicredi_xlsx):
    """O mesmo endpoint, sem escolher nada, lê os dois formatos."""
    nu = _upload(client, nubank_csv, vencimento="2026-08-10").json()
    assert [s["banco"] for s in nu["statements"]] == ["Nubank"]

    sic = _upload(client, sicredi_xlsx).json()
    assert [s["banco"] for s in sic["statements"]] == ["Sicredi"]


def test_lote_com_os_dois_bancos_vira_um_csv_so(client, nubank_csv, sicredi_xlsx):
    """Fatura do Sicredi e do Nubank no mesmo mês, num arquivo só.

    E o vencimento digitado só vale para quem PEDE: o Sicredi traz o dele
    dentro do arquivo, e deixar a data do Nubank vencer poria a fatura inteira
    no mês errado da planilha.
    """
    resposta = client.post("/upload", data={"vencimento": "2026-08-10"}, files=[
        ("files", ("nubank.csv", nubank_csv.read_bytes(), "text/csv")),
        ("files", ("extrato.xlsx", sicredi_xlsx.read_bytes(), "application/octet-stream")),
    ])
    assert resposta.status_code == 200, resposta.text
    dados = resposta.json()
    assert sorted(s["banco"] for s in dados["statements"]) == ["Nubank", "Sicredi"]
    venc = {s["banco"]: s["due_date"] for s in dados["statements"]}
    assert venc["Nubank"] == "2026-08-10"
    assert venc["Sicredi"] == "2026-08-10"   # a do arquivo, que por acaso é a mesma



def test_o_pre_voo_lista_os_titulares_e_sugere_quem_sou_eu(client, tmp_path):
    """A tela precisa dos nomes ANTES de processar — é onde a pergunta cabe."""
    from .conftest import _sicredi_app_csv
    caminho = _sicredi_app_csv(tmp_path / "c.csv", rows=[
        ("20/08/2025", "LOJA DELE", "", "R$ 100,00", ""),
        ("21/08/2025", "LOJA DELA", "", "R$ 50,00", ""),
    ], nomes=["Leonardo S Castilho", "Rhyesla Siqueira"])
    corpo = client.post(
        "/upload/periodo", data={"banco": "sicredi"},
        files=[("files", ("c.csv", caminho.read_bytes(), "text/csv"))]).json()
    assert corpo["titulares"] == ["Leonardo S Castilho", "Rhyesla Siqueira"]
    assert corpo["eu_sugerido"] == "Leonardo S Castilho"


def test_sugestao_que_nao_esta_na_lista_vira_None(client, tmp_path):
    """O "Associado" pode não aparecer nos lançamentos — ele é o dono da conta,
    não necessariamente quem passou o cartão. Sugerir um nome fora da lista
    deixaria a tela com um rádio marcado em ninguém, e aí `eu` não casaria com
    titular nenhum e TODOS levariam marca."""
    from .conftest import _sicredi_app_csv
    caminho = _sicredi_app_csv(tmp_path / "c.csv", rows=[
        ("20/08/2025", "LOJA DELA", "", "R$ 100,00", ""),
        ("21/08/2025", "LOJA DA FILHA", "", "R$ 50,00", ""),
    ], nomes=["Rhyesla Siqueira", "Alice Castilho"])
    # A fixture põe o primeiro nome como Associado; troco só o Associado.
    texto = caminho.read_text(encoding="utf-8-sig").replace(
        " Associado ;Rhyesla Siqueira", " Associado ;Leonardo S Castilho", 1)
    caminho.write_text(texto, encoding="utf-8-sig")

    corpo = client.post(
        "/upload/periodo", data={"banco": "sicredi"},
        files=[("files", ("c.csv", caminho.read_bytes(), "text/csv"))]).json()
    assert corpo["titulares"] == ["Alice Castilho", "Rhyesla Siqueira"]
    assert corpo["eu_sugerido"] is None


def test_upload_aplica_o_mapa_de_titulares(client, tmp_path):
    """A escolha da primeira aba tem que CASCATEAR: a marca entra na descrição
    no processamento, então ela já chega pronta em Novos, Revisão e no CSV."""
    from .conftest import _sicredi_app_csv
    caminho = _sicredi_app_csv(tmp_path / "c.csv", rows=[
        ("20/08/2025", "LOJA DELE", "", "R$ 100,00", ""),
        ("21/08/2025", "LOJA DELA", "", "R$ 50,00", ""),
    ], nomes=["Leonardo S Castilho", "Rhyesla Siqueira"])
    corpo = client.post(
        "/upload",
        data={"banco": "sicredi", "titulares": "Rhyesla Siqueira=Rhyesla"},
        files=[("files", ("c.csv", caminho.read_bytes(), "text/csv"))]).json()

    amostras = [s for g in corpo["unmapped_items"] + corpo["auto_classified_items"]
                for s in g["samples"]]
    assert any(s.endswith("<Rhyesla>") for s in amostras)
    assert not any("Dele" in s and s.endswith(">") for s in amostras)


def test_rotulo_sem_nome_nao_marca_o_extrato_inteiro(client, sicredi_xlsx):
    """`=Rhyesla`, sem o lado esquerdo, gravaria `{"": "Rhyesla"}`.

    O `.xls` do site não traz a coluna de titular, então TODO lançamento dele
    tem titular vazio — e sairia marcado com o nome de outra pessoa.
    """
    corpo = client.post(
        "/upload", data={"banco": "sicredi", "titulares": "=Rhyesla"},
        files=[("files", ("s.xlsx", sicredi_xlsx.read_bytes(),
                          "application/octet-stream"))]).json()
    amostras = [s for g in corpo["unmapped_items"] + corpo["auto_classified_items"]
                for s in g["samples"]]
    assert amostras and not any(s.endswith(">") for s in amostras)


def test_a_fatura_do_app_sobe_sem_perguntar_nada(client, sicredi_app_csv):
    """O outro formato do MESMO banco, pelo mesmo caminho e sem vencimento.

    Nenhum campo a mais na tela, nenhuma escolha de formato: a data está dentro
    do arquivo e a extensão diz qual leitor usar.
    """
    corpo = _upload(client, sicredi_app_csv, banco="sicredi").json()
    assert corpo["statements"][0]["due_date"].startswith("2025-09-10")
    assert corpo["statements"][0]["reconciles"] is True


# ---------------------------------------------------------------------------
# Paridade com a CLI
# ---------------------------------------------------------------------------

def test_api_e_cli_produzem_o_mesmo_csv(client, sicredi_xlsx, config_dir):
    """Se isto quebrar, o portal e o script discordam — e um dos dois mente."""
    tx = _upload(client, sicredi_xlsx).json()["transaction_id"]
    do_portal = client.post("/export", json={"transaction_id": tx, "assignments": [],
                                             "commit_mapping": False}).content

    cfg = ConfigSet.load(config_dir)
    rules = Ruleset.from_text(cfg.categories_text)
    lines, _, _ = classify_sources([(sicredi_xlsx.name, sicredi_xlsx)], rules,
                                   profile=cfg.bank("sicredi"), schema=cfg.output)
    assert do_portal == lines_to_csv(lines, schema=cfg.output)


# ---------------------------------------------------------------------------
# Multi-banco
# ---------------------------------------------------------------------------

def test_config_lista_bancos_com_tema(client):
    data = client.get("/config").json()
    por_id = {b["id"]: b for b in data["banks"]}
    assert por_id["sicredi"]["tema"]["primaria"] == "#3FA110"
    assert por_id["nubank"]["tema"]["primaria"] == "#820AD1"
    # Não existe mais "banco padrão": quem responde de qual banco é o arquivo
    # é o próprio arquivo, e um padrão seria o palpite que a detecção evita.
    assert "banco_padrao" not in data


def test_upload_nubank_usa_as_regras_compartilhadas(client, nubank_csv):
    data = _upload(client, nubank_csv, vencimento="2026-08-10").json()
    categorias = {g["merchant"]: g["categoria"] for g in data["auto_classified_items"]}
    assert categorias["RENNER"] == "Vestuário"
    # O perfil foi validado contra um export real, então não há aviso a dar.
    assert not any("não foi validado" in w for w in data["warnings"])


def test_nubank_sem_vencimento_da_422(client, nubank_csv):
    resposta = _upload(client, nubank_csv, banco="nubank")
    assert resposta.status_code == 422
    assert "vencimento" in resposta.json()["detail"]


def test_nubank_sem_vencimento_diz_o_nome_do_banco_detectado(client, nubank_csv):
    """A exigência vem do arquivo, não de uma escolha na tela."""
    resposta = _upload(client, nubank_csv)
    assert resposta.status_code == 422
    assert "Nubank" in resposta.json()["detail"]


# ---------------------------------------------------------------------------
# Regras pela API
# ---------------------------------------------------------------------------

def test_rules_marca_chutes_e_redundancias(client, config_dir):
    # O chute é plantado aqui: no arquivo real eles TENDEM A ZERO conforme são
    # confirmados pela aba Regras, e um teste que dependesse disso quebraria
    # justamente quando o portal fosse usado como se espera.
    palavra = com_um_chute(config_dir)
    data = client.get("/rules").json()
    assert data["flagged_count"] > 0, "os `# ?` têm que aparecer"
    assert any(e["value"] == palavra and e["flagged"] for e in data["entries"])


def test_rules_marca_redundancias(client, config_dir):
    """Uma palavra que já é coberta por outra mais curta nunca vence sozinha."""
    curta, longa = com_uma_redundancia(config_dir)
    data = client.get("/rules").json()
    redundantes = {e["value"]: e["redundant_with"] for e in data["entries"]
                   if e["redundant_with"]}
    assert longa in redundantes, f"{longa!r} é coberta por {curta!r}"
    assert any(curta in quem for quem in redundantes[longa])


def test_rules_edit_e_atomico(client, config_dir):
    """Uma operação inválida no meio não pode deixar o arquivo pela metade."""
    antes = (config_dir / "categories.yml").read_text(encoding="utf-8")
    resposta = client.post("/rules/edit", json={"operations": [
        {"op": "add", "block": "palavras", "categoria": "Casa", "value": "TESTE OK"},
        {"op": "remove", "block": "palavras", "categoria": "Casa", "value": "NAO EXISTE"},
    ]})
    assert resposta.status_code == 422
    assert (config_dir / "categories.yml").read_text(encoding="utf-8") == antes


def test_regex_test_usa_a_descricao_normalizada(client):
    """`iFood Clube` vira `I FOOD CLUBE` — um `^IFOOD` que parece certo não casa."""
    data = client.post("/rules/regex/test", json={
        "padrao": "^IFOOD", "amostras": ["IFOOD *RESTAURANTE", "iFood Clube"]}).json()
    assert data["valido"]
    assert data["resultados"][0]["casa"] is True
    assert data["resultados"][1]["casa"] is False
    assert data["resultados"][1]["normalizado"] == "I FOOD CLUBE"


def test_regex_invalido_nao_estoura(client):
    data = client.post("/rules/regex/test", json={"padrao": "MERCADO(",
                                                  "amostras": []}).json()
    assert data["valido"] is False and data["erro"]


def test_regex_move_reordena_de_verdade(client):
    antes = client.get("/rules/regex").json()
    resposta = client.post("/rules/regex", json={"operations": [
        {"op": "move", "index": len(antes) - 1, "delta": -(len(antes) - 1)}]}).json()
    assert resposta["rules"][0]["padrao"] == antes[-1]["padrao"]
    assert len(resposta["rules"]) == len(antes)


def test_regex_add_invalido_da_422(client):
    assert client.post("/rules/regex", json={"operations": [
        {"op": "add", "padrao": "MERCADO(", "categoria": "Casa"}]}).status_code == 422


# ---------------------------------------------------------------------------
# Pacote de configuração
# ---------------------------------------------------------------------------

def test_export_import_roundtrip(client, config_dir):
    pacote = client.get("/config/export")
    assert pacote.status_code == 200

    dentro = set(zipfile.ZipFile(io.BytesIO(pacote.content)).namelist())
    assert {"categories.yml", "output.yml", "MANIFEST.yml"} <= dentro
    assert any(n.startswith("banks/") for n in dentro)

    conferido = client.post("/config/import", data={"dry_run": "true"},
                            files={"file": ("cfg.zip", pacote.content, "application/zip")})
    assert conferido.status_code == 200
    corpo = conferido.json()
    assert corpo["gravado"] is False, "dry-run não pode gravar"
    # A lista sai do DISCO, não de um conjunto escrito à mão: o que este teste
    # prova é que o pacote leva todos os bancos configurados e traz todos de
    # volta. Fixado à mão, ele passava a falhar a cada banco novo — e a correção
    # era editar o teste, que é o oposto de uma prova.
    esperados = {p.stem for p in (config_dir / "banks").glob("*.yml")}
    assert esperados, "a config de teste precisa ter ao menos um banco"
    assert set(corpo["conteudo"]["banks"]) == esperados


def test_import_recusa_arquivo_que_nao_e_zip(client):
    resposta = client.post("/config/import",
                           files={"file": ("x.zip", b"nao sou um zip", "application/zip")})
    assert resposta.status_code == 422


def test_import_recusa_pacote_sem_categories(client):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("output.yml", "colunas: [A]\n")
    assert client.post("/config/import",
                       files={"file": ("c.zip", buffer.getvalue(), "application/zip")}
                       ).status_code == 422


def test_import_bloqueia_zip_slip(client):
    """Caminho com `..` sairia de config/ e escreveria onde quisesse."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("categories.yml", "configuracao: {}\npalavras: {}\n")
        z.writestr("../../etc/evil.yml", "x: 1\n")
    resposta = client.post("/config/import",
                           files={"file": ("c.zip", buffer.getvalue(), "application/zip")})
    assert resposta.status_code == 422
    assert "suspeito" in resposta.json()["detail"]


@pytest.mark.parametrize("rota,metodo", [
    ("/config/bank/sicredi", "get"),
    ("/config/bank", "post"),
    ("/config/bank/test", "post"),
])
def test_editar_formato_de_entrada_nao_existe_mais(client, rota, metodo):
    """A tela saiu e as rotas com ela.

    O jeito de o banco exportar é fato do banco, não preferência de quem usa —
    e um fato que muda para todo mundo ao mesmo tempo. Editável, cada instalação
    podia ter um leitor diferente, e suportar o CSV novo do app do Sicredi teria
    virado "edite o seu YAML" em vez de simplesmente funcionar.
    """
    assert getattr(client, metodo)(rota).status_code == 404


def test_o_pacote_de_configuracao_ainda_leva_os_bancos(client):
    """Sair da tela não é sair do pacote: tema e leitura continuam viajando."""
    corpo = client.get("/config/export").content
    with zipfile.ZipFile(io.BytesIO(corpo)) as z:
        assert any(n.startswith("banks/") for n in z.namelist())


def test_output_schema_invalido_da_422(client):
    assert client.post("/config/output",
                       json={"yaml_text": "colunas: []\n"}).status_code == 422


# ---------------------------------------------------------------------------
# GitHub ausente ou quebrado não pode impedir o download
# ---------------------------------------------------------------------------

def test_token_vazio_significa_sem_github(monkeypatch):
    """O compose passa `${FATURA_GITHUB_TOKEN:-}`; string vazia NÃO é um token.

    Regressão do 500 no /export: `SecretStr("")` não é None, `github_enabled`
    dizia True, e o PyGithub estourava num `assert len(token) > 0`.
    """
    from api.settings import Settings

    assert Settings(github_token="").github_enabled is False
    assert Settings(github_token="   ").github_enabled is False
    assert Settings(github_token=None).github_enabled is False
    assert Settings(github_token="ghp_x").github_enabled is True


def test_export_baixa_mesmo_sem_github(client, sicredi_xlsx, monkeypatch):
    monkeypatch.setenv("FATURA_GITHUB_TOKEN", "")
    data = _upload(client, sicredi_xlsx).json()
    tx, novo = data["transaction_id"], data["unmapped_items"][0]["merchant"]

    client.post("/update-mapping", json={"transaction_id": tx, "assignments": [
        {"scope": "merchant", "target": novo, "categoria": "Casa",
         "persist_keyword": novo}]})

    resposta = client.post("/export", json={"transaction_id": tx,
                                            "commit_mapping": True})
    assert resposta.status_code == 200, "sem GitHub o CSV tem que baixar assim mesmo"
    assert resposta.headers["x-mapping-commit"] == ""
    assert resposta.headers["x-mapping-commit-error"], "e o motivo tem que voltar"


def test_falha_do_github_nao_vira_500(client, sicredi_xlsx, monkeypatch):
    """Token revogado, 401, DNS fora do ar: nada disso perde a revisão."""
    from api import app as app_module

    def explode(*args, **kwargs):
        raise RuntimeError("bad credentials")

    monkeypatch.setattr(app_module.GitHubSync, "commit", explode)
    monkeypatch.setattr(app_module.GitHubSync, "current_sha", lambda self: None)

    data = _upload(client, sicredi_xlsx).json()
    tx, novo = data["transaction_id"], data["unmapped_items"][0]["merchant"]
    client.post("/update-mapping", json={"transaction_id": tx, "assignments": [
        {"scope": "merchant", "target": novo, "categoria": "Casa",
         "persist_keyword": novo}]})

    from api.settings import get_settings
    get_settings.cache_clear()
    monkeypatch.setenv("FATURA_GITHUB_TOKEN", "ghp_invalido")
    get_settings.cache_clear()

    resposta = client.post("/export", json={"transaction_id": tx,
                                            "commit_mapping": True})
    assert resposta.status_code == 200
    assert resposta.content.decode().startswith("Data,")


def test_aviso_de_commit_cabe_num_header_http(client, sicredi_xlsx):
    """Headers são latin-1: um travessão na mensagem derrubava a resposta."""
    data = _upload(client, sicredi_xlsx).json()
    tx, novo = data["transaction_id"], data["unmapped_items"][0]["merchant"]
    client.post("/update-mapping", json={"transaction_id": tx, "assignments": [
        {"scope": "merchant", "target": novo, "categoria": "Casa",
         "persist_keyword": novo}]})
    resposta = client.post("/export", json={"transaction_id": tx,
                                            "commit_mapping": True})
    resposta.headers["x-mapping-commit-error"].encode("latin-1")   # não pode estourar


# ---------------------------------------------------------------------------
# Confirmar um chute
# ---------------------------------------------------------------------------

def test_confirmar_chute_mantem_o_mapeamento(client, config_dir):
    """Antes só dava para apagar (perdendo o mapeamento) ou trocar a categoria."""
    com_um_chute(config_dir)
    antes = client.get("/rules").json()
    chute = next(e for e in antes["entries"] if e["flagged"])

    depois = client.post("/rules/edit", json={"operations": [
        {"op": "confirm", "block": chute["block"],
         "categoria": chute["categoria"], "value": chute["value"]}]}).json()

    assert depois["flagged_count"] == antes["flagged_count"] - 1

    igual = [e for e in depois["entries"]
             if e["value"] == chute["value"] and e["categoria"] == chute["categoria"]]
    assert len(igual) == 1, "a entrada não pode sumir"
    assert igual[0]["flagged"] is False
    assert igual[0]["comment"] == ""


def test_confirmar_nao_mexe_nas_outras_entradas(client, config_dir):
    com_um_chute(config_dir)
    antes = client.get("/rules").json()
    chute = next(e for e in antes["entries"] if e["flagged"])
    depois = client.post("/rules/edit", json={"operations": [
        {"op": "confirm", "block": chute["block"],
         "categoria": chute["categoria"], "value": chute["value"]}]}).json()

    chave = lambda entradas: {(e["block"], e["categoria"], e["value"]) for e in entradas}
    assert chave(antes["entries"]) == chave(depois["entries"])


def test_confirmar_entrada_inexistente_da_422(client):
    assert client.post("/rules/edit", json={"operations": [
        {"op": "confirm", "block": "palavras", "categoria": "Casa",
         "value": "NAO EXISTE"}]}).status_code == 422


# ---------------------------------------------------------------------------
# Persistência do "lembrar"
# ---------------------------------------------------------------------------
#
# A gravação no disco morava DENTRO do commit no GitHub, depois do push. Sem
# token — que é como o portal roda localmente — marcar "lembrar" devolvia
# 200 OK e não gravava nada: a palavra-chave ficava só no `yaml_working` da
# transação, que expira em 24h. No mês seguinte o estabelecimento voltava como
# novo, sem nenhum sinal de que algo se perdeu.

def _lembrar(client, sicredi_xlsx, categoria="Casa"):
    data = _upload(client, sicredi_xlsx).json()
    tx, novo = data["transaction_id"], data["unmapped_items"][0]["merchant"]
    resposta = client.post("/update-mapping", json={
        "transaction_id": tx, "assignments": [
            {"scope": "merchant", "target": novo, "categoria": categoria,
             "persist_keyword": novo}]})
    assert resposta.status_code == 200, resposta.text
    return tx, novo


def test_lembrar_grava_no_arquivo_sem_github(client, sicredi_xlsx, config_dir,
                                             monkeypatch):
    """Sem token, "lembrar" tem que persistir do mesmo jeito."""
    monkeypatch.setenv("FATURA_GITHUB_TOKEN", "")
    _, novo = _lembrar(client, sicredi_xlsx)

    gravado = (config_dir / "categories.yml").read_text(encoding="utf-8")
    assert novo in gravado, "a palavra-chave não chegou ao categories.yml"


def test_lembrar_persiste_mesmo_com_github_quebrado(client, sicredi_xlsx,
                                                    config_dir, monkeypatch):
    """Token revogado não pode custar a palavra-chave."""
    from api import app as app_module

    def explode(*args, **kwargs):
        raise RuntimeError("bad credentials")

    monkeypatch.setattr(app_module.GitHubSync, "commit", explode)
    monkeypatch.setattr(app_module.GitHubSync, "current_sha", lambda self: None)
    monkeypatch.setenv("FATURA_GITHUB_TOKEN", "ghp_invalido")

    _, novo = _lembrar(client, sicredi_xlsx)
    assert novo in (config_dir / "categories.yml").read_text(encoding="utf-8")


def test_lembrar_sobrevive_a_transacao_seguinte(client, sicredi_xlsx, config_dir):
    """O ponto da feature: no próximo upload o estabelecimento já sai
    classificado, em vez de voltar para o balde de novos."""
    _, novo = _lembrar(client, sicredi_xlsx, categoria="Casa")

    segunda = _upload(client, sicredi_xlsx).json()
    ainda_novo = {g["merchant"] for g in segunda["unmapped_items"]}
    assert novo not in ainda_novo, "voltou como novo — o mapeamento não pegou"

    classificados = {g["merchant"]: g["categoria"]
                     for g in segunda["auto_classified_items"]}
    assert classificados.get(novo) == "Casa"


def test_nao_lembrar_nao_grava_nada(client, sicredi_xlsx, config_dir):
    """Sem marcar "lembrar", o arquivo fica exatamente como estava."""
    antes = (config_dir / "categories.yml").read_text(encoding="utf-8")
    data = _upload(client, sicredi_xlsx).json()
    client.post("/update-mapping", json={
        "transaction_id": data["transaction_id"], "assignments": [
            {"scope": "merchant", "target": data["unmapped_items"][0]["merchant"],
             "categoria": "Casa"}]})   # sem persist_keyword
    assert (config_dir / "categories.yml").read_text(encoding="utf-8") == antes


def test_marcar_desconhecido_tambem_persiste(client, sicredi_xlsx, config_dir):
    """"Não sei" é uma decisão e também precisa sobreviver: senão o portal
    volta a perguntar todo mês sobre o mesmo estabelecimento."""
    data = _upload(client, sicredi_xlsx).json()
    novo = data["unmapped_items"][0]["merchant"]
    client.post("/update-mapping", json={
        "transaction_id": data["transaction_id"], "assignments": [
            {"scope": "merchant", "target": novo, "mark_unknown": True}]})

    gravado = (config_dir / "categories.yml").read_text(encoding="utf-8")
    assert novo in gravado
    segunda = _upload(client, sicredi_xlsx).json()
    assert novo not in {g["merchant"] for g in segunda["unmapped_items"]}


def _lote_de_dois(client, tmp_path, rows):
    """Um lote de conta conjunta já processado, com a marca aplicada."""
    from .conftest import _sicredi_app_csv
    caminho = _sicredi_app_csv(tmp_path / "conjunta.csv", rows=rows,
                               nomes=["Leonardo S Castilho", "Rhyesla Siqueira"])
    return client.post(
        "/upload",
        data={"banco": "sicredi", "titulares": "Rhyesla Siqueira=Rhyesla"},
        files=[("files", ("conjunta.csv", caminho.read_bytes(), "text/csv"))]).json()


def test_grupo_diz_de_quem_sao_as_linhas_dele(client, tmp_path):
    """O filtro por pessoa das telas de revisão se apoia nisto.

    A lista vem do grupo INTEIRO e inclui a string vazia — o balde de quem se
    identificou como "eu". Sem o vazio, filtrar pelas MINHAS compras esconderia
    todo estabelecimento em que ela também comprou.
    """
    corpo = _lote_de_dois(client, tmp_path, [
        ("20/08/2025", "SO DELE", "", "R$ 100,00", ""),
        ("21/08/2025", "SO DELA", "", "R$ 50,00", ""),
    ])
    grupos = {g["merchant"]: g["titulares"]
              for g in corpo["unmapped_items"] + corpo["auto_classified_items"]}
    assert grupos["SO DELE"] == [""]
    assert grupos["SO DELA"] == ["Rhyesla"]


def test_grupo_dos_dois_aparece_nos_dois_filtros(client, tmp_path):
    """O mesmo mercado nos dois cartões. A decisão dele vale para as duas."""
    corpo = _lote_de_dois(client, tmp_path, [
        ("20/08/2025", "MERCADO COMUM", "", "R$ 100,00", ""),
        ("21/08/2025", "MERCADO COMUM", "", "R$ 50,00", ""),
    ])
    grupo = next(g for g in corpo["unmapped_items"] + corpo["auto_classified_items"]
                 if g["merchant"] == "MERCADO COMUM")
    assert grupo["count"] == 2
    assert grupo["titulares"] == ["", "Rhyesla"]


def test_titulares_saem_do_grupo_inteiro_e_nao_das_amostras(client, tmp_path):
    """`samples` tem três; um estabelecimento pode ter dez linhas.

    Se a lista viesse das amostras, a pessoa que só aparece na quarta compra
    sumiria do filtro — e o estabelecimento dela ficaria invisível.
    """
    from .conftest import _sicredi_app_csv
    # `nomes` gira um titular por linha: quatro minhas e a DELA só na quinta,
    # fora das três amostras. É o caso que distingue "do grupo" de "da amostra".
    caminho = _sicredi_app_csv(
        tmp_path / "tarde.csv",
        rows=[("20/08/2025", "MUITAS LINHAS", "", "R$ 10,00", "")] * 5,
        nomes=["Leonardo S Castilho"] * 4 + ["Rhyesla Siqueira"])
    corpo = client.post(
        "/upload",
        data={"banco": "sicredi", "titulares": "Rhyesla Siqueira=Rhyesla"},
        files=[("files", ("tarde.csv", caminho.read_bytes(), "text/csv"))]).json()

    grupo = next(g for g in corpo["unmapped_items"] + corpo["auto_classified_items"]
                 if g["merchant"] == "MUITAS LINHAS")
    assert grupo["count"] == 5
    assert len(grupo["samples"]) == 3
    assert not any(s.endswith("<Rhyesla>") for s in grupo["samples"]), (
        "a montagem do teste falhou: ela precisa estar FORA das amostras")
    assert grupo["titulares"] == ["", "Rhyesla"]


def test_a_linha_diz_de_quem_ela_e(client, tmp_path):
    """`LineItem.titular`, para as telas que listam LINHA e não estabelecimento.

    Sai do backend, e não de uma segunda leitura da descrição em JavaScript:
    a regra é a mesma de `core.text.titular_de` e ter duas cópias dela faria a
    tela e o arquivo discordarem no dia em que uma passasse a aceitar espaço
    antes do `<`.
    """
    corpo = _lote_de_dois(client, tmp_path, [
        ("20/08/2025", "AMAZON BR", "", "R$ 100,00", ""),
        ("21/08/2025", "AMAZON BR", "", "R$ 50,00", ""),
    ])
    linhas = corpo["marketplace_items"]
    assert sorted(l["titular"] for l in linhas) == ["", "Rhyesla"]
    dela = next(l for l in linhas if l["titular"] == "Rhyesla")
    assert dela["descricao"].endswith("<Rhyesla>")


def test_nubank_de_verdade_e_reconhecido_e_lido(client, tmp_path):
    """O arquivo REAL do app, com vírgula decimal e o menos separado.

    A fixture anterior deste banco usava `270.51` — inventado — e o perfil
    tinha sido escrito para casar com a invenção. Este caso lê o formato que o
    Nubank exporta de verdade.
    """
    real = tmp_path / "nubank.csv"
    real.write_text(
        "date,title,amount\n"
        '2026-07-31,Jackson Bull Steak Bar,"65,33"\n'
        '2026-07-22,"IOF de ""Anthropic* Claude Sub""","3,98"\n'
        '2026-07-08,Pagamento recebido,"- 5.664,61"\n',
        encoding="utf-8")
    dados = _upload(client, real, vencimento="2026-08-10").json()
    assert [s["banco"] for s in dados["statements"]] == ["Nubank"]

    resumo = dados["statements"][0]
    assert resumo["debits"] == 69.31          # 65,33 + 3,98
    assert resumo["credits"] == 5664.61       # o menos com espaço no meio
    # Sem total declarado no arquivo, não há o que conferir — e "não há o que
    # conferir" não pode virar "não fechou".
    assert resumo["reconciles"] is True
    assert resumo["declared_credits"] is None


def test_um_sinal_so_nao_basta_para_reconhecer(client, tmp_path):
    """O Sicredi exige `Associado` E `Data de Vencimento`, os dois.

    Um `.csv` com só um dos dois não é a fatura do app — e aceitar por metade
    da assinatura faria o portal ler um arquivo qualquer com o parser errado,
    entregando números em vez de recusa.
    """
    meio = tmp_path / "meio.csv"
    meio.write_text(" Associado ;Fulano;;;;\nqualquer;coisa\n", encoding="utf-8")
    resposta = _upload(client, meio)
    assert resposta.status_code == 415
    assert "não parece de nenhum banco" in resposta.json()["detail"]


def test_assinatura_ambigua_recusa_em_vez_de_escolher(client, tmp_path):
    """Casar com dois bancos é erro de CONFIGURAÇÃO, e tem de aparecer.

    Escolher o primeiro da lista esconderia perfis mal escritos até o dia em
    que a ordem alfabética do diretório mudasse e as faturas passassem a ser
    lidas por outro banco, sem nada ter sido alterado.
    """
    ambiguo = tmp_path / "ambiguo.csv"
    ambiguo.write_text(
        " Associado ;Fulano;;;;\n Data de Vencimento ;10/08/2026;;;;\n"
        "date,title,amount\n2026-07-01,X,\"1,00\"\n", encoding="utf-8")
    resposta = _upload(client, ambiguo)
    assert resposta.status_code == 415
    detalhe = resposta.json()["detail"]
    assert "mais de um" in detalhe and "deteccao" in detalhe


def test_perfil_sem_deteccao_nao_reconhece_nada(client, config_dir, nubank_csv):
    """Sem assinatura declarada, o perfil não pode vencer por acidente.

    Ele funciona enquanto for o único da extensão dele e vira indetectável
    quando aparece um concorrente. O que não pode é reconhecer QUALQUER coisa:
    aí a ordem do diretório decidiria de que banco é a fatura.
    """
    caminho = config_dir / "banks" / "nubank.yml"
    texto = caminho.read_text(encoding="utf-8")
    caminho.write_text(
        texto.replace('    contem: ["date,title,amount"]', "    contem: []"),
        encoding="utf-8")

    resposta = _upload(client, nubank_csv, vencimento="2026-08-10")
    assert resposta.status_code == 415
    assert "não parece de nenhum banco" in resposta.json()["detail"]


def test_o_pre_voo_diz_qual_banco_reconheceu(client, nubank_csv):
    """A tela usa isto para três coisas: dizer, pintar o tema, e pedir a data."""
    bruto = client.post("/upload/periodo", files=[
        ("files", ("nubank.csv", nubank_csv.read_bytes(), "text/csv"))])
    assert bruto.status_code == 200, bruto.text
    resposta = bruto.json()
    assert [b["nome"] for b in resposta["bancos"]] == ["Nubank"]
    assert resposta["bancos"][0]["pede_vencimento"] is True
    assert resposta["bancos"][0]["tema"]["primaria"] == "#820AD1"


def test_o_vencimento_digitado_nao_atropela_quem_traz_o_seu(client, nubank_csv, tmp_path):
    """O caso que só aparece num lote misto — e erra o MÊS inteiro na planilha.

    `read_statement` deixa o argumento vencer o que está no arquivo. Com os
    dois bancos juntos, passar a data do Nubank para todos poria a fatura do
    Sicredi no mês errado, e o erro só apareceria na conferência contra as
    linhas de `Cartão de crédito`.
    """
    from .conftest import _sicredi_workbook
    sicredi = _sicredi_workbook(tmp_path / "sic.xlsx", vencimento="10/09/2026")

    dados = client.post("/upload", data={"vencimento": "2026-08-10"}, files=[
        ("files", ("nubank.csv", nubank_csv.read_bytes(), "text/csv")),
        ("files", ("sic.xlsx", sicredi.read_bytes(), "application/octet-stream")),
    ]).json()

    venc = {s["banco"]: s["due_date"] for s in dados["statements"]}
    assert venc["Nubank"] == "2026-08-10", "o que pede usa a data digitada"
    assert venc["Sicredi"] == "2026-09-10", "quem traz a sua não é atropelado"


def test_pagamento_da_fatura_do_nubank_e_excluido(client, tmp_path):
    """O crédito do pagamento anterior não é despesa, e não pode virar linha.

    O Sicredi já tinha `PAG FAT DEB CC` na lista de exclusão; o Nubank chama a
    mesma coisa de "Pagamento recebido". Sem isso ele aparece todo mês na aba
    Novos pedindo categoria — e o crédito de milhares de reais entra no CSV e
    conta duas vezes na planilha.
    """
    arquivo = tmp_path / "nu.csv"
    arquivo.write_text(
        "date,title,amount\n"
        '2026-07-31,Jackson Bull Steak Bar,"65,33"\n'
        '2026-07-08,Pagamento recebido,"- 5.664,61"\n', encoding="utf-8")
    dados = _upload(client, arquivo, vencimento="2026-08-10").json()

    todos = [g["merchant"] for balde in ("unmapped_items", "auto_classified_items",
                                         "ignored_items")
             for g in dados[balde]]
    assert "PAGAMENTO RECEBIDO" not in todos
    assert any("PAGAMENTO RECEBIDO" in d["descricao"].upper() for d in dados["dropped"])


# ---------------------------------------------------------------------------
# BTG — a fatura vem cifrada e a senha é DO ARQUIVO
# ---------------------------------------------------------------------------

# As senhas viajam como `indice=senha`, uma linha por arquivo cifrado: dois
# protegidos no mesmo lote podem ter chaves diferentes. Com um arquivo só, o
# índice é sempre 0.
def _upload_btg(client, path, senha="", vencimento=""):
    return client.post(
        "/upload",
        data={"vencimento": vencimento, "senhas": f"0={senha}" if senha else ""},
        files=[("files", (path.name, path.read_bytes(),
                          "application/octet-stream"))])


def _preflight(client, path, senha=""):
    return client.post(
        "/upload/periodo", data={"senhas": f"0={senha}" if senha else ""},
        files=[("files", (path.name, path.read_bytes(),
                          "application/octet-stream"))]).json()


def test_upload_btg_com_a_senha_certa(client, btg_xlsx_cifrado):
    from tests.conftest import SENHA_BTG
    resposta = _upload_btg(client, btg_xlsx_cifrado, senha=SENHA_BTG)
    assert resposta.status_code == 200

    dados = resposta.json()
    assert [s["banco"] for s in dados["statements"]] == ["BTG Pactual"]
    assert dados["statements"][0]["due_date"] == "2026-06-01"
    assert dados["statements"][0]["reconciles"] is True


def test_upload_btg_sem_senha_diz_que_falta_a_senha(client, btg_xlsx_cifrado):
    """Não é 415 nem 500: o arquivo é bom, falta a chave dele."""
    resposta = _upload_btg(client, btg_xlsx_cifrado)
    assert resposta.status_code == 422
    assert "protegido por senha" in resposta.json()["detail"]


def test_upload_btg_com_senha_errada_diz_que_a_senha_nao_confere(client, btg_xlsx_cifrado):
    """Texto DIFERENTE do "falta a senha" — senão quem errou não sabe se chegou."""
    resposta = _upload_btg(client, btg_xlsx_cifrado, senha="nao-e-essa")
    assert resposta.status_code == 422
    detalhe = resposta.json()["detail"]
    assert "não abre" in detalhe
    assert "protegido por senha" not in detalhe


def test_preflight_conta_que_o_arquivo_pede_senha(client, btg_xlsx_cifrado):
    """A tela só sabe montar o campo porque o pré-voo conta.

    Sem isto, ou o portal pediria senha a todo mundo por causa de um banco, ou
    a pessoa descobriria o problema depois de clicar em Processar.
    """
    dados = _preflight(client, btg_xlsx_cifrado)
    assert [p["nome"] for p in dados["protegidos"]] == [btg_xlsx_cifrado.name]
    assert dados["protegidos"][0]["senha_incorreta"] is False
    assert dados["bancos"] == [], "cifrado, ainda não dá para saber de quem é"


def test_preflight_marca_a_senha_errada(client, btg_xlsx_cifrado):
    dados = _preflight(client, btg_xlsx_cifrado, senha="nao-e-essa")
    assert dados["protegidos"][0]["senha_incorreta"] is True


def test_preflight_com_a_senha_certa_reconhece_o_btg(client, btg_xlsx_cifrado):
    from tests.conftest import SENHA_BTG
    dados = _preflight(client, btg_xlsx_cifrado, senha=SENHA_BTG)

    assert dados["protegidos"] == []
    assert [b["id"] for b in dados["bancos"]] == ["btg"]
    assert dados["bancos"][0]["tema"]["primaria"] == "#195AB4"
    assert dados["bancos"][0]["pede_vencimento"] is False, "o arquivo traz a data"
    # O BTG não imprime nome: quem separa as compras é o final do cartão, e é
    # ele que alimenta o filtro de "mostrar de quem".
    assert dados["titulares"] == ["4108", "8134"]


def test_a_senha_do_arquivo_nunca_e_devolvida_nem_gravada(client, btg_xlsx_cifrado,
                                                          tmp_path):
    """A senha entra, decifra e morre. Não volta e não fica em disco.

    É a mesma classe de segredo do token do GitHub: só que este não é nem
    configuração — é digitado uma vez por lote. Guardá-lo no SQLite da
    transação o deixaria em disco por horas, legível por quem abrisse o arquivo.
    """
    from tests.conftest import SENHA_BTG

    corpo = _upload_btg(client, btg_xlsx_cifrado, senha=SENHA_BTG)
    assert corpo.status_code == 200
    assert SENHA_BTG not in corpo.text

    preflight = client.post("/upload/periodo", data={"senhas": f"0={SENHA_BTG}"},
                            files=[("files", (btg_xlsx_cifrado.name,
                                              btg_xlsx_cifrado.read_bytes(),
                                              "application/octet-stream"))])
    assert SENHA_BTG not in preflight.text

    banco = tmp_path / "state.db"
    assert banco.exists(), "a transação foi mesmo gravada"
    assert SENHA_BTG.encode() not in banco.read_bytes()


def test_lote_misto_com_um_cifrado_e_um_aberto(client, btg_xlsx_cifrado, sicredi_xlsx):
    """Uma senha só, e ela vale para o arquivo que precisa dela.

    O que está aberto não é tocado — passar todo blob pelo msoffcrypto por
    precaução transformaria um lote de dez CSVs numa espera sem motivo.
    """
    from tests.conftest import SENHA_BTG
    resposta = client.post(
        "/upload", data={"senhas": f"0={SENHA_BTG}"},
        files=[("files", ("btg.xlsx", btg_xlsx_cifrado.read_bytes(),
                          "application/octet-stream")),
               ("files", ("sic.xlsx", sicredi_xlsx.read_bytes(),
                          "application/octet-stream"))])
    assert resposta.status_code == 200
    bancos = {s["banco"] for s in resposta.json()["statements"]}
    assert bancos == {"BTG Pactual", "Sicredi"}


def test_upload_btg_aberto_tambem_e_reconhecido(client, btg_xlsx):
    """Sem cifra, o `.xlsx` do BTG ainda tem de se distinguir do do Sicredi.

    Os dois declaram a mesma extensão; quem desempata é a assinatura DENTRO do
    zip, e este é o teste de que ela é lida de lá.
    """
    resposta = _upload_btg(client, btg_xlsx)
    assert resposta.status_code == 200
    assert resposta.json()["statements"][0]["banco"] == "BTG Pactual"


def test_pagamento_da_fatura_do_btg_e_excluido(client, btg_xlsx):
    """Cada banco escreve o pagamento da fatura de um jeito, e todos têm que sair.

    O Sicredi chama de "Pag Fat Deb Cc", o Nubank de "Pagamento recebido", o BTG
    de "Pagamento de fatura". Faltando um, ele aparece todo mês na aba Novos
    pedindo categoria — e o crédito de milhares de reais entra no CSV e conta
    duas vezes na planilha.
    """
    dados = _upload_btg(client, btg_xlsx).json()

    todos = [g["merchant"] for balde in ("unmapped_items", "auto_classified_items",
                                         "ignored_items")
             for g in dados[balde]]
    assert "PAGAMENTO DE FATURA" not in todos
    assert any("PAGAMENTO DE FATURA" in d["descricao"].upper() for d in dados["dropped"])


# ---------------------------------------------------------------------------
# Lote com bancos diferentes — a etiqueta na descrição
# ---------------------------------------------------------------------------

def _lote(client, *arquivos, senhas="", vencimento=""):
    return client.post(
        "/upload", data={"senhas": senhas, "vencimento": vencimento},
        files=[("files", (nome, caminho.read_bytes(), "application/octet-stream"))
               for nome, caminho in arquivos])


def test_lote_de_bancos_diferentes_etiqueta_a_linha(client, btg_xlsx, sicredi_xlsx):
    """`[Cartão-BTG]` e `[Cartão-SICREDI]` na mesma exportação.

    Sem a etiqueta, duas faturas do mesmo mês viram um bloco só na planilha e
    não há como saber qual linha veio de qual cartão — nem para conferir o
    total de um deles, nem para achar a compra na fatura certa.
    """
    dados = _lote(client, ("btg.xlsx", btg_xlsx), ("sic.xlsx", sicredi_xlsx))
    assert dados.status_code == 200

    tx = dados.json()["transaction_id"]
    csv_texto = client.post("/export", json={"transaction_id": tx, "assignments": [],
                                             "commit_mapping": False}).content.decode()
    assert "[Cartão-BTG]" in csv_texto
    assert "[Cartão-SICREDI]" in csv_texto
    assert "[Cartão]" not in csv_texto, "num lote misto, toda linha leva etiqueta"


def test_lote_de_um_banco_so_nao_etiqueta_nada(client, btg_xlsx):
    """Com um banco só a etiqueta seria igual em toda linha, e não separaria nada.

    Escrevê-la assim mesmo acrescentaria seis caracteres a cada linha do
    histórico para repetir uma informação constante — e mudaria a cara de todo
    CSV que este portal já exportou.
    """
    tx = _lote(client, ("btg.xlsx", btg_xlsx)).json()["transaction_id"]
    csv_texto = client.post("/export", json={"transaction_id": tx, "assignments": [],
                                             "commit_mapping": False}).content.decode()
    assert "[Cartão]" in csv_texto
    assert "[Cartão-" not in csv_texto


def test_dois_arquivos_do_mesmo_banco_nao_sao_dois_bancos(client, btg_xlsx):
    """O que decide é a VARIEDADE, não a quantidade de arquivos."""
    tx = _lote(client, ("jan.xlsx", btg_xlsx),
               ("fev.xlsx", btg_xlsx)).json()["transaction_id"]
    csv_texto = client.post("/export", json={"transaction_id": tx, "assignments": [],
                                             "commit_mapping": False}).content.decode()
    assert "[Cartão-" not in csv_texto


def test_a_etiqueta_sobrevive_a_recategorizacao(client, btg_xlsx, sicredi_xlsx):
    """O CSV etiquetado volta pelo Recategorizar sem perder nem ganhar nada.

    A recategorização promete mudar SÓ a coluna Categoria. A etiqueta mora
    dentro do colchete que `merchant_of` já pulava, então o estabelecimento
    continua sendo lido igual — e a descrição tem que voltar caractere por
    caractere como entrou.
    """
    tx = _lote(client, ("btg.xlsx", btg_xlsx),
               ("sic.xlsx", sicredi_xlsx)).json()["transaction_id"]
    original = client.post("/export", json={"transaction_id": tx, "assignments": [],
                                            "commit_mapping": False}).content

    volta = client.post("/recategorize",
                        files=[("files", ("faturas.csv", original, "text/csv"))])
    assert volta.status_code == 200
    tx2 = volta.json()["transaction_id"]
    refeito = client.post("/export", json={"transaction_id": tx2, "assignments": [],
                                           "commit_mapping": False}).content

    def descricoes(bruto):
        import csv as _csv
        linhas = list(_csv.DictReader(io.StringIO(bruto.decode("utf-8-sig"))))
        col = next(c for c in linhas[0] if c in ("Descrição", "Item"))
        return [l[col] for l in linhas]

    assert descricoes(refeito) == descricoes(original)
    assert any(d.startswith("[Cartão-BTG]") for d in descricoes(refeito))
    # E o estabelecimento continua sendo lido de dentro da etiqueta.
    grupos = {g["merchant"] for g in volta.json()["auto_classified_items"]}
    assert "SUPERMERCADO CONFIANCA" in grupos


def test_dois_cifrados_com_senhas_diferentes(client, tmp_path):
    """Cada arquivo abre com a SUA senha, e o segundo é endereçado pelo índice.

    Uma senha só para o lote deixaria um dos dois sem jeito nenhum de abrir — e
    endereçar pelo nome exigiria escapar o `=`, que é caractere legal em nome de
    arquivo.
    """
    from tests.conftest import SENHA_BTG, _btg_workbook, _cifrar
    outra = "99887766554"
    claro = _btg_workbook(tmp_path / "claro.xlsx")
    um = _cifrar(claro, tmp_path / "btg-1.xlsx", SENHA_BTG)
    dois = _cifrar(claro, tmp_path / "btg-2.xlsx", outra)

    # A senha do primeiro no segundo (e vice-versa): as duas erradas.
    trocadas = client.post(
        "/upload", data={"senhas": f"0={outra}\n1={SENHA_BTG}"},
        files=[("files", ("btg-1.xlsx", um.read_bytes(), "application/octet-stream")),
               ("files", ("btg-2.xlsx", dois.read_bytes(), "application/octet-stream"))])
    assert trocadas.status_code == 422
    assert "não abre" in trocadas.json()["detail"]

    certas = client.post(
        "/upload", data={"senhas": f"0={SENHA_BTG}\n1={outra}"},
        files=[("files", ("btg-1.xlsx", um.read_bytes(), "application/octet-stream")),
               ("files", ("btg-2.xlsx", dois.read_bytes(), "application/octet-stream"))])
    assert certas.status_code == 200
    assert len(certas.json()["statements"]) == 2


def test_preflight_endereca_cada_protegido_pela_posicao(client, tmp_path,
                                                        sicredi_xlsx):
    """O índice do protegido é o do LOTE, não a ordem em que ele foi recusado.

    É por ele que a senha volta. Fixá-lo em zero faria a senha do terceiro
    arquivo ser tentada no primeiro — e o primeiro sequer estar cifrado.
    """
    from tests.conftest import SENHA_BTG, _btg_workbook, _cifrar
    cifrado = _cifrar(_btg_workbook(tmp_path / "claro.xlsx"),
                      tmp_path / "btg.xlsx", SENHA_BTG)

    resposta = client.post(
        "/upload/periodo", data={"senhas": ""},
        files=[("files", ("sic.xlsx", sicredi_xlsx.read_bytes(),
                          "application/octet-stream")),
               ("files", ("btg.xlsx", cifrado.read_bytes(),
                          "application/octet-stream"))]).json()

    assert [(p["indice"], p["nome"]) for p in resposta["protegidos"]] == [(1, "btg.xlsx")]
    # O arquivo aberto do lote foi lido assim mesmo: não é porque um pede senha
    # que os outros ficam esperando.
    assert [b["id"] for b in resposta["bancos"]] == ["sicredi"]

    # E a senha endereçada ao índice 1 abre o que faltava.
    completo = client.post(
        "/upload/periodo", data={"senhas": f"1={SENHA_BTG}"},
        files=[("files", ("sic.xlsx", sicredi_xlsx.read_bytes(),
                          "application/octet-stream")),
               ("files", ("btg.xlsx", cifrado.read_bytes(),
                          "application/octet-stream"))]).json()
    assert completo["protegidos"] == []
    assert {b["id"] for b in completo["bancos"]} == {"sicredi", "btg"}


def test_a_senha_do_arquivo_nao_e_aparada(client, tmp_path):
    """Espaço na ponta da senha é senha, não sujeira de formulário.

    Quem escolheu a senha foi o banco; `strip()` no valor transformaria uma
    senha válida em "não confere" sem nada na tela explicando por quê.
    """
    from tests.conftest import _btg_workbook, _cifrar
    com_espacos = "  1234  "
    cifrado = _cifrar(_btg_workbook(tmp_path / "claro.xlsx"),
                      tmp_path / "btg.xlsx", com_espacos)

    resposta = client.post(
        "/upload", data={"senhas": f"0={com_espacos}"},
        files=[("files", ("btg.xlsx", cifrado.read_bytes(),
                          "application/octet-stream"))])
    assert resposta.status_code == 200
