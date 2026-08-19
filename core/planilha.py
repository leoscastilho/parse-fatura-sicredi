"""Ler o CSV como a planilha EXPORTA, não como um CSV ideal.

O Google Sheets não baixa a tabela: baixa a aba inteira, com a formatação que
existe para o arquivo ficar bonito na tela. O que chega é isto:

    a,,,,,,,,
    ,Minhas contas,,,,,,,
    ,Data,Categoria,Item,Valor,Pago,Mês,Ano,Filtro
    ,Aug,Restante,Restante do mês de Agosto,"R$ 55,327.76",,8,2018,TRUE

Três coisas que um leitor ingênuo não sobrevive:

  1. o cabeçalho NÃO é a primeira linha — há título e sobra acima dele;
  2. existe uma coluna vazia à esquerda, que só serve de margem;
  3. os números vêm formatados como moeda (`R$ 55,327.76`), com o símbolo, o
     separador de milhar e às vezes o sinal antes do `R$`.

Isto tudo já era tolerado na aba de Análise. Este módulo é onde essa tolerância
passou a morar, para que a Recategorização use A MESMA regra em vez de uma
segunda implementação parecida — duas cópias divergem no dia em que a planilha
ganhar mais uma linha de enfeite, e aí um caminho aceita o arquivo e o outro
não.

Nada aqui interpreta significado: a coluna Categoria não é normalizada, a data
não é convertida, o valor não é arredondado. Isto é limpeza de FORMA.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass

# `-R$ 1.234,56` / `R$ 1,234.56` / `(R$ 10)` — o sinal pode vir antes do símbolo.
_LIMPA = re.compile(r"[^\d,.\-]")

# Como reconhecer a linha de cabeçalho no meio das linhas de enfeite. Categoria
# é a âncora porque é a única coluna que existe em toda versão da planilha e em
# todo formato de saída — Descrição já se chamou Item, Valor já foi Valor (R$).
ANCORAS = ("categoria", "category")


def chave(texto: str) -> str:
    """Compara textos sem depender de acento, caixa ou espaço sobrando."""
    t = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", t).strip().upper()


def parse_valor(bruto: str) -> float | None:
    """Número a partir do que o Sheets exporta.

    Devolve `None` em vez de levantar: uma célula ilegível numa planilha de 14
    anos é normal, e derrubar a leitura inteira por causa dela seria pior do que
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


@dataclass(frozen=True)
class Tabela:
    """A tabela de verdade que estava dentro do arquivo."""

    cabecalho: list[str]
    linhas: list[list[str]]
    # Número da linha do cabeçalho NO ARQUIVO, base 1. Mensagem de erro que
    # aponta para a linha 4 quando o usuário vê a linha 7 no editor é pior do
    # que mensagem nenhuma.
    linha_do_cabecalho: int

    def dicionarios(self) -> list[dict[str, str]]:
        """Cada linha como `{coluna: valor}`, no formato que o `csv.DictReader`
        devolveria se o arquivo fosse limpo desde o começo.

        Linha mais curta que o cabeçalho vira célula vazia, não KeyError: a
        última coluna costuma vir sem vírgula final quando está vazia.
        """
        return [
            {nome: (linha[i] if i < len(linha) else "")
             for i, nome in enumerate(self.cabecalho)}
            for linha in self.linhas
        ]


def ler_tabela(texto: str, ancoras: tuple[str, ...] = ANCORAS,
               limite_busca: int = 10) -> Tabela | None:
    """Acha a tabela dentro do arquivo. `None` quando nenhuma âncora aparece.

    Devolver `None` em vez de levantar é de propósito: sem âncora ainda pode
    haver uma tabela — só não deu para reconhecê-la. Quem chama decide entre
    `tabela_da_primeira_linha` (e uma mensagem de erro melhor, que diz QUAIS
    colunas faltam) e recusar de vez.
    """
    linhas = _linhas_uteis(texto)
    if not linhas:
        return None

    alvos = {chave(a).lower() for a in ancoras if a and str(a).strip()}
    for i, linha in enumerate(linhas[:limite_busca]):
        if any(chave(c).lower() in alvos for c in linha):
            return _sem_colunas_vazias(linha, linhas[i + 1:], i + 1)
    return None


def tabela_da_primeira_linha(texto: str) -> Tabela | None:
    """O caso simples: a primeira linha com conteúdo É o cabeçalho.

    Serve de recuo quando nenhuma âncora foi reconhecida — inclusive para poder
    dizer "faltam as colunas X e Y" em vez de "não achei o cabeçalho", que é
    verdadeiro e inútil para quem precisa saber o que consertar.
    """
    linhas = _linhas_uteis(texto)
    if not linhas:
        return None
    return _sem_colunas_vazias(linhas[0], linhas[1:], 1)


def _linhas_uteis(texto: str) -> list[list[str]]:
    return [linha for linha in csv.reader(io.StringIO(texto))
            if any(celula.strip() for celula in linha)]


def _sem_colunas_vazias(cabecalho: list[str], corpo: list[list[str]],
                        linha_do_cabecalho: int) -> Tabela:
    """Descarta as colunas vazias do começo ao fim — em CABEÇALHO E CORPO.

    A margem da esquerda é uma coluna sem nome e sem nenhum valor. Mantê-la
    custaria duas vezes: o `DictReader` a chamaria `''` e ela reapareceria no
    arquivo reescrito como uma vírgula solta no começo de cada linha.

    Só some a coluna que está vazia em TODAS as linhas. Uma coluna sem nome mas
    com dados fica — é o caso do `all.csv`, cujo cabeçalho declara 5 nomes para
    8 colunas de dados, e as três últimas (Mês, Ano, Filtro) carregam conteúdo.
    """
    largura = max([len(cabecalho), *(len(l) for l in corpo)], default=0)

    def vazia(i: int) -> bool:
        if i < len(cabecalho) and cabecalho[i].strip():
            return False
        return all(i >= len(l) or not l[i].strip() for l in corpo)

    manter = [i for i in range(largura) if not vazia(i)]

    # O CABEÇALHO não é preenchido até a largura do corpo, e isso importa: o
    # `all.csv` declara 5 nomes para 8 colunas de dados, e é justamente a
    # diferença entre os dois números que diz onde começam as colunas sem nome
    # (Mês, Ano, Filtro). Igualar os tamanhos apagaria essa informação.
    return Tabela(
        cabecalho=[cabecalho[i] for i in manter if i < len(cabecalho)],
        linhas=[[linha[i] if i < len(linha) else "" for i in manter]
                for linha in corpo],
        linha_do_cabecalho=linha_do_cabecalho,
    )
