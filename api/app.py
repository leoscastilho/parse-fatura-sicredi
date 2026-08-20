"""FastAPI — pipeline de 6 passos, sem nenhum `input()`.

O script de terminal parava e perguntava. Aqui ninguém pode parar: cada
pergunta virou um estado persistido, e o cliente responde num request
posterior carregando o `transaction_id`.

    /categories      o que existe hoje no YAML
    /upload          .xls  -> transaction_id + 4 baldes de itens
    /recategorize    CSV de saída -> mesma revisão, só a coluna Categoria muda
    /travel          períodos de viagem -> as compras que caem dentro deles
    /validate        "se eu fizer isso, o que acontece?" (dry-run, não grava)
    /update-mapping  grava a decisão no YAML de trabalho da transação
    /preview         linhas + atribuições -> dataset final, para a tela de revisão
    /export          mesmo dataset -> CSV em streaming (+ 1 commit no GitHub)
"""

from __future__ import annotations

import io
import unicodedata
from dataclasses import replace
from pathlib import Path
from contextlib import asynccontextmanager
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from core import (
    ClassifiedLine, LineState, Ruleset, classify_sources, lines_to_csv,
    lines_to_csv_preserving_order, output_name, read_output_csv, recategorize,
    sort_lines,
)
from core.analytics import AnalyticsConfig, AnalyticsError, analisar
from core.profiles import ProfileError
from core.recategorize import RecategorizeError
from core.travel import (
    TravelError, TravelRange, apply_travel, mark_travel, purchase_range,
    range_of,
    validate_ranges,
)
from core.text import compact, normalize
from core.yaml_edit import (
    YamlEditError, add_category, add_keyword, add_to_list,
    list_entries, move_entry, remove_entry, set_comment,
)

from .github_sync import GitHubConflict, GitHubDisabled, GitHubSync, commit_message
from .models import (
    Assignment,
    CategoryChangeItem,
    SourceFile,
    AssignmentImpact,
    CategoriesResponse,
    DroppedItem,
    ExportRequest,
    LineItem,
    MappingChange,
    MerchantGroup,
    PreviewRequest,
    PreviewResponse,
    PurchaseRange,
    PurchaseRangeResponse,
    StatementSummary,
    TravelRangeItem,
    TravelRequest,
    TravelResponse,
    UpdateMappingRequest,
    UpdateMappingResponse,
    RuleEntry,
    RuleOp,
    RulesEditRequest,
    RulesEditResponse,
    RulesResponse,
    UploadResponse,
    ValidateRequest,
    ValidateResponse,
    ValidationIssue,
)
from .config_routes import config_root, load_config, router as config_router
from .settings import Settings, get_settings
from .store import Store, TransactionNotFound

_store: Store | None = None


