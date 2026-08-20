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
# A configuração REAL do app — a que o portal edita. Só um teste olha para ela,
# e apenas para conferir que carrega.
CONFIG_REAL = REPO / "config"
# A configuração dos TESTES: uma fotografia congelada. É contra ela que a suíte
# inteira roda, para que editar suas regras no portal nunca derrube o build.
CONFIG = Path(__file__).resolve().parent / "fixtures" / "config"
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


def _sicredi_app_csv(path: Path, *, vencimento="10/09/2025", rows=None) -> Path:
    """O CSV que o APLICATIVO do Sicredi exporta — o outro formato do mesmo banco.

    Reproduz as três coisas que o separam do `.xls` do site e que o leitor
    precisa vencer: o preâmbulo de rótulos antes da tabela, o `;` como
    separador, e a parcela escrita entre parênteses.
    """
    rows = rows if rows is not None else [
        ("20/08/2025", "MERCADOLIVRE UNICAF", "(01/02)", "R$ 150,00", ""),
        ("14/08/2025", "CE PERU RAIL 1", "", "R$ 320,59", "U$ 58,25"),
        ("02/07/2025", "SUPERMERCADOS ALVORA", "", "R$ 270,51", ""),
        ("01/07/2025", "Pagamento Efetuado", "", "R$ -400,00", ""),
    ]
    brasil = sum(_valor(v) for _, _, _, v, dolar in rows if _valor(v) > 0 and not dolar)
    exterior = sum(_valor(v) for _, _, _, v, dolar in rows if _valor(v) > 0 and dolar)
    creditos = sum(_valor(v) for _, _, _, v, _ in rows if _valor(v) < 0)

    def brl(v):
        return f'"R$ {v:,.2f}"'.replace(",", "\x00").replace(".", ",").replace("\x00", ".")

    linhas = [
        " Associado ;Fulano de Tal;;;;",
        " Cooperativa ;0230;;;;",
        "",
        f" Data de Vencimento ;{vencimento};;;;",
        f" Valor Total (R$) ;{brl(brasil + exterior + creditos)};;;;",
        " Situação ;Fechada;;;;",
        "",
        " Resumo de Despesas ;;;;;",
        f" (-) Pagamentos / Creditos (R$) ;{brl(creditos)};;;;",
        f" (+) Despesas / Debitos no Brasil (R$) ;{brl(brasil)};;;;",
        f" (+) Despesas / Debitos no exterior (R$) ;{brl(exterior)};;;;",
        f" (=) Total desta fatura (R$) ;{brl(brasil + exterior + creditos)};;;;",
        "",
        " Data ; Descrição ; Parcela ; Valor ; Valor em Dólar ; Adicional ; Nome;",
    ]
    for data, desc, parcela, valor, dolar in rows:
        linhas.append(f'{data};{desc};{parcela};"{valor}";{dolar};;Fulano de Tal')
    # O app grava com BOM; `utf-8-sig` na escrita é o que o reproduz.
    path.write_text("\n".join(linhas) + "\n", encoding="utf-8-sig")
    return path


def _valor(texto: str) -> float:
    import re as _re
    limpo = _re.sub(r"[^\d,.\-]", "", texto).replace(".", "").replace(",", ".")
    return float(limpo) if limpo else 0.0


@pytest.fixture
def sicredi_app_csv(tmp_path) -> Path:
    return _sicredi_app_csv(tmp_path / "fatura-app.csv")


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
    """Cópia da config CONGELADA (tests/fixtures/config), gravável e isolada.

    Não é a configuração real de propósito: um teste que dependesse dela
    passaria a falhar assim que alguém confirmasse um chute ou mapeasse um
    estabelecimento novo pelo portal. Ver `test_config_real_e_valida` para a
    cobertura da configuração de verdade.
    """
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


# ---------------------------------------------------------------------------
# CSV de saída montado pelo SCHEMA, nunca por nomes fixos
# ---------------------------------------------------------------------------
#
# Os nomes das colunas são configuráveis pelo portal: `Descrição` vira `Item`,
# `Valor (R$)` vira `Valor`. Todo teste que escrevia o cabeçalho na mão passou
# a falhar assim que essa configuração foi exercida de verdade — uma edição
# legítima no "Formato de saída" derrubou o build inteiro.
#
# Estas ajudas montam o CSV a partir do schema. O teste fala de PAPÉIS (data,
# categoria, descricao, valor, pago) e o NOME da coluna fica por conta da
# configuração de quem estiver rodando a suíte.

@pytest.fixture
def output_schema(config_dir):
    from core.profiles import ConfigSet
    return ConfigSet.load(config_dir).output


def csv_de_saida_texto(schema, linhas: list[dict], extras: tuple = ()) -> str:
    """Monta um CSV no formato de saída ATUAL.

    `linhas` usa os papéis como chave. `extras` acrescenta colunas que o portal
    não conhece (`Mês`, `Ano`), para provar que sobrevivem à ida e volta.
    """
    import csv as _csv
    import io as _io

    colunas = list(schema.colunas) + list(extras)
    buffer = _io.StringIO()
    writer = _csv.DictWriter(buffer, fieldnames=colunas, lineterminator="\n")
    writer.writeheader()
    for linha in linhas:
        row = schema.linha(
            data=linha.get("data", ""), categoria=linha.get("categoria", ""),
            descricao=linha.get("descricao", ""), valor=linha.get("valor", ""),
            pago=linha.get("pago", "x"),
        )
        row.update({c: linha.get(c, "") for c in extras})
        writer.writerow(row)
    return buffer.getvalue()


def com_um_chute(config_dir, categoria: str = "Casa", palavra: str = "CHUTOMETRO"):
    """Garante um `# ?` no categories.yml da cópia de teste.

    Os chutes são para o usuário confirmar ou apagar pela aba Regras — ou seja,
    o arquivo real tende a ZERO com o uso. Testes sobre chutes precisam plantar
    o seu, em vez de depender de o arquivo de alguém ainda ter algum.
    """
    alvo = config_dir / "categories.yml"
    texto = alvo.read_text(encoding="utf-8")
    marca = f"  {categoria}:\n"
    if marca not in texto:
        raise AssertionError(f"categoria {categoria} não existe no categories.yml")
    texto = texto.replace(marca, f"{marca}    - {palavra}    # ? plantado pelo teste\n", 1)
    alvo.write_text(texto, encoding="utf-8")
    return palavra


def com_uma_redundancia(config_dir, categoria: str = "Casa",
                        curta: str = "ZZTESTE", longa: str = "ZZTESTE LOJA"):
    """Planta um par redundante: a longa nunca vence, a curta já pega tudo.

    Mesmo motivo do `com_um_chute` — o arquivo real fica MAIS limpo com o uso,
    então depender das redundâncias que ele tinha ontem é garantir build
    vermelho amanhã.
    """
    alvo = config_dir / "categories.yml"
    texto = alvo.read_text(encoding="utf-8")
    marca = f"  {categoria}:\n"
    if marca not in texto:
        raise AssertionError(f"categoria {categoria} não existe no categories.yml")
    texto = texto.replace(marca, f"{marca}    - {curta}\n    - {longa}\n", 1)
    alvo.write_text(texto, encoding="utf-8")
    return curta, longa


def real_statements() -> list[Path]:
    return sorted(REAL_INPUT.glob("*.xls")) if REAL_INPUT.is_dir() else []
