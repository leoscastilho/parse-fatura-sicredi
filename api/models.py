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
from core.text import titular_de


# ---------------------------------------------------------------------------
# Linha
# ---------------------------------------------------------------------------

# Aqui em cima, antes de `LineItem`, porque `LineItem.viagem_periodo` aponta
# para ele: com a definição lá embaixo o Pydantic teria de resolver uma
# referência adiante, que funciona por acidente e para de funcionar no dia em
# que alguém importar o módulo de um jeito diferente.
class TravelRangeItem(BaseModel):
    # Vazias nas duas = viagem SEM DATAS AINDA (a passagem comprada para uma
    # viagem futura). Nesse caso `rotulo` é obrigatório: é o que identifica a
    # viagem e o que vai para a descrição.
    inicio: str = ""            # AAAA-MM-DD
    fim: str = ""               # AAAA-MM-DD
    rotulo: str = ""
    # A identidade do período, calculada pelo backend. O front NÃO a recalcula:
    # ela é a janela para um período normal e o nome normalizado para uma
    # viagem sem datas, e ter as duas regras escritas de novo em JavaScript
    # faria a tela pendurar a linha numa viagem e o arquivo em outra.
    chave: str = ""


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
    # QUAL viagem pegou esta linha, resolvido pelo backend.
    #
    # Vem daqui e não do front de propósito: a resposta depende de duas regras
    # (a fixação à mão vence a data; períodos sobrepostos, vence o primeiro) e
    # ter uma segunda implementação delas em JavaScript significaria uma tela
    # dizendo "Peru" e o arquivo saindo "Ferroão", sem ninguém saber qual está
    # certo. `null` = nenhum período pegou.
    viagem_periodo: TravelRangeItem | None = None
    # A linha foi pendurada nesta viagem À MÃO, apesar da data. O front usa
    # para marcar a tarja e para oferecer o "tirar da viagem".
    viagem_a_mao: bool = False
    # Quem passou o cartão, extraído do ` <Rhyesla>` no fim da descrição.
    # Vazio = sem marca, que é como saem as compras de quem se identificou como
    # "eu" no upload. Vem do backend pelo mesmo motivo de `viagem_periodo`: a
    # regra é uma só e mora num lugar só (`core.text.titular_de`).
    titular: str = ""

    @classmethod
    def from_core(cls, line: ClassifiedLine, periodo=None,
                  a_mao: bool = False) -> "LineItem":
        return cls(**line.to_dict(),
                   viagem_periodo=(TravelRangeItem(**periodo.to_dict())
                                   if periodo is not None else None),
                   viagem_a_mao=a_mao,
                   titular=titular_de(line.descricao))

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
    # Quem passou o cartão nas linhas deste grupo, sem repetir, com `""` para
    # as linhas sem marca. Um grupo pode ter mais de um: o mesmo mercado
    # aparece nos dois cartões da conta conjunta. Por isso é lista — filtrar
    # por pessoa MOSTRA o grupo se ela estiver nele, e não o esconde por causa
    # das outras.
    titulares: list[str] = Field(default_factory=list)
    matched: str | None = None


class StatementSummary(BaseModel):
    name: str
    # O banco DETECTADO a partir do arquivo. Vem no resumo porque é a única
    # confirmação que o usuário tem de que o portal entendeu o que ele soltou
    # na tela — antes ele mesmo escolhia numa dropdown e sabia.
    banco: str = ""
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
    # "categoria" (a coluna muda) ou "marca" (a coluna continua `Viagem` e o
    # que mudou foi a categoria real, entre parênteses dentro da descrição).
    kind: str = "categoria"


class PurchaseRange(BaseModel):
    """Menor e maior data de COMPRA do lote.

    É o que limita os seletores de data da etapa de viagem: não faz sentido
    marcar uma viagem em março se as compras deste lote vão de maio a julho.
    """

    inicio: str
    fim: str


class BancoDetectado(BaseModel):
    """Um banco reconhecido no lote, com o que a tela de upload precisa dele.

    Acima de `PurchaseRangeResponse` pelo mesmo motivo de `TravelRangeItem`
    estar acima de `LineItem`: referência adiante em Pydantic funciona por
    acidente.
    """

    id: str
    nome: str
    # O arquivo não traz a data de vencimento e o portal precisa perguntar. É o
    # caso do Nubank — e é por isso que o campo de data só aparece DEPOIS de o
    # arquivo ser escolhido: antes disso ninguém sabe se ele faz falta.
    pede_vencimento: bool = False
    validado: bool = True
    tema: dict[str, str] = Field(default_factory=dict)