def get_store(settings: Settings = Depends(get_settings)) -> Store:
    global _store
    if _store is None:
        _store = Store(settings.db_path, settings.transaction_ttl_hours)
    return _store


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Limpa transações vencidas ao subir.

    O TTL é aplicado na leitura também, então isto é só higiene de disco — não
    existe janela em que uma transação expirada seja aceita.
    """
    get_store(get_settings()).purge_expired()
    yield


app = FastAPI(
    title="parser-de-fatura-multibancos",
    version="2.0.0",
    description="Converte extratos de cartão (Sicredi, Nubank) no CSV da planilha de finanças.",
    lifespan=lifespan,
)

# Middleware precisa ser registrado ANTES de a aplicação subir — dentro de um
# handler de startup o Starlette recusa ("Cannot add middleware after an
# application has started").  Em produção o nginx faz o proxy e não há CORS;
# isto existe para o `npm run dev` falando direto com o backend.
app.include_router(config_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    # Sem `expose_headers`, o navegador esconde estes do JS em modo CORS — e o
    # front perde o nome do arquivo e o aviso de commit.
    expose_headers=["Content-Disposition", "X-Rows",
                    "X-Mapping-Commit", "X-Mapping-Commit-Error"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_transaction(store: Store, transaction_id: str) -> dict:
    try:
        return store.get(transaction_id)
    except TransactionNotFound:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="transaction_id desconhecido ou expirado — refaça o upload",
        )


def _lines_of(record: dict) -> list[ClassifiedLine]:
    """As linhas do extrato, já com a marca de viagem derivada dos períodos.

    A marca NÃO é gravada em `lines_json`: ela é recalculada aqui a cada
    leitura, a partir dos períodos que o usuário salvou. É o que mantém as
    linhas lidas do extrato imutáveis e faz editar um período ser idempotente
    — apagou o período, sumiu a marca, sem nada para desfazer.
    """
    lines = [ClassifiedLine.from_dict(payload) for payload in record["lines"]]
    ranges = _ranges_of(record)
    return mark_travel(lines, ranges) if ranges else lines


def _ranges_of(record: dict) -> list[TravelRange]:
    saida = []
    for payload in record.get("travel") or []:
        try:
            saida.append(TravelRange.from_dict(payload))
        except TravelError:
            # Período gravado inválido não deve derrubar a transação inteira;
            # ele simplesmente não marca nada.
            continue
    return saida


def _group_by_merchant(lines: list[ClassifiedLine]) -> list[MerchantGroup]:
    """Agrupa por estabelecimento — 23 linhas do Alvorada viram 1 decisão."""
    buckets: dict[tuple[str, str], list[ClassifiedLine]] = defaultdict(list)
    for line in lines:
        buckets[(line.merchant, line.categoria)].append(line)

    groups = [
        MerchantGroup(
            merchant=merchant,
            state=items[0].state,
            categoria=categoria,
            count=len(items),
            total=round(sum(i.valor for i in items), 2),
            line_ids=[i.line_id for i in items],
            samples=[i.descricao for i in items[:3]],
            statements=sorted({i.statement for i in items}),
            matched=items[0].matched,
        )
        for (merchant, categoria), items in buckets.items()
    ]
    return sorted(groups, key=lambda g: -abs(g.total))


def _apply_assignments(
    lines: list[ClassifiedLine], assignments: list[Assignment]
) -> list[ClassifiedLine]:
    """Sobrepõe as decisões do usuário às linhas originais.

    Precedência: decisão de LINHA > decisão de ESTABELECIMENTO > o que a regra
    já tinha dado. As linhas originais nunca são mutadas — cada chamada produz
    cópias novas, então /preview é idempotente.
    """
    by_merchant = {a.target: a for a in assignments if a.scope == "merchant"}
    by_line = {a.target: a for a in assignments if a.scope == "line"}

    resolved: list[ClassifiedLine] = []
    for line in lines:
        assignment = by_line.get(line.line_id) or by_merchant.get(line.merchant)
        if assignment is None:
            resolved.append(line)
            continue

        state = line.state
        if assignment.mark_unknown:
            categoria, state = "", LineState.IGNORED
        elif assignment.mark_marketplace:
            categoria, state = assignment.categoria, LineState.MARKETPLACE
        else:
            categoria = assignment.categoria
            if categoria:
                state = LineState.AUTO

        resolved.append(
            ClassifiedLine(
                **{**line.to_dict(), "categoria": categoria, "state": state,
                   "matched": "manual"}
            )
        )
    return resolved


def _apply_travel(
    lines: list[ClassifiedLine], rejected: list[str] | set[str],
    ranges: list[TravelRange] | None = None,
) -> list[ClassifiedLine]:
    """Converte em `Viagem` as linhas confirmadas, guardando a categoria real.

    Roda DEPOIS de `_apply_assignments` de propósito: a categoria que vai para
    o parêntese é a final, já com as decisões do marketplace e as correções
    manuais aplicadas. Rodar antes gravaria a categoria da regra e perderia
    exatamente o que o usuário acabou de resolver.

    O nome do período é resolvido aqui, redescobrindo qual janela pegou cada
    linha, em vez de ser gravado em `lines_json` no upload. É a mesma razão de
    `_lines_of` não gravar a marca de viagem: renomear um período passa a ser só
    reenviar a lista, sem migrar linha nenhuma.
    """
    rejeitadas = set(rejected)
    periodos = ranges or []
    saida: list[ClassifiedLine] = []
    for line in lines:
        if not line.viagem or line.line_id in rejeitadas:
            saida.append(line)
            continue
        periodo = range_of(line, periodos)
        categoria, descricao = apply_travel(
            line, line.categoria, periodo.rotulo if periodo else "")
        saida.append(replace(line, categoria=categoria, descricao=descricao))
    return saida


def _staged_yaml(record: dict, assignments: list[Assignment]) -> tuple[str, list[MappingChange]]:
    """Aplica ao YAML de trabalho tudo que o usuário pediu para persistir."""
    text = record["yaml_working"]
    known = set(Ruleset.from_text(text).all_categories())
    changes: list[MappingChange] = []

    for assignment in assignments:
        if assignment.mark_unknown:
            text = add_to_list(
                text, "desconhecidos", normalize(assignment.target),
                ["# Estabelecimentos sem classificação conhecida."],
            )
            changes.append(MappingChange(kind="unknown", value=normalize(assignment.target)))
            continue

        if assignment.mark_marketplace:
            text = add_to_list(
                text, "marketplaces", normalize(assignment.target),
                ["# Categoria varia a cada compra; sai em branco de propósito."],
            )
            changes.append(MappingChange(kind="marketplace", value=normalize(assignment.target)))
            continue

        if not assignment.persist_keyword or not assignment.categoria:
            continue

        if assignment.categoria not in known:
            text = add_category(text, assignment.categoria)
            known.add(assignment.categoria)
            changes.append(MappingChange(kind="category", value=assignment.categoria))

        keyword = normalize(assignment.persist_keyword)
        text = add_keyword(text, assignment.categoria, keyword)
        changes.append(
            MappingChange(kind="keyword", categoria=assignment.categoria, value=keyword)
        )

    return text, changes


def _header_safe(text: str) -> str:
    """Headers HTTP são latin-1: acento e travessão estouram na serialização.

    Um "—" numa mensagem de aviso derrubava a resposta inteira com
    UnicodeEncodeError — ou seja, o tratamento de erro virava outro erro 500.
    """
    if not text:
        return ""
    normalizado = unicodedata.normalize("NFKD", text)
    return normalizado.encode("ascii", "replace").decode("ascii")


def _purchase_range(lines: list[ClassifiedLine]) -> PurchaseRange | None:
    inicio, fim = purchase_range(lines)
    if inicio is None or fim is None:
        return None
    return PurchaseRange(inicio=inicio.isoformat(), fim=fim.isoformat())


def _linhas_do_form(bruto: str) -> list[str]:
    """Uma lista que veio num campo de formulário, uma entrada por linha."""
    return [item for item in (l.strip() for l in (bruto or "").splitlines()) if item]


def _apelidos_do_form(bruto: str) -> dict[str, str]:
    """`Nome Completo=Rótulo`, uma linha por pessoa.

    Quem não está no mapa não recebe marca — é assim que "sou eu" viaja: pela
    ausência. Rótulo vazio dá no mesmo, e é por isso que o cliente nem manda o
    par: seriam duas grafias para a mesma coisa.

    A GUARDA DO NOME não é formalidade. Uma linha `=Rhyesla`, sem o lado
    esquerdo, gravaria `{"": "Rhyesla"}` — e aí todo lançamento cujo titular é
    vazio (ou seja, o `.xls` inteiro do site, que não traz a coluna) sairia
    marcado com o nome de outra pessoa.
    """
    saida: dict[str, str] = {}
    for linha in _linhas_do_form(bruto):
        nome, _, apelido = linha.partition("=")
        if nome.strip():
            saida[nome.strip()] = apelido.strip()
    return saida


def _vencimento(bruto: str, profile) -> datetime | None:
    if bruto.strip():
        try:
            return datetime.strptime(bruto.strip(), "%Y-%m-%d")
        except ValueError:
            raise HTTPException(422, detail="vencimento deve ser AAAA-MM-DD")
    if profile.pede_vencimento:
        raise HTTPException(
            422,
            detail=f"{profile.nome} não traz a data de vencimento no arquivo — "
                   "informe a data no upload")
    return None


async def _fontes(files: list[UploadFile], profile,
                  settings: Settings) -> list[tuple[str, io.BytesIO]]:
    """Lê os arquivos para memória, validando extensão e tamanho.

    Extraído porque `/upload` e `/upload/periodo` precisam ler o MESMO lote com
    as mesmas regras. Duas cópias divergiriam no dia em que uma ganhasse um
    limite novo, e o portal passaria a aceitar no pré-voo o que rejeita no
    processamento — ou pior, o contrário.
    """
    if len(files) > settings.max_files_per_upload:
        raise HTTPException(413, detail=f"máximo {settings.max_files_per_upload} arquivos")

    fontes = []
    for upload_file in files:
        if not profile.accepts(upload_file.filename):
            raise HTTPException(
                415,
                detail=f"{upload_file.filename}: {profile.nome} espera "
                       f"{', '.join(profile.extensoes)}")
        blob = await upload_file.read()
        if len(blob) > settings.max_upload_bytes:
            raise HTTPException(413, detail=f"{upload_file.filename} excede o limite")
        fontes.append((upload_file.filename, io.BytesIO(blob)))
    return fontes


def _period_of(record: dict) -> str:
    periods = sorted({s["due_date"][:7] for s in record["statements"]})
    if not periods:
        return "?"
    return periods[0] if len(periods) == 1 else f"{periods[0]}..{periods[-1]}"


# ---------------------------------------------------------------------------
# 1. GET /categories
# ---------------------------------------------------------------------------

@app.get("/categories", response_model=CategoriesResponse)
def get_categories(settings: Settings = Depends(get_settings)) -> CategoriesResponse:
    """Categorias e mapeamentos válidos, lidos do YAML."""
    source, sha = "local", None
    try:
        text = settings.rules_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(500, detail=f"não consegui ler {settings.rules_path}: {exc}")

    if settings.github_enabled:
        sha = GitHubSync(settings).current_sha()

    rules = Ruleset.from_text(text)
    keywords: dict[str, list[str]] = defaultdict(list)
    for trecho, categoria in rules.keywords:
        keywords[categoria].append(trecho)

    return CategoriesResponse(
        categories=rules.all_categories(),
        # Filtrado por `all_categories` de propósito: um nome digitado errado em
        # `categorias_fixas` sumiria de uma lista onde nunca esteve, e a tela
        # perderia uma opção sem que nada ficasse protegido em troca.
        fixed_categories=[c for c in rules.all_categories() if rules.is_fixed(c)],
        keywords_by_category={k: sorted(v) for k, v in sorted(keywords.items())},
        ordered_rules=[{"padrao": p.pattern, "categoria": c} for p, c in rules.ordered_rules],
        marketplaces=rules.manual,
        unknown=rules.unknown,
        excluded=rules.exclude,
        default_category=rules.default_category,
        source=source,
        source_sha=sha,
    )


# ---------------------------------------------------------------------------
# 2. POST /upload
# ---------------------------------------------------------------------------

@app.post("/upload", response_model=UploadResponse)
async def upload(
    files: list[UploadFile] = File(..., description="extrato do banco escolhido"),
    banco: str = Form("", description="id do perfil; vazio = o primeiro validado"),
    vencimento: str = Form("", description="AAAA-MM-DD, para bancos que não trazem a data"),
    titulares: str = Form("", description="`Nome Completo=Rótulo` por linha; vazio = sou eu"),
    settings: Settings = Depends(get_settings),
    store: Store = Depends(get_store),
) -> UploadResponse:
    """Lê os extratos com o perfil do banco e abre a transação."""
    cfg = load_config(settings)
    try:
        profile = cfg.bank(banco or None)
    except Exception as exc:
        raise HTTPException(422, detail=str(exc))

    text = cfg.categories_text
    yaml_sha = GitHubSync(settings).current_sha() if settings.github_enabled else None
    rules = Ruleset.from_text(text)

    due = _vencimento(vencimento, profile)

    warnings = []
    if not profile.validado:
        warnings.append(
            f"O perfil de {profile.nome} ainda não foi validado contra uma fatura real. "
            "Confira os totais antes de colar na planilha.")

    sources = await _fontes(files, profile, settings)

    try:
        lines, dropped, statements = classify_sources(
            sources, rules, profile=profile, schema=cfg.output, due_date=due,
            apelidos=_apelidos_do_form(titulares))
    # `ProfileError` entra aqui junto com `ValueError` porque agora ele é
    # alcançável pelo uso normal: com o Sicredi aceitando `.csv`, soltar um CSV
    # que não é a fatura do app é um erro de CONTEÚDO, não de extensão — e o
    # que o usuário precisa ler é "não achei o cabeçalho", não um 500.
    except (ValueError, ProfileError) as exc:
        raise HTTPException(422, detail=str(exc))

    summaries = []
    for statement in statements:
        dropped_here = sum(1 for d in dropped if d.statement == statement.name)
        summary = StatementSummary(
            name=statement.name,
            due_date=statement.due_date.date().isoformat(),
            data_column=statement.due_date.strftime("%m/%d/%Y"),
            entries=len(statement.entries),
            dropped=dropped_here,
            debits=statement.debits,
            declared_debits=statement.declared_debits,
            credits=statement.credits,
            declared_credits=statement.declared_credits,
            reconciles=statement.reconciles(),
        )
        summaries.append(summary)
        if not summary.reconciles:
            warnings.append(
                f"{statement.name}: a soma lida não bate com o total da fatura — "
                "algum lançamento pode não ter sido interpretado"
            )

    transaction_id, expires = store.create(
        filename=output_name(statements, cfg.output),
        yaml_text=text,
        yaml_sha=yaml_sha,
        statements=[s.model_dump() for s in summaries],
        dropped=[d.__dict__ for d in dropped],
        lines=[line.to_dict() for line in lines],
    )

    def bucket(state: LineState) -> list[ClassifiedLine]:
        return [line for line in lines if line.state is state]

    return UploadResponse(
        transaction_id=transaction_id,
        expires_at=expires,
        statements=summaries,
        dropped=[DroppedItem(**d.__dict__) for d in dropped],
        unmapped_items=_group_by_merchant(bucket(LineState.UNMAPPED)),
        auto_classified_items=_group_by_merchant(bucket(LineState.AUTO)),
        marketplace_items=[LineItem.from_core(l) for l in sort_lines(bucket(LineState.MARKETPLACE))],
        ignored_items=_group_by_merchant(bucket(LineState.IGNORED)),
        warnings=warnings,
        purchase_range=_purchase_range(lines),
    )


# ---------------------------------------------------------------------------
# 2a. POST /upload/periodo — pré-voo
# ---------------------------------------------------------------------------

@app.post("/upload/periodo", response_model=PurchaseRangeResponse)
async def upload_periodo(
    files: list[UploadFile] = File(..., description="extrato do banco escolhido"),
    banco: str = Form(""),
    vencimento: str = Form(""),
    settings: Settings = Depends(get_settings),
) -> PurchaseRangeResponse:
    """De quando a quando vão as COMPRAS deste lote — e nada além disso.

    Existe para a pergunta "viajou neste período?" poder nomear as datas antes
    do processamento. Sem isto, os seletores de data da tela de upload ficam
    soltos: dá para escolher uma viagem em 2019 num lote que cobre julho de
    2026, e o erro só aparece do outro lado.

    É uma LEITURA, não uma transação: nada vai para o SQLite, nada é gravado,
    não há `transaction_id` para expirar. Roda a mesma `classify_sources` do
    /upload de propósito — um caminho paralelo mais barato acabaria devolvendo
    uma data que o processamento depois contradiz, que é pior do que não ter
    data nenhuma.
    """
    cfg = load_config(settings)
    try:
        profile = cfg.bank(banco or None)
    except Exception as exc:
        raise HTTPException(422, detail=str(exc))

    # O vencimento pode não ter sido preenchido ainda: aqui ele não faz falta,
    # porque a data comparada é a da COMPRA, não a do vencimento da fatura.
    due = _vencimento(vencimento, profile) if vencimento.strip() else None
    sources = await _fontes(files, profile, settings)

    try:
        lines, _, statements = classify_sources(
            sources, Ruleset.from_text(cfg.categories_text),
            profile=profile, schema=cfg.output, due_date=due)
    # `ProfileError` entra aqui junto com `ValueError` porque agora ele é
    # alcançável pelo uso normal: com o Sicredi aceitando `.csv`, soltar um CSV
    # que não é a fatura do app é um erro de CONTEÚDO, não de extensão — e o
    # que o usuário precisa ler é "não achei o cabeçalho", não um 500.
    except (ValueError, ProfileError) as exc:
        raise HTTPException(422, detail=str(exc))

    # Os titulares saem dos EXTRATOS, não das linhas: `ClassifiedLine` já é a
    # descrição pronta, e a essa altura o nome ou virou marca ou foi descartado.
    vistos: list[str] = []
    for statement in statements:
        for nome in statement.cardholders:
            if nome not in vistos:
                vistos.append(nome)
    sugerido = next((s.titular for s in statements if s.titular), None)

    return PurchaseRangeResponse(
        purchase_range=_purchase_range(lines),
        titulares=sorted(vistos),
        # Só sugere o que existe: com dois arquivos de bancos diferentes, o
        # "Associado" de um pode não aparecer nos lançamentos do outro, e
        # sugerir um nome que não está na lista deixaria a tela sem seleção.
        eu_sugerido=sugerido if sugerido in vistos else None,
    )


# ---------------------------------------------------------------------------
# 2b. POST /recategorize
# ---------------------------------------------------------------------------

@app.post("/recategorize", response_model=UploadResponse)
async def recategorize_csv(
    files: list[UploadFile] = File(..., description="CSV no formato de saída"),
    settings: Settings = Depends(get_settings),
    store: Store = Depends(get_store),
) -> UploadResponse:
    """Passa as regras ATUAIS por cima de um CSV que já saiu daqui.

    Mesma revisão do fluxo de fatura — novos, marketplace, conferência — só que
    a origem é o próprio formato de saída. Sai o mesmo arquivo com a coluna
    Categoria atualizada, nas mesmas linhas e na mesma ordem.
    """
    if len(files) > settings.max_files_per_upload:
        raise HTTPException(413, detail=f"máximo {settings.max_files_per_upload} arquivos")

    cfg = load_config(settings)
    rules = Ruleset.from_text(cfg.categories_text)
    yaml_sha = GitHubSync(settings).current_sha() if settings.github_enabled else None

    lidas: list[ClassifiedLine] = []
    fontes: list[SourceFile] = []

    for indice, upload_file in enumerate(files):
        if not upload_file.filename.lower().endswith(".csv"):
            raise HTTPException(
                415, detail=f"{upload_file.filename}: a recategorização espera .csv "
                            "no formato de saída deste portal")
        blob = await upload_file.read()
        if len(blob) > settings.max_upload_bytes:
            raise HTTPException(413, detail=f"{upload_file.filename} excede o limite")
        try:
            linhas = read_output_csv(io.BytesIO(blob), name=upload_file.filename,
                                     schema=cfg.output, index=indice)
        except RecategorizeError as exc:
            raise HTTPException(422, detail=str(exc))

        lidas += linhas
        fontes.append(SourceFile(name=upload_file.filename, rows=len(linhas),
                                 total=round(sum(l.valor for l in linhas), 2)))

    linhas, mudancas = recategorize(lidas, rules)

    nome = (f"recategorizado_{Path(files[0].filename).stem}.csv" if len(files) == 1
            else f"recategorizado_{len(files)}_arquivos.csv")

    transaction_id, expires = store.create(
        filename=nome, yaml_text=cfg.categories_text, yaml_sha=yaml_sha,
        statements=[], dropped=[],
        lines=[l.to_dict() for l in linhas], modo="recategorizacao",
    )

    def balde(state: LineState) -> list[ClassifiedLine]:
        return [l for l in linhas if l.state is state]

    avisos = []
    sem_categoria = sum(1 for l in linhas if not l.categoria)
    if sem_categoria:
        avisos.append(
            f"{sem_categoria} linha(s) continuam sem categoria: nem a regra opinou "
            "nem havia categoria no arquivo de origem.")

    return UploadResponse(
        modo="recategorizacao",
        transaction_id=transaction_id,
        expires_at=expires,
        statements=[],
        dropped=[],
        unmapped_items=_group_by_merchant(balde(LineState.UNMAPPED)),
        auto_classified_items=_group_by_merchant(balde(LineState.AUTO)),
        marketplace_items=[LineItem.from_core(l) for l in balde(LineState.MARKETPLACE)],
        ignored_items=_group_by_merchant(balde(LineState.IGNORED)),
        warnings=avisos,
        source_files=fontes,
        changes=[CategoryChangeItem(**m.__dict__) for m in mudancas],
        unchanged=len(linhas) - len(mudancas),
    )


# ---------------------------------------------------------------------------
# 2b. POST /analytics
# ---------------------------------------------------------------------------

@app.post("/analytics")
async def analytics(
    files: list[UploadFile] = File(..., description="um ou mais CSVs de histórico"),
    inicio: str = Form("", description="AAAA-MM, inclusivo"),
    fim: str = Form("", description="AAAA-MM, inclusivo"),
    sem_categorias: str = Form("", description="categorias a excluir, uma por linha"),
    sem_linhas: str = Form("", description="ids de lançamento a excluir, um por linha"),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Analisa um histórico inteiro e devolve tudo já agregado.

    SEM ESTADO: não há `transaction_id`, nada vai para o SQLite e nada é
    gravado. O arquivo é lido, virado em números e esquecido — é uma leitura,
    não uma revisão, e não faz sentido poder "voltar uma etapa" nela.
    """
    if len(files) > settings.max_files_per_upload:
        raise HTTPException(413, detail=f"máximo {settings.max_files_per_upload} arquivos")

    # Vários arquivos NÃO são deduplicados: eles são de pessoas diferentes (a
    # análise do casal), então duas linhas idênticas em arquivos diferentes são
    # dois gastos de verdade. Deduplicar apagaria metade de um mercado dividido.
    conteudos: list[tuple[str, str]] = []
    for arquivo in files:
        if not arquivo.filename or not arquivo.filename.lower().endswith(".csv"):
            raise HTTPException(415, detail="a análise aceita .csv")
        blob = await arquivo.read()
        if len(blob) > settings.max_upload_bytes:
            raise HTTPException(413, detail=f"{arquivo.filename}: arquivo grande demais")
        # utf-8-sig come o BOM que o Excel adora deixar no começo.
        conteudos.append((arquivo.filename, blob.decode("utf-8-sig", errors="replace")))

    caminho = config_root(settings) / "analytics.yml"
    try:
        cfg = AnalyticsConfig.from_text(caminho.read_text(encoding="utf-8"))
    except OSError:
        # Sem o arquivo a análise ainda roda: todo mundo vira gasto, que é o
        # padrão declarado. Melhor um número explicado do que uma tela vazia.
        cfg = AnalyticsConfig()

    try:
        # O recorte acontece AQUI, antes de qualquer soma. Filtrar no cliente
        # depois de agregar daria totais que não batem com os gráficos: média
        # mensal, custo fixo e anomalias precisam ser recalculados sobre o
        # período escolhido, não fatiados a posteriori.
        # As listas viajam separadas por quebra de linha, não por vírgula: nome
        # de categoria e descrição de lançamento têm vírgula com frequência
        # ("Alimentação, bar"), e o separador não pode aparecer no dado.
        resultado = analisar(conteudos, cfg,
                             inicio=inicio.strip() or None, fim=fim.strip() or None,
                             sem_categorias=_linhas_do_form(sem_categorias),
                             sem_linhas=_linhas_do_form(sem_linhas))
    except AnalyticsError as exc:
        raise HTTPException(422, detail=str(exc))

    resultado["arquivo"] = ", ".join(nome for nome, _ in conteudos)
    return resultado


