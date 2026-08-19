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


def purchase_range(linhas: list[ClassifiedLine]) -> tuple[date | None, date | None]:
    """Menor e maior data de COMPRA do lote — o intervalo em que faz sentido
    marcar uma viagem. É o que a interface usa para limitar os seletores de data.
    """
    dias = purchase_dates(linhas)
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


def annotate(descricao: str, categoria_real: str) -> str:
    """Insere `(Categoria)` logo antes do `{Em 15/Jul}`.

        [Cartão] B91 Supremo Pizzaria {Em 15/Jul}
        [Cartão] B91 Supremo Pizzaria (Alimentação) {Em 15/Jul}

    Sem categoria real não há o que anexar — a linha vira Viagem e fica sem o
    parêntese em vez de ganhar um rótulo inventado.
    """
    categoria_real = (categoria_real or "").strip()
    if not categoria_real:
        return descricao

    # Idempotente: reanotar não empilha parênteses.
    if f"({categoria_real})" in descricao:
        return descricao

    sufixo = DATE_SUFFIX_RE.search(descricao)
    if sufixo:
        base = descricao[:sufixo.start()].rstrip()
        return f"{base} ({categoria_real}){sufixo.group(0).rstrip()}"
    return f"{descricao.rstrip()} ({categoria_real})"


def apply_travel(linha: ClassifiedLine, categoria_real: str) -> tuple[str, str]:
    """(categoria, descrição) de uma linha confirmada como viagem."""
    return TRAVEL_CATEGORY, annotate(linha.descricao, categoria_real)
