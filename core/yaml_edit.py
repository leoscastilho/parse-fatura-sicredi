"""Edição do `categories.yml` por INSERÇÃO DE LINHA.

Por que não `yaml.safe_load` + `yaml.dump`: o arquivo é cheio de comentários
explicativos ("# ? chocolateria — histórico oscila Lazer/Alimentação"), de
agrupamento visual e de ordem escolhida a mão.  Um round-trip pelo PyYAML
destrói tudo isso e devolve um arquivo ordenado alfabeticamente, sem um único
comentário.  Então a gente edita o TEXTO, inserindo uma linha no bloco certo,
e depois relê com o parser só para confirmar que a edição pegou.

Todas as funções recebem e devolvem `str` (o conteúdo do arquivo), de modo que
servem tanto para o arquivo no disco quanto para o blob vindo do GitHub.
"""

from __future__ import annotations

import re

import yaml

LIST_ITEM_RE = re.compile(r"^(\s*)-\s")


class YamlEditError(RuntimeError):
    pass


def _block_end(lines: list[str], start: int) -> int:
    """Índice logo após o último item da lista que começa em `start`."""
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if not line.strip() or line.lstrip().startswith("#") or LIST_ITEM_RE.match(line):
            end += 1
            continue
        break
    # Recua sobre comentários/brancos finais para colar junto do último item.
    while end - 1 > start and (not lines[end - 1].strip()
                               or lines[end - 1].lstrip().startswith("#")):
        end -= 1
    return end


def _top_level_block_end(lines: list[str], start: int) -> int:
    for i in range(start + 1, len(lines)):
        if lines[i].strip() and not lines[i].startswith((" ", "\t", "#")):
            return i
    return len(lines)


def add_keyword(text: str, categoria: str, keyword: str) -> str:
    """Acrescenta `keyword` em `palavras: <categoria>:`, criando se preciso."""
    lines = text.splitlines()

    palavras_at = next(
        (i for i, line in enumerate(lines) if re.match(r"^palavras:\s*$", line)), None
    )
    if palavras_at is None:
        raise YamlEditError("bloco `palavras:` não encontrado")

    palavras_end = _top_level_block_end(lines, palavras_at)
    heading = re.compile(rf"^(\s+){re.escape(categoria)}:\s*$")

    for i in range(palavras_at + 1, palavras_end):
        match = heading.match(lines[i])
        if match:
            lines.insert(_block_end(lines, i), f"{match.group(1)}  - {keyword}")
            break
    else:
        at = palavras_end
        while at - 1 > palavras_at and not lines[at - 1].strip():
            at -= 1
        lines[at:at] = ["", f"  {categoria}:", f"    - {keyword}"]

    updated = "\n".join(lines) + "\n"
    _verify(updated, "palavras", categoria, keyword)
    return updated


def add_to_list(text: str, block: str, value: str, header_comment: list[str]) -> str:
    """Acrescenta `value` a uma lista de topo (`desconhecidos`, `marketplaces`)."""
    lines = text.splitlines()
    at = next(
        (i for i, line in enumerate(lines) if re.match(rf"^{re.escape(block)}:\s*$", line)),
        None,
    )
    if at is None:
        lines += ["", *header_comment, f"{block}:", f"  - {value}"]
    else:
        lines.insert(_block_end(lines, at), f"  - {value}")

    updated = "\n".join(lines) + "\n"
    _verify(updated, block, None, value)
    return updated


def add_category(text: str, categoria: str) -> str:
    """Acrescenta a categoria em `configuracao.categorias`, mantendo A→Z."""
    lines = text.splitlines()
    at = next(
        (i for i, line in enumerate(lines) if re.match(r"^\s+categorias:\s*$", line)), None
    )
    if at is None:
        raise YamlEditError("bloco `configuracao.categorias:` não encontrado")

    end = _block_end(lines, at)
    indent = "    "
    insert_at = end
    for i in range(at + 1, end):
        item = LIST_ITEM_RE.match(lines[i])
        if item and lines[i].strip()[2:].strip().lower() > categoria.lower():
            insert_at = i
            indent = item.group(1)
            break
    lines.insert(insert_at, f"{indent}- {categoria}")

    updated = "\n".join(lines) + "\n"
    raw = yaml.safe_load(updated) or {}
    if categoria not in ((raw.get("configuracao") or {}).get("categorias") or []):
        raise YamlEditError(f"não consegui gravar a categoria '{categoria}'")
    return updated


