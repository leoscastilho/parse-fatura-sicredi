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

  1. **Só a coluna Categoria e as marcas de viagem mudam.** Todas as outras
     células voltam EXATAMENTE como entraram, na mesma ordem de linhas e de
     colunas. A linha original é guardada inteira e reescrita célula por
     célula — é o que preserva `270.50` (em vez de virar `270.5`), espaços na
     descrição e colunas que este portal nem conhece, como `Mês` e `Ano` de uma
     exportação antiga da planilha.

     A descrição entra nessa lista porque a viagem mora dentro dela: a
     categoria real vai entre parênteses e o nome da viagem entre chaves. Fora
     dessas duas marcas a descrição é intocada, byte a byte — o
     estabelecimento, o `(Parcela 04/05)` e o `{Em 28/Sep}` voltam como
     entraram.
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
from .text import merchant_key, merchant_of, normalize, purchase_date_of
from .travel import TRAVEL_CATEGORY, annotate, desanotar


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
    # `categoria`: a coluna Categoria muda de `de` para `para`.
    # `marca`: a coluna NÃO muda — continua `Viagem` — e o que foi atualizado é
    # a categoria real guardada dentro da descrição, entre parênteses.
    #
    # A distinção não é cosmética. Recusar uma mudança de coluna, na tela de
    # Mudanças, é fixar a categoria antiga naquela linha; fazer isso com uma
    # marca gravaria "Lazer" na coluna de uma linha que precisa continuar
    # "Viagem". Misturar as duas listas seria oferecer um botão que estraga.
    kind: str = "categoria"


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


def protegida(anterior: str, rules: Ruleset) -> str:
    """Por que esta linha não pode ser reclassificada — ou "" se ela pode.

    DUAS FAMÍLIAS, pelo mesmo motivo de fundo: nas duas a categoria não responde
    "o que foi comprado", e responder isso é a única coisa que a regra sabe
    fazer — ela lê a DESCRIÇÃO e procura palavra de estabelecimento.

    1. **As fixas** (`configuracao.categorias_fixas`): Renda Fixa, Renda
       Variável, Resgate Poupança, Poupança, Investimento. Dizem de onde o
       dinheiro veio ou para onde foi guardado. Na planilha as três primeiras
       são SOMADAS e o resto é subtraído, então trocar uma delas por `Presentes`
       não erra só o rótulo: erra o SINAL, e o estrago é o dobro do valor.

    2. **Viagem**: a linha foi para lá numa decisão manual, e a categoria real
       está guardada dentro da descrição, entre parênteses — `(Lazer) {Campo
       Belo}`. A regra reescreveria a coluna e deixaria o parêntese órfão: a
       linha voltaria a ser Lazer com um `(Lazer)` colado no nome, e a viagem
       sumiria da planilha sem deixar rastro.

       Aqui a proteção é só da COLUNA. A marca de dentro da descrição continua
       sendo reprocessada, porque ela é exatamente a resposta que as regras dão
       — ver `_atualizar_marca`.
    """
    if rules.is_fixed(anterior):
        return "categoria fixa"
    # Mesma régua das fixas — `normalize` — e não uma comparação exata: uma
    # proteção que exige o V maiúsculo e outra que aceita `renda variavel`
    # seriam duas regras para a mesma ideia, e a diferença só apareceria no dia
    # em que um arquivo antigo trouxesse `VIAGEM`.
    if normalize(anterior) == normalize(TRAVEL_CATEGORY):
        return "viagem"
    return ""