# ---------------------------------------------------------------------------
# 2c. POST /travel
# ---------------------------------------------------------------------------

@app.post("/travel", response_model=TravelResponse)
def travel(payload: TravelRequest, store: Store = Depends(get_store)) -> TravelResponse:
    """Define os períodos de viagem da transação e devolve o que eles pegam.

    Idempotente e substitutivo: a lista enviada VIRA a lista de períodos, então
    remover um período é mandar a lista sem ele. Nada de categoria muda aqui —
    isto só marca as candidatas. A conversão em `Viagem` acontece no /preview e
    no /export, depois da etapa de confirmação.
    """
    record = _load_transaction(store, payload.transaction_id)

    # Na recategorização o contrato é que a descrição não seja tocada, e a
    # viagem escreve a categoria real dentro dela. Os dois não cabem juntos.
    if record["modo"] == "recategorizacao":
        raise HTTPException(
            409,
            detail="períodos de viagem só valem na importação de fatura — a "
                   "recategorização não altera a descrição do arquivo")

    try:
        ranges = [TravelRange.from_dict(r.model_dump()) for r in payload.ranges]
    except TravelError as exc:
        raise HTTPException(422, detail=str(exc))

    store.save_travel(payload.transaction_id, [r.to_dict() for r in ranges])

    cru = [ClassifiedLine.from_dict(p) for p in record["lines"]]
    marcadas = mark_travel(cru, ranges)
    candidatas = [l for l in marcadas if l.viagem]

    # Períodos novos podem ter deixado de pegar linhas que o usuário já havia
    # desmarcado; a rejeição só faz sentido para o que ainda é candidato.
    vivas = {l.line_id for l in candidatas}
    rejeitadas = [i for i in (record.get("travel_rejected") or []) if i in vivas]
    store.save_travel_rejected(payload.transaction_id, rejeitadas)

    return TravelResponse(
        transaction_id=payload.transaction_id,
        ranges=[TravelRangeItem(**r.to_dict()) for r in ranges],
        purchase_range=_purchase_range(cru),
        warnings=validate_ranges(ranges, cru),
        items=[LineItem.from_core(l) for l in sort_lines(candidatas)],
        count=len(candidatas),
        total=round(sum(l.valor for l in candidatas), 2),
    )


