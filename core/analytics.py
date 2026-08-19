"""Análise de um extrato histórico inteiro — de um CSV só.

O arquivo que entra aqui não é uma fatura: é a planilha da vida financeira
inteira, 6.700 linhas desde 2012, exportada de um Google Sheets que mudou de
formato algumas vezes pelo caminho. Este módulo existe para tirar disso números
em que dá para confiar, e o trabalho real não é somar — é decidir O QUE somar.

TRÊS ARMADILHAS QUE ESTE MÓDULO DESARMA
---------------------------------------

1. **Parte do gasto não está detalhada.** `Cartão de crédito` é a fatura inteira
   lançada numa linha só, nos meses em que ela ainda não foi aberta em
   categorias. O dinheiro saiu de verdade — conta como gasto —, mas ninguém sabe
   quanto foi mercado, transporte ou lazer. São R$ 325 mil, 11% do total: é o
   teto do erro de qualquer leitura por categoria, e a aba mostra o número em
   vez de deixá-lo implícito.

   A suspeita inicial era de DUPLA contagem (a linha da fatura mais os itens com
   `[Cartão]`). Conferindo mês a mês: em nenhum mês os dois coexistem — ou a
   fatura entra agregada, ou itemizada. Excluí-la teria apagado R$ 310 mil de
   gasto real. A condição continua sendo verificada a cada análise
   (`possivel_dupla_contagem`), porque colar um extrato num mês que já tem a
   linha agregada a criaria.

2. **Poupança não é gasto nem receita.** É o mecanismo de zerar o mês: o que
   sobra vira `Poupança` e volta no mês seguinte como `Resgate Poupança`. O
   mesmo dinheiro, duas vezes, com sinais opostos. Somá-lo dobraria o orçamento.
   Em compensação ele dá uma identidade verificável:

       receita − gasto − poupança + resgate ≈ 0

   Mês que não fecha tem lançamento faltando — e a aba mostra quais.

3. **O formato é irregular.** O cabeçalho declara 5 colunas e as linhas têm 8.
   Valores vêm como `R$ 1,234.56` e os negativos como `-R$ 0.27` (com o sinal
   ANTES do símbolo). A coluna de data às vezes é `Feb-12`, às vezes
   `02/15/2019`, às vezes vazia — mas há colunas de mês e ano numéricos que são
   confiáveis. O leitor prefere o confiável e cai para o irregular só quando
   precisa.

Nada aqui persiste nada nem depende da rede: entra texto, saem números.
"""

from __future__ import annotations

import csv
import io
import re
import statistics
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

import yaml

# Papéis. Só `gasto` entra na conta de "quanto gastei".
GASTO = "gasto"
RECEITA = "receita"
CARREGAMENTO = "carregamento"
ARTEFATO = "artefato"
INVESTIMENTO = "investimento"

MESES_PT = ["jan", "fev", "mar", "abr", "mai", "jun",
            "jul", "ago", "set", "out", "nov", "dez"]
MESES_EN = ["jan", "feb", "mar", "apr", "may", "jun",
            "jul", "aug", "sep", "oct", "nov", "dec"]


class AnalyticsError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

@dataclass
class AnalyticsConfig:
    papel_por_categoria: dict[str, str] = field(default_factory=dict)
    # Categorias que são gasto real mas ainda não foram abertas em categorias
    # de verdade — a fatura do cartão lançada numa linha só.
    nao_detalhado: set[str] = field(default_factory=set)
    sinonimos: dict[str, str] = field(default_factory=dict)
    marcador_cartao: str = "[Cartão]"
    recorrente_minimo: int = 6
    recorrente_janela: int = 4
    anomalia_desvios: float = 3.5
    anomalia_minimo_meses: int = 6
    anomalia_minimo_valor: float = 200.0

    @classmethod
    def from_text(cls, text: str) -> "AnalyticsConfig":
        raw = yaml.safe_load(text) or {}
        if not isinstance(raw, dict):
            raise AnalyticsError("analytics.yml precisa ser um mapa YAML")

        papel: dict[str, str] = {}
        for nome, categorias in (raw.get("papeis") or {}).items():
            for categoria in categorias or []:
                papel[_chave(categoria)] = str(nome)

        cfg = cls()
        rec = raw.get("recorrentes") or {}
        ano = raw.get("anomalias") or {}
        return cls(
            papel_por_categoria=papel,
            nao_detalhado={_chave(c) for c in (raw.get("nao_detalhado") or [])},
            sinonimos={_chave(k): str(v) for k, v in (raw.get("sinonimos") or {}).items()},
            marcador_cartao=str((raw.get("cartao") or {}).get("marcador", cfg.marcador_cartao)),
            recorrente_minimo=int(rec.get("minimo_ocorrencias", cfg.recorrente_minimo)),
            recorrente_janela=int(rec.get("meses_recentes", cfg.recorrente_janela)),
            anomalia_desvios=float(ano.get("desvios", cfg.anomalia_desvios)),
            anomalia_minimo_meses=int(ano.get("minimo_meses", cfg.anomalia_minimo_meses)),
            anomalia_minimo_valor=float(ano.get("minimo_valor", cfg.anomalia_minimo_valor)),
        )

    def papel(self, categoria: str) -> str:
        """Categoria desconhecida é GASTO: é o caso comum e o erro é nulo."""
        return self.papel_por_categoria.get(_chave(categoria), GASTO)

    def generica(self, categoria: str) -> bool:
        return _chave(categoria) in self.nao_detalhado

    def canonica(self, categoria: str) -> str:
        return self.sinonimos.get(_chave(categoria), categoria.strip())