def _atualizar_marca(
    linha: ClassifiedLine, rules: Ruleset, mudancas: list[CategoryChange]
) -> ClassifiedLine:
    """Refaz a categoria real de uma linha que já está em `Viagem`.

    A coluna fica onde está — a linha foi para Viagem numa decisão manual e
    continua lá. O que envelhece é a marca DENTRO da descrição: `(Lazer)` foi a
    resposta das regras do ano passado para "o que essa compra seria se não
    fosse viagem", e reprocessar o arquivo é justamente pedir a resposta de
    hoje. Sem isto, reprocessar não faria nada nessas linhas e a marca ficaria
    congelada para sempre.

    Só ATUALIZA; nunca inventa. Linha de viagem sem parêntese nenhum é linha
    que este portal não anotou — veio marcada à mão da planilha — e escrever
    uma marca ali seria acrescentar informação que ninguém pediu, num arquivo
    cujo contrato é mudar o mínimo possível.

    O estabelecimento é relido da descrição LIMPA, e não reaproveitado de
    `linha.merchant_raw`: com as marcas dentro, a classificação veria
    "Campo Belo Country C (Lazer) {Campo Belo}" e uma palavra-chave que casasse
    com o nome da viagem ou com o nome da categoria decidiria a categoria — a
    marca escolhendo a si mesma.
    """
    conhecidas = rules.all_categories()
    base, categoria_antiga, rotulo = desanotar(linha.descricao, conhecidas)

    descricao = linha.descricao
    if categoria_antiga:
        match = rules.classify(merchant_of(base))
        if match.categoria and match.categoria != categoria_antiga:
            descricao = annotate(base, match.categoria, rotulo, conhecidas)
            mudancas.append(CategoryChange(
                line_id=linha.line_id, descricao=descricao, valor=linha.valor,
                de=categoria_antiga, para=match.categoria, matched=match.matched,
                kind="marca",
            ))

    return ClassifiedLine(**{
        **linha.to_dict(), "categoria": linha.categoria_anterior or "",
        "descricao": descricao, "state": LineState.AUTO, "matched": "viagem",
    })


def recategorize(
    linhas: list[ClassifiedLine], rules: Ruleset
) -> tuple[list[ClassifiedLine], list[CategoryChange]]:
    """Aplica as regras atuais, preservando o que a regra não sabe decidir.

    Quando a regra devolve categoria e ela difere da que estava no arquivo, a
    regra vence — é por isso que você está reprocessando. Mas toda troca dessas
    entra em `mudancas`, para você conferir uma a uma antes de exportar: uma
    regra nova pode desfazer um ajuste que você fez à mão na planilha.

    A exceção são as linhas PROTEGIDAS (ver `protegida`), onde a regra não vence
    nunca: ali a categoria de origem não é um chute velho esperando correção, é
    a informação que a linha existe para carregar.
    """
    resultado: list[ClassifiedLine] = []
    mudancas: list[CategoryChange] = []

    for linha in linhas:
        anterior = linha.categoria_anterior or ""

        motivo = protegida(anterior, rules)
        if motivo == "viagem":
            # Viagem é protegida na COLUNA, não na descrição: ver
            # `_atualizar_marca`.
            resultado.append(_atualizar_marca(linha, rules, mudancas))
            continue
        if motivo:
            # `AUTO`, não o estado da regra: a linha está resolvida e não pode
            # cair em "Novos" pedindo categoria — ela já tem a certa. São 826
            # linhas no arquivo dele; perguntadas uma a uma, a tela vira ruído.
            resultado.append(ClassifiedLine(**{
                **linha.to_dict(), "categoria": anterior,
                "state": LineState.AUTO, "matched": motivo,
            }))
            continue

        match = rules.classify(linha.merchant_raw)

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
        col_desc = schema.coluna("descricao")
        for linha in linhas:
            row = (dict(linha.origem_row) if linha.origem_row
                   else linha.as_csv_row(schema))
            row[col_cat] = linha.categoria
            # A descrição volta da LINHA, não da célula original, porque a
            # viagem escreve dentro dela. Quando nada de viagem aconteceu as
            # duas são o mesmo texto — `read_output_csv` guarda a descrição sem
            # aparar nem normalizar exatamente para que esta atribuição seja um
            # no-op no caso comum.
            row[col_desc] = linha.descricao
            writer.writerow(row)
        return buffer.getvalue().encode(encoding or schema.encoding)

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=schema.colunas, lineterminator="\n")
    writer.writeheader()
    for linha in linhas:
        writer.writerow(linha.as_csv_row(schema))
    return buffer.getvalue().encode(encoding or schema.encoding)
