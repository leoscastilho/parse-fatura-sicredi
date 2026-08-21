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

from .text import normalize


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
    def formatos(self) -> tuple[dict[str, Any], ...]:
        """Os formatos de arquivo que este banco exporta.

        Um banco pode ter mais de um. O Sicredi tem dois — o `.xls` que o site
        baixa e o `.csv` que o aplicativo manda —, e eles não se parecem: um é
        planilha em seções, o outro é CSV com preâmbulo de rótulos.

        Sem a lista, `leitura` inteiro é UM formato. É o que mantém o Nubank e
        os perfis antigos funcionando sem tocar em nada.
        """
        lista = self.leitura.get("formatos")
        return tuple(lista) if lista else (self.leitura,)

    @property
    def estrategia(self) -> str:
        return self.formatos[0].get("estrategia", "excel_secoes")

    @property
    def extensoes(self) -> tuple[str, ...]:
        """A UNIÃO das extensões de todos os formatos, sem repetir.

        É esta lista que a dropzone anuncia e que o `/upload` usa para recusar
        arquivo. Somando os formatos, aceitar um novo é acrescentá-lo ao YAML —
        a tela e a validação seguem sozinhas.
        """
        vistas: list[str] = []
        for formato in self.formatos:
            for ext in formato.get("extensoes") or (".xls", ".xlsx"):
                if ext not in vistas:
                    vistas.append(ext)
        return tuple(vistas)

    def formato_de(self, filename: str) -> dict[str, Any] | None:
        """Qual formato lê ESTE arquivo — decidido pela extensão, nunca perguntado.

        Perguntar seria transferir para o usuário uma distinção que o próprio
        nome do arquivo já resolve: quem exportou do app tem um `.csv`, quem
        baixou do site tem um `.xls`, e ninguém precisa saber que existem duas
        rotinas de leitura por trás.
        """
        nome = filename.lower()
        for formato in self.formatos:
            if nome.endswith(tuple(formato.get("extensoes") or (".xls", ".xlsx"))):
                return formato
        return None

    @property
    def pede_vencimento(self) -> bool:
        """Algum formato deste banco não traz a data e obriga a perguntar?

        Basta UM: a tela de upload monta o campo antes de saber qual arquivo
        virá. Nos dois formatos do Sicredi a data está dentro do arquivo, então
        lá o campo não aparece; no Nubank aparece.
        """
        return any((f.get("vencimento") or {}).get("perguntar")
                   for f in self.formatos)

    def accepts(self, filename: str) -> bool:
        return filename.lower().endswith(self.extensoes)

    @property
    def reconhece_algo(self) -> bool:
        """Todo formato deste banco diz como se reconhece?

        Invariante de configuração, não de execução: um perfil novo sem
        `deteccao` funciona enquanto for o único da extensão dele e vira
        indetectável no dia em que aparecer um concorrente — falha longe da
        causa, num arquivo que sempre funcionou.
        """
        return all((f.get("deteccao") or {}).get("contem") for f in self.formatos)

    def reconhece(self, filename: str, amostra: str) -> bool:
        """Este arquivo é DESTE banco? Extensão certa e assinatura presente.

        `amostra` é o começo do arquivo já normalizado (maiúsculas, sem acento,
        pontuação virando espaço). Normalizar antes de comparar é o que faz a
        assinatura não depender de o palpite de encoding ter acertado o
        "Descrição" nem de o Sicredi decidir escrever "ASSOCIADO" um dia.

        Formato sem `deteccao` NUNCA reconhece nada por conta própria: só é
        escolhido quando é o único candidato pela extensão. Um formato que
        aceitasse qualquer coisa venceria os outros por acidente de ordem
        alfabética do diretório, e o usuário veria "Nubank" numa fatura do
        Sicredi sem nenhuma pista do porquê.
        """
        formato = self.formato_de(filename)
        if formato is None:
            return False
        sinais = (formato.get("deteccao") or {}).get("contem") or []
        return bool(sinais) and all(normalize(s) in amostra for s in sinais)

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

# Os cinco PAPÉIS que uma linha do CSV cumpre, na ordem canônica. O nome da
# coluna é escolha do usuário (`Descrição` ou `Item`, `Valor (R$)` ou `Valor`);
# o papel é o que o código conhece. Sem esta separação, renomear uma coluna no
# formato de saída quebrava a exportação inteira — o writer montava a linha com
# os nomes antigos e o csv.DictWriter recusava.
PAPEIS = ("data", "categoria", "descricao", "valor", "pago")