# ---------------------------------------------------------------------------
# 3. POST /validate
# ---------------------------------------------------------------------------

@app.post("/validate", response_model=ValidateResponse)
def validate(
    payload: ValidateRequest, store: Store = Depends(get_store)
) -> ValidateResponse:
    """Dry-run: valida as decisões e mostra o estrago antes de gravar.

    O valor real aqui não é "a categoria existe?" — isso o front já sabe. É o
    IMPACTO: se você gravar a palavra-chave `SEGURO` em Seguros, quantas linhas
    desta fatura mudam, e quais delas estavam classificadas em OUTRA categoria
    e seriam roubadas. Nada é persistido.
    """
    record = _load_transaction(store, payload.transaction_id)
    lines = _lines_of(record)
    rules = Ruleset.from_text(record["yaml_working"])
    valid = set(rules.all_categories())

    issues: list[ValidationIssue] = []
    impacts: list[AssignmentImpact] = []

    by_merchant_state = {line.merchant: line.state for line in lines}
    known_ids = {line.line_id for line in lines}

    for assignment in payload.assignments:
        target = assignment.target

        if assignment.scope == "line" and target not in known_ids:
            issues.append(ValidationIssue(severity="error", target=target,
                                          message="line_id não pertence a esta transação"))
            continue
        if assignment.scope == "merchant" and target not in by_merchant_state:
            issues.append(ValidationIssue(severity="error", target=target,
                                          message="estabelecimento não pertence a esta transação"))
            continue

        if assignment.mark_unknown and assignment.categoria:
            issues.append(ValidationIssue(
                severity="error", target=target,
                message="não dá para marcar como desconhecido e atribuir categoria"))
            continue

        if assignment.categoria and assignment.categoria not in valid:
            issues.append(ValidationIssue(
                severity="warning", target=target,
                message=f"categoria nova '{assignment.categoria}' — "
                        "será criada no YAML ao confirmar"))

        if not assignment.categoria and not (assignment.mark_unknown or assignment.mark_marketplace):
            issues.append(ValidationIssue(severity="error", target=target,
                                          message="categoria vazia"))
            continue

        if assignment.persist_keyword and by_merchant_state.get(target) is LineState.MARKETPLACE:
            issues.append(ValidationIssue(
                severity="error", target=target,
                message="marketplace não aceita palavra-chave: a categoria muda "
                        "a cada compra, então a decisão é por linha"))
            continue

        impact = AssignmentImpact(target=target, categoria=assignment.categoria,
                                  lines_affected=0)

        if assignment.persist_keyword:
            keyword = normalize(assignment.persist_keyword)
            if not keyword:
                issues.append(ValidationIssue(severity="error", target=target,
                                              message="palavra-chave vazia depois de normalizar"))
                continue

            impact.keyword_conflicts = [
                {"keyword": trecho, "categoria": categoria}
                for trecho, categoria in rules.keyword_conflicts(keyword)
            ]

            # Simula: quem mais nesta fatura passaria a casar com o trecho novo?
            for line in lines:
                if not Ruleset._hit(keyword, normalize(line.merchant_raw)):
                    continue
                impact.lines_affected += 1
                if line.categoria and line.categoria != assignment.categoria:
                    impact.reclassified_away.append(
                        {"descricao": line.descricao, "de": line.categoria,
                         "para": assignment.categoria}
                    )

            if impact.reclassified_away:
                issues.append(ValidationIssue(
                    severity="warning", target=target,
                    message=f"'{keyword}' também casa com {len(impact.reclassified_away)} "
                            "lançamento(s) já classificado(s) em outra categoria"))
        else:
            impact.lines_affected = sum(
                1 for line in lines
                if (line.line_id == target if assignment.scope == "line"
                    else line.merchant == target)
            )

        impacts.append(impact)

    return ValidateResponse(
        ok=not any(i.severity == "error" for i in issues), issues=issues, impacts=impacts
    )


