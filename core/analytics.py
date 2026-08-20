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

# A limpeza do que o Sheets exporta (título acima do cabeçalho, coluna de
# margem, número formatado como moeda) mora em `planilha` porque a
# Recategorização usa exatamente a mesma — não uma segunda parecida.
from .planilha import (
    chave as _chave, ler_tabela, parse_valor, tabela_da_primeira_linha,
)

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
    # Dentro de `carregamento`, o que a DESCRIÇÃO diz que a linha é. Ver
    # `movimento()` — a categoria sozinha não separa essas três coisas.
    padroes_carry: tuple[str, ...] = ()
    padroes_aplicacao: tuple[str, ...] = ()

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
        mov = raw.get("movimentos") or {}
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
            padroes_carry=tuple(_chave(p) for p in (mov.get("carry") or [])),
            padroes_aplicacao=tuple(_chave(p) for p in (mov.get("aplicacao") or [])),
        )

    def papel(self, categoria: str) -> str:
        """Categoria desconhecida é GASTO: é o caso comum e o erro é nulo."""
        return self.papel_por_categoria.get(_chave(categoria), GASTO)

    def generica(self, categoria: str) -> bool:
        return _chave(categoria) in self.nao_detalhado

    def canonica(self, categoria: str) -> str:
        return self.sinonimos.get(_chave(categoria), categoria.strip())

    def movimento(self, categoria: str, descricao: str) -> str:
        """Que TIPO de carregamento é esta linha: carry, aplicação ou reserva.

        `Poupança` e `Resgate Poupança` guardam três mecanismos diferentes na
        mesma categoria, e só a descrição os separa:

          carry      "Transferido para o próximo mês" / "Resgatado do mês
                     anterior" — o mês sendo zerado. Sai de um mês e entra no
                     seguinte pelo mesmo valor, então dá para CONFERIR a
                     corrente elo a elo.
          aplicacao  "Resgate Aplicação Sicredi", "Resgate Rico" — dinheiro
                     voltando de investimento. O aporte correspondente está em
                     `Investimento`, não em `Poupança`; sem separar, a poupança
                     aparece com saldo negativo de R$ 349 mil, que é a diferença
                     entre o que voltou de aplicação e o que foi guardado.
          reserva    o resto: caixinha com objetivo escrito na descrição
                     ("PS5", "Viagem", "Reserva de Emergência").

        Todos continuam com papel `carregamento` — nada aqui muda a identidade
        do mês zerado, que hoje fecha com resíduo mediano de R$ 117. Isto é
        rótulo para leitura, não reclassificação contábil.
        """
        if self.papel(categoria) != CARREGAMENTO:
            return ""
        chave = _chave(descricao)
        if any(p in chave for p in self.padroes_carry):
            return "carry"
        if any(p in chave for p in self.padroes_aplicacao):
            return "aplicacao"
        return "reserva"




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
    # De qual arquivo esta linha veio. Existe porque a aba aceita mais de um
    # CSV e eles são de PESSOAS diferentes — a análise do casal só faz sentido
    # se der para dizer quem trouxe e quem gastou. Vazio com um arquivo só.
    fonte: str = ""

    @property
    def titular(self) -> str:
        """Quem passou o cartão, lido do `<Nome>` no fim da descrição.

        A marca é posta na IMPORTAÇÃO (ver `pipeline.build_description`), então
        aqui ela chega como texto e sai como texto — não há coluna a criar na
        planilha nem formato novo a manter. Vazio quer dizer "sem marca": as
        compras de quem se identificou como "eu" no upload, e todo o histórico
        anterior a este recurso existir.
        """
        achado = TITULAR_RE.search(self.descricao)
        return achado.group(1).strip() if achado else ""

    @property
    def periodo(self) -> str | None:
        if self.ano is None or self.mes is None:
            return None
        return f"{self.ano:04d}-{self.mes:02d}"


