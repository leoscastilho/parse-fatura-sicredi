"""Normalização de texto e datas — compartilhado entre CLI e API.

Regra de ouro deste módulo: nada aqui depende de pandas, YAML ou FastAPI.
São só funções puras sobre strings e datas, o que as torna triviais de testar
e garante que a CLI e a API classifiquem exatamente igual.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime

# Abreviações de mês em inglês, fixas — `strftime('%b')` depende do locale da
# máquina e quebraria o formato histórico da planilha.
MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTH_INDEX = {abbr.upper(): i + 1 for i, abbr in enumerate(MONTH_ABBR)}

DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")

# "[Cartão] Renner (Parcela 03/05) {Em 10/May}"
DESC_RE = re.compile(
    r"^\s*(?:\[[^\]]+\]\s*)?"
    r"(?P<merchant>.*?)"
    r"(?:\s*\(Parcela\s*[^)]*\))?"
    r"(?:\s*\{Em\s*(?P<day>\d{1,2})/(?P<month>[A-Za-z]{3})\})?"
    r"\s*$"
)

# Números de transação grudados no nome: "BEST BUY 000026", "UNITED016245...".
TRANSACTION_NOISE = re.compile(r"\d{4,}")


def normalize(text: str) -> str:
    """MAIÚSCULA, sem acentos, pontuação -> espaço, espaços colapsados.

    Também separa CamelCase, porque o Sicredi mistura os dois estilos no
    mesmo extrato ("OggiSantaRita", "GrelhaGrill", "ArmazemDoValeRest").
    """
    text = unicodedata.normalize("NFKD", str(text))
    text = text.encode("ascii", "ignore").decode()
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    text = re.sub(r"[^A-Za-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip().upper()


def compact(text: str) -> str:
    """Versão sem espaços, para casar 'GRELHA GRILL' com 'GrelhaGrill'."""
    return text.replace(" ", "")


def merchant_key(text: str) -> str:
    """Identidade do estabelecimento, sem o número da transação.

    Sem isso "UNITED01624563906420" e "UNITED01624563906431" seriam dois
    estabelecimentos, e a palavra-chave gravada para um nunca casaria com a
    próxima compra.  Só tira sequências de 4+ dígitos, para não estragar
    nomes como "199 RIACHUELO" ou "MERCADO 11PRODUTOS".
    """
    normalized = normalize(text)
    cleaned = re.sub(r"\s+", " ", TRANSACTION_NOISE.sub(" ", normalized)).strip()
    return cleaned or normalized


def merchant_of(descricao: str) -> str:
    """Extrai o estabelecimento de uma descrição já formatada.

    "[Cartão] Renner (Parcela 03/05) {Em 10/May}"  ->  "Renner"
    """
    match = DESC_RE.match(descricao)
    return (match.group("merchant") if match else descricao).strip()


def purchase_date_of(data_vencimento: str, descricao: str) -> date | None:
    """Reconstrói a data da compra a partir do CSV.

    A descrição guarda só dia/mês (`{Em 21/Aug}`); o ano é o mais recente que
    não passa do vencimento — o que resolve parcelas antigas (uma compra de
    Ago/2024 numa fatura de Abr/2025).
    """
    match = DESC_RE.match(descricao)
    if not match or not match.group("day"):
        return None
    month = MONTH_INDEX.get(match.group("month").upper())
    if not month:
        return None
    day = int(match.group("day"))

    due = due_date_of(data_vencimento)
    if due == date.min:
        return None

    for year in range(due.year, due.year - 6, -1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate <= due:
            return candidate
    return None


def due_date_of(data: str) -> date:
    """Coluna `Data` (MM/DD/YYYY) como data de verdade.

    Ordenar a string quebraria na virada do ano ("12/10/2025" viria depois de
    "01/10/2026").
    """
    try:
        return datetime.strptime(str(data), "%m/%d/%Y").date()
    except (ValueError, TypeError):
        return date.min


def sort_key_category(categoria: str) -> tuple[int, str]:
    """Alfabético ignorando acentos; categoria vazia vai para o fim."""
    categoria = (categoria or "").strip()
    return (1, "") if not categoria else (0, normalize(categoria))


def format_purchase_suffix(purchase_date: date) -> str:
    """`{Em 15/Jul}` — sempre em inglês, independente do locale."""
    return f"{{Em {purchase_date.day}/{MONTH_ABBR[purchase_date.month - 1]}}}"


# " <Rhyesla>" no FIM da descrição — a marca de quem passou o cartão numa conta
# conjunta, posta na importação por `pipeline.build_description`.
#
# Ancorado no fim de propósito: um `<` no meio do nome de um estabelecimento não
# é marca de titular, e sem a âncora "[Cartão] Loja <3 {Em 3/Jan}" viraria um
# titular chamado "3".
#
# Mora aqui, e não em `analytics`, porque três lugares perguntam a mesma coisa:
# a aba de Análise (para filtrar por pessoa), as telas de revisão (idem) e
# qualquer coisa que venha depois. Duas cópias deste padrão divergiriam no dia
# em que uma delas aceitasse espaço antes do `<`.
TITULAR_RE = re.compile(r"<([^<>]+)>\s*$")


def titular_de(descricao: str) -> str:
    """Quem passou o cartão, ou "" quando a linha não tem marca.

    Vazio quer dizer "sem marca": as compras de quem se identificou como "eu"
    no upload, e todo o histórico anterior a este recurso existir.
    """
    achado = TITULAR_RE.search(descricao or "")
    return achado.group(1).strip() if achado else ""
