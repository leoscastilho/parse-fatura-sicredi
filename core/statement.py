"""Leitura do extrato, dirigida pelo perfil do banco.

Aceita caminho OU buffer em memória, porque a API recebe `UploadFile` e não
tem (nem deveria ter) um arquivo no disco.

Cada estratégia é uma função `_ler_<estrategia>`; o `read_statement` só
despacha. Um banco novo com formato novo = uma função nova aqui + um
`estrategia:` no YAML do banco.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

import pandas as pd

from .profiles import BankProfile, ProfileError
from .text import normalize

DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")


@dataclass
class Entry:
    purchase_date: datetime
    description: str
    installment: str | None
    amount: float
    international: bool = False


@dataclass
class Statement:
    name: str
    due_date: datetime | None
    entries: list[Entry]
    declared_debits: float
    declared_credits: float | None
    declared_balance: float | None
    bank_id: str = "sicredi"

    @property
    def debits(self) -> float:
        return round(sum(e.amount for e in self.entries if e.amount > 0), 2)

    @property
    def credits(self) -> float:
        return round(-sum(e.amount for e in self.entries if e.amount < 0), 2)

    def reconciles(self) -> bool:
        """A soma lida bate com o que a própria fatura declara?

        Quando o extrato não declara totais (caso do CSV do Nubank), não há o
        que conferir — devolvemos True em vez de fingir um erro.
        """
        if not self.declared_debits and self.declared_credits is None:
            return True
        if abs(self.debits - self.declared_debits) >= 0.01:
            return False
        if self.declared_credits is not None:
            return abs(self.credits - self.declared_credits) < 0.01
        return True


# ---------------------------------------------------------------------------
# Números e datas, conforme o perfil
# ---------------------------------------------------------------------------

def make_amount_parser(numeros: dict[str, Any] | None):
    milhar = (numeros or {}).get("milhar", ".")
    decimal = (numeros or {}).get("decimal", ",")

    def parse(text: Any) -> float | None:
        raw = re.sub(r"[^\d,.\-]", "", str(text)).strip()
        if not raw:
            return None
        if milhar:
            raw = raw.replace(milhar, "")
        if decimal and decimal != ".":
            raw = raw.replace(decimal, ".")
        try:
            return float(raw)
        except ValueError:
            return None

    return parse


# Parser padrão pt-BR, usado pelo `parse_amount` público (compatibilidade).
parse_amount = make_amount_parser({"milhar": ".", "decimal": ","})


def _cell(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


# ---------------------------------------------------------------------------
# Estratégia: excel_secoes  (Sicredi)
# ---------------------------------------------------------------------------

def _ler_excel_secoes(source, name: str, profile: BankProfile,
                      due_date: datetime | None) -> Statement:
    cfg = profile.leitura
    amount_of = make_amount_parser(cfg.get("numeros"))
    header = cfg.get("cabecalho") or {}
    col_data = header.get("data", "Data")
    col_desc = header.get("descricao", "Descrição")
    fim = cfg.get("fim_secao") or "Valor Total"
    intl_marker = (cfg.get("internacional") or {}).get("marcador")
    fmt = cfg.get("data_lancamento", "%d/%m/%Y")

    grid = pd.read_excel(source, sheet_name=cfg.get("planilha", 0),
                         header=None, dtype=object)
    rows = [[_cell(v) for v in row] for row in grid.itertuples(index=False)]

    def summary_value(prefix: str | None) -> float | None:
        if not prefix:
            return None
        for row in rows:
            if row and normalize(row[0]).startswith(normalize(prefix)):
                for cell in reversed(row[1:]):
                    value = amount_of(cell)
                    if value is not None:
                        return value
        return None

    if due_date is None:
        venc = cfg.get("vencimento") or {}
        rotulo = venc.get("rotulo")
        venc_fmt = venc.get("formato", "%d/%m/%Y")
        if rotulo:
            for row in rows:
                if row and row[0].lower().startswith(rotulo.lower()):
                    for cell in row[1:]:
                        if DATE_RE.match(cell):
                            due_date = datetime.strptime(cell, venc_fmt)
                            break
                if due_date:
                    break

    entries: list[Entry] = []
    totals: list[float] = []

    for idx, row in enumerate(rows):
        if len(row) < 4 or row[0] != col_data or row[1] != col_desc:
            continue
        international = bool(intl_marker) and intl_marker in row[2]

        for data_row in rows[idx + 1:]:
            head = data_row[0]
            if head.lower().startswith(fim.lower()):
                value = amount_of(data_row[-1])
                if value is not None:
                    totals.append(value)
                break
            if not DATE_RE.match(head):
                if head:      # "Não existem lançamentos.", próxima seção…
                    break
                continue
            amount = amount_of(data_row[3])
            if amount is None:
                continue
            entries.append(Entry(
                purchase_date=datetime.strptime(head, fmt),
                description=data_row[1],
                installment=None if international or not data_row[2] else data_row[2],
                amount=amount,
                international=international,
            ))

    resumo = cfg.get("resumo") or {}
    return Statement(
        name=name,
        due_date=due_date,
        entries=entries,
        declared_debits=round(sum(totals), 2),
        declared_credits=summary_value(resumo.get("creditos")),
        declared_balance=summary_value(resumo.get("total")),
        bank_id=profile.id,
    )


# ---------------------------------------------------------------------------
# Estratégia: csv_simples  (Nubank — placeholder)
# ---------------------------------------------------------------------------

def _ler_csv_simples(source, name: str, profile: BankProfile,
                     due_date: datetime | None) -> Statement:
    cfg = profile.leitura
    amount_of = make_amount_parser(cfg.get("numeros"))
    colunas = cfg.get("colunas") or {}
    fmt = cfg.get("data_lancamento", "%Y-%m-%d")

    blob = source.read() if hasattr(source, "read") else Path(source).read_bytes()
    if isinstance(blob, bytes):
        blob = blob.decode(cfg.get("encoding", "utf-8"), errors="replace")

    frame = pd.read_csv(io.StringIO(blob), sep=cfg.get("delimitador", ","), dtype=str)
    frame.columns = [str(c).strip() for c in frame.columns]

    def column(role: str) -> str | None:
        wanted = colunas.get(role)
        if not wanted:
            return None
        for candidate in frame.columns:
            if normalize(candidate) == normalize(str(wanted)):
                return candidate
        raise ProfileError(
            f"{name}: coluna '{wanted}' não encontrada — o arquivo tem "
            f"{list(frame.columns)}"
        )

    col_data, col_desc, col_valor = column("data"), column("descricao"), column("valor")
    col_parcela = column("parcela")
    if not (col_data and col_desc and col_valor):
        raise ProfileError(f"{name}: perfil precisa mapear data, descricao e valor")

    entries: list[Entry] = []
    for _, row in frame.iterrows():
        amount = amount_of(row[col_valor])
        if amount is None:
            continue
        try:
            purchase = datetime.strptime(str(row[col_data]).strip(), fmt)
        except ValueError:
            continue
        entries.append(Entry(
            purchase_date=purchase,
            description=str(row[col_desc]).strip(),
            installment=(str(row[col_parcela]).strip() if col_parcela
                         and _cell(row[col_parcela]) else None),
            amount=amount,
        ))

    # O CSV não declara totais; a conferência fica a cargo de quem exportou.
    return Statement(
        name=name, due_date=due_date, entries=entries,
        declared_debits=0.0, declared_credits=None, declared_balance=None,
        bank_id=profile.id,
    )


STRATEGIES = {
    "excel_secoes": _ler_excel_secoes,
    "csv_simples": _ler_csv_simples,
}


def read_statement(
    source: Path | BinaryIO,
    name: str | None = None,
    profile: BankProfile | None = None,
    due_date: datetime | None = None,
) -> Statement:
    """Lê um extrato usando o perfil do banco.

    Sem perfil, assume o layout Sicredi — é o que mantém a CLI e os testes
    antigos funcionando sem mudança.
    """
    if name is None:
        name = Path(str(source)).name

    if profile is None:
        from .profiles import BankProfile as _BP
        profile = _BP(id="sicredi", nome="Sicredi", leitura={
            "estrategia": "excel_secoes",
            "vencimento": {"rotulo": "Data de Vencimento", "formato": "%d/%m/%Y"},
            "cabecalho": {"data": "Data", "descricao": "Descrição"},
            "internacional": {"marcador": "US$"},
            "fim_secao": "Valor Total",
            "resumo": {"creditos": "Pagamentos / Créditos", "total": "Valor Total(R$)"},
            "numeros": {"milhar": ".", "decimal": ","},
        })

    reader = STRATEGIES.get(profile.estrategia)
    if reader is None:
        raise ProfileError(
            f"estratégia de leitura desconhecida: {profile.estrategia!r} "
            f"(disponíveis: {', '.join(STRATEGIES)})"
        )
    return reader(source, name, profile, due_date)