# " <Rhyesla>" no FIM da descrição. Ancorado no fim de propósito: um `<` no meio
# do nome de um estabelecimento não é marca de titular, e sem a âncora
# "[Cartão] Loja <3 {Em 3/Jan}" viraria um titular chamado "3".
TITULAR_RE = re.compile(r"<([^<>]+)>\s*$")

# O balde de quem NÃO tem marca precisa de um nome para viajar pela rede: o
# campo de formulário é uma lista separada por quebra de linha, e linha vazia é
# descartada junto com o resto do espaço em branco. Os sinais de maior e menor
# tornam a colisão impossível, e não improvável: `TITULAR_RE` recusa `<` e `>`
# dentro do nome, então nenhum titular de verdade pode se chamar assim.
SEM_TITULAR = "<sem marca>"


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


def read_ledger(texto: str, cfg: AnalyticsConfig,
                fonte: str = "") -> tuple[list[Lancamento], list[str]]:
    """Lê o CSV histórico. Devolve os lançamentos e os avisos do que não deu."""
    # A limpeza do que o Sheets exporta é a MESMA da Recategorização (ver
    # `core/planilha.py`): título acima do cabeçalho, coluna de margem à
    # esquerda, número formatado como moeda.
    # Sem coluna Categoria em lugar nenhum, tenta a primeira linha como
    # cabeçalho: é o formato antigo, sem título nenhum em cima.
    tabela = ler_tabela(texto) or tabela_da_primeira_linha(texto)
    if tabela is None:
        raise AnalyticsError("arquivo vazio")
    idx_cab = tabela.linha_do_cabecalho - 1
    cabecalho, corpo = tabela.cabecalho, tabela.linhas
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
            fonte=fonte,
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


def _e_resgate(categoria: str) -> bool:
    """Dentro de `carregamento`, resgate é dinheiro voltando; o resto é guardar."""
    return "RESGATE" in _chave(categoria)


def _proximo_mes(periodo: str) -> str:
    ano, mes = int(periodo[:4]), int(periodo[5:]) + 1
    return f"{ano + 1}-01" if mes == 13 else f"{ano}-{mes:02d}"


def _mes_anterior(periodo: str) -> str:
    ano, mes = int(periodo[:4]), int(periodo[5:]) - 1
    return f"{ano - 1}-12" if mes == 0 else f"{ano}-{mes:02d}"


def corrente_do_carry(lancamentos: list[Lancamento], cfg: AnalyticsConfig,
                      tolerancia: float = 1.0) -> dict:
    """Confere a corrente do saldo: o que sai de um mês tem que entrar no outro.

    Esta é a checagem mais barata e mais afiada do arquivo inteiro, e existe só
    porque o carry foi separado da poupança com objetivo. "Transferido para o
    próximo mês" e "Resgatado do mês anterior" são as DUAS PONTAS DO MESMO
    LANÇAMENTO: se o elo não fecha, alguém digitou um valor errado ou esqueceu a
    contrapartida — e o mês seguinte inteiro passa a mentir.

    Misturado com "PS5" e "Reserva de Emergência" na mesma categoria, esse
    pareamento é impossível: não há como saber qual poupança devia reaparecer no
    mês seguinte e qual é caixinha de longo prazo.
    """
    saiu: dict[str, float] = defaultdict(float)
    entrou: dict[str, float] = defaultdict(float)
    for l in lancamentos:
        if not l.periodo or cfg.movimento(l.categoria, l.descricao) != "carry":
            continue
        (entrou if _e_resgate(l.categoria) else saiu)[l.periodo] += l.valor

    # Um elo é (mês que manda, mês que recebe), e ele é conferido pelos DOIS
    # lados: "saiu e não chegou" e "chegou sem ter saído" são erros diferentes.
    quebrados = []
    for periodo in sorted(saiu):
        destino = _proximo_mes(periodo)
        diferenca = round(saiu[periodo] - entrou.get(destino, 0.0), 2)
        if abs(diferenca) > tolerancia:
            quebrados.append({"de": periodo, "para": destino,
                              "saiu": round(saiu[periodo], 2),
                              "entrou": round(entrou.get(destino, 0.0), 2),
                              "diferenca": diferenca})

    orfaos = [{"periodo": p, "entrou": round(entrou[p], 2),
               "origem": _mes_anterior(p)}
              for p in sorted(entrou)
              if abs(entrou[p]) > tolerancia
              and abs(saiu.get(_mes_anterior(p), 0.0)) <= tolerancia]

    return {
        "elos": len(saiu),
        "total_saiu": round(sum(saiu.values()), 2),
        "total_entrou": round(sum(entrou.values()), 2),
        "quebrados": sorted(quebrados, key=lambda d: -abs(d["diferenca"])),
        # Mês que recebeu carry sem que o mês anterior tenha mandado nada.
        "sem_origem": orfaos,
    }


