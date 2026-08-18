"""Endpoints de configuração: bancos, formato de saída, regex e o pacote.

Tudo aqui edita ARQUIVOS em `config/`, que são commitados no GitHub. Não
existe banco de dados de regras: o estado de configuração da aplicação é o
conteúdo desses YAMLs, versionado como código.

O par `/config/export` + `/config/import` é o que permite alguém usar o portal
sem nada disso no servidor: baixa o pacote, edita, sobe de volta. É também o
esqueleto do modo "outra pessoa" — a mesma função de import já aceita anexar a
config a uma transação em vez de gravar em disco.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import yaml
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from core import BankProfile, ConfigSet, OutputSchema, ProfileError, Ruleset
from core.pipeline import build_description, classify_statement
from core.statement import Entry, read_statement
from core.yaml_edit import (
    YamlEditError, list_rules, rule_add, rule_move, rule_remove, rule_update,
)

from .github_sync import GitHubConflict, GitHubDisabled, GitHubSync, commit_message
from .settings import Settings, get_settings

router = APIRouter(tags=["config"])


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------

class BankSummary(BaseModel):
    id: str
    nome: str
    validado: bool
    estrategia: str
    extensoes: list[str]
    pede_vencimento: bool
    tema: dict[str, str]


class ConfigResponse(BaseModel):
    banks: list[BankSummary]
    banco_padrao: str
    output_yaml: str
    output_exemplo: dict[str, str]
    source_sha: str | None = None


class BankYaml(BaseModel):
    id: str
    yaml_text: str
    commit: bool = False


class OutputYaml(BaseModel):
    yaml_text: str
    commit: bool = False


class RegexRule(BaseModel):
    index: int
    padrao: str
    categoria: str
    comment: str = ""


class RegexOp(BaseModel):
    op: Literal["add", "remove", "update", "move"]
    index: int | None = None
    padrao: str | None = None
    categoria: str | None = None
    comment: str | None = None
    delta: int = 0
    at: int | None = None


class RegexEditRequest(BaseModel):
    operations: list[RegexOp]
    commit: bool = False


class RegexTestRequest(BaseModel):
    padrao: str
    amostras: list[str] = Field(default_factory=list)


class RegexTestResponse(BaseModel):
    valido: bool
    erro: str | None = None
    resultados: list[dict] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def config_root(settings: Settings) -> Path:
    return settings.rules_path.parent


def load_config(settings: Settings) -> ConfigSet:
    try:
        return ConfigSet.load(config_root(settings))
    except ProfileError as exc:
        raise HTTPException(500, detail=str(exc))


def read_categories(settings: Settings) -> str:
    try:
        return settings.rules_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(500, detail=f"não consegui ler {settings.rules_path}: {exc}")


def publish(settings: Settings, text: str, changes: list[dict], label: str) -> str:
    if not settings.github_enabled:
        raise HTTPException(503, detail="GitHub desabilitado: sem FATURA_GITHUB_TOKEN")
    try:
        return GitHubSync(settings).commit(
            text=text, message=commit_message(changes, label), expected_sha=None)
    except (GitHubConflict, GitHubDisabled) as exc:
        raise HTTPException(409, detail=str(exc))


# ---------------------------------------------------------------------------
# Configuração geral
# ---------------------------------------------------------------------------

@router.get("/config", response_model=ConfigResponse)
def get_config(settings: Settings = Depends(get_settings)) -> ConfigResponse:
    """Bancos disponíveis, tema de cada um e o schema de saída."""
    cfg = load_config(settings)
    schema = cfg.output

    # Uma linha de exemplo, montada com o schema atual — assim dá para ver o
    # efeito de mudar o modelo da descrição sem processar uma fatura inteira.
    exemplo_entry = Entry(
        purchase_date=datetime(2026, 7, 15),
        description="SUPERMERCADOS ALVORA",
        installment="03/05",
        amount=270.51,
    )
    exemplo = {
        schema.colunas[0]: datetime(2026, 8, 10).strftime(schema.data_formato),
        "Categoria": "Alimentação",
        "Descrição": build_description(exemplo_entry, schema),
        "Valor (R$)": "270.51",
        "Pago": schema.pago,
    }

    return ConfigResponse(
        banks=[
            BankSummary(
                id=b.id, nome=b.nome, validado=b.validado,
                estrategia=b.estrategia, extensoes=list(b.extensoes),
                pede_vencimento=b.pede_vencimento, tema=b.tema.to_dict(),
            )
            for b in sorted(cfg.banks.values(), key=lambda b: (not b.validado, b.nome))
        ],
        banco_padrao=cfg.default_bank.id,
        output_yaml=schema.raw_text or "",
        output_exemplo=exemplo,
        source_sha=GitHubSync(settings).current_sha() if settings.github_enabled else None,
    )


@router.get("/config/bank/{bank_id}")
def get_bank(bank_id: str, settings: Settings = Depends(get_settings)) -> dict:
    cfg = load_config(settings)
    if bank_id not in cfg.banks:
        raise HTTPException(404, detail=f"banco desconhecido: {bank_id}")
    profile = cfg.banks[bank_id]
    return {"id": profile.id, "nome": profile.nome, "validado": profile.validado,
            "yaml_text": profile.raw_text, "tema": profile.tema.to_dict()}


@router.post("/config/bank")
def save_bank(payload: BankYaml, settings: Settings = Depends(get_settings)) -> dict:
    """Grava o perfil de um banco. Valida ANTES de tocar no disco."""
    if not re.fullmatch(r"[a-z0-9_-]{2,32}", payload.id):
        raise HTTPException(422, detail="id inválido (use a-z, 0-9, - e _)")
    try:
        profile = BankProfile.from_text(payload.yaml_text)
    except (ProfileError, yaml.YAMLError) as exc:
        raise HTTPException(422, detail=f"perfil inválido: {exc}")
    if profile.id != payload.id:
        raise HTTPException(422, detail=f"o `id` dentro do YAML é '{profile.id}'")

    path = config_root(settings) / "banks" / f"{payload.id}.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload.yaml_text, encoding="utf-8")

    return {"ok": True, "id": profile.id, "validado": profile.validado,
            "tema": profile.tema.to_dict()}


@router.post("/config/bank/test")
async def test_bank(
    bank_id: str = Form(...),
    yaml_text: str = Form(""),
    vencimento: str = Form(""),
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Roda o perfil contra um arquivo de verdade, sem gravar nada.

    É a única forma honesta de saber se um perfil de banco funciona: o YAML
    pode estar bem-formado e mesmo assim apontar para uma coluna que não existe.
    """
    cfg = load_config(settings)
    if yaml_text.strip():
        try:
            profile = BankProfile.from_text(yaml_text)
        except (ProfileError, yaml.YAMLError) as exc:
            raise HTTPException(422, detail=f"perfil inválido: {exc}")
    else:
        if bank_id not in cfg.banks:
            raise HTTPException(404, detail=f"banco desconhecido: {bank_id}")
        profile = cfg.banks[bank_id]

    due = None
    if vencimento.strip():
        try:
            due = datetime.strptime(vencimento.strip(), "%Y-%m-%d")
        except ValueError:
            raise HTTPException(422, detail="vencimento deve ser AAAA-MM-DD")

    blob = await file.read()
    try:
        statement = read_statement(io.BytesIO(blob), name=file.filename,
                                   profile=profile, due_date=due)
    except (ProfileError, ValueError, KeyError) as exc:
        raise HTTPException(422, detail=f"não consegui ler o arquivo: {exc}")

    rules = Ruleset.from_text(cfg.categories_text)
    amostra = []
    if statement.due_date:
        lines, _ = classify_statement(statement, rules, schema=cfg.output)
        amostra = [l.to_dict() for l in lines[:8]]

    return {
        "banco": profile.nome,
        "validado": profile.validado,
        "vencimento": statement.due_date.date().isoformat() if statement.due_date else None,
        "pede_vencimento": profile.pede_vencimento,
        "lancamentos": len(statement.entries),
        "debitos": statement.debits,
        "creditos": statement.credits,
        "declarado_debitos": statement.declared_debits,
        "declarado_creditos": statement.declared_credits,
        "confere": statement.reconciles(),
        "amostra": amostra,
    }


