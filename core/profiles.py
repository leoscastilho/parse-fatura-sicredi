"""Perfis de banco e schema de saída — a config que substitui código.

O que era hardcode de Sicredi (o rótulo "Data de Vencimento", a coluna
"Parcela", o marcador "US$" da seção internacional) virou dados em
`config/banks/*.yml`.  Adicionar um banco passa a ser escrever um YAML.

Duas estratégias de leitura cobrem o que existe hoje:

  * `excel_secoes` — planilha com blocos de lançamento, cada um com cabeçalho
    próprio e um "Valor Total" fechando (Sicredi);
  * `csv_simples`  — uma tabela, cabeçalho na primeira linha (Nubank).

Uma estratégia nova é uma classe nova aqui e um `estrategia:` novo no YAML;
nada mais no sistema precisa saber que ela existe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ProfileError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Tema
# ---------------------------------------------------------------------------

@dataclass
class Theme:
    primaria: str = "#3FA110"
    escura: str = "#146E37"
    clara: str = "#D7E6C8"
    suave: str = "#EDF5E5"
    destaque: str = "#FFCD00"
    neutra: str = "#5A645A"
    aviso: str = "#5A3C1E"
    erro: str = "#E60050"
    fundo: str = "#F4F7F1"
    texto: str = "#24291F"
    inicial: str = "?"

    def to_dict(self) -> dict[str, str]:
        return dict(self.__dict__)


# ---------------------------------------------------------------------------
# Perfil de banco
# ---------------------------------------------------------------------------

@dataclass
class BankProfile:
    id: str
    nome: str
    validado: bool = False
    tema: Theme = field(default_factory=Theme)
    leitura: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None
    raw_text: str = ""

    @property
    def estrategia(self) -> str:
        return self.leitura.get("estrategia", "excel_secoes")

    @property
    def extensoes(self) -> tuple[str, ...]:
        return tuple(self.leitura.get("extensoes") or (".xls", ".xlsx"))

    @property
    def pede_vencimento(self) -> bool:
        """O arquivo não traz a data de vencimento; o portal precisa perguntar."""
        return bool((self.leitura.get("vencimento") or {}).get("perguntar"))

    def accepts(self, filename: str) -> bool:
        return filename.lower().endswith(self.extensoes)

    @classmethod
    def from_text(cls, text: str, path: Path | None = None) -> "BankProfile":
        raw = yaml.safe_load(text) or {}
        if not isinstance(raw, dict):
            raise ProfileError("o perfil precisa ser um mapa YAML (chave: valor)")
        for required in ("id", "nome"):
            if not raw.get(required):
                raise ProfileError(f"perfil sem `{required}`: {path or '<memória>'}")

        tema_raw = raw.get("tema") or {}
        tema = Theme(**{k: v for k, v in tema_raw.items() if k in Theme.__annotations__})

        return cls(
            id=str(raw["id"]),
            nome=str(raw["nome"]),
            validado=bool(raw.get("validado", False)),
            tema=tema,
            leitura=raw.get("leitura") or {},
            path=path,
            raw_text=text,
        )

    @classmethod
    def load(cls, path: Path) -> "BankProfile":
        return cls.from_text(path.read_text(encoding="utf-8"), path=path)


# ---------------------------------------------------------------------------
# Schema de saída
# ---------------------------------------------------------------------------

@dataclass
class OutputSchema:
    colunas: list[str] = field(default_factory=lambda: [
        "Data", "Categoria", "Descrição", "Valor (R$)", "Pago"])
    data_origem: str = "vencimento"
    data_formato: str = "%m/%d/%Y"
    modelo: str = "[Cartão] {descricao}{parcela}{sufixo_data}"
    parcela_modelo: str = " (Parcela {parcela})"
    sufixo_data: str = " {{Em {dia}/{mes}}}"
    titlecase: bool = True
    colapsar_espacos: bool = True
    pago: str = "x"
    ordenacao: list[str] = field(default_factory=lambda: ["data", "categoria", "data_compra"])
    categoria_vazia_no_fim: bool = True
    encoding: str = "utf-8"
    nome_um: str = "fatura_{periodo}.csv"
    nome_varios: str = "faturas_{inicio}_a_{fim}.csv"
    path: Path | None = None
    raw_text: str = ""

    @classmethod
    def from_text(cls, text: str, path: Path | None = None) -> "OutputSchema":
        raw = yaml.safe_load(text) or {}
        if not isinstance(raw, dict):
            raise ProfileError("o formato de saída precisa ser um mapa YAML")
        data = raw.get("data") or {}
        desc = raw.get("descricao") or {}
        arquivo = raw.get("arquivo") or {}

        ordenacao_raw = raw.get("ordenacao") or {}
        if isinstance(ordenacao_raw, list):
            # Formato antigo: lista simples de chaves.
            chaves = [str(x) for x in ordenacao_raw]
            vazia_no_fim = True
        else:
            chaves = [str(x) for x in (ordenacao_raw.get("chaves") or [])]
            vazia_no_fim = bool(ordenacao_raw.get("categoria_vazia_no_fim", True))

        defaults = cls()
        return cls(
            # `colunas` ausente cai no default; `colunas: []` fica vazio de
            # propósito, para o endpoint poder recusar em vez de mascarar.
            colunas=([str(c) for c in raw["colunas"]] if "colunas" in raw
                     else list(defaults.colunas)),
            data_origem=data.get("origem", defaults.data_origem),
            data_formato=data.get("formato", defaults.data_formato),
            modelo=desc.get("modelo", defaults.modelo),
            parcela_modelo=desc.get("parcela", defaults.parcela_modelo),
            sufixo_data=desc.get("sufixo_data", defaults.sufixo_data),
            titlecase=bool(desc.get("titlecase", True)),
            colapsar_espacos=bool(desc.get("colapsar_espacos", True)),
            pago=str(raw.get("pago", defaults.pago)),
            ordenacao=chaves or defaults.ordenacao,
            categoria_vazia_no_fim=vazia_no_fim,
            encoding=arquivo.get("encoding", defaults.encoding),
            nome_um=arquivo.get("um_extrato", defaults.nome_um),
            nome_varios=arquivo.get("varios_extratos", defaults.nome_varios),
            path=path,
            raw_text=text,
        )

    @classmethod
    def load(cls, path: Path) -> "OutputSchema":
        return cls.from_text(path.read_text(encoding="utf-8"), path=path)


# ---------------------------------------------------------------------------
# Coleção
# ---------------------------------------------------------------------------

@dataclass
class ConfigSet:
    """Toda a configuração da aplicação, num objeto só.

    É o que o `/config/export` empacota e o `/config/import` valida — e é o que
    uma sessão de outra pessoa carrega dentro da transação, sem nada disso
    precisar existir no disco do servidor.
    """

    banks: dict[str, BankProfile] = field(default_factory=dict)
    output: OutputSchema = field(default_factory=OutputSchema)
    categories_text: str = ""
    root: Path | None = None

    @property
    def default_bank(self) -> BankProfile:
        """Primeiro banco VALIDADO (desempate por nome).

        A ordem do diretório colocaria `nubank.yml` antes de `sicredi.yml`, e um
        `banco` vazio no request escolheria o placeholder — que nem sabe ler a
        data de vencimento. O padrão tem que ser o mesmo que a interface mostra.
        """
        if not self.banks:
            raise ProfileError("nenhum perfil de banco carregado")
        return sorted(self.banks.values(), key=lambda b: (not b.validado, b.nome))[0]

    def bank(self, bank_id: str | None) -> BankProfile:
        if not bank_id:
            return self.default_bank
        if bank_id in self.banks:
            return self.banks[bank_id]
        raise ProfileError(f"banco desconhecido: {bank_id}")

    @classmethod
    def load(cls, root: Path) -> "ConfigSet":
        banks: dict[str, BankProfile] = {}
        bank_dir = root / "banks"
        if bank_dir.is_dir():
            for path in sorted(bank_dir.glob("*.yml")) + sorted(bank_dir.glob("*.yaml")):
                profile = BankProfile.load(path)
                banks[profile.id] = profile
        if not banks:
            raise ProfileError(f"nenhum perfil de banco em {bank_dir}")

        output_path = root / "output.yml"
        output = OutputSchema.load(output_path) if output_path.exists() else OutputSchema()

        categories_path = root / "categories.yml"
        if not categories_path.exists():
            # Compatibilidade com o layout antigo (categories.yml na raiz).
            legacy = root.parent / "categories.yml"
            categories_path = legacy if legacy.exists() else categories_path
        if not categories_path.exists():
            raise ProfileError(f"categories.yml não encontrado em {root}")

        return cls(
            banks=banks,
            output=output,
            categories_text=categories_path.read_text(encoding="utf-8"),
            root=root,
        )
