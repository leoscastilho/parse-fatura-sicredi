"""Modelos de transporte (Pydantic) e a ponte com os dataclasses do `core`.

COMO OS MODELOS ATRAVESSAM OS ENDPOINTS
---------------------------------------
`core.ClassifiedLine` (dataclass) é o modelo canônico. Ele nasce no parsing e
não muda de forma em lugar nenhum: `LineItem` abaixo é o espelho Pydantic dele,
campo a campo, só para validação e serialização na borda HTTP.

    .xls  --read_statement-->  Entry        (dataclass, cru)
          --classify_statement-->  ClassifiedLine  (dataclass, canônico)
          --LineItem.from_core-->  LineItem  (pydantic, JSON)
          --to_core-->  ClassifiedLine       (de volta, no /preview e /export)

O que NUNCA muda entre os passos é o `line_id` (`"<índice do extrato>:<linha>"`,
determinístico). O React devolve atribuições referenciando exatamente o id que
recebeu, então o backend não precisa guardar mapa de tradução nenhum — só
aplicar overrides sobre as linhas que já estão no SQLite.

As decisões do usuário vivem separadas das linhas, num `AssignmentSet`. Isso
mantém as linhas originais imutáveis durante toda a transação: dá para refazer
o /preview quantas vezes quiser, e um erro de atribuição nunca corrompe o que
foi lido do extrato.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from core import ClassifiedLine, LineState


# ---------------------------------------------------------------------------
# Linha
# ---------------------------------------------------------------------------

class LineItem(BaseModel):
    line_id: str
    statement: str
    data: str
    purchase_date: str
    merchant_raw: str
    merchant: str
    descricao: str
    valor: float
    pago: str
    categoria: str
    state: LineState
    matched: str | None = None
    categoria_anterior: str | None = None
    # A compra caiu dentro de um período de viagem. Sugestão para a etapa de
    # confirmação; não implica que a linha já esteja como `Viagem`.
    viagem: bool = False
    # "03/05" da coluna Parcela — o que separa uma compra deste ciclo da data
    # original de uma parcela antiga.
    parcela: str | None = None

    @classmethod
    def from_core(cls, line: ClassifiedLine) -> "LineItem":
        return cls(**line.to_dict())

    def to_core(self) -> ClassifiedLine:
        return ClassifiedLine.from_dict(self.model_dump())


class MerchantGroup(BaseModel):
    """Um estabelecimento, não um lançamento.

    É o que a tela de revisão consome: "Supermercados Alvora, 23x, R$ 2.140,
    Alimentação" em vez de 23 linhas idênticas para confirmar uma a uma.
    """

    merchant: str
    state: LineState
    categoria: str
    count: int
    total: float
    line_ids: list[str]
    samples: list[str] = Field(default_factory=list)
    statements: list[str] = Field(default_factory=list)
    matched: str | None = None


class StatementSummary(BaseModel):
    name: str
    due_date: str
    data_column: str
    entries: int
    dropped: int
    debits: float
    declared_debits: float
    credits: float
    declared_credits: float | None
    reconciles: bool


class DroppedItem(BaseModel):
    statement: str
    descricao: str
    valor: float


# ---------------------------------------------------------------------------
# Atribuições
# ---------------------------------------------------------------------------

AssignmentScope = Literal["merchant", "line"]


class Assignment(BaseModel):
    """Uma decisão do usuário.

    `scope="merchant"` vale para todas as linhas do estabelecimento (o caso
    normal). `scope="line"` vale para uma linha só — é o que os marketplaces
    exigem, porque a mesma Amazon é Casa numa compra e Hobby na seguinte.
    """

    scope: AssignmentScope
    target: str                 # merchant_key ou line_id
    categoria: str = ""
    persist_keyword: str | None = None   # grava no YAML; None = só nesta fatura
    mark_unknown: bool = False           # manda para `desconhecidos`
    mark_marketplace: bool = False       # manda para `marketplaces`

    @field_validator("categoria")
    @classmethod
    def _strip(cls, value: str) -> str:
        return (value or "").strip()


class AssignmentSet(BaseModel):
    assignments: list[Assignment] = Field(default_factory=list)
    # line_ids que caíram numa janela de viagem mas que o usuário DESMARCOU na
    # etapa de confirmação. Só a exceção viaja pela rede: o padrão é que tudo
    # dentro da janela seja viagem, então a lista costuma vir vazia.
    travel_rejected: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Requests / responses
# ---------------------------------------------------------------------------

class CategoriesResponse(BaseModel):
    categories: list[str]
    # Subconjunto de `categories` que descreve o MOVIMENTO do dinheiro (Renda
    # Fixa, Poupança, Investimento…). Vai junto, e não numa rota própria, porque
    # quem monta um seletor precisa das duas listas ao mesmo tempo — separadas,
    # a tela renderizaria uma vez com a lista cheia antes da segunda resposta
    # chegar, e as fixas apareceriam por um instante.
    fixed_categories: list[str] = Field(default_factory=list)
    keywords_by_category: dict[str, list[str]]
    ordered_rules: list[dict[str, str]]
    marketplaces: list[str]
    unknown: list[str]
    excluded: list[str]
    default_category: str
    source: str                 # "local" | "github"
    source_sha: str | None = None


class SourceFile(BaseModel):
    name: str
    rows: int
    total: float


class CategoryChangeItem(BaseModel):
    line_id: str
    descricao: str
    valor: float
    de: str
    para: str
    matched: str | None = None


class PurchaseRange(BaseModel):
    """Menor e maior data de COMPRA do lote.

    É o que limita os seletores de data da etapa de viagem: não faz sentido
    marcar uma viagem em março se as compras deste lote vão de maio a julho.
    """

    inicio: str
    fim: str


class PurchaseRangeResponse(BaseModel):
    """Resposta do pré-voo: só o intervalo, sem transação e sem estado.

    `None` quando nenhuma linha do lote tem data de compra legível — o que não é
    erro, e a tela trata como "não consegui limitar, siga solto".
    """

    purchase_range: PurchaseRange | None = None
    # Os nomes distintos na coluna de titular, quando o banco a tem. Um só (ou
    # nenhum) é cartão de uma pessoa: a tela não pergunta nada.
    titulares: list[str] = Field(default_factory=list)
    # Quem o extrato diz ser o dono da conta — a sugestão de "esse sou eu",
    # para o usuário confirmar em vez de procurar o próprio nome numa lista.
    eu_sugerido: str | None = None


class TravelRangeItem(BaseModel):
    inicio: str                 # AAAA-MM-DD
    fim: str                    # AAAA-MM-DD
    rotulo: str = ""


class TravelRequest(BaseModel):
    transaction_id: str
    ranges: list[TravelRangeItem] = Field(default_factory=list)


class TravelResponse(BaseModel):
    transaction_id: str
    ranges: list[TravelRangeItem]
    purchase_range: PurchaseRange | None = None
    # Avisos, não erros: um período fora do lote não impede nada, só não marca
    # nada. Quem decide se isso é engano é o usuário.
    warnings: list[str] = Field(default_factory=list)
    items: list[LineItem] = Field(default_factory=list)
    count: int = 0
    total: float = 0.0


class UploadResponse(BaseModel):
    # "fatura" = extrato do banco; "recategorizacao" = CSV que já saiu daqui.
    # O front usa isto para trocar os textos e pular a conferência de totais,
    # que não existe num CSV de saída.
    modo: Literal["fatura", "recategorizacao"] = "fatura"
    transaction_id: str
    expires_at: datetime
    statements: list[StatementSummary]
    dropped: list[DroppedItem]
    unmapped_items: list[MerchantGroup]
    auto_classified_items: list[MerchantGroup]
    marketplace_items: list[LineItem]
    ignored_items: list[MerchantGroup]
    warnings: list[str] = Field(default_factory=list)
    # Limites para o editor de períodos de viagem. `None` quando nenhuma linha
    # trouxe data de compra legível (exportação antiga sem `{Em 15/Jul}`).
    purchase_range: PurchaseRange | None = None
    # Só na recategorização:
    source_files: list[SourceFile] = Field(default_factory=list)
    changes: list[CategoryChangeItem] = Field(default_factory=list)
    unchanged: int = 0


class ValidationIssue(BaseModel):
    severity: Literal["error", "warning"]
    target: str
    message: str


class AssignmentImpact(BaseModel):
    target: str
    categoria: str
    lines_affected: int
    reclassified_away: list[dict[str, str]] = Field(default_factory=list)
    keyword_conflicts: list[dict[str, str]] = Field(default_factory=list)


class ValidateRequest(AssignmentSet):
    transaction_id: str


class ValidateResponse(BaseModel):
    ok: bool
    issues: list[ValidationIssue]
    impacts: list[AssignmentImpact]


class MappingChange(BaseModel):
    kind: Literal["keyword", "unknown", "marketplace", "category"]
    categoria: str | None = None
    value: str


class UpdateMappingRequest(AssignmentSet):
    transaction_id: str
    commit_now: bool = False


class UpdateMappingResponse(BaseModel):
    staged: list[MappingChange]
    total_staged: int
    yaml_valid: bool
    committed: bool = False
    commit_url: str | None = None


class PreviewRequest(AssignmentSet):
    transaction_id: str


class PreviewResponse(BaseModel):
    transaction_id: str
    rows: list[LineItem]
    total: float
    by_category: dict[str, float]
    remaining_blank: int
    filename: str


class ExportRequest(BaseModel):
    """`assignments` é opcional AQUI de propósito.

    Omitir o campo = "usa o que ficou guardado do /preview". Mandar `[]` = "sem
    atribuição nenhuma, exporta a classificação crua". Se o default fosse lista
    vazia como nos outros requests, os dois casos seriam indistinguíveis e um
    `[]` explícito silenciosamente traria de volta as decisões antigas.
    """

    transaction_id: str
    assignments: list[Assignment] | None = None
    # Mesma semântica de `assignments`: omitido = usa o que ficou do /preview,
    # `[]` = "confirmei todas as viagens, não rejeitei nenhuma".
    travel_rejected: list[str] | None = None
    commit_mapping: bool = True
    encoding: Literal["utf-8", "utf-8-sig"] = "utf-8"


# ---------------------------------------------------------------------------
# Revisão de regras (aba "Regras")
# ---------------------------------------------------------------------------

class RuleEntry(BaseModel):
    """Uma linha editável do categories.yml.

    `flagged` marca as entradas comentadas com `# ?` — os chutes que ainda
    esperam sua confirmação. Essa informação só existe no TEXTO do arquivo;
    o parser YAML descarta comentário.
    """

    block: Literal["palavras", "palavras_genericas", "marketplaces",
                   "desconhecidos", "excluir"]
    categoria: str | None = None
    value: str
    comment: str = ""
    flagged: bool = False
    line: int
    # A regra de casamento é "trecho mais longo vence".  Daí duas relações
    # diferentes entre palavras-chave em que uma contém a outra:
    #   redundant_with -> um trecho MAIS CURTO da MESMA categoria já cobre este;
    #                     apagar não muda nada e o arquivo fica menor.
    #   overrides      -> um trecho mais curto de OUTRA categoria; este vence
    #                     onde os dois casam.  É intencional, só informativo.
    redundant_with: list[str] = Field(default_factory=list)
    overrides: list[str] = Field(default_factory=list)


class RulesResponse(BaseModel):
    entries: list[RuleEntry]
    categories: list[str]
    ordered_rules: list[dict[str, str]]
    flagged_count: int
    source_sha: str | None = None


class RuleOp(BaseModel):
    """Uma operação sobre o YAML.

    `move` só faz sentido em `palavras` (trocar a categoria de um trecho);
    para os blocos planos use `add`/`remove`.
    """

    # `confirm` só limpa o comentário `# ?` — a palavra-chave e a categoria
    # continuam exatamente onde estão.
    op: Literal["add", "remove", "move", "confirm"]
    block: Literal["palavras", "palavras_genericas", "marketplaces",
                   "desconhecidos", "excluir"] = "palavras"
    categoria: str | None = None
    new_categoria: str | None = None
    value: str


class RulesEditRequest(BaseModel):
    operations: list[RuleOp]
    commit: bool = False


class RulesEditResponse(BaseModel):
    applied: int
    entries: list[RuleEntry]
    categories: list[str]
    flagged_count: int
    committed: bool = False
    commit_url: str | None = None
