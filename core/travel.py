"""Viagens: períodos em que todo gasto vira `Viagem`.

Uma viagem não é um estabelecimento, é uma JANELA DE TEMPO. O restaurante da
esquina e o restaurante de Gramado casam com a mesma palavra-chave; o que os
separa é a data da compra. Por isso as regras normais não dão conta, e por isso
o recorte é por período.

Três decisões que valem registrar:

* **A data que conta é a da COMPRA**, não a do vencimento da fatura. Uma viagem
  em maio aparece na fatura de junho, e uma parcela de março continua sendo de
  março mesmo que caia na mesma fatura.
* **A categoria real não se perde.** A linha vira `Viagem` na coluna Categoria,
  e o que ela seria — Alimentação, Transporte, Hobby — vai para a descrição,
  entre parênteses, logo antes do `{Em 15/Jul}`. Assim a planilha continua
  respondendo "quanto gastei em comida naquela viagem?".
* **Estar na janela é sugestão, não sentença.** Comprar um jogo no Nintendo
  eShop no meio da viagem não é despesa de viagem. Por isso existe uma etapa de
  confirmação, e não uma reclassificação automática e silenciosa.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date

from .pipeline import ClassifiedLine
from .text import normalize

TRAVEL_CATEGORY = "Viagem"

# " {Em 15/Jul}" no fim da descrição — é antes disto que a categoria real entra.
#
# O formato é EXIGIDO (dia/mês abreviado), e não um `{Em qualquer coisa}` frouxo:
# uma viagem batizada de "Em Paris" vira `{Em Paris}` e passaria por marca de
# data, fazendo `desanotar` devolver o rótulo como se fosse o sufixo. É a mesma
# forma que `text.DESC_RE` exige para ler a data de volta.
DATE_SUFFIX_RE = re.compile(r"\s*\{Em\s+\d{1,2}/[A-Za-z]{3}\}\s*$")

# "{Campo Belo}" — o nome da viagem. Reconhecido por EXCLUSÃO do `{Em 15/Jul}`,
# e isso é seguro porque a chave é marca exclusiva deste portal: extrato nenhum
# escreve `{}` no nome do estabelecimento.
ROTULO_RE = re.compile(r"\s*\{([^{}]*)\}")

# "(Lazer)" — a categoria real. Aqui a exclusão NÃO serve: `(Parcela 03/05)`
# tem exatamente a mesma forma, e um nome de estabelecimento com parênteses
# ("Padaria (Matriz)") também. Por isso só sai da descrição o parêntese cujo
# conteúdo é um nome de categoria CONHECIDO — ver `desanotar`.
PARENTESES_RE = re.compile(r"\s*\(([^()]*)\)")

# Como as duas marcas são ESCRITAS. São constantes, e não literais no meio do
# `annotate`, porque a tela "Formato de saída" documenta a forma delas — e
# documentação retipada é documentação que mente no dia em que alguém troca a
# chave pelo colchete aqui e esquece do outro lado.
MARCA_CATEGORIA = "({})"
MARCA_ROTULO = "{{{}}}"


# Acima disto, os períodos vazios viram um aviso só. Ver `validate_ranges`.
LIMITE_DE_AVISOS = 3


class TravelError(ValueError):
    pass


@dataclass(frozen=True)
class TravelRange:
    # `None` nos dois = VIAGEM SEM DATAS AINDA. É a passagem comprada em agosto
    # para uma viagem que você ainda não sabe quando será: o nome existe, a
    # janela não. Ela não pega nada por data — nunca — e serve só como destino
    # para pendurar linhas à mão. Sem isso, a única saída era inventar um
    # período falso, que arrastaria junto tudo que caísse nele.
    inicio: date | None
    fim: date | None
    rotulo: str = ""

    @property
    def sem_datas(self) -> bool:
        return self.inicio is None or self.fim is None

    def contains(self, dia: date) -> bool:
        return not self.sem_datas and self.inicio <= dia <= self.fim

    @property
    def dias(self) -> int:
        return 0 if self.sem_datas else (self.fim - self.inicio).days + 1

    @property
    def chave(self) -> str:
        """A identidade de um período: a JANELA, não o nome.

        É por esta chave que uma linha fixada à mão aponta para a viagem dela.
        Pelo nome não daria: renomear "Peru" para "Peru 2025" desligaria as
        linhas fixadas, e período sem nome não teria chave nenhuma. Pelo índice
        na lista também não: remover o primeiro período mudaria a viagem de
        todas as outras linhas em silêncio.

        É a mesma identidade que a importação de CSV usa para não duplicar
        período (`web/src/travelCsv.js::juntarPeriodos`).

        A viagem SEM DATAS não tem janela, então ali a identidade é o nome
        normalizado — é a única coisa que ela tem, e é por isso que o nome é
        obrigatório nesse caso. Duas viagens sem datas com o mesmo nome são a
        mesma viagem, o que é o comportamento certo: digitar "Peru" duas vezes
        não deveria criar dois destinos diferentes.
        """
        if self.sem_datas:
            return f"|{normalize(self.rotulo)}"
        return f"{self.inicio.isoformat()}|{self.fim.isoformat()}"

    def to_dict(self) -> dict:
        return {"inicio": self.inicio.isoformat() if self.inicio else "",
                "fim": self.fim.isoformat() if self.fim else "",
                "rotulo": self.rotulo}

    @classmethod
    def from_dict(cls, payload: dict) -> "TravelRange":
        rotulo = str(payload.get("rotulo") or "").strip()
        cru_inicio = str(payload.get("inicio") or "").strip()
        cru_fim = str(payload.get("fim") or "").strip()

        # As duas datas vazias = viagem sem datas ainda. UMA vazia é engano de
        # quem preencheu meio formulário, e recusar é melhor do que adivinhar
        # que a volta é igual à ida.
        if not cru_inicio and not cru_fim:
            if not rotulo:
                raise TravelError(
                    "viagem sem datas precisa de nome — é a única coisa que a "
                    "identifica e o que vai para a descrição")
            return cls(inicio=None, fim=None, rotulo=rotulo)

        try:
            inicio = date.fromisoformat(cru_inicio)
            fim = date.fromisoformat(cru_fim)
        except ValueError as exc:
            raise TravelError(f"período inválido: {payload!r} ({exc})")
        if fim < inicio:
            raise TravelError(
                f"período invertido: {inicio.isoformat()} termina depois de {fim.isoformat()}")
        return cls(inicio=inicio, fim=fim, rotulo=rotulo)


def purchase_dates(linhas: list[ClassifiedLine]) -> list[date]:
    saida = []
    for linha in linhas:
        try:
            dia = date.fromisoformat(linha.purchase_date)
        except (ValueError, TypeError):
            continue
        if dia != date.min:
            saida.append(dia)
    return saida


# "03/05" — a coluna Parcela do extrato. Formato aberto de propósito: o Nubank
# não tem essa coluna e o valor chega None, o que aqui significa "compra
# normal".
PARCELA_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*$")


def parcela_seguinte(parcela: str | None) -> bool:
    """`03/05` sim; `01/05`, vazio ou formato estranho, não.

    Só a PRIMEIRA parcela foi comprada no ciclo desta fatura. Da segunda em
    diante a linha carrega a data da compra ORIGINAL, que pode ser de meses
    atrás — a fatura de julho traz uma parcela 06/06 comprada em 3 de janeiro.

    Formato irreconhecível devolve `False` de propósito: na dúvida, a linha
    conta como compra do ciclo. Errar para o lado de um intervalo mais largo
    apenas oferece datas a mais; errar para o outro esconderia datas em que a
    viagem realmente aconteceu.
    """
    if not parcela:
        return False
    achado = PARCELA_RE.match(str(parcela))
    return bool(achado) and int(achado.group(1)) > 1


def purchase_range(linhas: list[ClassifiedLine]) -> tuple[date | None, date | None]:
    """Menor e maior data de COMPRA do lote — o intervalo em que faz sentido
    marcar uma viagem. É o que a interface usa para limitar os seletores de data.

    PARCELAS ANTIGAS FICAM DE FORA. Uma fatura cobre um mês, mas traz parcelas
    de compras de até um ano atrás com a data original: a de julho ia de 3 de
    janeiro a 28 de junho, quando as compras dela mesma vão de 26 de maio a 28
    de junho. O seletor então oferecia seis meses para marcar uma viagem que só
    poderia ter caído em um.

    Isto muda o que é OFERECIDO, não o que é pego: `mark_travel` continua olhando
    todas as linhas, então uma parcela cuja compra original caiu dentro da
    janela escolhida continua entrando na viagem — que é o certo, porque ela foi
    comprada lá.
    """
    do_ciclo = [l for l in linhas if not parcela_seguinte(l.parcela)]
    # Lote só de parcelas antigas (uma fatura sem nenhuma compra nova) ainda
    # precisa de um intervalo: sem isto o seletor ficaria desabilitado.
    dias = purchase_dates(do_ciclo) or purchase_dates(linhas)
    return (min(dias), max(dias)) if dias else (None, None)


def validate_ranges(
    ranges: list[TravelRange], linhas: list[ClassifiedLine]
) -> list[str]:
    """Avisos (não erros) sobre períodos que não fazem sentido para este lote."""
    inicio, fim = purchase_range(linhas)
    avisos: list[str] = []
    if inicio is None:
        return avisos

    # PERÍODO VAZIO NÃO É ERRO desde que dá para pendurar linhas nele à mão.
    # A fatura de agosto que só tem a passagem de trem do Peru é o caso exato:
    # a viagem é em outubro, a janela de outubro não pega nada ali, e mesmo
    # assim ela precisa existir — é o nome ao qual a passagem se pendura.
    # "Fora do lote" não precisa entrar nesta conta: um período que termina
    # antes da primeira compra também não contém nenhuma delas. A distinção
    # entre os dois casos existe só para escolher a FRASE, no laço abaixo.
    # A viagem SEM DATAS fica de fora da conta: ela não pega nada por data
    # porque não tem data, e isso é o desenho dela, não um aviso a dar.
    dias = purchase_dates(linhas)
    vazios = [p for p in ranges
              if not p.sem_datas and not any(p.contains(d) for d in dias)]

    # UM AVISO POR PERÍODO SÓ ENQUANTO FOREM POUCOS. Quem importa o CSV com as
    # 57 viagens dos últimos oito anos — que é o uso recomendado, porque é
    # assim que todo nome fica disponível para pendurar — recebia 55 caixas
    # amarelas iguais empurrando a tabela para fora da tela. O aviso vira
    # ruído no exato momento em que a lista fica útil.
    if len(vazios) > LIMITE_DE_AVISOS:
        avisos.append(
            f"{len(vazios)} dos {len(ranges)} períodos não pegaram compra "
            f"nenhuma por data neste lote (as compras vão de "
            f"{inicio:%d/%m/%Y} a {fim:%d/%m/%Y}). É o normal quando se importa "
            "a lista de viagens inteira — os nomes continuam disponíveis para "
            "pendurar compras à mão lá embaixo.")
    else:
        for periodo in vazios:
            rotulo = periodo.rotulo or f"{periodo.inicio:%d/%m/%Y} a {periodo.fim:%d/%m/%Y}"
            if periodo.fim < inicio or periodo.inicio > fim:
                avisos.append(
                    f"O período {rotulo} está fora das compras deste lote "
                    f"({inicio:%d/%m/%Y} a {fim:%d/%m/%Y}) — por data ele não pega "
                    "nada, mas dá para pendurar compras nele à mão lá embaixo.")
            else:
                avisos.append(
                    f"O período {rotulo} não tem nenhuma compra por data — dá para "
                    "pendurar compras nele à mão lá embaixo.")

    for i, a in enumerate(ranges):
        for b in ranges[i + 1:]:
            if a.inicio <= b.fim and b.inicio <= a.fim:
                # Com o ano, pela mesma razão do resto da tela: entre 57
                # viagens de oito anos, "24/11–26/11" não identifica nenhuma.
                avisos.append(
                    f"Os períodos {a.inicio:%d/%m/%Y}–{a.fim:%d/%m/%Y} e "
                    f"{b.inicio:%d/%m/%Y}–{b.fim:%d/%m/%Y} se sobrepõem; "
                    "as compras em comum contam uma vez só.")
    return avisos


def mark_travel(
    linhas: list[ClassifiedLine], ranges: list[TravelRange],
    fixadas: dict[str, str] | None = None,
) -> list[ClassifiedLine]:
    """Marca como candidatas a viagem as linhas cuja COMPRA cai numa janela —
    mais as que o usuário pendurou na viagem à mão.

    `fixadas` é `line_id -> chave do período`, e existe porque A DATA NÃO
    ENTREGA TUDO: passagem, hospedagem e passeio são pagos meses antes da
    viagem. A passagem de trem do Peru saiu em 14 de agosto e a viagem foi em
    outubro — nenhuma janela razoável pega as duas coisas, e alargar a janela
    até agosto arrastaria junto o supermercado do mês inteiro.

    O período continua sendo o atalho que resolve o caso comum; a fixação é a
    exceção nomeada, do mesmo jeito que `travel_rejected` é a exceção para o
    contrário (caiu na janela e não era viagem).
    """
    penduradas = fixadas or {}
    marcadas: list[ClassifiedLine] = []
    for linha in linhas:
        try:
            dia = date.fromisoformat(linha.purchase_date)
        except (ValueError, TypeError):
            dia = date.min
        dentro = (linha.line_id in penduradas
                  or (dia != date.min and any(p.contains(dia) for p in ranges)))
        # `replace` e não `ClassifiedLine(**linha.to_dict())`: o to_dict
        # serializa `state` para string (é o que a API guarda no SQLite), e
        # reconstruir a partir dele devolveria um `state` str em vez de
        # LineState — um objeto que parece certo e quebra na próxima serialização.
        marcadas.append(replace(linha, viagem=dentro))
    return marcadas


def range_of(linha: ClassifiedLine, ranges: list[TravelRange],
             fixadas: dict[str, str] | None = None) -> TravelRange | None:
    """Qual período pegou esta linha.

    A FIXAÇÃO À MÃO VENCE A DATA, e não é empate técnico: quem pendurou aquela
    linha naquela viagem foi explícito sobre aquela linha, enquanto o período é
    um atalho que fala de um intervalo inteiro. A passagem comprada em agosto
    pode cair, por azar, dentro de um feriado marcado em agosto — e ela é do
    Peru mesmo assim.

    Sem fixação, vence o primeiro período que contém a data da COMPRA. Períodos
    sobrepostos: vence o primeiro da lista. A compra em comum já conta uma vez
    só (é o que `validate_ranges` avisa), então o único efeito aqui é qual dos
    dois nomes vai para a descrição — e escolher um em silêncio é melhor do que
    escrever os dois.
    """
    chave = (fixadas or {}).get(linha.line_id)
    if chave:
        achado = next((p for p in ranges if p.chave == chave), None)
        if achado is not None:
            return achado

    try:
        dia = date.fromisoformat(linha.purchase_date)
    except (ValueError, TypeError):
        return None
    if dia == date.min:
        return None
    return next((p for p in ranges if p.contains(dia)), None)


def desanotar(
    descricao: str, conhecidas: list[str] | tuple[str, ...] = ()
) -> tuple[str, str, str]:
    """O inverso de `annotate`: (descrição limpa, categoria real, rótulo).

        [Cartão] Campo Belo C (Lazer) {Campo Belo} {Em 23/Mar}
        -> ("[Cartão] Campo Belo C {Em 23/Mar}", "Lazer", "Campo Belo")

    Existe para que REPROCESSAR não empilhe. Um arquivo de saída pode voltar
    pela recategorização quantas vezes ele quiser, e a segunda passada tem de
    reescrever `(Lazer)` — não escrever `(Lazer) (Vestuário)`.

    O PERIGO AQUI É COMER O QUE NÃO É MARCA, e as duas marcas correm riscos
    diferentes:

    * `{...}` é seguro por exclusão. Só este portal escreve chaves; a única
      outra é o `{Em 15/Jul}`, que sai da frente antes da busca.
    * `(...)` NÃO é. `(Parcela 03/05)` é escrito pelo próprio portal e
      `Padaria (Matriz)` é nome de estabelecimento. Por isso o parêntese só é
      removido quando o conteúdo é um nome de categoria que existe —
      `conhecidas` é a lista de `Ruleset.all_categories()`. Sem ela, nada de
      parêntese sai, e o pior caso é a marca antiga sobreviver, não o nome do
      estabelecimento ser mutilado.
    """
    achado = DATE_SUFFIX_RE.search(descricao)
    corpo = descricao[:achado.start()] if achado else descricao
    sufixo = achado.group(0).strip() if achado else ""

    rotulo = ""

    def _tira_rotulo(match: re.Match) -> str:
        nonlocal rotulo
        # O ÚLTIMO vence. Com uma marca só — o caso real — dá no mesmo; com
        # duas, a mais à direita é a que `annotate` escreveu por último.
        rotulo = match.group(1).strip()
        return ""

    corpo = ROTULO_RE.sub(_tira_rotulo, corpo)

    indice = {normalize(c): c for c in conhecidas if c and c.strip()}
    categoria = ""

    def _tira_categoria(match: re.Match) -> str:
        nonlocal categoria
        alvo = normalize(match.group(1))
        if alvo in indice:
            categoria = indice[alvo]
            return ""
        return match.group(0)

    if indice:
        corpo = PARENTESES_RE.sub(_tira_categoria, corpo)

    corpo = corpo.rstrip()
    return (f"{corpo} {sufixo}".strip() if sufixo else corpo), categoria, rotulo


def annotate(descricao: str, categoria_real: str, rotulo: str = "",
             conhecidas: list[str] | tuple[str, ...] = ()) -> str:
    """Escreve `(Categoria)` e `{Nome da viagem}` logo antes do `{Em 15/Jul}`.

        [Cartão] Campo Belo Country C {Em 23/Mar}
        [Cartão] Campo Belo Country C (Lazer) {Campo Belo} {Em 23/Mar}

    As duas marcas são independentes e as duas são opcionais: quem não nomeou a
    viagem não ganha chave nenhuma, e sem categoria real não há parêntese — em
    vez de um rótulo inventado. Uma viagem nomeada cuja linha ficou sem
    categoria ainda recebe `{Campo Belo}`.

    SUBSTITUI, não acrescenta. A marca antiga é retirada primeiro (`desanotar`)
    e a nova entra no lugar, o que torna a função idempotente de verdade e não
    só para valores idênticos — reprocessar um arquivo com regras novas troca
    `(Lazer)` por `(Vestuário)` em vez de escrever os dois.

    O que não vier na chamada é HERDADO da descrição: reprocessar sem saber o
    nome da viagem mantém o `{Campo Belo}` que já estava lá, em vez de apagá-lo.
    """
    categoria_real = (categoria_real or "").strip()
    # `Viagem` nunca é a categoria REAL: numa linha que já está em Viagem, a
    # categoria da coluna é a marca, e escrevê-la de volta daria `(Viagem)` —
    # a marca comendo a si mesma e perdendo o `(Lazer)` que ela guardava.
    if normalize(categoria_real) == normalize(TRAVEL_CATEGORY):
        categoria_real = ""

    # A categoria que está sendo escrita entra na lista de reconhecidas mesmo
    # quem chamou sem `conhecidas`. É o que garante a idempotência barata do
    # caminho da fatura, onde não há Ruleset à mão: reescrever a mesma marca
    # remove a anterior em vez de duplicá-la.
    base, categoria_antiga, rotulo_antigo = desanotar(
        descricao, [*conhecidas, categoria_real])

    categoria_real = categoria_real or categoria_antiga
    rotulo = (rotulo or "").strip() or rotulo_antigo

    partes = []
    if categoria_real:
        partes.append(MARCA_CATEGORIA.format(categoria_real))
    if rotulo:
        partes.append(MARCA_ROTULO.format(rotulo))
    if not partes:
        return base

    extra = " ".join(partes)
    sufixo = DATE_SUFFIX_RE.search(base)
    if sufixo:
        return f"{base[:sufixo.start()].rstrip()} {extra} {sufixo.group(0).strip()}"
    return f"{base.rstrip()} {extra}"


def apply_travel(linha: ClassifiedLine, categoria_real: str, rotulo: str = "",
                 conhecidas: list[str] | tuple[str, ...] = ()) -> tuple[str, str]:
    """(categoria, descrição) de uma linha confirmada como viagem."""
    return TRAVEL_CATEGORY, annotate(linha.descricao, categoria_real, rotulo,
                                     conhecidas)