@router.post("/config/output")
def save_output(payload: OutputYaml, settings: Settings = Depends(get_settings)) -> dict:
    """Grava o schema de saída. Devolve uma linha de exemplo já com o novo formato."""
    try:
        schema = OutputSchema.from_text(payload.yaml_text)
    except yaml.YAMLError as exc:
        raise HTTPException(422, detail=f"YAML inválido: {exc}")
    if not schema.colunas:
        raise HTTPException(422, detail="`colunas` não pode ficar vazio")

    try:
        build_description(
            Entry(purchase_date=datetime(2026, 7, 15), description="TESTE",
                  installment="01/02", amount=1.0),
            schema,
        )
    except (KeyError, IndexError, ValueError) as exc:
        raise HTTPException(422, detail=f"modelo de descrição inválido: {exc}")

    (config_root(settings) / "output.yml").write_text(payload.yaml_text, encoding="utf-8")
    return {"ok": True, "colunas": schema.colunas}


# ---------------------------------------------------------------------------
# Regras ordenadas (regex)
# ---------------------------------------------------------------------------

@router.get("/rules/regex", response_model=list[RegexRule])
def get_regex_rules(settings: Settings = Depends(get_settings)) -> list[RegexRule]:
    return [RegexRule(index=r["index"], padrao=r["padrao"],
                      categoria=r["categoria"], comment=r["comment"])
            for r in list_rules(read_categories(settings))]


