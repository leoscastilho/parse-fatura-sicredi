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
from core.pipeline import build_description
from core.profiles import PAPEIS
from core.statement import Entry
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


class ColunaDoc(BaseModel):
    """Uma coluna do CSV, descrita para quem vai olhar a planilha.

    Acima de `ConfigResponse` porque referência adiante em Pydantic funciona
    por acidente — mesma razão de `BancoDetectado` em `api/models.py`.
    """

    nome: str          # o nome que ESTA configuração deu à coluna
    papel: str         # o papel canônico: data, categoria, descricao, valor, pago
    conteudo: str      # o que vai nela, em uma frase
    tipo: str          # data | texto | número | texto fixo
    exemplo: str


class MarcaDoc(BaseModel):
    """Uma das partes que compõem a Descrição, e o delimitador dela.

    `lido` é o campo que importa de verdade: diz o que o portal FAZ com a marca
    quando o CSV volta pela Recategorização. Vazio quer dizer que ela existe
    para você ler, e mais ninguém.
    """

    forma: str         # "{Em 15/Jul}" — como sai no arquivo
    delimitador: str   # "{ }"
    nome: str
    origem: str        # de onde o valor sai
    quando: str        # em que linhas aparece
    lido: str = ""     # o que o portal reencontra por ela na volta


class FormatoDoc(BaseModel):
    colunas: list[ColunaDoc] = Field(default_factory=list)
    marcas: list[MarcaDoc] = Field(default_factory=list)
    # O esqueleto da descrição, como está escrito no `output.yml`.
    modelo: str = ""
    exemplo_descricao: str = ""
    ordenacao: list[str] = Field(default_factory=list)
    categoria_vazia_no_fim: bool = True
    encoding: str = ""
    nome_um: str = ""
    nome_varios: str = ""
    caminho: str = ""


class ConfigResponse(BaseModel):
    banks: list[BankSummary]
    output_yaml: str
    output_exemplo: dict[str, str]
    output_doc: FormatoDoc | None = None
    source_sha: str | None = None


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


# A compra fictícia que serve de exemplo em toda a tela de Formato de saída.
# UMA só, e não uma por bloco: a linha da tabela de colunas e o exemplo anotado
# da descrição têm que ser a MESMA linha, senão quem lê compara duas coisas
# diferentes achando que são a mesma.
EXEMPLO_ENTRY = Entry(
    purchase_date=datetime(2026, 7, 15),
    description="SUPERMERCADOS ALVORA",
    installment="03/05",
    amount=270.51,
)
EXEMPLO_VENCIMENTO = datetime(2026, 8, 10)


_CONTEUDO = {
    "data": ("Uma data por FATURA, não por compra: {origem}. É por ela que a "
             "planilha agrega o mês. A data da compra vive na descrição.",
             "data"),
    "categoria": ("Uma das categorias do `categories.yml`, ou vazia quando "
                  "nenhuma regra opinou e você ainda não decidiu.", "texto"),
    "descricao": ("O lançamento como ele vai para a planilha — montado pelas "
                  "marcas da tabela abaixo.", "texto"),
    "valor": ("O valor do lançamento. Negativo em crédito e estorno; o "
              "pagamento da fatura anterior não vira linha.", "número"),
    "pago": ("Texto fixo. A fatura já foi paga quando você exporta.",
             "texto fixo"),
}

_ORIGEM_DA_DATA = {
    "vencimento": "o vencimento da fatura",
    "compra": "a data de cada compra",
}