def _verify(text: str, block: str, categoria: str | None, value: str) -> None:
    """Relê o YAML e confirma que a edição realmente pegou."""
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise YamlEditError(f"a edição gerou YAML inválido: {exc}") from exc

    got = raw.get(block) or {}
    got = (got.get(categoria) or []) if categoria else got
    if value not in [str(x) for x in got]:
        target = f"{block} -> {categoria}" if categoria else block
        raise YamlEditError(f"não consegui gravar '{value}' em {target}")


# ---------------------------------------------------------------------------
# Leitura estruturada e remoção — usadas pela tela de revisão de regras
# ---------------------------------------------------------------------------

# "    - DIAS ADAMI CHOCOLATE     # ? chocolateria — histórico oscila"
ENTRY_RE = re.compile(r"^(?P<indent>\s*)-\s+(?P<value>[^#]*?)\s*(?:#\s*(?P<comment>.*?))?\s*$")
TOP_KEY_RE = re.compile(r"^(?P<key>[A-Za-z_][\w-]*):\s*$")
SUB_KEY_RE = re.compile(r"^(?P<indent>\s+)(?P<key>[^#\s][^:]*):\s*$")

# Blocos que são listas simples no topo do arquivo.
FLAT_BLOCKS = ("excluir", "desconhecidos", "marketplaces")
# Blocos que são mapa Categoria -> lista.
NESTED_BLOCKS = ("palavras", "palavras_genericas")


def list_entries(text: str) -> list[dict]:
    """Todas as entradas editáveis do YAML, com número de linha e comentário.

    O comentário importa: é onde ficam os `# ?` — os chutes que precisam da sua
    confirmação. O `yaml.safe_load` descarta comentário, então a leitura aqui é
    feita sobre o texto, igual à escrita.
    """
    entries: list[dict] = []
    block: str | None = None
    categoria: str | None = None

    for number, line in enumerate(text.splitlines()):
        if not line.strip() or line.lstrip().startswith("#"):
            continue

        top = TOP_KEY_RE.match(line)
        if top:
            block = top.group("key")
            categoria = None
            continue

        if block in NESTED_BLOCKS:
            sub = SUB_KEY_RE.match(line)
            if sub and len(sub.group("indent")) <= 2:
                categoria = sub.group("key").strip()
                continue

        if block not in FLAT_BLOCKS + NESTED_BLOCKS:
            continue

        item = ENTRY_RE.match(line)
        if not item or not item.group("value"):
            continue

        comment = (item.group("comment") or "").strip()
        entries.append({
            "block": block,
            "categoria": categoria if block in NESTED_BLOCKS else None,
            "value": item.group("value").strip(),
            "comment": comment,
            "flagged": comment.startswith("?"),
            "line": number + 1,
        })

    return entries


def remove_entry(text: str, block: str, categoria: str | None, value: str) -> str:
    """Apaga UMA linha de lista, sem tocar em comentário nenhum ao redor.

    Só remove se a linha estiver no bloco (e categoria) certos — assim
    `RESTAURANTE` em Alimentação não some porque existe um homônimo em Lazer.
    """
    lines = text.splitlines()
    target = value.strip()

    for entry in list_entries(text):
        if (entry["block"] == block
                and entry["categoria"] == categoria
                and entry["value"] == target):
            del lines[entry["line"] - 1]
            updated = "\n".join(lines) + "\n"
            _verify_absent(updated, block, categoria, target)
            return updated

    where = f"{block}" + (f" -> {categoria}" if categoria else "")
    raise YamlEditError(f"'{value}' não encontrado em {where}")


def move_entry(text: str, categoria_de: str, categoria_para: str, value: str) -> str:
    """Troca uma palavra-chave de categoria, preservando o resto do arquivo."""
    text = remove_entry(text, "palavras", categoria_de, value)
    if categoria_para not in ((yaml.safe_load(text) or {}).get("palavras") or {}):
        pass  # add_keyword cria o bloco se precisar
    return add_keyword(text, categoria_para, value)