def _chave(texto: str) -> str:
    """Compara categorias sem depender de acento, caixa ou espaço sobrando."""
    t = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", t).strip().upper()


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Lancamento:
    ano: int | None
    mes: int | None
    categoria: str
    descricao: str
    valor: float
    papel: str
    cartao: bool
    # Gasto real cuja categoria verdadeira é desconhecida (fatura não aberta).
    nao_detalhado: bool = False

    @property
    def periodo(self) -> str | None:
        if self.ano is None or self.mes is None:
            return None
        return f"{self.ano:04d}-{self.mes:02d}"


# `-R$ 1.234,56` / `R$ 1,234.56` / `(R$ 10)` — o sinal pode vir antes do símbolo.
_LIMPA = re.compile(r"[^\d,.\-]")


def parse_valor(bruto: str) -> float | None:
    """Número a partir do que o Sheets exporta.

    Devolve `None` em vez de levantar: uma célula ilegível numa planilha de 14
    anos é normal, e derrubar a análise inteira por causa dela seria pior do que
    contá-la como faltante e avisar.
    """
    if bruto is None:
        return None
    texto = str(bruto).strip()
    if not texto:
        return None
    negativo = texto.startswith("-") or (texto.startswith("(") and texto.endswith(")"))
    limpo = _LIMPA.sub("", texto).lstrip("-")
    if not limpo:
        return None

    # Qual separador é o decimal: o ÚLTIMO que aparecer, se sobrar 1-2 dígitos.
    ultimo_ponto, ultima_virgula = limpo.rfind("."), limpo.rfind(",")
    corte = max(ultimo_ponto, ultima_virgula)
    if corte >= 0 and len(limpo) - corte - 1 in (1, 2):
        inteiro = re.sub(r"[.,]", "", limpo[:corte])
        decimal = limpo[corte + 1:]
        limpo = f"{inteiro}.{decimal}"
    else:
        limpo = re.sub(r"[.,]", "", limpo)

    try:
        valor = float(limpo)
    except ValueError:
        return None
    return -valor if negativo else valor


def parse_periodo(data: str, mes: str = "", ano: str = "") -> tuple[int | None, int | None]:
    """(ano, mês). As colunas numéricas mandam; a coluna Data é o plano B.

    A coluna `Data` mistura `Feb-12`, `02/15/2019` e vazio ao longo dos anos.
    `Mês`/`Ano` são gerados por fórmula e não têm essa variação, então quando
    existem são a fonte melhor.
    """
    def inteiro(x, minimo, maximo):
        try:
            v = int(str(x).strip())
        except (ValueError, TypeError):
            return None
        return v if minimo <= v <= maximo else None

    a, m = inteiro(ano, 1900, 2200), inteiro(mes, 1, 12)
    if a and m:
        return a, m

    texto = (data or "").strip()
    if not texto:
        return a, m

    # 02/15/2019 ou 15/02/2019
    numeros = re.findall(r"\d+", texto)
    if len(numeros) >= 3 and len(numeros[2]) == 4:
        return int(numeros[2]), m or inteiro(numeros[0], 1, 12) or inteiro(numeros[1], 1, 12)

    # Feb-12 / fev-12 / Aug
    nome = re.match(r"([A-Za-zçÇãÃéÉ]{3,})", texto)
    if nome:
        prefixo = _chave(nome.group(1))[:3].lower()
        for tabela in (MESES_EN, MESES_PT):
            if prefixo in tabela:
                m = m or (tabela.index(prefixo) + 1)
                break
        sufixo = re.search(r"-(\d{2,4})$", texto)
        if sufixo and not a:
            n = int(sufixo.group(1))
            a = n if n > 100 else 2000 + n
    return a, m


