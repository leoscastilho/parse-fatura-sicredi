#!/usr/bin/env python3
"""
parse-fatura-sicredi
====================

Lê os extratos mensais do cartão Sicredi (.xls) e gera um CSV no formato da
planilha de finanças pessoais:

    Data,Categoria,Descrição,Valor (R$),Pago

Como a fatura é sempre extraída depois de fechada, ela contém as despesas do
mês ANTERIOR, pagas na fatura DESTE mês.  Por isso:

  * a coluna `Data` recebe a data de vencimento da fatura (uma data só para a
    fatura inteira, que é como a planilha agrega o mês); e
  * a data real da compra é preservada no fim da descrição, entre chaves:
    `[Cartão] Supermercados Alvora {Em 15/Mar}`.

Todos os extratos do input viram UM CSV só, pronto pra colar na planilha,
ordenado por fatura (Data), depois Categoria, depois data da compra.
Lançamentos sem categoria ficam no fim de cada fatura.

Uso:
    python main.py                       # input/ -> output/fatura_AAAA-MM.csv
    python main.py --split               # um CSV por extrato
    python main.py --no-interactive      # não abre o categorizador no fim
    python main.py --report-file review.txt
    python main.py --strict              # falha se sobrar algo sem categoria
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import yaml

# Abreviações de mês em inglês, fixas — `datetime.strftime('%b')` depende do
# locale da máquina e quebraria o formato histórico da planilha.
MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTH_INDEX = {abbr.upper(): i + 1 for i, abbr in enumerate(MONTH_ABBR)}

DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
OUTPUT_COLUMNS = ["Data", "Categoria", "Descrição", "Valor (R$)", "Pago"]

# Marcador devolvido por Ruleset.categorize() para "casou, mas é decisão sua".
MANUAL = "\x00manual"

# "[Cartão] Renner (Parcela 03/05) {Em 10/May}"
DESC_RE = re.compile(
    r"^\s*(?:\[[^\]]+\]\s*)?"          # prefixo [Cartão]
    r"(?P<merchant>.*?)"
    r"(?:\s*\(Parcela\s*[^)]*\))?"     # (Parcela 03/05)
    r"(?:\s*\{Em\s*(?P<day>\d{1,2})/(?P<month>[A-Za-z]{3})\})?"
    r"\s*$"
)


# ---------------------------------------------------------------------------
# Normalização
# ---------------------------------------------------------------------------

def normalize(text: str) -> str:
    """MAIÚSCULA, sem acentos, pontuação -> espaço, espaços colapsados.

    Também separa CamelCase, porque o Sicredi mistura os dois estilos no
    mesmo extrato ("OggiSantaRita", "GrelhaGrill", "ArmazemDoValeRest").
    """
    text = unicodedata.normalize("NFKD", str(text))
    text = text.encode("ascii", "ignore").decode()
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    text = re.sub(r"[^A-Za-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip().upper()


def compact(text: str) -> str:
    """Versão sem espaços, para casar 'GRELHA GRILL' com 'GRELHAGRILL'."""
    return text.replace(" ", "")


def merchant_of(descricao: str) -> str:
    """Extrai o nome do estabelecimento de uma descrição já formatada.

    "[Cartão] Renner (Parcela 03/05) {Em 10/May}"  ->  "Renner"
    Permite que o `categorize.py` rode sozinho, direto sobre os CSVs.
    """
    match = DESC_RE.match(descricao)
    return (match.group("merchant") if match else descricao).strip()


def purchase_date_of(data_vencimento: str, descricao: str) -> date | None:
    """Reconstrói a data da compra a partir do CSV.

    A descrição guarda só dia/mês (`{Em 21/Aug}`); o ano é o mais recente que
    não passa do vencimento da fatura — o que resolve parcelas antigas
    (uma compra de Ago/2024 numa fatura de Abr/2025).
    """
    match = DESC_RE.match(descricao)
    if not match or not match.group("day"):
        return None
    month = MONTH_INDEX.get(match.group("month").upper())
    if not month:
        return None
    day = int(match.group("day"))

    try:
        due = datetime.strptime(data_vencimento, "%m/%d/%Y").date()
    except (ValueError, TypeError):
        return None

    for year in range(due.year, due.year - 6, -1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate <= due:
            return candidate
    return None


def sort_key_category(categoria: str) -> tuple[int, str]:
    """Ordena alfabeticamente ignorando acentos; vazio vai para o fim."""
    categoria = (categoria or "").strip()
    if not categoria:
        return (1, "")
    return (0, normalize(categoria))


def due_date_of(data: str) -> date:
    """Coluna `Data` (MM/DD/YYYY) como data de verdade.

    Ordenar a string quebraria na virada do ano ("12/10/2025" viria depois de
    "01/10/2026").
    """
    try:
        return datetime.strptime(str(data), "%m/%d/%Y").date()
    except (ValueError, TypeError):
        return date.min


def sort_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Fatura (Data) → Categoria (A→Z, vazia por último) → data da compra.

    Com vários extratos num CSV só, cada fatura fica num bloco contíguo.
    """
    if frame.empty:
        return frame
    keys = frame.apply(
        lambda row: (
            due_date_of(row["Data"]),
            sort_key_category(row["Categoria"]),
            purchase_date_of(row["Data"], row["Descrição"]) or date.min,
        ),
        axis=1,
    )
    order = sorted(range(len(frame)), key=lambda i: keys.iloc[i])
    return frame.iloc[order].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Regras