def _verify_absent(text: str, block: str, categoria: str | None, value: str) -> None:
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise YamlEditError(f"a remoção gerou YAML inválido: {exc}") from exc

    got = raw.get(block) or {}
    got = (got.get(categoria) or []) if categoria else got
    if value in [str(x) for x in got]:
        raise YamlEditError(f"'{value}' continua presente depois da remoção")


# ---------------------------------------------------------------------------
# Regras ordenadas (regex) — bloco `regras:`
# ---------------------------------------------------------------------------
#
# Cada regra ocupa duas linhas:
#
#     - padrao: "MERCADOLIVRE (LIVROS|LEITURA)"
#       categoria: Educação          # comentário opcional
#
# A ORDEM importa (a primeira que casa vence), então as operações trabalham
# sobre blocos de linhas inteiros: reordenar é mover o bloco, não reescrever o
# arquivo.  Comentários soltos antes de uma regra viajam junto com ela.

RULE_START_RE = re.compile(r"^(?P<indent>\s*)-\s+padrao\s*:\s*(?P<rest>.*)$")
RULE_FIELD_RE = re.compile(r"^\s+(?P<key>padrao|categoria)\s*:\s*(?P<rest>.*)$")


def _split_value_comment(rest: str) -> tuple[str, str]:
    """Separa o valor do comentário inline, respeitando aspas."""
    rest = rest.strip()
    if rest[:1] in ("'", '"'):
        quote = rest[0]
        i = 1
        while i < len(rest):
            if rest[i] == quote:
                if quote == "'" and rest[i + 1:i + 2] == "'":
                    i += 2
                    continue
                break
            i += 1
        value = rest[1:i].replace("''", "'") if quote == "'" else rest[1:i]
        tail = rest[i + 1:].lstrip()
        return value, tail[1:].strip() if tail.startswith("#") else ""
    if "#" in rest:
        value, comment = rest.split("#", 1)
        return value.strip(), comment.strip()
    return rest, ""


def _quote(value: str) -> str:
    """Aspas simples: em YAML elas não interpretam `\\d`, `\\s` etc.

    Aspas duplas quebrariam qualquer regex com barra invertida.
    """
    return "'" + value.replace("'", "''") + "'"


def _regras_bounds(lines: list[str]) -> tuple[int, int]:
    at = next((i for i, line in enumerate(lines) if re.match(r"^regras:\s*$", line)), None)
    if at is None:
        raise YamlEditError("bloco `regras:` não encontrado")
    return at, _top_level_block_end(lines, at)


def _parse_regras(text: str) -> tuple[int, int, list[dict], list[str]]:
    """(início, fim, regras, rodapé).

    O `rodapé` são os comentários que ficam DEPOIS da última regra e antes da
    próxima chave de topo — tipicamente o cabeçalho da seção seguinte.  Sem
    devolvê-lo em separado, qualquer edição no bloco engolia esses comentários.
    """
    lines = text.splitlines()
    start, end = _regras_bounds(lines)

    rules: list[dict] = []
    current: dict | None = None
    pending: list[str] = []          # comentários/brancos antes da próxima regra

    for i in range(start + 1, end):
        line = lines[i]
        head = RULE_START_RE.match(line)
        if head:
            if current:
                rules.append(current)
            padrao, comment = _split_value_comment(head.group("rest"))
            current = {"padrao": padrao, "categoria": "", "comment": comment,
                       "lead": pending, "raw": [line], "line": i + 1}
            pending = []
            continue

        if current is not None:
            field = RULE_FIELD_RE.match(line)
            if field:
                value, comment = _split_value_comment(field.group("rest"))
                current[field.group("key")] = value
                if comment and not current["comment"]:
                    current["comment"] = comment
                current["raw"].append(line)
                continue
            if not line.strip() or line.lstrip().startswith("#"):
                pending.append(line)
                continue

        if not line.strip() or line.lstrip().startswith("#"):
            pending.append(line)

    if current:
        rules.append(current)

    for position, rule in enumerate(rules):
        rule["index"] = position
    return start, end, rules, pending