class ArquivoProtegido(BaseModel):
    """Um arquivo cifrado que ainda não abriu — o BTG manda a fatura assim.

    NÃO carrega a senha em direção nenhuma: ela sobe no formulário, decifra em
    memória e morre ali. O que volta é o nome do arquivo e o motivo de não ter
    aberto, que é o suficiente para a tela pedir de novo.

    `senha_incorreta` separa os dois estados que a tela precisa dizer com
    palavras diferentes: "digite a senha deste arquivo" e "a senha não confere".
    Sem a distinção, quem errou a senha veria o mesmo texto de quem ainda não
    digitou nada e não saberia se o portal chegou a receber o que ele escreveu.

    Acima de `PurchaseRangeResponse` pelo mesmo motivo de `BancoDetectado`:
    referência adiante em Pydantic funciona por acidente.
    """

    nome: str
    senha_incorreta: bool = False


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
    # Os bancos DETECTADOS neste lote, sem repetir. A tela usa para três coisas
    # que antes dependiam da dropdown: dizer o que reconheceu, pintar o tema, e
    # decidir se pergunta a data de vencimento.
    bancos: list[BancoDetectado] = Field(default_factory=list)
    # Os arquivos que estão cifrados e ainda não abriram. Lista vazia é o caso
    # normal; com algo dentro, a tela pede a senha e SEGURA o "Processar" — sem
    # isso o upload iria adiante lendo só metade do lote.
    protegidos: list[ArquivoProtegido] = Field(default_factory=list)


class TravelRequest(BaseModel):
    transaction_id: str
    ranges: list[TravelRangeItem] = Field(default_factory=list)
    # `line_id -> chave do período` (`AAAA-MM-DD|AAAA-MM-DD`). Substitutivo,
    # como `ranges`: o mapa enviado VIRA o mapa guardado, então despendurar uma
    # linha é mandar o mapa sem ela.
    pinned: dict[str, str] = Field(default_factory=dict)


class TravelResponse(BaseModel):
    transaction_id: str
    ranges: list[TravelRangeItem]
    purchase_range: PurchaseRange | None = None
    # Avisos, não erros: um período fora do lote não impede nada, só não marca
    # nada. Quem decide se isso é engano é o usuário.
    warnings: list[str] = Field(default_factory=list)
    items: list[LineItem] = Field(default_factory=list)
    # Todo o resto do lote, para a gaveta "comprou algo antes da viagem?".
    # Ordenado pelo valor absoluto: passagem e hospedagem são caras e aparecem
    # antes de o usuário digitar qualquer coisa.
    outros: list[LineItem] = Field(default_factory=list)
    pinned: dict[str, str] = Field(default_factory=dict)
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
    # Linhas que já estavam em `Viagem` e cuja categoria real (a de dentro dos
    # parênteses) as regras de hoje respondem diferente. Fora de `changes` de
    # propósito: a coluna Categoria dessas linhas não muda.
    travel_marks: list[CategoryChangeItem] = Field(default_factory=list)
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


class MonthTotal(BaseModel):
    """Um mês de vencimento e o que ele soma no lote.

    Acima de `PreviewResponse` pelo mesmo motivo de `TravelRangeItem` estar
    acima de `LineItem`: referência adiante em Pydantic funciona por acidente e
    para de funcionar no dia em que alguém importar o módulo de outro jeito.
    """

    rotulo: str                 # "Dez/2025"
    total: float
    lancamentos: int


class PreviewResponse(BaseModel):
    transaction_id: str
    rows: list[LineItem]
    total: float
    by_category: dict[str, float]
    remaining_blank: int
    filename: str
    # Total por mês de VENCIMENTO, na ordem do calendário. Só faz diferença
    # quando o lote tem mais de uma fatura — é a conferência contra as linhas
    # de "Cartão de crédito" da planilha, uma por mês.
    by_month: list[MonthTotal] = Field(default_factory=list)



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