# ---------------------------------------------------------------------------

@dataclass
class Ruleset:
    path: Path | None = None
    default_category: str = ""
    collapse_whitespace: bool = True
    categories: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    ordered_rules: list[tuple[re.Pattern, str]] = field(default_factory=list)
    keywords: list[tuple[str, str]] = field(default_factory=list)   # (trecho, categoria)
    always_review: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)     # não sei o que é
    manual: list[str] = field(default_factory=list)      # marketplace: varia a cada compra

    @classmethod
    def load(cls, path: Path) -> "Ruleset":
        with path.open(encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}

        cfg = raw.get("configuracao") or {}
        rs = cls(
            path=path,
            default_category=(cfg.get("categoria_padrao") or ""),
            collapse_whitespace=bool(cfg.get("colapsar_espacos", True)),
            categories=list(cfg.get("categorias") or []),
            exclude=[normalize(x) for x in (raw.get("excluir") or [])],
            always_review=[normalize(x) for x in (raw.get("sempre_revisar") or [])],
            unknown=[normalize(x) for x in (raw.get("desconhecidos") or [])],
            manual=[normalize(x) for x in (raw.get("marketplaces") or [])],
        )

        for rule in raw.get("regras") or []:
            rs.ordered_rules.append(
                (re.compile(rule["padrao"], re.IGNORECASE), rule["categoria"])
            )

        # `palavras` e `palavras_genericas` são tratados igual; a separação no
        # YAML existe só para facilitar a leitura.
        for block in ("palavras", "palavras_genericas"):
            for categoria, trechos in (raw.get(block) or {}).items():
                for trecho in trechos or []:
                    rs.keywords.append((normalize(trecho), categoria))

        # Trecho mais longo (mais específico) vence.
        rs.keywords.sort(key=lambda kv: len(kv[0]), reverse=True)
        return rs

    @staticmethod
    def _hit(term: str, norm_desc: str) -> bool:
        if not term:
            return False
        return term in norm_desc or compact(term) in compact(norm_desc)

    def is_excluded(self, norm_desc: str) -> bool:
        return any(self._hit(t, norm_desc) for t in self.exclude)

    def is_known_unknown(self, norm_desc: str) -> bool:
        """Estabelecimento que você já disse que não sabe classificar.

        Compara sem espaços porque o nome cru do Sicredi ("FotoSantaRita")
        e o já formatado no CSV ("Fotosantarita") normalizam diferente.
        """
        target = compact(norm_desc)
        return any(compact(t) == target for t in self.unknown)

    def is_manual(self, norm_desc: str) -> bool:
        """Marketplace: a categoria muda a cada compra, então fica em branco."""
        return any(self._hit(t, norm_desc) for t in self.manual)

    def needs_review(self, norm_desc: str) -> bool:
        return any(self._hit(t, norm_desc) for t in self.always_review)

    def categorize(self, norm_desc: str) -> tuple[str, str | None]:
        """Retorna (categoria, regra_que_casou).

        `regra_que_casou is None` significa "nada casou" — é o que faz o
        `categorize.py` perguntar.  `MANUAL` significa "casou com um
        marketplace": sai em branco, mas de propósito, sem perguntar.
        """
        for pattern, categoria in self.ordered_rules:
            if pattern.search(norm_desc):
                return categoria, pattern.pattern
        if self.is_manual(norm_desc):
            return "", MANUAL
        for trecho, categoria in self.keywords:
            if self._hit(trecho, norm_desc):
                return categoria, trecho
        return self.default_category, None


# ---------------------------------------------------------------------------
# Leitura do extrato
# ---------------------------------------------------------------------------

@dataclass
class Entry:
    purchase_date: datetime
    description: str
    installment: str | None
    amount: float
    international: bool