def reservas(lancamentos: list[Lancamento], cfg: AnalyticsConfig) -> dict:
    """Quanto está guardado, para quê, e o que na verdade é resgate de aplicação.

    O saldo por objetivo NÃO é calculado, e não é esquecimento: os depósitos
    nomeiam o objetivo ("PS5", "Documentos Veículos") mas os resgates quase
    nunca usam o mesmo nome ("Resgate manutenção carro" contra "Documentos
    Veículos"). Parear os dois daria um saldo que parece exato e está errado.
    O que se pode afirmar com os dados como estão é para onde o dinheiro FOI
    guardado, e qual o saldo total — os dois vêm abaixo, cada um no seu nome.
    """
    grupos = {t: {"guardado": 0.0, "resgatado": 0.0, "movimentos": 0}
              for t in ("carry", "reserva", "aplicacao")}
    objetivos: dict[str, dict] = {}
    por_mes: dict[str, float] = defaultdict(float)

    for l in lancamentos:
        tipo = cfg.movimento(l.categoria, l.descricao)
        if not tipo:
            continue
        saida = _e_resgate(l.categoria)
        grupos[tipo]["resgatado" if saida else "guardado"] += l.valor
        grupos[tipo]["movimentos"] += 1

        if tipo != "reserva":
            continue
        if l.periodo:
            por_mes[l.periodo] += -l.valor if saida else l.valor
        if saida:
            continue   # o resgate não nomeia o objetivo; ver docstring
        nome = l.descricao.strip() or "(sem descrição)"
        alvo = objetivos.setdefault(_chave(nome), {
            "objetivo": nome, "total": 0.0, "movimentos": 0,
            "primeiro": l.periodo, "ultimo": l.periodo,
            # "Reserva de Emergência" e "Reserva de emergência" são o mesmo
            # objetivo; o rótulo exibido é a grafia mais usada, não a primeira
            # que apareceu — senão um deslize de digitação de 2019 batiza a
            # linha que aparece no gráfico.
            "grafias": Counter(),
        })
        alvo["total"] += l.valor
        alvo["movimentos"] += 1
        alvo["grafias"][nome] += 1
        if l.periodo:
            alvo["primeiro"] = min(alvo["primeiro"] or l.periodo, l.periodo)
            alvo["ultimo"] = max(alvo["ultimo"] or l.periodo, l.periodo)

    for g in grupos.values():
        g["guardado"] = round(g["guardado"], 2)
        g["resgatado"] = round(g["resgatado"], 2)
        g["saldo"] = round(g["guardado"] - g["resgatado"], 2)

    # Saldo mês a mês, SEM pular os meses parados.
    #
    # Emitir só os meses com movimento faria o eixo colocar fev/14 ao lado de
    # jun/15 como se fossem consecutivos — e a linha desenharia uma subida
    # suave onde houve quatro anos de nada. Aqui o valor é carregado para
    # frente, que é o que de fato acontece com um saldo: ele não some, ele fica.
    corrida, acumulado = [], 0.0
    if por_mes:
        periodo = min(por_mes)
        fim = max(por_mes)
        while True:
            acumulado += por_mes.get(periodo, 0.0)
            corrida.append({"periodo": periodo, "saldo": round(acumulado, 2)})
            if periodo == fim:
                break
            periodo = _proximo_mes(periodo)

    return {
        "grupos": grupos,
        "objetivos": sorted(({**{k: v for k, v in o.items() if k != "grafias"},
                              "objetivo": o["grafias"].most_common(1)[0][0],
                              "total": round(o["total"], 2)}
                             for o in objetivos.values()),
                            key=lambda d: -d["total"]),
        "saldo_mensal": corrida,
        "corrente": corrente_do_carry(lancamentos, cfg),
    }


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
            chave = "resgate" if _e_resgate(l.categoria) else "poupanca"
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
    """Conferência dos DADOS — e por isso recebe os lançamentos SEM exclusão.

    Todo painel desta aba responde "para onde vai o dinheiro" e obedece à barra
    de filtros. Este responde outra coisa: "dá para confiar nestes números?".
    A diferença não é de estilo, é de aritmética.

    A identidade do mês zerado (`receita − gasto − investimento − poupança +
    resgate ≈ 0`) só tem sentido sobre o lançamento COMPLETO do mês. Tire uma
    categoria de gasto e a conta passa a acusar como buraco justamente o que
    você mandou esconder: excluindo `Casa` e `Construção`, out/25 — o mês da
    compra do imóvel — virava R$ 643.083,77 de "sobra sem destino", que é a
    soma exata das duas. O painel existe para apontar lançamento FALTANDO, e
    passou a inventar um.

    Vale para os sete números daqui, não só para a identidade: esconder
    `Cartão de crédito` esconderia a dupla contagem, esconder uma categoria
    esvaziaria um mês e ele apareceria como "mês faltando", e `total_lancamentos`
    contaria menos linhas do que o arquivo tem.

    O RECORTE DE DATAS continua valendo: "estes meses fecham?" é pergunta por
    mês, e olhar seis meses e ler "69 meses não fecham" seria ruído.
    """
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
    # `2024-03-15` vira `2024-03`. A planilha não tem dia: das 6.717 linhas do
    # histórico, ZERO trazem data com dia legível — a coluna Data é `Feb-12` ou
    # `Aug`, e o que é confiável são as colunas numéricas de Mês e Ano. Então o
    # seletor de datas escolhe o MÊS, e o dia serve só para dizer qual.
    #
    # O corte é inclusivo nas duas pontas: pedir de 15/03 a 20/06 traz março
    # inteiro e junho inteiro. Excluir as pontas parciais tiraria lançamentos
    # que o usuário espera ver e não teria como avisar — não existe o dado que
    # decidiria quais.
    de = (inicio or "")[:7]
    # O FIM não é cortado, e não é esquecimento: `"2025-06" <= "2025-06-20"` já
    # é verdadeiro por prefixo, e `"2025-07"` já é maior. Um `[:7]` aqui ficaria
    # simétrico e bonito sem mudar uma linha de resultado — e nenhum teste
    # conseguiria distinguir sua ausência, que é a definição de código morto.
    ate = fim or ""
    return [l for l in lancamentos if l.periodo
            and (not de or l.periodo >= de)
            and (not ate or l.periodo <= ate)]