_ALIASES = {
    "data": ("data", "date"),
    "categoria": ("categoria", "category"),
    "descricao": ("descricao", "item", "descrição", "description", "historico"),
    "valor": ("valor", "valor (r$)", "amount", "value"),
    "pago": ("pago", "paid"),
    "mes": ("mes", "mês", "month"),
    "ano": ("ano", "year"),
}


def _mapear_colunas(cabecalho: list[str], largura: int) -> dict[str, int]:
    """Papel -> índice da coluna.

    O `all.csv` declara 5 nomes e traz 8 valores por linha: as três últimas
    (Mês, Ano, Filtro) ficaram de fora quando o cabeçalho foi reescrito. Em vez
    de exigir que o arquivo seja consertado, as colunas conhecidas são
    localizadas pelo nome e as extras entram por convenção posicional.
    """
    indices: dict[str, int] = {}
    normalizado = [_chave(c).lower() for c in cabecalho]
    for papel, nomes in _ALIASES.items():
        for i, nome in enumerate(normalizado):
            if nome in [_chave(n).lower() for n in nomes]:
                indices[papel] = i
                break

    # Convenção do Sheets: depois de `Pago` vêm Mês, Ano e o auxiliar Filtro.
    if "mes" not in indices and largura > len(cabecalho):
        base = len(cabecalho)
        if base < largura:
            indices["mes"] = base
        if base + 1 < largura:
            indices["ano"] = base + 1
    return indices


def read_ledger(texto: str, cfg: AnalyticsConfig) -> tuple[list[Lancamento], list[str]]:
    """Lê o CSV histórico. Devolve os lançamentos e os avisos do que não deu."""
    linhas = [r for r in csv.reader(io.StringIO(texto)) if any(c.strip() for c in r)]
    if not linhas:
        raise AnalyticsError("arquivo vazio")

    # O cabeçalho pode não ser a primeira linha (a planilha tem títulos acima).
    idx_cab = 0
    for i, linha in enumerate(linhas[:10]):
        if any(_chave(c).lower() in ("categoria", "category") for c in linha):
            idx_cab = i
            break
    cabecalho, corpo = linhas[idx_cab], linhas[idx_cab + 1:]
    if not corpo:
        raise AnalyticsError("nenhuma linha de dados abaixo do cabeçalho")

    largura = Counter(len(r) for r in corpo).most_common(1)[0][0]
    col = _mapear_colunas(cabecalho, largura)
    faltando = [p for p in ("categoria", "descricao", "valor") if p not in col]
    if faltando:
        raise AnalyticsError(
            f"não achei as colunas {faltando}. O cabeçalho lido foi {cabecalho}")

    def celula(linha, papel, padrao=""):
        i = col.get(papel)
        return linha[i] if i is not None and i < len(linha) else padrao

    lancamentos: list[Lancamento] = []
    ilegiveis = 0
    for numero, linha in enumerate(corpo, start=idx_cab + 2):
        valor = parse_valor(celula(linha, "valor"))
        descricao = celula(linha, "descricao").strip()
        categoria_bruta = celula(linha, "categoria").strip()
        if valor is None:
            if descricao or categoria_bruta:
                ilegiveis += 1
            continue

        ano, mes = parse_periodo(celula(linha, "data"), celula(linha, "mes"),
                                 celula(linha, "ano"))
        categoria = cfg.canonica(categoria_bruta)
        lancamentos.append(Lancamento(
            ano=ano, mes=mes, categoria=categoria, descricao=descricao,
            valor=valor, papel=cfg.papel(categoria),
            cartao=cfg.marcador_cartao.lower() in descricao.lower(),
            nao_detalhado=cfg.generica(categoria),
        ))

    avisos: list[str] = []
    if ilegiveis:
        avisos.append(f"{ilegiveis} linha(s) com valor ilegível foram ignoradas.")
    if not lancamentos:
        raise AnalyticsError("nenhuma linha com valor legível")
    return lancamentos, avisos


# ---------------------------------------------------------------------------
# Agregações
# ---------------------------------------------------------------------------

def _soma(itens: Iterable[Lancamento]) -> float:
    return round(sum(i.valor for i in itens), 2)


def periodos(lancamentos: list[Lancamento]) -> list[str]:
    return sorted({l.periodo for l in lancamentos if l.periodo})