@dataclass
class OutputSchema:
    colunas: list[str] = field(default_factory=lambda: [
        "Data", "Categoria", "Descrição", "Valor (R$)", "Pago"])
    # papel -> nome da coluna. Preenchido por posição a partir de `colunas`
    # quando o YAML não traz um bloco `campos:` explícito.
    campos: dict[str, str] = field(default_factory=dict)
    data_origem: str = "vencimento"
    data_formato: str = "%m/%d/%Y"
    modelo: str = "[Cartão{banco}] {descricao}{parcela}{sufixo_data}"
    parcela_modelo: str = " (Parcela {parcela})"
    # De qual banco veio a linha, e SÓ quando o lote tem mais de um. Com uma
    # fatura só — ou várias do mesmo banco — a etiqueta não distingue nada e
    # não entra: escrevê-la sempre acrescentaria seis caracteres a toda linha
    # do histórico para repetir uma informação que já é a mesma em todas.
    #
    # Mora DENTRO do colchete (`[Cartão-BTG]`) porque é uma qualificação do
    # "Cartão", não um campo novo — e porque `merchant_of` já pula o colchete
    # inteiro, então nada que lê a descrição precisou mudar.
    banco_modelo: str = "-{banco}"
    # Numa conta conjunta, de quem foi a compra. Vai no FIM da descrição, depois
    # da data, porque é a informação menos usada das três — e porque colocá-la
    # antes moveria o `{Em 3/Jan}` de lugar em toda linha já exportada.
    titular_modelo: str = " <{titular}>"
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
        arquivo = raw.get("arquivo") or {}

        defaults_cols = cls().colunas
        colunas = ([str(c) for c in raw["colunas"]] if "colunas" in raw
                   else list(defaults_cols))

        # `campos:` explícito vence; sem ele, os papéis saem da POSIÇÃO em
        # `colunas`. É o que faz `colunas: [Data, Categoria, Item, Valor, Pago]`
        # simplesmente funcionar, sem exigir um bloco a mais de quem só quis
        # renomear a coluna para casar com a planilha.
        campos_raw = raw.get("campos") or {}
        campos: dict[str, str] = {}
        for i, papel in enumerate(PAPEIS):
            if papel in campos_raw:
                campos[papel] = str(campos_raw[papel])
            elif i < len(colunas):
                campos[papel] = colunas[i]
            else:
                campos[papel] = defaults_cols[i]

        # O bloco que descreve como montar a descrição pode vir com a chave
        # canônica `descricao:` ou com o nome que o usuário deu à coluna
        # (`Item:`). Aceitar os dois evita que renomear a coluna desligue o
        # modelo em silêncio, que é exatamente o que acontecia antes.
        desc = raw.get("descricao")
        if desc is None:
            desc = raw.get(campos["descricao"])
        desc = desc or {}

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
            colunas=colunas,
            campos=campos,
            data_origem=data.get("origem", defaults.data_origem),
            data_formato=data.get("formato", defaults.data_formato),
            modelo=desc.get("modelo", defaults.modelo),
            parcela_modelo=desc.get("parcela", defaults.parcela_modelo),
            banco_modelo=desc.get("banco", defaults.banco_modelo),
            titular_modelo=desc.get("titular", defaults.titular_modelo),
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

    def coluna(self, papel: str) -> str:
        """Nome da coluna que cumpre `papel` neste formato de saída.

        Funciona mesmo num `OutputSchema()` construído à mão (sem passar pelo
        YAML), caindo na posição canônica e depois no nome padrão.
        """
        if papel in self.campos:
            return self.campos[papel]
        i = PAPEIS.index(papel)
        if i < len(self.colunas):
            return self.colunas[i]
        return OutputSchema.__dataclass_fields__["colunas"].default_factory()[i]

    def linha(self, *, data: str, categoria: str, descricao: str,
              valor, pago: str) -> dict:
        """Monta a linha do CSV com os nomes de coluna DESTE formato."""
        return {
            self.coluna("data"): data,
            self.coluna("categoria"): categoria,
            self.coluna("descricao"): descricao,
            self.coluna("valor"): valor,
            self.coluna("pago"): pago,
        }

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

    def bank(self, bank_id: str) -> BankProfile:
        """Um banco pelo id. NÃO existe mais "banco padrão".

        Existia enquanto o portal perguntava de qual banco era o arquivo: um
        `banco` vazio no request precisava cair em algum lugar, e cair no
        perfil errado era pior do que recusar. Agora quem responde é o próprio
        arquivo (`detectar`), e um padrão só voltaria a ser o palpite que a
        detecção existe para não dar.
        """
        if bank_id in self.banks:
            return self.banks[bank_id]
        raise ProfileError(f"banco desconhecido: {bank_id}")

    def detectar(self, filename: str, amostra: bytes | str) -> BankProfile:
        """De qual banco é este arquivo — pelo CONTEÚDO, não por uma pergunta.

        Perguntar era pedir ao usuário uma informação que o arquivo já tem: ele
        acabou de exportar do app do banco e sabe muito bem de qual, mas ter de
        dizer isso a cada upload é uma chance por mês de escolher errado — e
        escolher errado dava um erro de parsing sem relação óbvia com a causa.

        A ordem é: extensão primeiro (barata e decisiva na maioria dos casos),
        assinatura de conteúdo só quando a extensão empata. Hoje o empate é
        entre os dois `.csv`, o do app do Sicredi e o do Nubank.
        """
        candidatos = [b for b in self.banks.values() if b.formato_de(filename)]
        if not candidatos:
            extensoes = sorted({e for b in self.banks.values() for e in b.extensoes})
            raise ProfileError(
                f"{filename}: não reconheço esta extensão — "
                f"os bancos configurados exportam {', '.join(extensoes)}")
        if len(candidatos) == 1:
            return candidatos[0]

        # Só aqui o arquivo é lido. `errors="replace"` porque a amostra serve
        # para procurar assinatura, não para virar dado: um byte estranho no
        # meio não pode derrubar a detecção.
        if isinstance(amostra, bytes):
            amostra = amostra.decode("utf-8", errors="replace")
        normalizada = normalize(amostra)

        achados = [b for b in candidatos if b.reconhece(filename, normalizada)]
        if len(achados) == 1:
            return achados[0]
        if not achados:
            raise ProfileError(
                f"{filename}: é um {filename.rsplit('.', 1)[-1]} que não parece "
                f"de nenhum banco conhecido ({', '.join(b.nome for b in candidatos)}). "
                "Confira se o arquivo é a exportação da fatura, e não outra coisa.")
        raise ProfileError(
            f"{filename}: a assinatura casa com mais de um banco "
            f"({', '.join(b.nome for b in achados)}) — os perfis precisam de "
            "`deteccao.contem` mais específico.")

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