def identidade(l: Lancamento) -> str:
    """Um nome estável para UM lançamento, para poder excluí-lo por fora.

    A planilha não tem coluna de id, então a identidade é o conteúdo: período,
    categoria, descrição e valor. Duas linhas idênticas nos quatro campos são
    indistinguíveis — e excluir uma exclui as duas, o que é o comportamento
    certo: se elas são iguais em tudo, o usuário não teria como escolher entre
    elas na tela.
    """
    return f"{l.periodo or '?'}|{l.categoria}|{l.descricao.strip()}|{l.valor:.2f}"


def excluir(lancamentos: list[Lancamento], categorias: Iterable[str] = (),
            linhas: Iterable[str] = (),
            titulares: Iterable[str] = ()) -> list[Lancamento]:
    """Tira de cena categorias inteiras, lançamentos avulsos e pessoas.

    É a leitura ao contrário: em vez de perguntar "quanto gastei em Casa?", tira
    Casa e pergunta como fica o resto. Num histórico com uma compra de imóvel de
    R$ 635 mil, é a única forma de enxergar a rotina — e o número que sobra é
    honesto desde que a tela diga o que saiu, que é o que a barra de filtros faz.

    O TITULAR é a terceira dimensão, e a única que separa PESSOAS em vez de
    assuntos: numa conta conjunta, "para onde vai o dinheiro" tem duas respostas
    diferentes e somá-las esconde as duas. Vazio (`""`) é um titular como
    qualquer outro aqui — é o balde de quem não tem marca, que a tela mostra
    como "(sem marca)" e que precisa poder sair igual aos outros.
    """
    fora_cat = {_chave(c) for c in categorias}
    fora_linha = set(linhas)
    fora_titular = {_chave(t) for t in titulares}
    if not fora_cat and not fora_linha and not fora_titular:
        return lancamentos
    return [l for l in lancamentos
            if _chave(l.categoria) not in fora_cat
            and identidade(l) not in fora_linha
            and _chave(l.titular) not in fora_titular]


