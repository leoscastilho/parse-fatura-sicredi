"""Extrato -> linhas classificadas -> CSV.

`ClassifiedLine` é o modelo que atravessa todo o sistema.  Na CLI ele vira
CSV direto; na API ele é serializado num `LineItem` Pydantic, guardado no
SQLite e devolvido ao React.  Um modelo só, um `line_id` estável, nenhuma
tradução de formato pelo caminho.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import BinaryIO, Iterable

from .profiles import BankProfile, OutputSchema
from .rules import LineState, Ruleset
from .statement import Entry, Statement, read_statement
from .text import MONTH_ABBR, merchant_key, sort_key_category

CSV_COLUMNS = ["Data", "Categoria", "Descrição", "Valor (R$)", "Pago"]


@dataclass
class ClassifiedLine:
    """Uma linha da fatura, já formatada no padrão da planilha.

    `line_id` é determinístico (índice do extrato + índice da linha), então o
    React devolve atribuições referenciando o mesmo id que recebeu, sem o
    backend precisar guardar um mapa de tradução.
    """

    line_id: str
    statement: str
    data: str                # coluna Data, MM/DD/YYYY (vencimento da fatura)
    purchase_date: str       # ISO, só para ordenar/exibir
    merchant_raw: str        # como veio do Sicredi
    merchant: str            # merchant_key: identidade sem nº de transação
    descricao: str           # "[Cartão] Renner (Parcela 03/05) {Em 10/May}"
    valor: float
    pago: str
    categoria: str
    state: LineState
    matched: str | None = None
    # Preenchido só na recategorização: a categoria que veio no arquivo de
    # entrada. Serve de fallback quando a regra não opina, e é o outro lado do
    # diff mostrado na revisão.
    categoria_anterior: str | None = None
    # A linha ORIGINAL do CSV de entrada, célula por célula. É o que permite
    # devolver o arquivo com tudo intacto — inclusive colunas que este portal
    # nem conhece e formatação de número que ele reescreveria.
    origem_row: dict | None = None
    # A COMPRA caiu dentro de um período de viagem. É candidatura, não
    # sentença: quem decide é a etapa de confirmação (ver core/travel.py).
    viagem: bool = False

    def as_csv_row(self, schema: OutputSchema | None = None) -> dict:
        """A linha no formato de saída — com os nomes de coluna DO schema.

        Sem o schema os nomes seriam fixos, e renomear `Descrição` para `Item`
        no formato de saída derrubava a exportação inteira: o writer recebia as
        chaves antigas e o `csv.DictWriter` recusava a linha.
        """
        schema = schema or OutputSchema()
        return schema.linha(data=self.data, categoria=self.categoria,
                            descricao=self.descricao, valor=self.valor,
                            pago=self.pago)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["state"] = self.state.value
        return payload

    @classmethod
    def from_dict(cls, payload: dict) -> "ClassifiedLine":
        payload = dict(payload)
        payload["state"] = LineState(payload["state"])
        return cls(**payload)


@dataclass
class DroppedLine:
    statement: str
    descricao: str
    valor: float


def build_description(entry: Entry, schema: OutputSchema | bool | None = None) -> str:
    """Monta a descrição conforme o modelo do `config/output.yml`.

    Aceita um bool no lugar do schema por compatibilidade com o código antigo,
    que passava só o flag `collapse_whitespace`.
    """
    if schema is None or isinstance(schema, bool):
        schema = OutputSchema(colapsar_espacos=True if schema is None else schema)

    merchant = entry.description.strip()
    if schema.colapsar_espacos:
        merchant = re.sub(r"\s+", " ", merchant)
    if schema.titlecase:
        merchant = merchant.title()

    parcela = (schema.parcela_modelo.format(parcela=entry.installment)
               if entry.installment else "")

    sufixo = schema.sufixo_data.format(
        dia=entry.purchase_date.day,
        mes=MONTH_ABBR[entry.purchase_date.month - 1],
        ano=entry.purchase_date.year,
    )

    return schema.modelo.format(
        descricao=merchant, parcela=parcela, sufixo_data=sufixo,
    )


def classify_statement(
    statement: Statement, rules: Ruleset, index: int = 0,
    schema: OutputSchema | None = None,
) -> tuple[list[ClassifiedLine], list[DroppedLine]]:
    schema = schema or OutputSchema()
    if statement.due_date is None:
        raise ValueError(
            f"{statement.name}: sem data de vencimento — o perfil deste banco "
            "não extrai a data do arquivo, então ela precisa ser informada no upload"
        )

    data_column = statement.due_date.strftime(schema.data_formato)
    lines: list[ClassifiedLine] = []
    dropped: list[DroppedLine] = []

    for row, entry in enumerate(statement.entries):
        if rules.is_excluded(entry.description):
            dropped.append(DroppedLine(statement.name, entry.description.strip(), entry.amount))
            continue

        match = rules.classify(entry.description)
        lines.append(
            ClassifiedLine(
                line_id=f"{index}:{row}",
                statement=statement.name,
                data=data_column,
                purchase_date=entry.purchase_date.date().isoformat(),
                merchant_raw=entry.description.strip(),
                merchant=merchant_key(entry.description),
                descricao=build_description(entry, schema),
                valor=entry.amount,
                pago=schema.pago,
                categoria=match.categoria,
                state=match.state,
                matched=match.matched,
            )
        )

    return lines, dropped


def classify_sources(
    sources: Iterable[tuple[str, Path | BinaryIO]], rules: Ruleset,
    profile: BankProfile | None = None, schema: OutputSchema | None = None,
    due_date=None,
) -> tuple[list[ClassifiedLine], list[DroppedLine], list[Statement]]:
    """Processa vários extratos de uma vez, mantendo `line_id` único."""
    schema = schema or OutputSchema()
    all_lines: list[ClassifiedLine] = []
    all_dropped: list[DroppedLine] = []
    statements: list[Statement] = []

    for index, (name, source) in enumerate(sources):
        statement = read_statement(source, name=name, profile=profile, due_date=due_date)
        lines, dropped = classify_statement(statement, rules, index=index, schema=schema)
        all_lines += lines
        all_dropped += dropped
        statements.append(statement)

    return all_lines, all_dropped, statements


def sort_lines(lines: list[ClassifiedLine],
               schema: OutputSchema | None = None) -> list[ClassifiedLine]:
    """Ordena conforme `ordenacao:` do config/output.yml.

    Padrão: fatura (Data) -> Categoria (A→Z, vazia por último) -> data da compra.
    """
    schema = schema or OutputSchema()

    def part(line: ClassifiedLine, chave: str):
        if chave == "data":
            try:
                return datetime.strptime(line.data, schema.data_formato).date()
            except (ValueError, TypeError):
                return date.min
        if chave == "categoria":
            return (sort_key_category(line.categoria) if schema.categoria_vazia_no_fim
                    else (0, sort_key_category(line.categoria)[1]))
        if chave == "data_compra":
            try:
                return date.fromisoformat(line.purchase_date)
            except (ValueError, TypeError):
                return date.min
        if chave == "valor":
            return -line.valor
        if chave == "descricao":
            return line.descricao
        return ""

    return sorted(lines, key=lambda l: tuple(part(l, c) for c in schema.ordenacao))


def lines_to_csv(lines: list[ClassifiedLine], encoding: str | None = None,
                 schema: OutputSchema | None = None) -> bytes:
    schema = schema or OutputSchema()
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=schema.colunas, lineterminator="\n")
    writer.writeheader()
    for line in sort_lines(lines, schema):
        writer.writerow(line.as_csv_row(schema))
    return buffer.getvalue().encode(encoding or schema.encoding)


def output_name(statements: list[Statement],
                schema: OutputSchema | None = None) -> str:
    """Nome derivado do período coberto, para não sobrescrever o mês passado."""
    schema = schema or OutputSchema()
    periods = sorted({s.due_date.strftime("%Y-%m") for s in statements if s.due_date})
    if not periods:
        return "fatura.csv"
    if len(periods) == 1:
        return schema.nome_um.format(periodo=periods[0], inicio=periods[0], fim=periods[0])
    return schema.nome_varios.format(
        periodo=periods[0], inicio=periods[0], fim=periods[-1])
