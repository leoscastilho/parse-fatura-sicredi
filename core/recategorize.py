"""Recategorizar um CSV que já saiu daqui.

Todo mês as regras melhoram: uma palavra-chave nova, um chute confirmado, um
marketplace resolvido. Os CSVs antigos ficaram com a categorização de quando
foram gerados. Este módulo passa o motor atual por cima deles.

A entrada é o PRÓPRIO formato de saída, o que torna a leitura quase trivial:
o estabelecimento já está dentro de `Descrição` e a data da compra está no
`{Em 15/Jul}`. As mesmas funções que a interface usa para agrupar por
estabelecimento (`merchant_of`, `merchant_key`, `purchase_date_of`) servem aqui
sem adaptação.

DUAS GARANTIAS que os testes fixam:

  1. **Só a coluna Categoria muda.** Todas as outras células voltam EXATAMENTE
     como entraram, na mesma ordem de linhas e de colunas. A linha original é
     guardada inteira e reescrita célula por célula, trocando só a categoria —
     é o que preserva `270.50` (em vez de virar `270.5`), espaços na descrição
     e colunas que este portal nem conhece, como `Mês` e `Ano` de uma
     exportação antiga da planilha.
  2. **Nada é perdido em silêncio.** Onde a regra não tem opinião (marketplace,
     desconhecido, sem regra), a categoria que já estava no arquivo é mantida.
     Zerar essas linhas descartaria anos de decisão manual.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import BinaryIO

from .pipeline import ClassifiedLine
from .planilha import ANCORAS, ler_tabela, parse_valor, tabela_da_primeira_linha
from .profiles import OutputSchema
from .rules import LineState, Ruleset
from .text import merchant_key, merchant_of, purchase_date_of


class RecategorizeError(ValueError):
    pass


@dataclass
class CategoryChange:
    line_id: str
    descricao: str
    valor: float
    de: str
    para: str
    matched: str | None


def _decode(source: Path | BinaryIO) -> str:
    blob = source.read() if hasattr(source, "read") else Path(source).read_bytes()
    if isinstance(blob, str):
        return blob
    # utf-8-sig também come o BOM que o Excel adora deixar no começo.
    return blob.decode("utf-8-sig", errors="replace")


def read_output_csv(
    source: Path | BinaryIO,
    name: str = "",
    schema: OutputSchema | None = None,
    index: int = 0,
) -> list[ClassifiedLine]:
    """Lê um CSV no formato de saída e devolve linhas prontas para reclassificar.

    `categoria` vem vazia de propósito: quem preenche é a regra. A categoria que
    estava no arquivo fica em `categoria_anterior`, que é o que permite mostrar
    o diff e servir de fallback.
    """
    schema = schema or OutputSchema()
    texto = _decode(source)

    # O arquivo pode vir direto do Sheets, com título acima do cabeçalho e uma
    # coluna de margem à esquerda. É a MESMA limpeza que a aba de Análise faz
    # (`core/planilha.py`), não uma segunda parecida: duas cópias divergiriam no
    # dia em que a planilha ganhasse mais uma linha de enfeite, e aí uma aba
    # aceitaria o arquivo e a outra não.
    #
    # A âncora inclui o nome que a coluna de categoria tem NESTE formato de
    # saída: quem renomeou `Categoria` para `Classe` continua reprocessando os
    # próprios arquivos. Sem âncora reconhecida, cai para "a primeira linha é o
    # cabeçalho" — que é o formato de sempre, e deixa o erro seguinte dizer
    # quais colunas faltam em vez de um genérico "não achei o cabeçalho".
    tabela = (ler_tabela(texto, ANCORAS + (schema.coluna("categoria"),))
              or tabela_da_primeira_linha(texto))
    if tabela is None:
        raise RecategorizeError(f"{name}: arquivo vazio")

    presentes = [c.strip() for c in tabela.cabecalho]
    faltando = [c for c in schema.colunas if c not in presentes]
    if faltando:
        raise RecategorizeError(
            f"{name}: faltam as colunas {faltando}. "
            f"O arquivo tem {presentes} e o formato de saída espera {schema.colunas}"
        )

    # Linha mais larga que o cabeçalho é coluna sem nome COM dado dentro, e
    # reescrever o arquivo a perderia em silêncio — justamente o que este módulo
    # promete não fazer. Melhor recusar e dizer em qual linha.
    larga = next((i for i, l in enumerate(tabela.linhas) if len(l) > len(presentes)), None)
    if larga is not None:
        raise RecategorizeError(
            f"{name}: a linha {tabela.linha_do_cabecalho + larga + 1} tem "
            f"{len(tabela.linhas[larga])} colunas e o cabeçalho tem {len(presentes)}. "
            "Exporte de novo com nome em todas as colunas — sem nome, elas se "
            "perderiam no arquivo de volta.")

    reader = tabela.dicionarios()

    # Os nomes vêm do formato de saída, não de constantes: quem renomeou a
    # coluna para `Item`/`Valor` na planilha continua conseguindo reprocessar.
    col_data, col_cat = schema.coluna("data"), schema.coluna("categoria")
    col_desc, col_valor = schema.coluna("descricao"), schema.coluna("valor")
    col_pago = schema.coluna("pago")

    linhas: list[ClassifiedLine] = []
    for numero, row in enumerate(reader):
        # A descrição NÃO é normalizada nem aparada: ela volta como entrou.
        # O `.strip()` existe só para decidir se a linha tem conteúdo e para
        # alimentar a classificação.
        descricao = row.get(col_desc) or ""
        if not descricao.strip():
            continue

        bruto = (row.get(col_valor) or "").strip()
        # `parse_valor` é o mesmo leitor da aba de Análise: aceita tanto o
        # `270.50` que este portal exporta quanto o `R$ 55,327.76` que a
        # planilha formata, com o sinal antes ou depois do símbolo. A célula
        # ORIGINAL continua indo para `origem_row` — o número aqui é só para
        # somar e exibir, e o arquivo de volta leva a formatação que entrou.
        valor = parse_valor(bruto)
        if valor is None:
            raise RecategorizeError(
                f"{name}: linha {tabela.linha_do_cabecalho + numero + 1}, "
                f"valor ilegível: {bruto!r}."
            )

        data = (row.get(col_data) or "").strip()
        # `{Em 15/Jul}` é opcional: exportações antigas podem não ter. Sem ele
        # não há data de compra, e isso não impede nada — a recategorização não
        # reordena, então a data só serviria para ordenar.
        compra = purchase_date_of(data, descricao)
        anterior = (row.get(col_cat) or "").strip()
        estabelecimento = merchant_of(descricao.strip())

        linhas.append(ClassifiedLine(
            line_id=f"{index}:{numero}",
            statement=name,
            data=data,
            purchase_date=(compra or date.min).isoformat(),
            merchant_raw=estabelecimento,
            merchant=merchant_key(estabelecimento),
            descricao=descricao,
            valor=valor,
            pago=(row.get(col_pago) or "").strip(),
            categoria="",
            state=LineState.UNMAPPED,
            matched=None,
            categoria_anterior=anterior,
            origem_row=dict(row),
        ))

    if not linhas:
        raise RecategorizeError(f"{name}: nenhuma linha com descrição")
    return linhas


def recategorize(
    linhas: list[ClassifiedLine], rules: Ruleset
) -> tuple[list[ClassifiedLine], list[CategoryChange]]:
    """Aplica as regras atuais, preservando o que a regra não sabe decidir.

    Quando a regra devolve categoria e ela difere da que estava no arquivo, a
    regra vence — é por isso que você está reprocessando. Mas toda troca dessas
    entra em `mudancas`, para você conferir uma a uma antes de exportar: uma
    regra nova pode desfazer um ajuste que você fez à mão na planilha.
    """
    resultado: list[ClassifiedLine] = []
    mudancas: list[CategoryChange] = []

    for linha in linhas:
        match = rules.classify(linha.merchant_raw)
        anterior = linha.categoria_anterior or ""

        if match.categoria:
            categoria, state, matched = match.categoria, match.state, match.matched
            if categoria != anterior:
                mudancas.append(CategoryChange(
                    line_id=linha.line_id, descricao=linha.descricao,
                    valor=linha.valor, de=anterior, para=categoria,
                    matched=match.matched,
                ))
        else:
            # A regra não opina (marketplace, desconhecido, sem regra): mantém o
            # que já estava. O estado continua sendo o da regra, para a linha
            # aparecer no balde certo da revisão.
            categoria, state, matched = anterior, match.state, match.matched

        resultado.append(ClassifiedLine(**{
            **linha.to_dict(), "categoria": categoria,
            "state": state, "matched": matched,
        }))

    return resultado, mudancas


def lines_to_csv_preserving_order(
    linhas: list[ClassifiedLine], schema: OutputSchema | None = None,
    encoding: str | None = None,
) -> bytes:
    """Grava sem reordenar, devolvendo cada linha como ela entrou.

    Quando a linha veio de um CSV (`origem_row` preenchido), a saída é a linha
    original com UMA célula trocada. Isso mantém intactas coisas que o portal
    reescreveria sem querer: `270.50` não vira `270.5`, colunas desconhecidas
    como `Mês`/`Ano` não somem, e a descrição volta com os mesmos espaços.
    """
    schema = schema or OutputSchema()
    com_origem = [l for l in linhas if l.origem_row]

    if com_origem:
        colunas = list(com_origem[0].origem_row.keys())
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=colunas, lineterminator="\n",
                                extrasaction="ignore")
        writer.writeheader()
        col_cat = schema.coluna("categoria")
        for linha in linhas:
            row = (dict(linha.origem_row) if linha.origem_row
                   else linha.as_csv_row(schema))
            row[col_cat] = linha.categoria
            writer.writerow(row)
        return buffer.getvalue().encode(encoding or schema.encoding)

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=schema.colunas, lineterminator="\n")
    writer.writeheader()
    for linha in linhas:
        writer.writerow(linha.as_csv_row(schema))
    return buffer.getvalue().encode(encoding or schema.encoding)