# ---------------------------------------------------------------------------
# 4. POST /update-mapping
# ---------------------------------------------------------------------------

@app.post("/update-mapping", response_model=UpdateMappingResponse)
def update_mapping(
    payload: UpdateMappingRequest,
    settings: Settings = Depends(get_settings),
    store: Store = Depends(get_store),
) -> UpdateMappingResponse:
    """Grava as decisões no YAML de trabalho da transação.

    Por padrão NÃO commita: acumula, e o /export publica tudo num commit só.
    `commit_now=true` força a publicação imediata, se você quiser.
    """
    record = _load_transaction(store, payload.transaction_id)

    try:
        text, changes = _staged_yaml(record, payload.assignments)
    except YamlEditError as exc:
        raise HTTPException(422, detail=str(exc))

    previous = [MappingChange(**c) for c in record["mapping_changes"]]
    merged = previous + changes
    store.save_yaml_working(
        payload.transaction_id, text, [c.model_dump() for c in merged]
    )

    # Grava no disco JÁ. Marcar "lembrar" e clicar em continuar é a decisão;
    # esperar o export para persistir significava perder tudo se a revisão
    # fosse abandonada — e, sem GitHub, perder para sempre.
    if changes:
        _persistir_local(settings, text)

    committed, url = False, None
    if payload.commit_now and changes:
        url = _commit(settings, store, payload.transaction_id, text, merged, record)
        committed = True

    return UpdateMappingResponse(
        staged=changes, total_staged=len(merged), yaml_valid=True,
        committed=committed, commit_url=url,
    )