def titulares_do_periodo(lancamentos: list[Lancamento]) -> list[dict]:
    """Quem aparece nas descrições, com quanto cada um gastou.

    Devolve VAZIO quando há um balde só. Numa fatura de uma pessoa — ou num
    histórico anterior à marcação existir — todas as linhas caem no mesmo grupo,
    e uma seção de filtro que não consegue mudar a tela é ruído com aparência de
    controle.

    O valor ao lado do nome é o gasto, não a contagem: "Rhyesla, R$ 12 mil"
    responde a pergunta que faz alguém querer isolar um titular; "Rhyesla, 29
    linhas" não responde nenhuma.
    """
    total: dict[str, float] = defaultdict(float)
    quantos: dict[str, int] = defaultdict(int)
    for l in lancamentos:
        total[l.titular] += l.valor if l.papel == GASTO else 0.0
        quantos[l.titular] += 1
    if len(quantos) < 2:
        return []
    return sorted(
        ({"titular": nome, "total": round(total[nome], 2),
          "lancamentos": quantos[nome]} for nome in quantos),
        key=lambda t: (-t["total"], t["titular"]))


def maiores_lancamentos(lancamentos: list[Lancamento], limite: int = 60) -> list[dict]:
    """Os maiores GASTOS avulsos, para a lista de exclusão da barra de filtros.

    Só gasto: receita e carregamento não distorcem gráfico de gasto, e oferecer
    a linha de salário para "remover outlier" só confundiria.
    """
    gastos = sorted((l for l in lancamentos if l.papel == GASTO),
                    key=lambda l: -l.valor)[:limite]
    return [{"id": identidade(l), "periodo": l.periodo, "categoria": l.categoria,
             "descricao": l.descricao, "valor": round(l.valor, 2)} for l in gastos]


