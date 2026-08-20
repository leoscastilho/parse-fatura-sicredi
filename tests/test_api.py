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
    """`.pdf` nem chega a ser lido — o Sicredi não exporta assim."""
    assert _upload(client, nubank_csv, nome="fatura.pdf",
                   banco="sicredi").status_code == 415


def test_csv_de_outro_banco_no_sicredi_explica_em_vez_de_estourar(client, nubank_csv):
    """`.csv` deixou de ser exclusividade do Nubank, e isso muda o erro.

    Antes a extensão barrava (415). Agora o arquivo é ACEITO e falha na
    leitura, que é um erro de conteúdo — e o usuário precisa ler "não achei o
    cabeçalho", não um 500 de servidor.
    """
    resposta = _upload(client, nubank_csv, banco="sicredi")
    assert resposta.status_code == 422
    assert "cabeçalho" in resposta.json()["detail"]


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
