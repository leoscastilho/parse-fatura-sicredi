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

TRAVEL_CATEGORY = "Viagem"

# " {Em 15/Jul}" no fim da descrição — é antes disto que a categoria real entra.
DATE_SUFFIX_RE = re.compile(r"\s*\{Em\s[^}]*\}\s*$")


class TravelError(ValueError):
    pass


@dataclass(frozen=True)
class TravelRange:
    inicio: date
    fim: date
    rotulo: str = ""

    def contains(self, dia: date) -> bool:
        return self.inicio <= dia <= self.fim

    @property
    def dias(self) -> int:
        return (self.fim - self.inicio).days + 1

    def to_dict(self) -> dict:
        return {"inicio": self.inicio.isoformat(), "fim": self.fim.isoformat(),
                "rotulo": self.rotulo}

    @classmethod
    def from_dict(cls, payload: dict) -> "TravelRange":
        try:
            inicio = date.fromisoformat(str(payload["inicio"]))
            fim = date.fromisoformat(str(payload["fim"]))
        except (KeyError, ValueError) as exc:
            raise TravelError(f"período inválido: {payload!r} ({exc})")
        if fim < inicio:
            raise TravelError(
                f"período invertido: {inicio.isoformat()} termina depois de {fim.isoformat()}")
        return cls(inicio=inicio, fim=fim, rotulo=str(payload.get("rotulo") or "").strip())


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

    for periodo in ranges:
        rotulo = periodo.rotulo or f"{periodo.inicio:%d/%m} a {periodo.fim:%d/%m}"
        if periodo.fim < inicio or periodo.inicio > fim:
            avisos.append(
                f"O período {rotulo} está fora das compras deste lote "
                f"({inicio:%d/%m/%Y} a {fim:%d/%m/%Y}) e não marca nada.")
        elif not any(periodo.contains(d) for d in purchase_dates(linhas)):
            avisos.append(f"O período {rotulo} não tem nenhuma compra.")

    for i, a in enumerate(ranges):
        for b in ranges[i + 1:]:
            if a.inicio <= b.fim and b.inicio <= a.fim:
                avisos.append(
                    f"Os períodos {a.inicio:%d/%m}–{a.fim:%d/%m} e "
                    f"{b.inicio:%d/%m}–{b.fim:%d/%m} se sobrepõem; "
                    "as compras em comum contam uma vez só.")
    return avisos


def mark_travel(
    linhas: list[ClassifiedLine], ranges: list[TravelRange]
) -> list[ClassifiedLine]:
    """Marca como candidatas a viagem as linhas cuja COMPRA cai numa janela."""
    marcadas: list[ClassifiedLine] = []
    for linha in linhas:
        try:
            dia = date.fromisoformat(linha.purchase_date)
        except (ValueError, TypeError):
            dia = date.min
        dentro = dia != date.min and any(p.contains(dia) for p in ranges)
        # `replace` e não `ClassifiedLine(**linha.to_dict())`: o to_dict
        # serializa `state` para string (é o que a API guarda no SQLite), e
        # reconstruir a partir dele devolveria um `state` str em vez de
        # LineState — um objeto que parece certo e quebra na próxima serialização.
        marcadas.append(replace(linha, viagem=dentro))
    return marcadas


def range_of(linha: ClassifiedLine, ranges: list[TravelRange]) -> TravelRange | None:
    """Qual período pegou esta linha — o primeiro que contém a data da COMPRA.

    Períodos sobrepostos: vence o primeiro da lista. A compra em comum já conta
    uma vez só (é o que `validate_ranges` avisa), então o único efeito aqui é
    qual dos dois nomes vai para a descrição — e escolher um em silêncio é
    melhor do que escrever os dois.
    """
    try:
        dia = date.fromisoformat(linha.purchase_date)
    except (ValueError, TypeError):
        return None
    if dia == date.min:
        return None
    return next((p for p in ranges if p.contains(dia)), None)


def annotate(descricao: str, categoria_real: str, rotulo: str = "") -> str:
    """Insere `(Categoria)` e `{Nome da viagem}` logo antes do `{Em 15/Jul}`.

        [Cartão] Campo Belo Country C {Em 23/Mar}
        [Cartão] Campo Belo Country C (Lazer) {Campo Belo} {Em 23/Mar}

    As duas marcas são independentes e as duas são opcionais: quem não nomeou a
    viagem não ganha chave nenhuma, e sem categoria real não há parêntese — em
    vez de um rótulo inventado. Uma viagem nomeada cuja linha ficou sem
    categoria ainda recebe `{Campo Belo}`.
    """
    categoria_real = (categoria_real or "").strip()
    rotulo = (rotulo or "").strip()

    # Idempotente nas duas marcas: refazer o /preview não empilha parênteses
    # nem chaves.
    partes = []
    if categoria_real and f"({categoria_real})" not in descricao:
        partes.append(f"({categoria_real})")
    if rotulo and f"{{{rotulo}}}" not in descricao:
        partes.append(f"{{{rotulo}}}")
    if not partes:
        return descricao

    extra = " ".join(partes)
    sufixo = DATE_SUFFIX_RE.search(descricao)
    if sufixo:
        base = descricao[:sufixo.start()].rstrip()
        return f"{base} {extra}{sufixo.group(0).rstrip()}"
    return f"{descricao.rstrip()} {extra}"


def apply_travel(linha: ClassifiedLine, categoria_real: str,
                 rotulo: str = "") -> tuple[str, str]:
    """(categoria, descrição) de uma linha confirmada como viagem."""
    return TRAVEL_CATEGORY, annotate(linha.descricao, categoria_real, rotulo)