@router.post("/rules/regex")
def edit_regex_rules(
    payload: RegexEditRequest, settings: Settings = Depends(get_settings),
) -> dict:
    """Aplica operações nas regras ordenadas. Tudo ou nada.

    A ordem é o que dá sentido a estas regras (a primeira que casa vence), por
    isso `move` existe como operação de primeira classe em vez de um campo de
    prioridade — reordenar é mover o bloco de linhas no arquivo.
    """
    text = read_categories(settings)
    try:
        for op in payload.operations:
            if op.op == "add":
                if not op.padrao or not op.categoria:
                    raise HTTPException(422, detail="`add` precisa de padrao e categoria")
                text = rule_add(text, op.padrao, op.categoria, at=op.at,
                                comment=op.comment or "")
            elif op.op == "remove":
                text = rule_remove(text, op.index or 0)
            elif op.op == "update":
                if not op.padrao or not op.categoria:
                    raise HTTPException(422, detail="`update` precisa de padrao e categoria")
                text = rule_update(text, op.index or 0, op.padrao, op.categoria, op.comment)
            else:
                text = rule_move(text, op.index or 0, op.delta)
    except re.error as exc:
        raise HTTPException(422, detail=f"regex inválido: {exc}")
    except YamlEditError as exc:
        raise HTTPException(422, detail=str(exc))

    settings.rules_path.write_text(text, encoding="utf-8")

    commit_url = None
    if payload.commit:
        commit_url = publish(
            settings, text,
            [{"kind": o.op, "value": o.padrao or f"#{o.index}"} for o in payload.operations],
            "regras regex")

    return {"applied": len(payload.operations),
            "rules": [RegexRule(index=r["index"], padrao=r["padrao"],
                                categoria=r["categoria"], comment=r["comment"]).model_dump()
                      for r in list_rules(text)],
            "commit_url": commit_url}


@router.post("/rules/regex/test", response_model=RegexTestResponse)
def test_regex(payload: RegexTestRequest) -> RegexTestResponse:
    """Testa um regex contra descrições, do jeito que o motor testa.

    A comparação roda sobre a descrição NORMALIZADA (maiúscula, sem acento,
    CamelCase separado) — testar contra o texto cru daria falsa confiança.
    """
    from core.text import normalize

    try:
        pattern = re.compile(payload.padrao, re.IGNORECASE)
    except re.error as exc:
        return RegexTestResponse(valido=False, erro=str(exc))

    resultados = []
    for amostra in payload.amostras:
        norm = normalize(amostra)
        hit = pattern.search(norm)
        resultados.append({
            "amostra": amostra,
            "normalizado": norm,
            "casa": bool(hit),
            "trecho": hit.group(0) if hit else "",
        })
    return RegexTestResponse(valido=True, resultados=resultados)


