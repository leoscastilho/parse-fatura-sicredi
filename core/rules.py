"""Carregamento e aplicação das regras do `categories.yml`.

`Ruleset` é a única fonte de verdade sobre "que categoria é esta compra".
A CLI e a API carregam o mesmo objeto, então não existe a possibilidade de
o site classificar diferente do script.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml

from .text import compact, merchant_key, normalize


class LineState(str, Enum):
    """Por que um lançamento está (ou não está) categorizado.

    Herda de `str` para serializar direto em JSON pelo Pydantic.
    """

    AUTO = "auto"                # casou com uma regra/palavra-chave
    UNMAPPED = "unmapped"        # nada casou -> perguntar e gravar palavra-chave
    MARKETPLACE = "marketplace"  # Amazon/ML -> em branco, decisão por LINHA
    IGNORED = "ignored"          # `desconhecidos` -> em branco, não perguntar


@dataclass
class MatchResult:
    categoria: str
    state: LineState
    matched: str | None = None   # a regra/trecho que casou, para depuração


@dataclass
class Ruleset:
    path: Path | None = None
    default_category: str = ""
    collapse_whitespace: bool = True
    categories: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    ordered_rules: list[tuple[re.Pattern, str]] = field(default_factory=list)
    keywords: list[tuple[str, str]] = field(default_factory=list)  # (trecho, categoria)
    unknown: list[str] = field(default_factory=list)
    manual: list[str] = field(default_factory=list)
    raw_text: str = ""

    # ------------------------------------------------------------------ load

    @classmethod
    def load(cls, path: Path) -> "Ruleset":
        return cls.from_text(path.read_text(encoding="utf-8"), path=path)

    @classmethod
    def from_text(cls, text: str, path: Path | None = None) -> "Ruleset":
        """Carrega de uma string.

        A API às vezes tem o YAML vindo do GitHub em memória, sem arquivo.
        """
        raw = yaml.safe_load(text) or {}
        cfg = raw.get("configuracao") or {}

        rs = cls(
            path=path,
            default_category=(cfg.get("categoria_padrao") or ""),
            collapse_whitespace=bool(cfg.get("colapsar_espacos", True)),
            categories=list(cfg.get("categorias") or []),
            exclude=[normalize(x) for x in (raw.get("excluir") or [])],
            unknown=[normalize(x) for x in (raw.get("desconhecidos") or [])],
            manual=[normalize(x) for x in (raw.get("marketplaces") or [])],
            raw_text=text,
        )

        for rule in raw.get("regras") or []:
            rs.ordered_rules.append(
                (re.compile(rule["padrao"], re.IGNORECASE), rule["categoria"])
            )

        for block in ("palavras", "palavras_genericas"):
            for categoria, trechos in (raw.get(block) or {}).items():
                for trecho in trechos or []:
                    rs.keywords.append((normalize(trecho), categoria))

        # Trecho mais longo (mais específico) vence.
        rs.keywords.sort(key=lambda kv: len(kv[0]), reverse=True)
        return rs

    # --------------------------------------------------------------- matching

    @staticmethod
    def _hit(term: str, norm_desc: str) -> bool:
        if not term:
            return False
        return term in norm_desc or compact(term) in compact(norm_desc)

    # Os três predicados abaixo normalizam a entrada por conta própria.
    # `normalize` é idempotente, então chamar com texto já normalizado é de
    # graça — e assim nenhum chamador consegue passar o texto cru por engano
    # (foi exatamente esse bug que deixou "Pag Fat Deb Cc" escapar do filtro
    # enquanto "PAGAMENTO DEBITO EM", já maiúsculo, era descartado).

    def is_excluded(self, description: str) -> bool:
        norm = normalize(description)
        return any(self._hit(t, norm) for t in self.exclude)

    def is_known_unknown(self, description: str) -> bool:
        target = compact(merchant_key(description))
        return any(compact(merchant_key(t)) == target for t in self.unknown)

    def is_manual(self, description: str) -> bool:
        norm = normalize(description)
        return any(self._hit(t, norm) for t in self.manual)

    def classify(self, description: str) -> MatchResult:
        """Ordem: regras -> marketplaces -> palavras -> desconhecidos -> vazio."""
        norm = normalize(description)

        for pattern, categoria in self.ordered_rules:
            if pattern.search(norm):
                return MatchResult(categoria, LineState.AUTO, pattern.pattern)

        if self.is_manual(norm):
            return MatchResult("", LineState.MARKETPLACE, "marketplaces")

        for trecho, categoria in self.keywords:
            if self._hit(trecho, norm):
                return MatchResult(categoria, LineState.AUTO, trecho)

        if self.is_known_unknown(norm):
            return MatchResult("", LineState.IGNORED, "desconhecidos")

        return MatchResult(self.default_category, LineState.UNMAPPED, None)

    # ------------------------------------------------------------------ misc

    def all_categories(self) -> list[str]:
        """Declaradas em `configuracao.categorias` + as usadas nas regras."""
        names = set(self.categories)
        names.update(categoria for _, categoria in self.keywords)
        names.update(categoria for _, categoria in self.ordered_rules)
        return sorted(names, key=normalize)

    def keyword_conflicts(self, keyword: str) -> list[tuple[str, str]]:
        """Palavras-chave existentes que colidem com `keyword`.

        Usado pelo `/validate`: avisa quando o trecho novo é substring (ou
        superstring) de um já existente, o que muda quem vence pela regra do
        "trecho mais longo".
        """
        target = compact(normalize(keyword))
        if not target:
            return []
        hits = []
        for trecho, categoria in self.keywords:
            other = compact(trecho)
            if other and (other in target or target in other):
                hits.append((trecho, categoria))
        return hits
