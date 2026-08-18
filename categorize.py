#!/usr/bin/env python3
"""
categorize.py — resolve os lançamentos que ficaram sem categoria.

Lê os CSVs já gerados em `output/`, junta os lançamentos com a coluna
`Categoria` vazia, pergunta a categoria de cada estabelecimento, grava a
resposta em `categories.yml` e reescreve os CSVs.  O conhecimento vai
acumulando: no mês seguinte o mesmo estabelecimento já sai classificado.

Roda sozinho:
    python categorize.py
    python categorize.py --output output --rules categories.yml

E também é chamado automaticamente pelo `main.py` quando aparece
estabelecimento novo (desligue com `python main.py --no-interactive`).

No prompt:
    <número>   escolhe a categoria
    n          cria uma categoria nova
    d          "não sei" — vai para `desconhecidos` e nunca mais é perguntado
    p          pula (pergunta de novo na próxima vez)
    q          sai e salva o que já foi decidido

O YAML é editado por inserção de linha, não por reserialização, então seus
comentários e sua formatação continuam exatamente como você deixou.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml

from main import (
    OUTPUT_COLUMNS,
    Ruleset,
    merchant_of,
    normalize,
    purchase_date_of,
    sort_frame,
)


# ---------------------------------------------------------------------------
# Edição do YAML preservando comentários
# ---------------------------------------------------------------------------

LIST_ITEM_RE = re.compile(r"^(\s*)-\s")


def _block_bounds(lines: list[str], start: int) -> int:
    """Índice logo após o último item da lista que começa em `start`."""
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if not line.strip() or line.lstrip().startswith("#"):
            end += 1
            continue
        if LIST_ITEM_RE.match(line):
            end += 1
            continue
        break
    # Recua sobre comentários/brancos finais para colar junto do último item.
    while end - 1 > start and (not lines[end - 1].strip()
                               or lines[end - 1].lstrip().startswith("#")):
        end -= 1
    return end


def add_keyword(path: Path, categoria: str, keyword: str) -> None:
    """Acrescenta `keyword` em `palavras: <categoria>:`, criando se preciso."""
    lines = path.read_text(encoding="utf-8").splitlines()

    palavras_at = next(
        (i for i, line in enumerate(lines) if re.match(r"^palavras:\s*$", line)), None
    )
    if palavras_at is None:
        raise ValueError(f"bloco `palavras:` não encontrado em {path}")

    # Fim do bloco `palavras:` (primeira linha na coluna 0 depois dele).
    palavras_end = len(lines)
    for i in range(palavras_at + 1, len(lines)):
        if lines[i].strip() and not lines[i].startswith((" ", "\t", "#")):
            palavras_end = i
            break

    heading = re.compile(rf"^(\s+){re.escape(categoria)}:\s*$")
    for i in range(palavras_at + 1, palavras_end):
        if heading.match(lines[i]):
            indent = heading.match(lines[i]).group(1)
            insert_at = _block_bounds(lines, i)
            lines.insert(insert_at, f"{indent}  - {keyword}")
            break
    else:
        # Categoria ainda não existe no YAML: cria o bloco no fim de `palavras`.
        at = palavras_end
        while at - 1 > palavras_at and not lines[at - 1].strip():
            at -= 1
        lines[at:at] = ["", f"  {categoria}:", f"    - {keyword}"]

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _verify(path, "palavras", categoria, keyword)


def add_unknown(path: Path, merchant: str) -> None:
    """Acrescenta `merchant` na lista `desconhecidos:`, criando se preciso."""
    lines = path.read_text(encoding="utf-8").splitlines()

    at = next(
        (i for i, line in enumerate(lines) if re.match(r"^desconhecidos:\s*$", line)), None
    )
    if at is None:
        lines += [
            "",
            "# -----------------------------------------------------------------------------",
            "# ESTABELECIMENTOS QUE VOCÊ NÃO SABE CLASSIFICAR",
            "# Saem com Categoria vazia e o categorize.py não pergunta de novo.",
            "# Apague uma linha daqui para voltar a ser perguntado.",
            "# -----------------------------------------------------------------------------",
            "desconhecidos:",
            f"  - {merchant}",
        ]
    else:
        lines.insert(_block_bounds(lines, at), f"  - {merchant}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _verify(path, "desconhecidos", None, merchant)


def _verify(path: Path, block: str, categoria: str | None, value: str) -> None:
    """Relê o YAML e confirma que a edição realmente pegou."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    got = (raw.get(block) or {})
    got = (got.get(categoria) or []) if categoria else got
    if value not in [str(x) for x in got]:
        raise RuntimeError(
            f"não consegui gravar '{value}' em {block}"
            + (f" -> {categoria}" if categoria else "")
            + f" ({path}). Adicione à mão."
        )


# ---------------------------------------------------------------------------
# Coleta dos pendentes
# ---------------------------------------------------------------------------

@dataclass
class Pending:
    merchant: str                 # normalizado, usado como palavra-chave
    samples: list[str]            # descrições como aparecem no CSV
    files: set[str]
    count: int = 0
    total: float = 0.0


def load_outputs(output_dir: Path, encoding: str) -> dict[Path, pd.DataFrame]:
    frames = {}
    for path in sorted(output_dir.glob("*.csv")):
        frame = pd.read_csv(path, encoding=encoding, dtype={"Categoria": str})
        if list(frame.columns) != OUTPUT_COLUMNS:
            print(f"  ignorando {path.name} (colunas inesperadas)", file=sys.stderr)
            continue
        frame["Categoria"] = frame["Categoria"].fillna("")
        frames[path] = frame
    return frames