def _persistir_local(settings: Settings, text: str) -> None:
    """Grava o `categories.yml` no volume — independente do GitHub.

    Isto ficava DENTRO do `_commit`, depois do push. Sem token (ou com o push
    falhando), o "lembrar" não chegava a lugar nenhum: vivia só no
    `yaml_working` da transação, que expira em 24h. O usuário marcava, via
    `200 OK`, e no mês seguinte o estabelecimento voltava como novo.

    Publicar no GitHub é sincronização; gravar no disco é a persistência. São
    coisas diferentes e a segunda não pode depender da primeira.
    """
    try:
        settings.rules_path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise HTTPException(
            500, detail=f"não consegui gravar {settings.rules_path}: {exc}")


def _commit(settings, store, transaction_id, text, changes, record) -> str:
    if not settings.github_enabled:
        raise HTTPException(
            503, detail="GitHub não configurado (FATURA_GITHUB_TOKEN vazio)")
    try:
        url = GitHubSync(settings).commit(
            text=text,
            message=commit_message([c.model_dump() for c in changes], _period_of(record)),
            expected_sha=record["yaml_sha"],
        )
    except GitHubConflict as exc:
        raise HTTPException(409, detail=str(exc))
    except GitHubDisabled as exc:
        raise HTTPException(503, detail=str(exc))
    except Exception as exc:
        # Token revogado, 401, DNS, timeout, PyGithub ausente… Nada vindo do
        # GitHub pode escapar como 500: quem chama decide se isso é fatal, e no
        # /export não é — o CSV vale mais que o commit.
        raise HTTPException(502, detail=f"falha ao publicar no GitHub: {exc}")

    store.mark_committed(transaction_id, url)
    return url