# ---------------------------------------------------------------------------
# Pacote de configuração (export / import)
# ---------------------------------------------------------------------------

BUNDLE_FILES = ("categories.yml", "output.yml")


@router.get("/config/export")
def export_config(settings: Settings = Depends(get_settings)):
    """Baixa toda a config num .zip — o que torna o portal portátil.

    Quem quiser rodar isto com as próprias regras baixa o pacote, edita e sobe
    de volta. Nenhuma linha disso precisa viver num banco de dados.
    """
    root = config_root(settings)
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name in BUNDLE_FILES:
            path = root / name
            if path.exists():
                bundle.write(path, arcname=name)
        for path in sorted((root / "banks").glob("*.yml")):
            bundle.write(path, arcname=f"banks/{path.name}")
        bundle.writestr("MANIFEST.yml", yaml.safe_dump({
            "gerado_em": datetime.now(timezone.utc).isoformat(),
            "versao": 1,
            "conteudo": ["categories.yml", "output.yml", "banks/*.yml"],
        }, allow_unicode=True, sort_keys=False))

    buffer.seek(0)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return StreamingResponse(
        buffer, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="config-fatura-{stamp}.zip"'},
    )


@router.post("/config/import")
async def import_config(
    file: UploadFile = File(...),
    dry_run: bool = Form(False),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Sobe um pacote de config. Valida TUDO antes de gravar QUALQUER coisa.

    Um pacote com um perfil de banco quebrado não pode deixar o servidor com
    metade da config nova e metade da antiga.
    """
    blob = await file.read()
    try:
        bundle = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        raise HTTPException(422, detail="não é um .zip válido")

    encontrados: dict[str, str] = {}
    for info in bundle.infolist():
        name = info.filename
        # Defesa contra zip-slip: nada de caminho absoluto ou `..`.
        if name.startswith("/") or ".." in Path(name).parts:
            raise HTTPException(422, detail=f"caminho suspeito no pacote: {name}")
        if info.is_dir() or name == "MANIFEST.yml":
            continue
        if name in BUNDLE_FILES or (name.startswith("banks/") and name.endswith(".yml")):
            encontrados[name] = bundle.read(name).decode("utf-8")

    if "categories.yml" not in encontrados:
        raise HTTPException(422, detail="pacote sem categories.yml")

    relatorio = {"categories.yml": {}, "output.yml": {}, "banks": {}}
    try:
        rules = Ruleset.from_text(encontrados["categories.yml"])
        relatorio["categories.yml"] = {"categorias": len(rules.all_categories()),
                                       "palavras": len(rules.keywords),
                                       "regras": len(rules.ordered_rules)}
        if "output.yml" in encontrados:
            schema = OutputSchema.from_text(encontrados["output.yml"])
            relatorio["output.yml"] = {"colunas": schema.colunas}
        for name, text in encontrados.items():
            if name.startswith("banks/"):
                profile = BankProfile.from_text(text)
                relatorio["banks"][profile.id] = {"nome": profile.nome,
                                                  "estrategia": profile.estrategia,
                                                  "validado": profile.validado}
    except (ProfileError, yaml.YAMLError, KeyError, ValueError) as exc:
        raise HTTPException(422, detail=f"pacote inválido: {exc}")

    if dry_run:
        return {"ok": True, "gravado": False, "conteudo": relatorio}

    root = config_root(settings)
    (root / "banks").mkdir(parents=True, exist_ok=True)
    for name, text in encontrados.items():
        (root / name).write_text(text, encoding="utf-8")

    return {"ok": True, "gravado": True, "conteudo": relatorio}