def documentar(schema: OutputSchema, caminho: str = "") -> FormatoDoc:
    """Descreve o formato de saída EM VIGOR, derivando tudo do próprio schema.

    A tela "Formato de saída" mostra isto e nada mais: ela é painel, não editor.
    O formato deixou de ser editável pelo portal porque quem manda nele é o
    arquivo — e um botão de salvar aqui só criaria uma segunda verdade para
    divergir da que está no disco.

    Nada é retipado. O nome das colunas vem de `schema.colunas`, a forma de cada
    marca vem do MESMO template que a escreve (`parcela_modelo`, `sufixo_data`,
    `titular_modelo`, `banco_modelo`, `MARCA_CATEGORIA`, `MARCA_ROTULO`), e o
    exemplo é montado pelo `build_description` de verdade. Documentação que
    repete a regra em prosa é documentação que mente na primeira mudança.
    """
    from core.travel import MARCA_CATEGORIA, MARCA_ROTULO, TRAVEL_CATEGORY

    def delim(forma: str) -> str:
        """"{Em 15/Jul}" -> "{ }". O par de fora, que é o que identifica a marca."""
        pares = {"[": "]", "(": ")", "{": "}", "<": ">"}
        f = forma.strip()
        if f and f[0] in pares and f.endswith(pares[f[0]]):
            return f"{f[0]} {pares[f[0]]}"
        return "—"

    exemplo = build_description(EXEMPLO_ENTRY, schema)
    valores = {
        "data": EXEMPLO_VENCIMENTO.strftime(schema.data_formato),
        "categoria": "Alimentação",
        "descricao": exemplo,
        "valor": f"{EXEMPLO_ENTRY.amount:.2f}",
        "pago": schema.pago,
    }

    colunas = []
    for papel in PAPEIS:
        conteudo, tipo = _CONTEUDO[papel]
        if papel == "data":
            conteudo = conteudo.format(
                origem=_ORIGEM_DA_DATA.get(schema.data_origem, schema.data_origem))
            tipo = f"data · {schema.data_formato}"
        if papel == "pago":
            tipo = f'texto fixo · "{schema.pago}"'
        colunas.append(ColunaDoc(
            nome=schema.coluna(papel), papel=papel,
            conteudo=conteudo, tipo=tipo, exemplo=valores[papel]))

    # O que vem ANTES do estabelecimento no modelo — hoje "[Cartão{banco}]".
    # Renderizado das duas maneiras (sem banco e com) em vez de mostrado cru:
    # `{banco}` no meio da tabela seria o template, não a forma que sai no
    # arquivo, e é a forma que a pessoa procura na planilha.
    prefixo = (schema.modelo.split("{descricao}")[0]
               if "{descricao}" in schema.modelo else "")

    marcas = [
        MarcaDoc(
            forma=prefixo.format(banco="").strip(),
            delimitador=delim(prefixo.format(banco="").strip()),
            nome="Etiqueta fixa",
            origem="o `modelo` do output.yml — o texto que está escrito lá",
            quando="em toda linha",
            lido="nada: ela é pulada quando o portal procura o estabelecimento",
        ),
        MarcaDoc(
            forma=prefixo.format(banco=schema.banco_modelo.format(banco="BTG")).strip(),
            delimitador="entra dentro da etiqueta acima",
            nome="Banco",
            origem="o perfil que reconheceu o arquivo",
            quando="só quando o mesmo lote traz faturas de bancos DIFERENTES — "
                   "com um banco só a etiqueta seria igual em toda linha e não "
                   "separaria nada",
            lido="nada: some junto com a etiqueta",
        ),
        MarcaDoc(
            forma="Supermercados Alvora",
            delimitador="— (sem marca)",
            nome="Estabelecimento",
            origem="a descrição que o banco imprime, em Title Case e sem espaços dobrados",
            quando="em toda linha",
            lido="É A CHAVE DE TUDO: as regras, o agrupamento por estabelecimento "
                 "e as palavras-chave gravadas casam contra ele. É o que sobra "
                 "depois de tirar todas as outras marcas.",
        ),
        MarcaDoc(
            forma=schema.parcela_modelo.format(parcela="03/05").strip(),
            delimitador=delim(schema.parcela_modelo.strip()),
            nome="Parcela",
            origem="a coluna de parcela do banco — ou, no BTG, o número colado no nome",
            quando="só nas compras parceladas",
            lido="nada, mas o parêntese é pulado para não virar parte do nome",
        ),
        MarcaDoc(
            forma=MARCA_CATEGORIA.format("Alimentação"),
            delimitador=delim(MARCA_CATEGORIA.format("x")),
            nome="Categoria real",
            origem=f"a etapa de Viagem, quando a coluna Categoria vira {TRAVEL_CATEGORY}",
            quando=f"só nas linhas de {TRAVEL_CATEGORY}",
            lido="Devolve a categoria verdadeira ao reprocessar — é o que faz a "
                 "planilha continuar respondendo \"quanto gastei em comida naquela "
                 "viagem?\". Só sai da descrição quando o conteúdo é uma categoria "
                 "que existe, senão \"Padaria (Matriz)\" perderia o (Matriz).",
        ),
        MarcaDoc(
            forma=MARCA_ROTULO.format("Peru"),
            delimitador=delim(MARCA_ROTULO.format("x")),
            nome="Nome da viagem",
            origem="o nome que você deu ao período na etapa de Viagem",
            quando="só nas linhas de viagem que têm nome",
            lido="Reconhece a viagem ao reprocessar, em vez de escrever o nome "
                 "duas vezes.",
        ),
        MarcaDoc(
            forma=schema.sufixo_data.format(dia=15, mes="Jul", ano=2026).strip(),
            delimitador=delim(schema.sufixo_data.format(dia=1, mes="Jan", ano=2026).strip()),
            nome="Data da compra",
            origem="a data em que a compra aconteceu",
            quando="em toda linha",
            lido="Reconstrói a data da compra quando o CSV volta — a coluna Data "
                 "guarda o vencimento. Sem ela a etapa de Viagem e a Análise "
                 "ficam sem a data que usam. O mês é sempre em inglês, fixo: "
                 "`%b` mudaria com o idioma da máquina.",
        ),
        MarcaDoc(
            forma=schema.titular_modelo.format(titular="Rhyesla").strip(),
            delimitador=delim(schema.titular_modelo.strip()),
            nome="Titular",
            origem="a coluna de titular do banco (no BTG, o final do cartão)",
            quando="conta conjunta, e só nas compras que NÃO são suas",
            lido="Alimenta o filtro \"mostrar de quem\" e a quebra por pessoa na "
                 "Análise. Ancorado no fim: um `<` no meio de um nome de "
                 "estabelecimento não é marca de titular.",
        ),
    ]

    return FormatoDoc(
        colunas=colunas,
        marcas=marcas,
        modelo=schema.modelo,
        exemplo_descricao=exemplo,
        ordenacao=list(schema.ordenacao),
        categoria_vazia_no_fim=schema.categoria_vazia_no_fim,
        encoding=schema.encoding,
        nome_um=schema.nome_um,
        nome_varios=schema.nome_varios,
        caminho=caminho,
    )