def serie_mensal(lancamentos: list[Lancamento]) -> list[dict]:
    """Gasto, receita e carregamento por mês — a espinha dorsal da aba.

    Meses sem lançamento NÃO entram com zero: o gráfico precisa poder desenhar
    buraco onde não há dado. Zerar faria a linha despencar até o chão e sugerir
    um mês de austeridade que nunca existiu (o histórico tem 23 meses assim).
    """
    balde: dict[str, list[Lancamento]] = defaultdict(list)
    for l in lancamentos:
        if l.periodo:
            balde[l.periodo].append(l)

    saida = []
    for periodo in sorted(balde):
        itens = balde[periodo]
        gasto = [i for i in itens if i.papel == GASTO]
        saida.append({
            "periodo": periodo,
            "gasto": _soma(gasto),
            "receita": _soma(i for i in itens if i.papel == RECEITA),
            "investimento": _soma(i for i in itens if i.papel == INVESTIMENTO),
            "carregamento": _soma(i for i in itens if i.papel == CARREGAMENTO),
            "no_cartao": _soma(i for i in gasto if i.cartao),
            "nao_detalhado": _soma(i for i in gasto if i.nao_detalhado),
            "lancamentos": len(itens),
        })
    return saida


def por_categoria(lancamentos: list[Lancamento], papel: str = GASTO) -> list[dict]:
    balde: dict[str, list[Lancamento]] = defaultdict(list)
    for l in lancamentos:
        if l.papel == papel:
            balde[l.categoria or "(sem categoria)"].append(l)

    total = sum(_soma(v) for v in balde.values()) or 1.0
    saida = [{
        "categoria": categoria,
        "total": _soma(itens),
        "lancamentos": len(itens),
        "media": round(_soma(itens) / len(itens), 2),
        "share": round(_soma(itens) / total, 4),
        "no_cartao": _soma(i for i in itens if i.cartao),
    } for categoria, itens in balde.items()]
    return sorted(saida, key=lambda d: -d["total"])


def categoria_por_periodo(lancamentos: list[Lancamento], top: int = 7) -> dict:
    """Série empilhável: as `top` maiores categorias, o resto virando "Outras".

    Dobrar a cauda em "Outras" não é economia de espaço — é a regra de cores.
    A paleta tem 8 posições fixas e validadas; uma 9ª série exigiria inventar
    um tom, e tons inventados quebram a separação para daltônicos.
    """
    maiores = [c["categoria"] for c in por_categoria(lancamentos)[:top]]
    conhecidas = set(maiores)

    balde: dict[tuple[str, str], float] = defaultdict(float)
    for l in lancamentos:
        if l.papel != GASTO or not l.periodo:
            continue
        nome = l.categoria if l.categoria in conhecidas else "Outras"
        balde[(l.periodo, nome)] += l.valor

    nomes = maiores + (["Outras"] if any(k[1] == "Outras" for k in balde) else [])
    return {
        "categorias": nomes,
        "periodos": [
            {"periodo": p,
             "valores": [round(balde.get((p, n), 0.0), 2) for n in nomes]}
            for p in sorted({k[0] for k in balde})
        ],
    }


def sazonalidade(lancamentos: list[Lancamento]) -> dict:
    """Gasto por mês-do-ano × ano — onde dezembro aparece como dezembro."""
    balde: dict[tuple[int, int], float] = defaultdict(float)
    for l in lancamentos:
        if l.papel == GASTO and l.ano and l.mes:
            balde[(l.ano, l.mes)] += l.valor

    anos = sorted({a for a, _ in balde})
    return {
        "anos": anos,
        "celulas": [{"ano": a, "mes": m, "total": round(v, 2)}
                    for (a, m), v in sorted(balde.items())],
        "media_por_mes": [
            round(statistics.mean(vs), 2) if (vs := [v for (_, m), v in balde.items()
                                                     if m == mes]) else 0.0
            for mes in range(1, 13)
        ],
    }


def top_estabelecimentos(lancamentos: list[Lancamento], limite: int = 15) -> list[dict]:
    """Agrupa pela descrição normalizada — "Mercado da Semana" e "MERCADO DA
    SEMANA " são o mesmo lugar."""
    balde: dict[str, list[Lancamento]] = defaultdict(list)
    for l in lancamentos:
        if l.papel == GASTO and l.descricao.strip():
            balde[_chave(l.descricao)].append(l)

    saida = [{
        "descricao": max((i.descricao.strip() for i in itens), key=len),
        "total": _soma(itens),
        "lancamentos": len(itens),
        "categoria": Counter(i.categoria for i in itens).most_common(1)[0][0],
    } for itens in balde.values()]
    return sorted(saida, key=lambda d: -d["total"])[:limite]


