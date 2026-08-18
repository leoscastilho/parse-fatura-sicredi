#!/usr/bin/env python3
"""
categorize.py — resolve os lançamentos que ficaram sem categoria (CLI).

Mesma decisão, mesmo efeito que a tela "New Classifications" do site: os dois
chamam `core.yaml_edit`, então a palavra-chave gravada aqui vale lá e vice-versa.

    python categorize.py            # sessão interativa
    python categorize.py --list     # só lista os pendentes
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from core import CSV_COLUMNS, Ruleset, merchant_key, merchant_of, normalize
from core.rules import LineState
from core.text import due_date_of, purchase_date_of, sort_key_category
from core.yaml_edit import YamlEditError, add_category, add_keyword, add_to_list

EOF_ANSWER = "\x00eof"


def _ask(prompt: str) -> str:
    """Não dá para confiar em `isatty()`: o console do PyCharm aceita input mas
    responde `False`. A gente tenta ler e trata a falta de entrada quando ocorre.
    """
    try:
        return input(prompt).strip()
    except (EOFError, OSError, RuntimeError):
        return EOF_ANSWER


@dataclass
class Pending:
    merchant: str
    samples: list[str] = field(default_factory=list)
    files: set[str] = field(default_factory=set)
    count: int = 0
    total: float = 0.0


def load_outputs(output_dir: Path, encoding: str) -> dict[Path, list[dict]]:
    tables: dict[Path, list[dict]] = {}
    for path in sorted(output_dir.glob("*.csv")):
        with path.open(encoding=encoding, newline="") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames != CSV_COLUMNS:
                print(f"  ignorando {path.name} (colunas inesperadas)", file=sys.stderr)
                continue
            tables[path] = [dict(row) for row in reader]
    return tables


def save(tables: dict[Path, list[dict]], encoding: str) -> None:
    for path, rows in tables.items():
        rows.sort(key=lambda r: (due_date_of(r["Data"]),
                                 sort_key_category(r["Categoria"]),
                                 purchase_date_of(r["Data"], r["Descrição"]) or ""))
        with path.open("w", encoding=encoding, newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)


def collect_pending(tables, rules: Ruleset) -> list[Pending]:
    grouped: dict[str, Pending] = {}
    for path, rows in tables.items():
        for row in rows:
            if row["Categoria"].strip():
                continue
            merchant = merchant_key(merchant_of(row["Descrição"]))
            # Já resolvidos de outro jeito: "não sei o que é" e marketplace.
            if not merchant or rules.is_known_unknown(merchant) or rules.is_manual(merchant):
                continue
            item = grouped.setdefault(merchant, Pending(merchant))
            item.count += 1
            item.total += float(row["Valor (R$)"])
            item.files.add(path.name)
            if len(item.samples) < 3:
                item.samples.append(row["Descrição"])
    return sorted(grouped.values(), key=lambda p: -abs(p.total))


def reapply(tables, rules: Ruleset) -> None:
    """Recategoriza só o que está vazio — decisões manuais são preservadas."""
    for rows in tables.values():
        for row in rows:
            if row["Categoria"].strip():
                continue
            match = rules.classify(merchant_of(row["Descrição"]))
            if match.categoria:
                row["Categoria"] = match.categoria


def interactive_session(rules_path: Path, output_dir: Path, encoding: str = "utf-8") -> int:
    rules = Ruleset.load(rules_path)
    tables = load_outputs(output_dir, encoding)
    if not tables:
        print(f"Nenhum CSV em {output_dir}/.")
        return 0

    pending = collect_pending(tables, rules)
    if not pending:
        print("Nada sem categoria.")
        return 0

    print(f"\n{len(pending)} estabelecimento(s) sem categoria.\n")
    decided = 0

    for position, item in enumerate(pending, 1):
        categories = rules.all_categories()
        print("─" * 78)
        print(f"[{position}/{len(pending)}]  {item.merchant}")
        print(f"    {item.count} lançamento(s) · R$ {item.total:,.2f} · "
              f"{', '.join(sorted(item.files))}")
        for sample in item.samples:
            print(f"    {sample}")
        print()
        for column in range(0, len(categories), 3):
            print("    " + "".join(f"{i + 1:>3}) {name:<22}" for i, name
                                   in enumerate(categories[column:column + 3], start=column)))
        print("\n    n) nova categoria    d) não sei (nunca mais perguntar)    "
              "p) pular    q) sair")

        answer = _ask("\n  > ").lower()
        if answer == EOF_ANSWER:
            print("\n  Sem entrada interativa disponível aqui.")
            print("  Rode `python categorize.py` num terminal para classificar.\n")
            break
        if answer == "q":
            break
        if answer in ("", "p"):
            continue

        try:
            text = rules_path.read_text(encoding="utf-8")
            if answer == "d":
                text = add_to_list(text, "desconhecidos", item.merchant,
                                   ["# Estabelecimentos sem classificação conhecida."])
                rules_path.write_text(text, encoding="utf-8")
                rules = Ruleset.load(rules_path)
                decided += 1
                print(f"  → '{item.merchant}' marcado como desconhecido.\n")
                continue

            if answer == "n":
                categoria = _ask("  Nome da nova categoria: ").strip()
                if not categoria or categoria == EOF_ANSWER:
                    continue
                if categoria not in categories:
                    text = add_category(text, categoria)
            elif answer.isdigit() and 1 <= int(answer) <= len(categories):
                categoria = categories[int(answer) - 1]
            else:
                print("  Resposta inválida.\n")
                continue

            typed = _ask(f"  Palavra-chave [{item.merchant}]: ").strip()
            keyword = normalize(item.merchant if typed in ("", EOF_ANSWER) else typed)
            text = add_keyword(text, categoria, keyword)
            rules_path.write_text(text, encoding="utf-8")
        except YamlEditError as exc:
            print(f"  !! {exc}\n", file=sys.stderr)
            continue

        rules = Ruleset.load(rules_path)
        reapply(tables, rules)
        save(tables, encoding)
        decided += 1
        print(f"  → {keyword} = {categoria}  (salvo em {rules_path.name})\n")

    reapply(tables, rules)
    save(tables, encoding)
    remaining = collect_pending(tables, rules)
    print("─" * 78)
    print(f"{decided} decisão(ões) gravada(s). "
          f"{len(remaining)} estabelecimento(s) ainda sem categoria.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", default="output", type=Path)
    parser.add_argument("--rules", default="config/categories.yml", type=Path)
    parser.add_argument("--encoding", default="utf-8")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    if args.list:
        rules = Ruleset.load(args.rules)
        pending = collect_pending(load_outputs(args.output, args.encoding), rules)
        for item in pending:
            print(f"{item.total:>12,.2f}  {item.count:>3}x  {item.merchant}")
        print(f"\n{len(pending)} estabelecimento(s) sem categoria.")
        return 0

    return interactive_session(args.rules, args.output, args.encoding)


if __name__ == "__main__":
    raise SystemExit(main())