@dataclass
class Statement:
    path: Path
    due_date: datetime | None
    entries: list[Entry]
    declared_debits: float          # soma dos "Valor Total R$:" de cada seção
    declared_credits: float | None  # "Pagamentos / Créditos (R$)" do resumo
    declared_balance: float | None  # "Valor Total(R$)" da fatura


def _cell(value) -> str:
    return "" if value is None or (isinstance(value, float) and pd.isna(value)) else str(value).strip()


def parse_amount(text: str) -> float | None:
    """'-8.254,35' -> -8254.35"""
    text = re.sub(r"[^\d,.\-]", "", str(text)).strip()
    if not text:
        return None
    text = text.replace(".", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def read_statement(path: Path) -> Statement:
    grid = pd.read_excel(path, sheet_name=0, header=None, dtype=object)
    rows = [[_cell(v) for v in row] for row in grid.itertuples(index=False)]

    def summary_value(prefix: str) -> float | None:
        for row in rows:
            if row and normalize(row[0]).startswith(normalize(prefix)):
                for cell in reversed(row[1:]):
                    value = parse_amount(cell)
                    if value is not None:
                        return value
        return None

    due_date = None
    for row in rows:
        if row and row[0].lower().startswith("data de vencimento"):
            for cell in row[1:]:
                if DATE_RE.match(cell):
                    due_date = datetime.strptime(cell, "%d/%m/%Y")
                    break
            if due_date:
                break

    entries: list[Entry] = []
    totals: list[float] = []

    for idx, row in enumerate(rows):
        # Cabeçalho de uma seção de lançamentos.
        if len(row) < 4 or row[0] != "Data" or row[1] != "Descrição":
            continue
        international = "US$" in row[2]

        for data_row in rows[idx + 1:]:
            head = data_row[0]
            if head.lower().startswith("valor total"):
                value = parse_amount(data_row[-1])
                if value is not None:
                    totals.append(value)
                break
            if not DATE_RE.match(head):
                if head:            # "Não existem lançamentos.", próxima seção…
                    break
                continue
            amount = parse_amount(data_row[3])
            if amount is None:
                continue
            entries.append(
                Entry(
                    purchase_date=datetime.strptime(head, "%d/%m/%Y"),
                    description=data_row[1],
                    installment=None if international or not data_row[2] else data_row[2],
                    amount=amount,
                    international=international,
                )
            )

    return Statement(
        path=path,
        due_date=due_date,
        entries=entries,
        declared_debits=round(sum(totals), 2),
        declared_credits=summary_value("Pagamentos / Créditos"),
        declared_balance=summary_value("Valor Total(R$)"),
    )


# ---------------------------------------------------------------------------
# Formatação da saída
# ---------------------------------------------------------------------------

def build_description(entry: Entry, collapse_whitespace: bool) -> str:
    merchant = entry.description.strip()
    if collapse_whitespace:
        merchant = re.sub(r"\s+", " ", merchant)
    merchant = merchant.title()

    if entry.installment:
        merchant = f"{merchant} (Parcela {entry.installment})"

    day = entry.purchase_date.day
    month = MONTH_ABBR[entry.purchase_date.month - 1]
    return f"[Cartão] {merchant} {{Em {day}/{month}}}"


def process_statement(path: Path, rules: Ruleset):
    statement = read_statement(path)
    if statement.due_date is None:
        raise ValueError(f"'Data de Vencimento' não encontrada em {path.name}")

    data_column = statement.due_date.strftime("%m/%d/%Y")

    records, dropped, review = [], [], []
    for entry in statement.entries:
        norm = normalize(entry.description)
        if rules.is_excluded(norm):
            dropped.append((entry.description, entry.amount))
            continue

        categoria, matched = rules.categorize(norm)
        records.append(
            {
                "Data": data_column,
                "Categoria": categoria,
                "Descrição": build_description(entry, rules.collapse_whitespace),
                "Valor (R$)": entry.amount,
                "Pago": "x",
            }
        )
        if matched is MANUAL:
            review.append((entry.description, categoria, entry.amount, "MARKETPLACE"))
        elif matched is None and not rules.is_known_unknown(norm):
            review.append((entry.description, categoria, entry.amount, "SEM CATEGORIA"))
        elif rules.needs_review(norm):
            review.append((entry.description, categoria, entry.amount, "genérico"))

    frame = pd.DataFrame(records, columns=OUTPUT_COLUMNS)
    return statement, frame, dropped, review


def output_name(statements: list[Statement]) -> str:
    """Nome derivado do período coberto, para não sobrescrever o mês passado."""
    periods = sorted({s.due_date.strftime("%Y-%m") for s in statements if s.due_date})
    if len(periods) == 1:
        return f"fatura_{periods[0]}.csv"
    return f"faturas_{periods[0]}_a_{periods[-1]}.csv"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", default="input", type=Path)
    parser.add_argument("--output", default="output", type=Path)
    parser.add_argument("--rules", default="categories.yml", type=Path)
    parser.add_argument("--output-file",
                        help="nome do CSV combinado (padrão: derivado do período)")
    parser.add_argument("--split", action="store_true",
                        help="um CSV por extrato, em vez de um arquivo só")
    parser.add_argument("--report-file", type=Path,
                        help="grava o relatório de revisão num arquivo além da tela")
    parser.add_argument("--encoding", default="utf-8",
                        help="utf-8 (padrão) ou utf-8-sig, que abre acentos certinho no Excel")
    parser.add_argument("--strict", action="store_true",
                        help="sai com código 1 se algum lançamento ficar sem categoria")
    parser.add_argument("--no-interactive", action="store_true",
                        help="não abre o categorizador ao encontrar novos estabelecimentos")
    args = parser.parse_args()

    rules = Ruleset.load(args.rules)
    args.output.mkdir(parents=True, exist_ok=True)

    files = sorted(p for p in args.input.iterdir()
                   if p.suffix.lower() in {".xls", ".xlsx"} and not p.name.startswith("~$"))
    if not files:
        print(f"Nenhum .xls/.xlsx em {args.input}/", file=sys.stderr)
        return 1

    report_lines: list[str] = []
    uncategorized = 0
    statements: list[Statement] = []
    frames: list[pd.DataFrame] = []

    for path in files:
        statement, frame, dropped, review = process_statement(path, rules)
        statements.append(statement)
        frames.append(frame)

        per_file = args.output / f"{path.stem}.csv"
        if args.split:
            destination = per_file
            sort_frame(frame).to_csv(destination, index=False, encoding=args.encoding)
        else:
            destination = None
            # Não deixar para trás o CSV de uma execução com --split: seria
            # fácil colar o arquivo velho na planilha sem perceber.
            per_file.unlink(missing_ok=True)

        amounts = [e.amount for e in statement.entries]
        debits = round(sum(v for v in amounts if v > 0), 2)
        credits = round(-sum(v for v in amounts if v < 0), 2)

        def check(read: float, declared: float | None) -> str:
            if declared is None:
                return f"R$ {read:>12,.2f}   (fatura não informa)"
            mark = "OK" if abs(read - declared) < 0.01 else f"DIVERGE {read - declared:+,.2f}"
            return f"R$ {read:>12,.2f}  vs. fatura R$ {declared:>12,.2f}   [{mark}]"

        report_lines += [
            "",
            "=" * 78,
            path.name + (f"  ->  {destination.name}" if destination else ""),
            f"  Vencimento .......... {statement.due_date:%d/%m/%Y}   (coluna Data = {statement.due_date:%m/%d/%Y})",
            f"  Lançamentos ......... {len(frame)} exportados, {len(dropped)} descartados",
            f"  Débitos ............. {check(debits, statement.declared_debits)}",
            f"  Créditos ............ {check(credits, statement.declared_credits)}",
        ]
        if dropped:
            report_lines.append("  Descartados (pagamento da fatura anterior):")
            report_lines += [f"    - {d.strip()}  R$ {v:,.2f}" for d, v in dropped]

        uncategorized += sum(1 for r in review if r[3] == "SEM CATEGORIA")
        if review:
            report_lines.append(f"  Revisar ({len(review)}):")
            for desc, categoria, valor, flag in sorted(review, key=lambda r: -r[2]):
                report_lines.append(
                    f"    [{flag:13s}] {desc.strip():24s} R$ {valor:>10,.2f}"
                    + (f"  -> {categoria}" if categoria else "")
                )

    combined_path = args.output / (args.output_file or output_name(statements))
    if args.split:
        combined_path.unlink(missing_ok=True)
    else:
        combined = sort_frame(pd.concat(frames, ignore_index=True))
        destination = combined_path
        combined.to_csv(destination, index=False, encoding=args.encoding)
        report_lines += [
            "",
            "=" * 78,
            f"{len(combined)} lançamento(s) de {len(files)} fatura(s)  ->  {destination}",
        ]

    report = "\n".join(report_lines).lstrip("\n")
    print(report)
    if args.report_file:
        args.report_file.write_text(report + "\n", encoding="utf-8")

    if uncategorized and not args.no_interactive and sys.stdin.isatty():
        print(f"\n{uncategorized} lançamento(s) sem categoria.")
        from categorize import interactive_session
        interactive_session(args.rules, args.output, args.encoding)
    elif args.strict and uncategorized:
        print(f"\n{uncategorized} lançamento(s) sem categoria.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