def recorrentes(lancamentos: list[Lancamento], cfg: AnalyticsConfig) -> list[dict]:
    """O que se repete mês a mês — o custo que existe antes de você decidir nada.

    Não basta repetir: tem que repetir em MESES diferentes. Cinco compras no
    mesmo mercado na mesma semana não são uma assinatura, e contá-las como tal
    inflaria o "custo fixo" com gasto perfeitamente variável.
    """
    balde: dict[str, list[Lancamento]] = defaultdict(list)
    for l in lancamentos:
        # `nao_detalhado` fica de fora: "Fatural do mês atual" aparece todo mês e
        # passaria por assinatura, mas é um BALDE de gasto variável, não um
        # custo fixo. Incluí-lo somava R$ 8 mil ao "custo fixo mensal" e tornava
        # o número inútil justamente para a pergunta que ele responde — quanto
        # do mês já está comprometido antes de eu decidir qualquer coisa.
        if l.nao_detalhado:
            continue
        if l.papel == GASTO and l.periodo and l.descricao.strip():
            balde[_chave(l.descricao)].append(l)

    todos = periodos(lancamentos)
    recentes = set(todos[-cfg.recorrente_janela:]) if todos else set()

    saida = []
    for itens in balde.values():
        meses = sorted({i.periodo for i in itens})
        if len(meses) < cfg.recorrente_minimo:
            continue

        por_mes = defaultdict(float)
        for i in itens:
            por_mes[i.periodo] += i.valor
        valores = [por_mes[m] for m in meses]

        # Primeiro e último terço: mede a tendência sem deixar um mês atípico
        # das pontas ditar o resultado.
        corte = max(1, len(valores) // 3)
        inicio = statistics.median(valores[:corte])
        fim = statistics.median(valores[-corte:])

        saida.append({
            "descricao": max((i.descricao.strip() for i in itens), key=len),
            "categoria": Counter(i.categoria for i in itens).most_common(1)[0][0],
            "meses": len(meses),
            "primeiro": meses[0],
            "ultimo": meses[-1],
            "ativo": bool(recentes & set(meses)),
            "mediana": round(statistics.median(valores), 2),
            # A mediana da série INTEIRA mistura preço de 2012 com preço de
            # 2026: a luz sai R$ 172 quando hoje custa R$ 500. Para a pergunta
            # "quanto do meu mês já está comprometido", o que vale é o preço de
            # agora — por isso o custo fixo usa esta, e não aquela.
            "mediana_recente": round(statistics.median(valores[-12:]), 2),
            "total": _soma(itens),
            "variacao": round((fim - inicio) / inicio, 4) if inicio else 0.0,
        })
    return sorted(saida, key=lambda d: -d["mediana"])


def custo_fixo_mensal(recorrentes_ativos: list[dict]) -> float:
    """Quanto do mês já está comprometido, a PREÇO DE HOJE.

    Somar a mediana histórica subestimaria: uma conta de luz que começou em
    R$ 70 e hoje é R$ 500 entraria com a mediana dos 14 anos.
    """
    return round(sum(r.get("mediana_recente", r["mediana"])
                     for r in recorrentes_ativos if r["ativo"]), 2)


def anomalias(lancamentos: list[Lancamento], cfg: AnalyticsConfig) -> list[dict]:
    """Meses em que uma categoria fugiu da PRÓPRIA mediana.

    A referência é a história daquela categoria, nunca as outras: R$ 800 é
    rotina em Casa e gritante em Cachorro.

    A dispersão é medida em MAD (desvio absoluto mediano) e não em desvio
    padrão, porque o desvio padrão é inflado pelo próprio outlier que se está
    procurando — um mês absurdo aumenta o desvio o bastante para se esconder
    dentro dele.
    """
    balde: dict[tuple[str, str], float] = defaultdict(float)
    for l in lancamentos:
        if l.papel == GASTO and l.periodo:
            balde[(l.categoria, l.periodo)] += l.valor

    por_categoria_: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for (categoria, periodo), total in balde.items():
        por_categoria_[categoria].append((periodo, total))

    achados = []
    for categoria, series in por_categoria_.items():
        if len(series) < cfg.anomalia_minimo_meses:
            continue
        valores = [v for _, v in series]
        mediana = statistics.median(valores)
        distancias = [abs(v - mediana) for v in valores]

        # MAD é robusto justamente porque ignora os extremos — mas quando a
        # série é quase constante com UM pico (ração de R$ 80 todo mês e uma
        # cirurgia de R$ 800), mais da metade das distâncias é zero e o MAD zera
        # junto. A anomalia mais óbvia que existe passaria batida.
        # O desvio absoluto médio serve de rede: ainda resiste bem a um único
        # outlier e nunca zera enquanto houver qualquer variação.
        dispersao = 1.4826 * statistics.median(distancias)
        if dispersao == 0:
            dispersao = statistics.mean(distancias)
        if dispersao == 0:
            continue   # série perfeitamente constante: não há o que destoar

        for periodo, total in series:
            desvio = (total - mediana) / dispersao
            if desvio >= cfg.anomalia_desvios and total - mediana >= cfg.anomalia_minimo_valor:
                achados.append({
                    "categoria": categoria, "periodo": periodo,
                    "total": round(total, 2), "mediana": round(mediana, 2),
                    "excesso": round(total - mediana, 2),
                    "desvios": round(desvio, 1),
                })
    return sorted(achados, key=lambda d: -d["excesso"])


def comparativo_anual(lancamentos: list[Lancamento]) -> list[dict]:
    balde: dict[int, list[Lancamento]] = defaultdict(list)
    for l in lancamentos:
        if l.ano:
            balde[l.ano].append(l)

    saida = []
    anterior = None
    for ano in sorted(balde):
        itens = balde[ano]
        gasto = _soma(i for i in itens if i.papel == GASTO)
        meses = len({i.mes for i in itens if i.mes})
        saida.append({
            "ano": ano,
            "gasto": gasto,
            "receita": _soma(i for i in itens if i.papel == RECEITA),
            "meses_com_dado": meses,
            "media_mensal": round(gasto / meses, 2) if meses else 0.0,
            "variacao": round((gasto - anterior) / anterior, 4) if anterior else None,
        })
        anterior = gasto or anterior
    return saida


def contribuicao_da_variacao(lancamentos: list[Lancamento], ano_a: int,
                             ano_b: int, limite: int = 8) -> list[dict]:
    """Quais categorias explicam a diferença de gasto entre dois anos.

    "Gastei R$ 20 mil a mais" só vira ação quando se sabe de onde veio.
    """
    def totais(ano):
        balde = defaultdict(float)
        for l in lancamentos:
            if l.papel == GASTO and l.ano == ano:
                balde[l.categoria] += l.valor
        return balde

    a, b = totais(ano_a), totais(ano_b)
    deltas = [{"categoria": c, "de": round(a.get(c, 0.0), 2),
               "para": round(b.get(c, 0.0), 2),
               "delta": round(b.get(c, 0.0) - a.get(c, 0.0), 2)}
              for c in set(a) | set(b)]
    return sorted(deltas, key=lambda d: -abs(d["delta"]))[:limite]


def concentracao(lancamentos: list[Lancamento]) -> dict:
    """Quanto do gasto está nas poucas maiores linhas."""
    valores = sorted((l.valor for l in lancamentos if l.papel == GASTO), reverse=True)
    total = sum(valores) or 1.0
    def fatia(n):
        return round(sum(valores[:n]) / total, 4)
    return {
        "lancamentos": len(valores),
        "top_10": fatia(10), "top_50": fatia(50),
        "top_1_pct": fatia(max(1, len(valores) // 100)),
        "mediana": round(statistics.median(valores), 2) if valores else 0.0,
    }


# ---------------------------------------------------------------------------
# Saúde dos dados
# ---------------------------------------------------------------------------

def meses_faltando(lancamentos: list[Lancamento]) -> list[str]:
    todos = periodos(lancamentos)
    if len(todos) < 2:
        return []
    def par(p): return int(p[:4]), int(p[5:])
    (a1, m1), (a2, m2) = par(todos[0]), par(todos[-1])
    existentes, vazios = set(todos), []
    while (a1, m1) <= (a2, m2):
        chave = f"{a1:04d}-{m1:02d}"
        if chave not in existentes:
            vazios.append(chave)
        m1 += 1
        if m1 == 13:
            a1, m1 = a1 + 1, 1
    return vazios


def meses_que_nao_fecham(lancamentos: list[Lancamento],
                         tolerancia: float = 1.0) -> list[dict]:
    """A identidade do mês zerado: receita − gasto − poupança + resgate ≈ 0.

    É a única checagem aqui capaz de apontar lançamento FALTANDO. Todas as
    outras só enxergam o que está escrito; esta enxerga o buraco.
    """
    balde: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for l in lancamentos:
        if not l.periodo:
            continue
        if l.papel == CARREGAMENTO:
            chave = "resgate" if "RESGATE" in _chave(l.categoria) else "poupanca"
        else:
            chave = l.papel
        balde[l.periodo][chave] += l.valor

    fora = []
    for periodo, v in sorted(balde.items()):
        if not v.get("receita") and not v.get("poupanca") and not v.get("resgate"):
            continue   # mês só de gastos: a identidade não se aplica
        saldo = (v.get("receita", 0.0) - v.get(GASTO, 0.0) - v.get(INVESTIMENTO, 0.0)
                 - v.get("poupanca", 0.0) + v.get("resgate", 0.0))
        if abs(saldo) > tolerancia:
            fora.append({
                "periodo": periodo, "saldo": round(saldo, 2),
                # O SINAL é a informação, e é o que diz o que fazer:
                #
                #   saldo < 0  saiu mais do que entrou. O gasto foi pago com
                #              dinheiro que não está lançado — quase sempre um
                #              "Resgate da poupança" que faltou registrar.
                #   saldo > 0  entrou mais do que saiu e a sobra não foi parada
                #              em lugar nenhum: falta a linha de Poupança.
                "tipo": "falta_resgate" if saldo < 0 else "sobra_sem_destino",
                "receita": round(v.get("receita", 0.0), 2),
                "gasto": round(v.get(GASTO, 0.0), 2),
                "poupanca": round(v.get("poupanca", 0.0), 2),
                "resgate": round(v.get("resgate", 0.0), 2),
            })
    return fora


def pares_que_se_anulam(nao_fecham: list[dict], tolerancia: float = 0.02) -> list[dict]:
    """Dois meses cujos resíduos são iguais e opostos.

    Quando jan sobra R$ 45 e fev falta R$ 45, não há dinheiro sumido: há um
    lançamento no mês errado. É a diferença entre "confira estes dois meses" e
    "confira estes sessenta e nove", e sai de graça a partir dos resíduos.
    """
    pendentes = list(nao_fecham)
    achados = []
    for i, a in enumerate(pendentes):
        for b in pendentes[i + 1:]:
            if abs(a["saldo"] + b["saldo"]) <= tolerancia and abs(a["saldo"]) > tolerancia:
                achados.append({"a": a["periodo"], "b": b["periodo"],
                                "valor": round(abs(a["saldo"]), 2)})
    return sorted(achados, key=lambda d: -d["valor"])[:10]


def possivel_dupla_contagem(lancamentos: list[Lancamento]) -> list[dict]:
    """Meses em que a fatura aparece itemizada E agregada.

    A planilha registra o cartão de UM jeito por mês: ou a linha única
    ("Fatural do mês atual"), ou os itens vindos do parser, cada um com
    `[Cartão]`. Os dois juntos somariam a mesma compra duas vezes.

    No histórico isso nunca aconteceu — foi conferido mês a mês antes de decidir
    contar a linha da fatura como gasto. Mas basta colar um extrato num mês que
    já tem a linha agregada para acontecer, e o total do mês dobraria em
    silêncio. Por isso a condição é verificada a cada análise em vez de suposta.
    """
    itens: dict[str, float] = defaultdict(float)
    fatura: dict[str, float] = defaultdict(float)
    for l in lancamentos:
        if not l.periodo:
            continue
        if l.cartao:
            itens[l.periodo] += l.valor
        elif "CARTAO DE CREDITO" in _chave(l.categoria):
            fatura[l.periodo] += l.valor

    return [{"periodo": p, "itens": round(itens[p], 2), "fatura": round(fatura[p], 2)}
            for p in sorted(set(itens) & set(fatura))]


def saude(lancamentos: list[Lancamento], avisos: list[str]) -> dict:
    sem_categoria = [l for l in lancamentos if not l.categoria.strip()]
    sem_data = [l for l in lancamentos if not l.periodo]
    # Maior resíduo primeiro: um mês com R$ 55 mil em aberto importa mais do que
    # sessenta com centavos de diferença.
    nao_fecham = sorted(meses_que_nao_fecham(lancamentos),
                        key=lambda d: -abs(d["saldo"]))
    return {
        "total_lancamentos": len(lancamentos),
        "avisos": avisos,
        "sem_categoria": {"quantidade": len(sem_categoria),
                          "total": _soma(sem_categoria),
                          "exemplos": [l.descricao for l in sem_categoria[:6]]},
        "sem_data": {"quantidade": len(sem_data), "total": _soma(sem_data),
                     "exemplos": [l.descricao for l in sem_data[:6]]},
        "meses_faltando": meses_faltando(lancamentos),
        "meses_que_nao_fecham": nao_fecham,
        "total_meses_que_nao_fecham": len(nao_fecham),
        "pares_que_se_anulam": pares_que_se_anulam(nao_fecham),
        "dupla_contagem": possivel_dupla_contagem(lancamentos),
    }


# ---------------------------------------------------------------------------
# Tudo junto
# ---------------------------------------------------------------------------

def recortar(lancamentos: list[Lancamento], inicio: str | None,
             fim: str | None) -> list[Lancamento]:
    """Mantém só o que cai no intervalo (YYYY-MM, inclusivo nas duas pontas).

    Lançamento SEM data fica de fora de qualquer recorte: ele não pertence a
    período nenhum, e deixá-lo passar faria a soma de "últimos 12 meses" incluir
    uma compra de terreno de 2021 sem data. Ele continua contabilizado no painel
    de saúde, que é onde a ausência de data importa.
    """
    if not inicio and not fim:
        return lancamentos
    return [l for l in lancamentos if l.periodo
            and (not inicio or l.periodo >= inicio)
            and (not fim or l.periodo <= fim)]


def analisar(texto: str, cfg: AnalyticsConfig, inicio: str | None = None,
             fim: str | None = None) -> dict:
    completos, avisos = read_ledger(texto, cfg)

    # Os limites do ARQUIVO, calculados antes do recorte: é o que o seletor de
    # datas usa para não oferecer um período que não existe.
    disponivel = periodos(completos)

    lancamentos = recortar(completos, inicio, fim)
    if not lancamentos:
        raise AnalyticsError(
            f"nenhum lançamento entre {inicio or 'início'} e {fim or 'fim'}. "
            f"O arquivo cobre {disponivel[0]} a {disponivel[-1]}."
            if disponivel else "nenhum lançamento no período")

    todos = periodos(lancamentos)
    gastos = [l for l in lancamentos if l.papel == GASTO]

    rec = recorrentes(lancamentos, cfg)
    anual = comparativo_anual(lancamentos)
    anos = [a["ano"] for a in anual]

    total_gasto = _soma(gastos)
    meses_com_gasto = len({l.periodo for l in gastos if l.periodo}) or 1

    return {
        "intervalo_disponivel": {
            "inicio": disponivel[0] if disponivel else None,
            "fim": disponivel[-1] if disponivel else None,
        },
        "filtro": {"inicio": inicio, "fim": fim},
        "resumo": {
            "periodo_inicio": todos[0] if todos else None,
            "periodo_fim": todos[-1] if todos else None,
            "meses_com_dado": len(todos),
            "total_gasto": total_gasto,
            "media_mensal": round(total_gasto / meses_com_gasto, 2),
            "total_receita": _soma(l for l in lancamentos if l.papel == RECEITA),
            "total_investido": _soma(l for l in lancamentos if l.papel == INVESTIMENTO),
            # Quanto do gasto passou pelo cartão — a fatia que este portal
            # categoriza sozinho, contra o que é pix, boleto e débito.
            "gasto_no_cartao": _soma(l for l in gastos if l.cartao),
            "custo_fixo_mensal": custo_fixo_mensal(rec),
            # Gasto que aconteceu de verdade mas está num balde genérico: a
            # fatura lançada numa linha só, sem abrir em categorias. É o teto do
            # erro de qualquer leitura por categoria.
            "gasto_nao_detalhado": _soma(l for l in gastos if l.nao_detalhado),
            "meses_nao_detalhados": sorted({l.periodo for l in gastos
                                            if l.nao_detalhado and l.periodo}),
            # O que foi excluído da conta, e por quê — para o número de cima ser
            # auditável em vez de mágico.
            "excluido": {
                "artefato": _soma(l for l in lancamentos if l.papel == ARTEFATO),
                "carregamento": _soma(l for l in lancamentos if l.papel == CARREGAMENTO),
            },
        },
        "serie_mensal": serie_mensal(lancamentos),
        "por_categoria": por_categoria(lancamentos),
        "categoria_por_periodo": categoria_por_periodo(lancamentos),
        "sazonalidade": sazonalidade(lancamentos),
        "top_estabelecimentos": top_estabelecimentos(lancamentos),
        "recorrentes": rec[:40],
        "anomalias": anomalias(lancamentos, cfg)[:20],
        "anual": anual,
        "variacao_recente": (contribuicao_da_variacao(lancamentos, anos[-2], anos[-1])
                             if len(anos) >= 2 else []),
        "concentracao": concentracao(lancamentos),
        "saude": saude(lancamentos, avisos),
    }
