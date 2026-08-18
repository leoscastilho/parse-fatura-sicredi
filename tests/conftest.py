"""Fixtures compartilhadas.

Os testes são HERMÉTICOS: geram os próprios extratos em vez de depender de
`input/*.xls`, que é gitignored e não entra na imagem Docker. Sem isso a suíte
passaria na sua máquina e sumiria no build.

Quando os extratos reais existem, um teste extra roda contra eles — mas nenhum
teste obrigatório depende disso.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import pytest
from openpyxl import Workbook

REPO = Path(__file__).resolve().parent.parent
CONFIG = REPO / "config"
REAL_INPUT = REPO / "input"


# ---------------------------------------------------------------------------
# Extratos sintéticos
# ---------------------------------------------------------------------------

def _sicredi_workbook(path: Path, *, vencimento="10/08/2026", rows=None,
                      internacional=None) -> Path:
    """Monta uma planilha com o mesmo formato do extrato Sicredi."""
    rows = rows if rows is not None else [
        ("21/08/2024", "MERCADOLIVRE*ESPLAN", "08/10", "86,80"),
        ("26/06/2026", "Prudent*APOL00188798", None, "521,58"),
        ("02/07/2026", "SUPERMERCADOS ALVORA", None, "270,51"),
        ("03/07/2026", "OggiSantaRita", None, "32,70"),
        ("05/07/2026", "AUTO POSTO Z LTDA", None, "182,07"),
        ("07/07/2026", "AMAZON BR", None, "59,13"),
        ("08/07/2026", "LOJA XPTO", None, "42,00"),
        ("01/07/2026", "Pag Fat Deb Cc", None, "-13.928,90"),
        ("04/07/2026", "Credito Anuidade Car", None, "-75,00"),
    ]
    total = sum(
        float(v.replace(".", "").replace(",", ".")) for _, _, _, v in rows
        if float(v.replace(".", "").replace(",", ".")) > 0
    )
    creditos = -sum(
        float(v.replace(".", "").replace(",", ".")) for _, _, _, v in rows
        if float(v.replace(".", "").replace(",", ".")) < 0
    )

    wb = Workbook()
    ws = wb.active
    ws.title = "Relatorio"
    grid = [
        [None, None, None, None],
        ["Associado:", "TESTE", None, None],
        ["Cartão Sicredi MASTER - Extrato Mensal", None, None, None],
        ["Data de Vencimento:", None, vencimento, None],
        ["Valor Total(R$):", None, f"{total:,.2f}".replace(",", "@").replace(".", ",").replace("@", "."), None],
        ["Pagamentos / Créditos (R$):", None, f"{creditos:.2f}".replace(".", ","), None],
        [None, None, None, None],
        ["Histórico de Despesas", None, None, None],
        ["Despesas no Brasil", None, None, None],
        ["Data", "Descrição", "Parcela", "Valor (R$)"],
    ]
    grid += [[d, desc, parc, val] for d, desc, parc, val in rows]
    grid.append(["Valor Total R$:", None, None,
                 f"{total:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")])
    grid.append([None, None, None, None])
    grid.append(["Despesas Internacionais", None, None, None])
    grid.append(["Data", "Descrição", "Valor (US$)", "Valor (R$)"])
    if internacional:
        for d, desc, usd, brl in internacional:
            grid.append([d, desc, usd, brl])
        grid.append(["Valor Total R$:", None, None, internacional[-1][3]])
    else:
        grid.append(["Não existem lançamentos.", None, None, None])

    for row in grid:
        ws.append(row)
    wb.save(path)
    return path


@pytest.fixture
def sicredi_xlsx(tmp_path) -> Path:
    return _sicredi_workbook(tmp_path / "extrato-sicredi.xlsx")


@pytest.fixture
def sicredi_xlsx_intl(tmp_path) -> Path:
    """Extrato com seção internacional — a que o parser antigo perdia."""
    return _sicredi_workbook(
        tmp_path / "extrato-intl.xlsx",
        internacional=[("27/06/2026", "CLOUDFLARE", "20,92", "109,23")],
    )


@pytest.fixture
def nubank_csv(tmp_path) -> Path:
    path = tmp_path / "nubank.csv"
    path.write_text(
        "date,title,amount\n"
        "2026-07-03,Supermercados Alvorada,270.51\n"
        "2026-07-05,Uber *Trip,26.74\n"
        "2026-07-08,Amazon BR,59.13\n"
        "2026-07-11,Renner,79.96\n",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# Config isolada por teste
# ---------------------------------------------------------------------------

@pytest.fixture
def config_dir(tmp_path) -> Path:
    """Cópia da config real, para os testes poderem escrever sem sujar o repo."""
    destino = tmp_path / "config"
    shutil.copytree(CONFIG, destino)
    return destino


@pytest.fixture
def client(config_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("FATURA_RULES_PATH", str(config_dir / "categories.yml"))
    monkeypatch.setenv("FATURA_DB_PATH", str(tmp_path / "state.db"))

    # `get_settings` é cacheado com lru_cache; sem limpar, um teste herdaria a
    # config do anterior.
    from api import app as app_module
    from api.settings import get_settings

    get_settings.cache_clear()
    app_module._store = None

    from fastapi.testclient import TestClient
    with TestClient(app_module.app) as c:
        yield c

    get_settings.cache_clear()
    app_module._store = None


@pytest.fixture
def categories_text(config_dir) -> str:
    return (config_dir / "categories.yml").read_text(encoding="utf-8")


def real_statements() -> list[Path]:
    return sorted(REAL_INPUT.glob("*.xls")) if REAL_INPUT.is_dir() else []