def collect_pending(frames: dict[Path, pd.DataFrame], rules: Ruleset) -> list[Pending]:
    grouped: dict[str, Pending] = {}
    for path, frame in frames.items():
        for _, row in frame.iterrows():
            if str(row["Categoria"]).strip():
                continue
            merchant = normalize(merchant_of(row["Descrição"]))
            if not merchant:
                continue
            # Já resolvidos de outro jeito: "não sei o que é" e marketplace
            # (onde a categoria muda a cada compra, então não há o que gravar).
            if rules.is_known_unknown(merchant) or rules.is_manual(merchant):
                continue
            item = grouped.setdefault(merchant, Pending(merchant, [], set()))
            item.count += 1
            item.total += float(row["Valor (R$)"])
            item.files.add(path.name)
            if len(item.samples) < 3:
                item.samples.append(str(row["Descrição"]))
    return sorted(grouped.values(), key=lambda p: -abs(p.total))


def known_categories(rules: Ruleset, frames: dict[Path, pd.DataFrame]) -> list[str]:
    """Categorias declaradas no YAML + as que já apareceram nos CSVs."""
    names = set(rules.categories)
    names.update(categoria for _, categoria in rules.keywords)
    names.update(categoria for _, categoria in rules.ordered_rules)
    for frame in frames.values():
        names.update(c for c in frame["Categoria"].astype(str) if c.strip())
    return sorted(names, key=normalize)


# ---------------------------------------------------------------------------
# Reaplicação e gravação
# ---------------------------------------------------------------------------

def reapply(frames: dict[Path, pd.DataFrame], rules: Ruleset) -> None:
    """Recategoriza só o que está vazio (decisões manuais são preservadas)."""
    for path, frame in frames.items():
        for idx, row in frame.iterrows():
            if str(row["Categoria"]).strip():
                continue
            categoria, _ = rules.categorize(normalize(merchant_of(row["Descrição"])))
            if categoria:
                frame.at[idx, "Categoria"] = categoria
        frames[path] = sort_frame(frame)


def save(frames: dict[Path, pd.DataFrame], encoding: str) -> None:
    for path, frame in frames.items():
        frame.to_csv(path, index=False, encoding=encoding)


# ---------------------------------------------------------------------------
# Sessão interativa
# ---------------------------------------------------------------------------

def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return "q"


def interactive_session(rules_path: Path, output_dir: Path, encoding: str = "utf-8") -> int:
    rules = Ruleset.load(rules_path)
    frames = load_outputs(output_dir, encoding)
    if not frames:
        print(f"Nenhum CSV em {output_dir}/.")
        return 0

    pending = collect_pending(frames, rules)
    if not pending:
        print("Nada sem categoria. 👍")
        return 0

    print(f"\n{len(pending)} estabelecimento(s) sem categoria.\n")
    decided = 0

    for position, item in enumerate(pending, 1):
        categories = known_categories(rules, frames)

        print("─" * 78)
        print(f"[{position}/{len(pending)}]  {item.merchant}")
        print(f"    {item.count} lançamento(s) · R$ {item.total:,.2f} · {', '.join(sorted(item.files))}")
        for sample in item.samples:
            print(f"    {sample}")
        print()
        for column in range(0, len(categories), 3):
            print("    " + "".join(
                f"{i + 1:>3}) {name:<22}"
                for i, name in enumerate(categories[column:column + 3], start=column)
            ))
        print("\n    n) nova categoria    d) não sei (nunca mais perguntar)    p) pular    q) sair")

        answer = _ask("\n  > ").lower()

        if answer == "q":
            break
        if answer in ("", "p"):
            continue

        if answer == "d":
            add_unknown(rules_path, item.merchant)
            rules = Ruleset.load(rules_path)
            decided += 1
            print(f"  → '{item.merchant}' marcado como desconhecido.\n")
            continue

        if answer == "n":
            categoria = _ask("  Nome da nova categoria: ").strip()
            if not categoria:
                continue
        elif answer.isdigit() and 1 <= int(answer) <= len(categories):
            categoria = categories[int(answer) - 1]
        else:
            print("  Resposta inválida.\n")
            continue

        default_kw = item.merchant
        keyword = _ask(f"  Palavra-chave [{default_kw}]: ").strip() or default_kw
        keyword = normalize(keyword)

        add_keyword(rules_path, categoria, keyword)
        rules = Ruleset.load(rules_path)
        reapply(frames, rules)
        save(frames, encoding)
        decided += 1
        print(f"  → {keyword} = {categoria}  (salvo em {rules_path.name})\n")

    reapply(frames, rules)
    save(frames, encoding)

    remaining = collect_pending(frames, rules)
    print("─" * 78)
    print(f"{decided} decisão(ões) gravada(s). {len(remaining)} estabelecimento(s) ainda sem categoria.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", default="output", type=Path)
    parser.add_argument("--rules", default="categories.yml", type=Path)
    parser.add_argument("--encoding", default="utf-8")
    parser.add_argument("--list", action="store_true",
                        help="só lista os pendentes, sem perguntar nada")
    args = parser.parse_args()

    if args.list:
        rules = Ruleset.load(args.rules)
        frames = load_outputs(args.output, args.encoding)
        pending = collect_pending(frames, rules)
        for item in pending:
            print(f"{item.total:>12,.2f}  {item.count:>3}x  {item.merchant}")
        print(f"\n{len(pending)} estabelecimento(s) sem categoria.")
        return 0

    return interactive_session(args.rules, args.output, args.encoding)


if __name__ == "__main__":
    raise SystemExit(main())