# ---------------------------------------------------------------------------
# 5. POST /preview
# ---------------------------------------------------------------------------

@app.post("/preview", response_model=PreviewResponse)
def preview(payload: PreviewRequest, store: Store = Depends(get_store)) -> PreviewResponse:
    """Linhas guardadas + atribuições resolvidas = dataset final."""
    record = _load_transaction(store, payload.transaction_id)
    store.save_assignments(
        payload.transaction_id, [a.model_dump() for a in payload.assignments]
    )
    store.save_travel_rejected(payload.transaction_id, payload.travel_rejected)

    resolvidas = _apply_assignments(_lines_of(record), payload.assignments)
    # A viagem entra por ÚLTIMO: a categoria que vai para o parêntese é a
    # final, já com marketplace e correções manuais resolvidos.
    resolvidas = _apply_travel(resolvidas, payload.travel_rejected, _ranges_of(record))
    # Recategorização não reordena: o compromisso é que só a coluna Categoria
    # mude em relação ao arquivo de entrada.
    resolved = (resolvidas if record["modo"] == "recategorizacao"
                else sort_lines(resolvidas))

    by_category: dict[str, float] = defaultdict(float)
    for line in resolved:
        by_category[line.categoria or "(sem categoria)"] += line.valor

    return PreviewResponse(
        transaction_id=payload.transaction_id,
        rows=[LineItem.from_core(line) for line in resolved],
        total=round(sum(line.valor for line in resolved), 2),
        by_category={k: round(v, 2) for k, v in sorted(by_category.items())},
        remaining_blank=sum(1 for line in resolved if not line.categoria),
        filename=record["filename"],
    )


# ---------------------------------------------------------------------------
# 6. POST /export
# ---------------------------------------------------------------------------

@app.post("/export")
def export(
    payload: ExportRequest,
    settings: Settings = Depends(get_settings),
    store: Store = Depends(get_store),
):
    """Monta o CSV final e devolve como download. Publica o YAML da sessão."""
    record = _load_transaction(store, payload.transaction_id)
    assignments = (
        payload.assignments
        if payload.assignments is not None
        else [Assignment(**a) for a in record["assignments"]]
    )
    rejected = (
        payload.travel_rejected
        if payload.travel_rejected is not None
        else record["travel_rejected"]
    )
    resolved = _apply_travel(
        _apply_assignments(_lines_of(record), assignments), rejected,
        _ranges_of(record))
    cfg = load_config(settings)
    blob = (lines_to_csv_preserving_order(resolved, schema=cfg.output,
                                          encoding=payload.encoding)
            if record["modo"] == "recategorizacao"
            else lines_to_csv(resolved, encoding=payload.encoding, schema=cfg.output))

    # A publicação do YAML no GitHub é BEST-EFFORT: o trabalho deste endpoint é
    # entregar o CSV. Sem token, sem rede ou com conflito de SHA, o download
    # continua acontecendo e o motivo volta num header — perder uma revisão
    # inteira porque um commit falhou seria desproporcional.
    commit_url, commit_error = None, None
    changes = [MappingChange(**c) for c in record["mapping_changes"]]
    if payload.commit_mapping and changes:
        # O disco primeiro, sempre. O GitHub é sincronização e é best-effort;
        # a persistência não pode ficar refém dele.
        _persistir_local(settings, record["yaml_working"])
        if not settings.github_enabled:
            commit_error = ("GitHub não configurado — o categories.yml foi "
                            "gravado no volume local, mas não publicado")
        else:
            try:
                commit_url = _commit(
                    settings, store, payload.transaction_id,
                    record["yaml_working"], changes, record,
                )
            except HTTPException as exc:
                commit_error = str(exc.detail)

    headers = {
        "Content-Disposition": f'attachment; filename="{record["filename"]}"',
        "X-Rows": str(len(resolved)),
        "X-Mapping-Commit": commit_url or "",
        "X-Mapping-Commit-Error": _header_safe(commit_error),
    }
    return StreamingResponse(io.BytesIO(blob), media_type="text/csv; charset=utf-8",
                            headers=headers)