def sankey(lancamentos: list[Lancamento], cfg: AnalyticsConfig,
           por_fonte: bool = False, top: int = 12) -> dict:
    """De onde veio e para onde foi — o período inteiro numa figura.

    TRÊS DECISÕES QUE DEFINEM O DESENHO
    -----------------------------------

    1. **O resgate de aplicação entra como ORIGEM.** Sem ele o diagrama não
       fecha: nos últimos 2 anos saíram R$ 414 mil a mais do que a receita
       explica, e esse buraco é exatamente o dinheiro que voltou de
       investimento. Chamá-lo de receita seria errado — não é renda nova, é
       patrimônio virando consumo —, então ele tem faixa e nome próprios, que é
       a informação que interessa.

    2. **O carry fica de fora.** "Transferido para o próximo mês" e "resgatado
       do mês anterior" somam R$ 757 mil em 2 anos do MESMO dinheiro circulando:
       entra e sai pelo mesmo valor. Seria a faixa mais grossa da tela sem ser
       renda nem gasto, e empurraria tudo que importa para uma tira fina.

    3. **A sobra é um nó, não um erro de arredondamento.** O que entrou menos o
       que saiu vira `Sobra do período` (ou `Origem não identificada`, se o
       sinal for o outro). Fechar o diagrama à força escondendo a diferença
       transformaria o resíduo — que tem painel próprio — num detalhe invisível.

    Com mais de um arquivo, `por_fonte` separa as origens por pessoa: é a
    resposta para "quem traz quanto", que é metade do motivo de existir uma
    análise do casal.
    """
    entra: dict[str, float] = defaultdict(float)
    sai: dict[str, float] = defaultdict(float)

    for l in lancamentos:
        quem = f" · {l.fonte}" if por_fonte and l.fonte else ""
        tipo = cfg.movimento(l.categoria, l.descricao)

        if l.papel == CARREGAMENTO:
            # Todo carregamento é dinheiro mudando de lugar; o que difere é se
            # ele atravessa a fronteira do PERÍODO ou fica dentro dele.
            if tipo == "carry":
                continue                               # ver decisão 2
            if _e_resgate(l.categoria):
                de_onde = "aplicação" if tipo == "aplicacao" else "reserva"
                entra[f"Resgate de {de_onde}{quem}"] += l.valor
            elif tipo == "aplicacao":
                sai["Investido"] += l.valor             # aporte lançado aqui
            else:
                sai["Guardado em reserva"] += l.valor
        elif l.papel == RECEITA:
            entra[f"{l.categoria or '(sem categoria)'}{quem}"] += l.valor
        elif l.papel == INVESTIMENTO:
            sai["Investido"] += l.valor
        elif l.papel == GASTO:
            sai[l.categoria or "(sem categoria)"] += l.valor

    # A cauda vira "Outras" pelo mesmo motivo de sempre: a paleta tem oito
    # posições validadas e uma nona cor inventada quebra a separação para
    # daltônicos. Aqui o corte é mais generoso porque a faixa carrega o rótulo.
    maiores = sorted(sai.items(), key=lambda kv: -kv[1])
    if len(maiores) > top:
        resto = sum(v for _, v in maiores[top:])
        maiores = maiores[:top] + [("Outras categorias", resto)]

    total_entra = sum(entra.values())
    total_sai = sum(v for _, v in maiores)
    diferenca = round(total_entra - total_sai, 2)

    origens = sorted(entra.items(), key=lambda kv: -kv[1])
    destinos = list(maiores)
    if diferenca > 0.01:
        destinos.append(("Sobra do período", diferenca))
    elif diferenca < -0.01:
        origens.append(("Origem não identificada", -diferenca))

    return {
        "origens": [{"nome": n, "valor": round(v, 2)} for n, v in origens],
        "destinos": [{"nome": n, "valor": round(v, 2)} for n, v in destinos],
        "total": round(max(total_entra, total_sai), 2),
        "diferenca": diferenca,
        "por_fonte": por_fonte,
    }


