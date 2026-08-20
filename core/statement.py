"""Leitura do extrato, dirigida pelo perfil do banco.

Aceita caminho OU buffer em memória, porque a API recebe `UploadFile` e não
tem (nem deveria ter) um arquivo no disco.

Cada estratégia é uma função `_ler_<estrategia>`; o `read_statement` só
despacha. Um banco novo com formato novo = uma função nova aqui + um
`estrategia:` no YAML do banco.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO

import pandas as pd

from .profiles import BankProfile, ProfileError
from .text import normalize

DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")


@dataclass
class Entry:
    purchase_date: datetime
    description: str
    installment: str | None
    amount: float
    international: bool = False
    # Quem passou o cartão. Só existe onde o banco diz — o CSV do app do
    # Sicredi tem uma coluna `Nome`; o `.xls` do site, não. Vazio significa
    # "o extrato não conta", não "foi o titular".
    cardholder: str = ""


@dataclass
class Statement:
    name: str
    due_date: datetime | None
    entries: list[Entry]
    declared_debits: float
    declared_credits: float | None
    declared_balance: float | None
    bank_id: str = "sicredi"
    # O nome que o banco imprime como dono da conta. Serve para SUGERIR quem é
    # "eu" na conta conjunta, em vez de fazer o usuário procurar o próprio nome
    # numa lista.
    titular: str = ""

    @property
    def cardholders(self) -> list[str]:
        """Os nomes distintos que aparecem nos lançamentos, em ordem.

        Um só (ou nenhum) significa cartão de uma pessoa: não há o que
        perguntar, e a tela não pergunta.
        """
        vistos: list[str] = []
        for e in self.entries:
            if e.cardholder and e.cardholder not in vistos:
                vistos.append(e.cardholder)
        return sorted(vistos)

    @property
    def debits(self) -> float:
        return round(sum(e.amount for e in self.entries if e.amount > 0), 2)

    @property
    def credits(self) -> float:
        return round(-sum(e.amount for e in self.entries if e.amount < 0), 2)

    def reconciles(self) -> bool:
        """A soma lida bate com o que a própria fatura declara?

        Quando o extrato não declara totais (caso do CSV do Nubank), não há o
        que conferir — devolvemos True em vez de fingir um erro.
        """
        if not self.declared_debits and self.declared_credits is None:
            return True
        if abs(self.debits - self.declared_debits) >= 0.01:
            return False
        if self.declared_credits is not None:
            return abs(self.credits - self.declared_credits) < 0.01
        return True


# ---------------------------------------------------------------------------
# Números e datas, conforme o perfil
# ---------------------------------------------------------------------------

def make_amount_parser(numeros: dict[str, Any] | None):
    milhar = (numeros or {}).get("milhar", ".")
    decimal = (numeros or {}).get("decimal", ",")

    def parse(text: Any) -> float | None:
        raw = re.sub(r"[^\d,.\-]", "", str(text)).strip()
        if not raw:
            return None
        if milhar:
            raw = raw.replace(milhar, "")
        if decimal and decimal != ".":
            raw = raw.replace(decimal, ".")
        try:
            return float(raw)
        except ValueError:
            return None

    return parse


# Parser padrão pt-BR, usado pelo `parse_amount` público (compatibilidade).
parse_amount = make_amount_parser({"milhar": ".", "decimal": ","})


def _cell(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


# ---------------------------------------------------------------------------
# Estratégia: excel_secoes  (Sicredi)
# ---------------------------------------------------------------------------

def _ler_excel_secoes(source, name: str, profile: BankProfile,
                      due_date: datetime | None) -> Statement:
    cfg = profile.leitura
    amount_of = make_amount_parser(cfg.get("numeros"))
    header = cfg.get("cabecalho") or {}
    col_data = header.get("data", "Data")
    col_desc = header.get("descricao", "Descrição")
    fim = cfg.get("fim_secao") or "Valor Total"
    intl_marker = (cfg.get("internacional") or {}).get("marcador")
    fmt = cfg.get("data_lancamento", "%d/%m/%Y")

    grid = pd.read_excel(source, sheet_name=cfg.get("planilha", 0),
                         header=None, dtype=object)
    rows = [[_cell(v) for v in row] for row in grid.itertuples(index=False)]

    def summary_value(prefix: str | None) -> float | None:
        if not prefix:
            return None
        for row in rows:
            if row and normalize(row[0]).startswith(normalize(prefix)):
                for cell in reversed(row[1:]):
                    value = amount_of(cell)
                    if value is not None:
                        return value
        return None

    if due_date is None:
        venc = cfg.get("vencimento") or {}
        rotulo = venc.get("rotulo")
        venc_fmt = venc.get("formato", "%d/%m/%Y")
        if rotulo:
            for row in rows:
                if row and row[0].lower().startswith(rotulo.lower()):
                    for cell in row[1:]:
                        if DATE_RE.match(cell):
                            due_date = datetime.strptime(cell, venc_fmt)
                            break
                if due_date:
                    break

    entries: list[Entry] = []
    totals: list[float] = []

    for idx, row in enumerate(rows):
        if len(row) < 4 or row[0] != col_data or row[1] != col_desc:
            continue
        international = bool(intl_marker) and intl_marker in row[2]

        for data_row in rows[idx + 1:]:
            head = data_row[0]
            if head.lower().startswith(fim.lower()):
                value = amount_of(data_row[-1])
                if value is not None:
                    totals.append(value)
                break
            if not DATE_RE.match(head):
                if head:      # "Não existem lançamentos.", próxima seção…
                    break
                continue
            amount = amount_of(data_row[3])
            if amount is None:
                continue
            entries.append(Entry(
                purchase_date=datetime.strptime(head, fmt),
                description=data_row[1],
                installment=None if international or not data_row[2] else data_row[2],
                amount=amount,
                international=international,
            ))

    resumo = cfg.get("resumo") or {}
    return Statement(
        name=name,
        due_date=due_date,
        entries=entries,
        declared_debits=round(sum(totals), 2),
        declared_credits=summary_value(resumo.get("creditos")),
        declared_balance=summary_value(resumo.get("total")),
        bank_id=profile.id,
    )


# ---------------------------------------------------------------------------
# Estratégia: csv_com_preambulo  (Sicredi, exportação do aplicativo)
# ---------------------------------------------------------------------------

# "(01/02)" — o app escreve a parcela entre parênteses; o site, sem. O resto do
# portal só entende a forma do site (`travel.PARCELA_RE`, o `parcela_modelo` do
# formato de saída), então a diferença morre aqui, na leitura. Deixá-la passar
# faria a mesma compra sair como "(Parcela (01/02))" num arquivo e
# "(Parcela 01/02)" no outro, e as parcelas antigas parariam de ser excluídas
# do intervalo de viagem — a regex não casa com o parêntese.
PARCELA_ENTRE_PARENTESES = re.compile(r"^\((\d+\s*/\s*\d+)\)$")


def _ler_csv_com_preambulo(source, name: str, profile: BankProfile,
                           due_date: datetime | None) -> Statement:
    """CSV que começa com um bloco de rótulos e só depois vira tabela.

    É como o aplicativo do Sicredi exporta a fatura: treze linhas de
    `rótulo;valor` (associado, vencimento, resumo de despesas) e, mais abaixo,
    o cabeçalho `Data;Descrição;Parcela;Valor;…` com os lançamentos.

    Vale a pena ler o preâmbulo, e não pular direto para a tabela: é dali que
    saem o VENCIMENTO — que o Nubank obriga a perguntar e este não — e os
    totais declarados, que fazem a conferência de soma existir para este
    formato como já existia para o `.xls`. Sem eles a tela diria "não confere"
    ou, pior, "confere" sem ter conferido nada.
    """
    cfg = profile.leitura
    amount_of = make_amount_parser(cfg.get("numeros"))
    header = cfg.get("cabecalho") or {}
    fmt = cfg.get("data_lancamento", "%d/%m/%Y")

    blob = source.read() if hasattr(source, "read") else Path(source).read_bytes()
    if isinstance(blob, bytes):
        # utf-8-sig come o BOM que o app deixa no começo — sem isso o primeiro
        # rótulo vira "﻿ Associado" e nenhuma busca por prefixo o acha.
        blob = blob.decode(cfg.get("encoding", "utf-8-sig"), errors="replace")

    linhas = [[c.strip() for c in linha]
              for linha in csv.reader(io.StringIO(blob),
                                      delimiter=cfg.get("delimitador", ";"))]

    def rotulado(prefixo: str | None) -> str | None:
        """O primeiro valor da linha cujo rótulo começa com `prefixo`.

        Comparação por `normalize` e por PREFIXO porque o app escreve
        `(-) Pagamentos / Creditos (R$)` — sem acento em "Créditos" e com um
        sufixo de unidade que não interessa. Exigir igualdade exata obrigaria a
        copiar a grafia do banco caractere a caractere no YAML.
        """
        if not prefixo:
            return None
        alvo = normalize(prefixo)
        for linha in linhas:
            if linha and linha[0] and normalize(linha[0]).startswith(alvo):
                for celula in linha[1:]:
                    if celula:
                        return celula
        return None

    def somar(rotulos) -> float | None:
        """Soma vários rótulos num número só — ou None se nenhum existir."""
        if not rotulos:
            return None
        if isinstance(rotulos, str):
            rotulos = [rotulos]
        achados = [amount_of(rotulado(r)) for r in rotulos]
        achados = [v for v in achados if v is not None]
        return round(sum(achados), 2) if achados else None

    if due_date is None:
        venc = cfg.get("vencimento") or {}
        bruto = rotulado(venc.get("rotulo"))
        if bruto and DATE_RE.match(bruto):
            due_date = datetime.strptime(bruto, venc.get("formato", "%d/%m/%Y"))

    # O cabeçalho não está numa posição fixa: o preâmbulo muda de tamanho com a
    # situação da fatura. Acha-se pelo NOME da primeira coluna.
    nome_data = header.get("data", "Data")
    inicio = next((i for i, l in enumerate(linhas)
                   if l and normalize(l[0]) == normalize(nome_data)), None)
    if inicio is None:
        raise ProfileError(
            f"{name}: não achei a linha de cabeçalho começando em "
            f"'{nome_data}'. Este é o CSV que o app do banco exporta?")

    nomes = [normalize(c) for c in linhas[inicio]]

    def coluna(papel: str, padrao: str | None = None) -> int | None:
        alvo = header.get(papel, padrao)
        if not alvo:
            return None
        try:
            return nomes.index(normalize(str(alvo)))
        except ValueError:
            return None

    i_data = coluna("data", "Data")
    i_desc = coluna("descricao", "Descrição")
    i_valor = coluna("valor", "Valor")
    i_parcela = coluna("parcela", "Parcela")
    i_moeda = coluna("moeda_estrangeira")
    i_nome = coluna("titular")
    if i_data is None or i_desc is None or i_valor is None:
        raise ProfileError(
            f"{name}: o cabeçalho tem {linhas[inicio]} e faltam colunas de "
            "data, descrição ou valor")

    def celula(linha, i):
        return linha[i] if i is not None and i < len(linha) else ""

    entries: list[Entry] = []
    for linha in linhas[inicio + 1:]:
        if not linha or not DATE_RE.match(celula(linha, i_data)):
            continue
        valor = amount_of(celula(linha, i_valor))
        if valor is None:
            continue
        parcela = celula(linha, i_parcela)
        entre_parenteses = PARCELA_ENTRE_PARENTESES.match(parcela)
        entries.append(Entry(
            purchase_date=datetime.strptime(celula(linha, i_data), fmt),
            description=celula(linha, i_desc),
            installment=(entre_parenteses.group(1) if entre_parenteses
                         else parcela or None),
            amount=valor,
            international=bool(celula(linha, i_moeda)),
            cardholder=celula(linha, i_nome),
        ))

    resumo = cfg.get("resumo") or {}
    creditos = somar(resumo.get("creditos"))
    return Statement(
        name=name,
        due_date=due_date,
        entries=entries,
        declared_debits=somar(resumo.get("debitos")) or 0.0,
        # O app escreve o crédito com o sinal de quem SUBTRAI ("R$ -9.857,03");
        # `Statement.credits` conta a mesma coisa em módulo. Sem o `abs` a
        # conferência compararia 9.857,03 com -9.857,03 e nunca fecharia.
        declared_credits=None if creditos is None else abs(creditos),
        declared_balance=somar(resumo.get("total")),
        bank_id=profile.id,
        titular=rotulado((cfg.get("titular") or {}).get("rotulo")) or "",
    )


# ---------------------------------------------------------------------------
# Estratégia: csv_simples  (Nubank — placeholder)
# ---------------------------------------------------------------------------

def _ler_csv_simples(source, name: str, profile: BankProfile,
                     due_date: datetime | None) -> Statement:
    cfg = profile.leitura
    amount_of = make_amount_parser(cfg.get("numeros"))
    colunas = cfg.get("colunas") or {}
    fmt = cfg.get("data_lancamento", "%Y-%m-%d")

    blob = source.read() if hasattr(source, "read") else Path(source).read_bytes()
    if isinstance(blob, bytes):
        blob = blob.decode(cfg.get("encoding", "utf-8"), errors="replace")

    frame = pd.read_csv(io.StringIO(blob), sep=cfg.get("delimitador", ","), dtype=str)
    frame.columns = [str(c).strip() for c in frame.columns]

    def column(role: str) -> str | None:
        wanted = colunas.get(role)
        if not wanted:
            return None
        for candidate in frame.columns:
            if normalize(candidate) == normalize(str(wanted)):
                return candidate
        raise ProfileError(
            f"{name}: coluna '{wanted}' não encontrada — o arquivo tem "
            f"{list(frame.columns)}"
        )

    col_data, col_desc, col_valor = column("data"), column("descricao"), column("valor")
    col_parcela = column("parcela")
    if not (col_data and col_desc and col_valor):
        raise ProfileError(f"{name}: perfil precisa mapear data, descricao e valor")

    entries: list[Entry] = []
    for _, row in frame.iterrows():
        amount = amount_of(row[col_valor])
        if amount is None:
            continue
        try:
            purchase = datetime.strptime(str(row[col_data]).strip(), fmt)
        except ValueError:
            continue
        entries.append(Entry(
            purchase_date=purchase,
            description=str(row[col_desc]).strip(),
            installment=(str(row[col_parcela]).strip() if col_parcela
                         and _cell(row[col_parcela]) else None),
            amount=amount,
        ))

    # O CSV não declara totais; a conferência fica a cargo de quem exportou.
    return Statement(
        name=name, due_date=due_date, entries=entries,
        declared_debits=0.0, declared_credits=None, declared_balance=None,
        bank_id=profile.id,
    )


STRATEGIES = {
    "excel_secoes": _ler_excel_secoes,
    "csv_com_preambulo": _ler_csv_com_preambulo,
    "csv_simples": _ler_csv_simples,
}


def read_statement(
    source: Path | BinaryIO,
    name: str | None = None,
    profile: BankProfile | None = None,
    due_date: datetime | None = None,
) -> Statement:
    """Lê um extrato usando o perfil do banco.

    Sem perfil, assume o layout Sicredi — é o que mantém a CLI e os testes
    antigos funcionando sem mudança.
    """
    if name is None:
        name = Path(str(source)).name

    if profile is None:
        from .profiles import BankProfile as _BP
        profile = _BP(id="sicredi", nome="Sicredi", leitura={
            "estrategia": "excel_secoes",
            "vencimento": {"rotulo": "Data de Vencimento", "formato": "%d/%m/%Y"},
            "cabecalho": {"data": "Data", "descricao": "Descrição"},
            "internacional": {"marcador": "US$"},
            "fim_secao": "Valor Total",
            "resumo": {"creditos": "Pagamentos / Créditos", "total": "Valor Total(R$)"},
            "numeros": {"milhar": ".", "decimal": ","},
        })

    # O FORMATO sai do arquivo, não de uma pergunta na tela. Um banco pode
    # exportar de mais de um jeito (o Sicredi exporta dois), e a extensão já
    # separa os casos — ver `BankProfile.formato_de`.
    formato = profile.formato_de(name)
    if formato is None:
        raise ProfileError(
            f"{name}: {profile.nome} exporta {', '.join(profile.extensoes)}")
    # A estratégia recebe o formato escolhido como se fosse o `leitura` inteiro,
    # e por isso nenhuma delas precisou saber que existem vários.
    profile = replace(profile, leitura=formato)

    reader = STRATEGIES.get(formato.get("estrategia", "excel_secoes"))
    if reader is None:
        raise ProfileError(
            f"estratégia de leitura desconhecida: "
            f"{formato.get('estrategia')!r} "
            f"(disponíveis: {', '.join(STRATEGIES)})"
        )
    return reader(source, name, profile, due_date)