def list_rules(text: str) -> list[dict]:
    """As regras ordenadas, com o texto bruto de cada uma."""
    return _parse_regras(text)[2]


def _render_rule(rule: dict) -> list[str]:
    comment = f"    # {rule['comment']}" if rule.get("comment") else ""
    return [
        f"  - padrao: {_quote(rule['padrao'])}",
        f"    categoria: {rule['categoria']}{comment}",
    ]


def _rebuild_regras(text: str, rules: list[dict]) -> str:
    lines = text.splitlines()
    start, end, _, trailing = _parse_regras(text)

    body: list[str] = []
    for rule in rules:
        body += rule.get("lead", [])
        body += rule.get("raw") or _render_rule(rule)
    body += trailing

    updated = lines[:start + 1] + body + lines[end:]
    result = "\n".join(updated) + "\n"

    try:
        parsed = yaml.safe_load(result) or {}
    except yaml.YAMLError as exc:
        raise YamlEditError(f"a edição gerou YAML inválido: {exc}") from exc
    if len(parsed.get("regras") or []) != len(rules):
        raise YamlEditError("a contagem de regras não bateu depois da edição")
    return result


def rule_add(text: str, padrao: str, categoria: str,
             at: int | None = None, comment: str = "") -> str:
    re.compile(padrao)          # falha cedo se o regex for inválido
    rules = list_rules(text)
    novo = {"padrao": padrao, "categoria": categoria, "comment": comment, "lead": []}
    novo["raw"] = _render_rule(novo)
    rules.insert(len(rules) if at is None else max(0, min(at, len(rules))), novo)
    return _rebuild_regras(text, rules)


def rule_remove(text: str, index: int) -> str:
    rules = list_rules(text)
    if not 0 <= index < len(rules):
        raise YamlEditError(f"regra {index} não existe (há {len(rules)})")
    rules.pop(index)
    return _rebuild_regras(text, rules)


def rule_update(text: str, index: int, padrao: str, categoria: str,
                comment: str | None = None) -> str:
    re.compile(padrao)
    rules = list_rules(text)
    if not 0 <= index < len(rules):
        raise YamlEditError(f"regra {index} não existe (há {len(rules)})")
    rule = rules[index]
    rule["padrao"], rule["categoria"] = padrao, categoria
    if comment is not None:
        rule["comment"] = comment
    rule["raw"] = _render_rule(rule)          # regenera as duas linhas
    return _rebuild_regras(text, rules)


def rule_move(text: str, index: int, delta: int) -> str:
    rules = list_rules(text)
    if not 0 <= index < len(rules):
        raise YamlEditError(f"regra {index} não existe (há {len(rules)})")
    destino = max(0, min(index + delta, len(rules) - 1))
    if destino == index:
        return text
    rules.insert(destino, rules.pop(index))
    return _rebuild_regras(text, rules)


def set_comment(text: str, block: str, categoria: str | None, value: str,
                comment: str) -> str:
    """Reescreve o comentário inline de UMA entrada, sem tocar no valor.

    É o que "confirmar um chute" faz: a linha `- OGGI  # ? gelateria` vira
    `- OGGI`, mantendo a palavra-chave e a categoria onde estão. Sem isto, a
    única forma de tirar o `# ?` era apagar a entrada (perdendo o mapeamento) ou
    movê-la de categoria (mudando o que ela faz).
    """
    lines = text.splitlines()
    alvo = value.strip()

    for entry in list_entries(text):
        if (entry["block"] == block and entry["categoria"] == categoria
                and entry["value"] == alvo):
            i = entry["line"] - 1
            indent = ENTRY_RE.match(lines[i]).group("indent")
            sufixo = f"    # {comment}" if comment else ""
            lines[i] = f"{indent}- {alvo}{sufixo}"

            updated = "\n".join(lines) + "\n"
            _verify(updated, block, categoria, alvo)   # o valor tem que sobreviver
            return updated

    where = block + (f" -> {categoria}" if categoria else "")
    raise YamlEditError(f"'{value}' não encontrado em {where}")