def analisar(texto: str | list[tuple[str, str]], cfg: AnalyticsConfig,
             inicio: str | None = None, fim: str | None = None,
             sem_categorias: Iterable[str] = (),
             sem_linhas: Iterable[str] = (),
             sem_titulares: Iterable[str] = ()) -> dict:
    """Analisa um ou mais CSVs juntos.

    Vários arquivos NÃO são deduplicados, e isso é decisão de produto: eles são
    de pessoas diferentes (a análise do casal), então duas linhas idênticas em
    arquivos diferentes são dois gastos de verdade. Deduplicar apagaria metade
    de um mercado dividido.
    """
    arquivos = [("", texto)] if isinstance(texto, str) else list(texto)
    completos: list[Lancamento] = []
    avisos: list[str] = []
    resumo_arquivos: list[dict] = []
    for nome, conteudo in arquivos:
        linhas, avisos_do_arquivo = read_ledger(conteudo, cfg, fonte=nome)
        completos.extend(linhas)
        avisos.extend(f"{nome}: {a}" if nome and len(arquivos) > 1 else a
                      for a in avisos_do_arquivo)
        resumo_arquivos.append({
            "nome": nome, "lancamentos": len(linhas),
            "receita": _soma(l for l in linhas if l.papel == RECEITA),
            "gasto": _soma(l for l in linhas if l.papel == GASTO),
        })

    # Os limites do ARQUIVO, calculados antes do recorte: é o que o seletor de
    # datas usa para não oferecer um período que não existe.
    disponivel = periodos(completos)

    # A ORDEM importa. O recorte de tempo vem primeiro, depois as exclusões:
    # a lista de "maiores lançamentos" que a barra de filtros oferece tem que
    # ser a do período na tela, não a do arquivo inteiro — senão ela sugere
    # excluir uma compra de 2021 num painel que mostra 2026.
    no_periodo = recortar(completos, inicio, fim)
    if not no_periodo:
        raise AnalyticsError(
            f"nenhum lançamento entre {inicio or 'início'} e {fim or 'fim'}. "
            f"O arquivo cobre {disponivel[0]} a {disponivel[-1]}."
            if disponivel else "nenhum lançamento no período")

    # As categorias e os maiores lançamentos OFERECIDOS pela barra são os do
    # período sem exclusão nenhuma: uma categoria que some da lista quando você
    # a exclui é uma categoria que você não consegue trazer de volta.
    oferecidos = {
        "categorias": [c["categoria"] for c in por_categoria(no_periodo)],
        "lancamentos": maiores_lancamentos(no_periodo),
        # Só vale oferecer com mais de um: com um balde só, "isolar o titular"
        # não isola nada, e a barra ganharia uma seção que nunca muda a tela.
        "titulares": titulares_do_periodo(no_periodo),
    }

    lancamentos = excluir(no_periodo, sem_categorias, sem_linhas,
                          ["" if t == SEM_TITULAR else t for t in sem_titulares])
    if not lancamentos:
        raise AnalyticsError(
            "os filtros tiraram tudo — sobrou nenhum lançamento no período")

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
        "filtro": {"inicio": inicio, "fim": fim,
                   "sem_categorias": list(sem_categorias),
                   "sem_linhas": list(sem_linhas),
                   "sem_titulares": list(sem_titulares)},
        # O que a barra de filtros oferece para tirar de cena.
        "disponiveis": oferecidos,
        "resumo": {
            "periodo_inicio": todos[0] if todos else None,
            "periodo_fim": todos[-1] if todos else None,
            "meses_com_dado": len(todos),
            # Quantas linhas os PAINÉIS estão somando — já sem as exclusões.
            # Fica aqui e não em `saude` porque as duas contagens são
            # diferentes de propósito: a conferência olha o arquivo, os painéis
            # olham o recorte. Mostrar a da conferência ao lado do gasto
            # filtrado dava "6.717 linhas" acima de um total que só cobria
            # 5.653 delas.
            "lancamentos": len(lancamentos),
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
        "reservas": reservas(lancamentos, cfg),
        # Com dois arquivos as origens se separam por pessoa — é metade do
        # motivo de existir uma análise do casal.
        "sankey": sankey(lancamentos, cfg, por_fonte=len(arquivos) > 1),
        "arquivos": resumo_arquivos,
        # `no_periodo`, não `lancamentos`: a conferência é sobre os dados, e as
        # exclusões da barra são leitura. Ver a docstring de `saude`.
        "saude": saude(no_periodo, avisos),
    }
