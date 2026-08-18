#!/usr/bin/env python3
"""
parse-fatura-sicredi — CLI

Mesma engine da API: tudo que classifica mora em `core/`. Este arquivo só cuida
de argumentos, arquivos e do relatório na tela, então CLI e site nunca podem
divergir na classificação.

    python main.py                       # input/ -> output/fatura_AAAA-MM.csv
    python main.py --split               # um CSV por extrato
    python main.py --no-interactive      # não abre o categorizador no fim
    python main.py --strict              # sai com erro se ficar algo sem categoria
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core import (
    ClassifiedLine,
    LineState,
    Ruleset,
    classify_sources,
    classify_statement,
    lines_to_csv,
    output_name,
    read_statement,
)


def report_for(statement, lines: list[ClassifiedLine], dropped, destination) -> tuple[list[str], int]:
    def check(read: float, declared: float | None) -> str:
        if declared is None:
            return f"R$ {read:>12,.2f}   (fatura não informa)"
        mark = "OK" if abs(read - declared) < 0.01 else f"DIVERGE {read - declared:+,.2f}"
        return f"R$ {read:>12,.2f}  vs. fatura R$ {declared:>12,.2f}   [{mark}]"

    out = [
        "",
        "=" * 78,
        statement.name + (f"  ->  {destination.name}" if destination else ""),
        f"  Vencimento .......... {statement.due_date:%d/%m/%Y}   "
        f"(coluna Data = {statement.due_date:%m/%d/%Y})",
        f"  Lançamentos ......... {len(lines)} exportados, {len(dropped)} descartados",
        f"  Débitos ............. {check(statement.debits, statement.declared_debits)}",
        f"  Créditos ............ {check(statement.credits, statement.declared_credits)}",
    ]
    if dropped:
        out.append("  Descartados (pagamento da fatura anterior):")
        out += [f"    - {d.descricao}  R$ {d.valor:,.2f}" for d in dropped]

    flags = {LineState.UNMAPPED: "SEM CATEGORIA", LineState.MARKETPLACE: "MARKETPLACE"}
    review = [(l, flags[l.state]) for l in lines if l.state in flags]
    if review:
        out.append(f"  Revisar ({len(review)}):")
        for line, flag in sorted(review, key=lambda r: -r[0].valor):
            out.append(
                f"    [{flag:13s}] {line.merchant_raw:24s} R$ {line.valor:>10,.2f}"
                + (f"  -> {line.categoria}" if line.categoria else "")
            )

    return out, sum(1 for l in lines if l.state is LineState.UNMAPPED)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", default="input", type=Path)
    parser.add_argument("--output", default="output", type=Path)
    parser.add_argument("--rules", default="config/categories.yml", type=Path)
    parser.add_argument("--output-file")
    parser.add_argument("--split", action="store_true")
    parser.add_argument("--report-file", type=Path)
    parser.add_argument("--encoding", default="utf-8")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--no-interactive", action="store_true")
    args = parser.parse_args()

    rules = Ruleset.load(args.rules)
    args.output.mkdir(parents=True, exist_ok=True)

    paths = sorted(p for p in args.input.iterdir()
                   if p.suffix.lower() in {".xls", ".xlsx"} and not p.name.startswith("~$"))
    if not paths:
        print(f"Nenhum .xls/.xlsx em {args.input}/", file=sys.stderr)
        return 1

    report: list[str] = []
    uncategorized = 0
    all_lines: list[ClassifiedLine] = []
    statements = []

    for index, path in enumerate(paths):
        statement = read_statement(path, name=path.name)
        lines, dropped = classify_statement(statement, rules, index=index)
        all_lines += lines
        statements.append(statement)

        per_file = args.output / f"{path.stem}.csv"
        if args.split:
            per_file.write_bytes(lines_to_csv(lines, args.encoding))
            destination = per_file
        else:
            destination = None
            # Não deixar para trás o CSV de uma execução com --split.
            per_file.unlink(missing_ok=True)

        chunk, pending = report_for(statement, lines, dropped, destination)
        report += chunk
        uncategorized += pending

    combined = args.output / (args.output_file or output_name(statements))
    if args.split:
        combined.unlink(missing_ok=True)
    else:
        combined.write_bytes(lines_to_csv(all_lines, args.encoding))
        report += ["", "=" * 78,
                   f"{len(all_lines)} lançamento(s) de {len(paths)} fatura(s)  ->  {combined}"]

    text = "\n".join(report).lstrip("\n")
    print(text)
    if args.report_file:
        args.report_file.write_text(text + "\n", encoding="utf-8")

    if uncategorized and not args.no_interactive:
        from categorize import interactive_session
        interactive_session(args.rules, args.output, args.encoding)

    if args.strict and uncategorized:
        print(f"\n{uncategorized} lançamento(s) sem categoria.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