def _caminho_do_output(settings: Settings) -> str:
    """Onde está o `output.yml` — dito como você abriria o arquivo.

    No arranjo normal a pasta se chama `config`, e o painel mostra
    `config/output.yml`: é o que dá para procurar no repositório. O caminho
    absoluto de DENTRO do contêiner (`/app/config/output.yml`) seria verdade e
    não ajudaria em nada — não existe na máquina de quem está lendo.

    Numa pasta com outro nome (`FATURA_CONFIG_DIR` apontando para outro lugar) o
    absoluto volta, porque aí ele é a única resposta útil.
    """
    raiz = config_root(settings)
    if raiz.name == "config":
        return "config/output.yml"
    return str(raiz / "output.yml")


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

    # Uma linha de exemplo, montada com o schema atual — assim dá para ver como
    # o formato em vigor escreve, sem processar uma fatura inteira.
    #
    # Pelo schema, não por nomes fixos: a linha tem que refletir os nomes de
    # coluna que estão no `output.yml` de agora.
    exemplo = schema.linha(
        data=EXEMPLO_VENCIMENTO.strftime(schema.data_formato),
        categoria="Alimentação",
        descricao=build_description(EXEMPLO_ENTRY, schema),
        valor=f"{EXEMPLO_ENTRY.amount:.2f}",
        pago=schema.pago,
    )

    return ConfigResponse(
        banks=[
            BankSummary(
                id=b.id, nome=b.nome, validado=b.validado,
                estrategia=b.estrategia, extensoes=list(b.extensoes),
                pede_vencimento=b.pede_vencimento, tema=b.tema.to_dict(),
            )
            for b in sorted(cfg.banks.values(), key=lambda b: (not b.validado, b.nome))
        ],
        output_yaml=schema.raw_text or "",
        output_exemplo=exemplo,
        output_doc=documentar(schema, caminho=_caminho_do_output(settings)),
        source_sha=GitHubSync(settings).current_sha() if settings.github_enabled else None,
    )


# NENHUM DOS DOIS FORMATOS se edita pelo portal.
#
# O de ENTRADA saiu primeiro, com a tela dele: o jeito de o banco exportar não é
# preferência de usuário, é fato do banco — e um fato que, quando muda, muda
# para todo mundo ao mesmo tempo. Deixar isso editável dava a cada instalação a
# chance de ter um leitor diferente, e o suporte ao formato novo do app do
# Sicredi teria virado "edite o seu YAML" em vez de simplesmente funcionar.
#
# O de SAÍDA saiu depois, por outro motivo: ele é definido no arquivo, à mão, e
# um `POST /config/output` criava uma SEGUNDA verdade sobre o mesmo `output.yml`.
# Bastava editar o arquivo com a tela aberta para o botão "Salvar" devolver o
# formato antigo por cima do novo, sem nada avisando. A tela virou painel — ela
# mostra o formato em vigor (ver `documentar`), e quem escreve é você.
#
# Os YAMLs continuam existindo, continuam sendo a configuração da aplicação e
# continuam viajando no pacote `/config/export` + `/config/import`. O que acabou
# foi editá-los daqui.


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
