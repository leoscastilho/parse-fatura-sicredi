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


def test_export_devolve_csv_para_download(client, sicredi_xlsx):
    tx = _upload(client, sicredi_xlsx).json()["transaction_id"]
    resposta = client.post("/export", json={"transaction_id": tx, "assignments": [],
                                            "commit_mapping": False})
    assert resposta.status_code == 200
    assert "attachment" in resposta.headers["content-disposition"]
    assert resposta.content.decode().splitlines()[0] == \
        "Data,Categoria,Descrição,Valor (R$),Pago"


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
    assert _upload(client, nubank_csv, banco="sicredi").status_code == 415


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
    assert data["banco_padrao"] == "sicredi", "placeholder não pode ser o padrão"


def test_upload_nubank_usa_as_regras_compartilhadas(client, nubank_csv):
    data = _upload(client, nubank_csv, banco="nubank", vencimento="2026-08-10").json()
    categorias = {g["merchant"]: g["categoria"] for g in data["auto_classified_items"]}
    assert categorias["RENNER"] == "Vestuário"
    assert any("não foi validado" in w for w in data["warnings"])


def test_nubank_sem_vencimento_da_422(client, nubank_csv):
    resposta = _upload(client, nubank_csv, banco="nubank")
    assert resposta.status_code == 422
    assert "vencimento" in resposta.json()["detail"]


def test_banco_inexistente_da_422(client, sicredi_xlsx):
    assert _upload(client, sicredi_xlsx, banco="itau").status_code == 422


# ---------------------------------------------------------------------------
# Regras pela API
# ---------------------------------------------------------------------------

def test_rules_marca_chutes_e_redundancias(client):
    data = client.get("/rules").json()
    assert data["flagged_count"] > 0, "os `# ?` têm que aparecer"
    redundantes = [e for e in data["entries"] if e["redundant_with"]]
    assert redundantes, "SUPERMERCADO/SUPERMERCADOS deveriam ser detectados"


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

def test_export_import_roundtrip(client):
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
    assert set(corpo["conteudo"]["banks"]) == {"sicredi", "nubank"}


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


def test_salvar_perfil_invalido_nao_grava(client, config_dir):
    antes = (config_dir / "banks" / "sicredi.yml").read_text(encoding="utf-8")
    assert client.post("/config/bank", json={"id": "sicredi",
                                             "yaml_text": "sem id nem nome"}).status_code == 422
    assert (config_dir / "banks" / "sicredi.yml").read_text(encoding="utf-8") == antes


def test_salvar_perfil_com_id_divergente_da_422(client):
    assert client.post("/config/bank", json={
        "id": "sicredi", "yaml_text": "id: outro\nnome: Outro\n"}).status_code == 422


def test_testar_perfil_nao_grava_nada(client, config_dir, nubank_csv):
    antes = (config_dir / "banks" / "nubank.yml").read_text(encoding="utf-8")
    resposta = client.post(
        "/config/bank/test",
        data={"bank_id": "nubank", "vencimento": "2026-08-10"},
        files={"file": ("nu.csv", nubank_csv.read_bytes(), "text/csv")},
    ).json()
    assert resposta["lancamentos"] == 4
    assert (config_dir / "banks" / "nubank.yml").read_text(encoding="utf-8") == antes


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

def test_confirmar_chute_mantem_o_mapeamento(client):
    """Antes só dava para apagar (perdendo o mapeamento) ou trocar a categoria."""
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


def test_confirmar_nao_mexe_nas_outras_entradas(client):
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