# ---------------------------------------------------------------------------
# 7. Revisão de regras — GET /rules, POST /rules/edit
# ---------------------------------------------------------------------------

def _read_rules_text(settings: Settings) -> str:
    try:
        return settings.rules_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(500, detail=f"não consegui ler {settings.rules_path}: {exc}")


def _rule_entries(text: str) -> list[RuleEntry]:
    """Entradas do YAML, anotadas com as relações de contenção.

    Vale "trecho mais longo vence".  Então quando um trecho CURTO está contido
    num LONGO:

      * mesma categoria  -> o longo é REDUNDANTE (`CAFETERIA` já pega
        `BARAO CAFETERIA`); apagar reduz o arquivo sem mudar resultado;
      * categorias diferentes -> o longo VENCE onde ambos casam
        (`MERCADO 11PRODUTOS` ganha de `MERCADO`); é intencional, só avisamos.
    """
    raw = list_entries(text)
    keywords = [e for e in raw if e["block"] in ("palavras", "palavras_genericas")]

    entries: list[RuleEntry] = []
    for entry in raw:
        redundant, overrides = [], []
        if entry["block"] in ("palavras", "palavras_genericas"):
            mine = compact(normalize(entry["value"]))
            for other in keywords:
                theirs = compact(normalize(other["value"]))
                if not theirs or theirs == mine or theirs not in mine:
                    continue
                if len(theirs) >= len(mine):
                    continue
                label = f"{other['value']} ({other['categoria']})"
                if other["categoria"] == entry["categoria"]:
                    redundant.append(label)
                else:
                    overrides.append(label)
        entries.append(RuleEntry(**entry, redundant_with=redundant, overrides=overrides))
    return entries


def _apply_rule_ops(text: str, operations: list[RuleOp]) -> tuple[str, int]:
    applied = 0
    known = set(Ruleset.from_text(text).all_categories())

    for op in operations:
        value = normalize(op.value) if op.block != "excluir" else op.value.strip()
        if not value:
            raise HTTPException(422, detail="valor vazio")

        if op.op == "confirm":
            # Confirma um chute: tira o `# ?` e mantém o mapeamento.
            text = set_comment(text, op.block, op.categoria, op.value, "")
        elif op.op == "remove":
            text = remove_entry(text, op.block, op.categoria, op.value)
        elif op.op == "move":
            if op.block not in ("palavras", "palavras_genericas"):
                raise HTTPException(422, detail="`move` só existe em `palavras`")
            if not op.categoria or not op.new_categoria:
                raise HTTPException(422, detail="`move` precisa de categoria e new_categoria")
            if op.new_categoria not in known:
                text = add_category(text, op.new_categoria)
                known.add(op.new_categoria)
            text = move_entry(text, op.categoria, op.new_categoria, op.value)
        else:  # add
            if op.block in ("palavras", "palavras_genericas"):
                if not op.categoria:
                    raise HTTPException(422, detail="`add` em palavras precisa de categoria")
                if op.categoria not in known:
                    text = add_category(text, op.categoria)
                    known.add(op.categoria)
                text = add_keyword(text, op.categoria, value)
            else:
                text = add_to_list(text, op.block, value, [f"# {op.block}"])
        applied += 1

    return text, applied


@app.get("/rules", response_model=RulesResponse)
def get_rules(settings: Settings = Depends(get_settings)) -> RulesResponse:
    """Todas as regras do YAML, com comentário e conflitos — para revisão."""
    text = _read_rules_text(settings)
    entries = _rule_entries(text)
    rules = Ruleset.from_text(text)
    return RulesResponse(
        entries=entries,
        categories=rules.all_categories(),
        ordered_rules=[{"padrao": p.pattern, "categoria": c} for p, c in rules.ordered_rules],
        flagged_count=sum(1 for e in entries if e.flagged),
        source_sha=GitHubSync(settings).current_sha() if settings.github_enabled else None,
    )


@app.post("/rules/edit", response_model=RulesEditResponse)
def edit_rules(
    payload: RulesEditRequest,
    settings: Settings = Depends(get_settings),
) -> RulesEditResponse:
    """Aplica edições no YAML. Tudo ou nada.

    O texto novo só substitui o arquivo depois que TODAS as operações passaram
    e o YAML foi relido com sucesso — uma operação inválida no meio da lista
    não deixa o arquivo pela metade.
    """
    original = _read_rules_text(settings)
    try:
        text, applied = _apply_rule_ops(original, payload.operations)
    except YamlEditError as exc:
        raise HTTPException(422, detail=str(exc))

    settings.rules_path.write_text(text, encoding="utf-8")

    commit_url = None
    if payload.commit:
        if not settings.github_enabled:
            raise HTTPException(503, detail="GitHub desabilitado")
        summary = [{"kind": op.op, "categoria": op.categoria, "value": op.value}
                   for op in payload.operations]
        try:
            commit_url = GitHubSync(settings).commit(
                text=text,
                message=commit_message(summary, "revisão de regras"),
                expected_sha=None,   # edição direta: última escrita vence
            )
        except (GitHubConflict, GitHubDisabled) as exc:
            raise HTTPException(409, detail=str(exc))

    entries = _rule_entries(text)
    return RulesEditResponse(
        applied=applied,
        entries=entries,
        categories=Ruleset.from_text(text).all_categories(),
        flagged_count=sum(1 for e in entries if e.flagged),
        committed=commit_url is not None,
        commit_url=commit_url,
    )


@app.get("/health")
def health(settings: Settings = Depends(get_settings)) -> dict:
    return {
        "status": "ok",
        "rules_path": str(settings.rules_path),
        "rules_present": settings.rules_path.exists(),
        "github": settings.github_enabled,
        "time": datetime.now(timezone.utc).isoformat(),
    }
