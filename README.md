# parse-fatura-sicredi

Converte os extratos mensais do cartão Sicredi (`.xls`) no CSV que a planilha
de finanças pessoais consome:

```
Data,Categoria,Descrição,Valor (R$),Pago
08/10/2026,Alimentação,[Cartão] Supermercados Alvora {Em 17/Jul},476.61,x
```

## Por que a data é assim

A fatura só é baixada **depois de fechada**, então ela contém as compras do mês
anterior que serão pagas na fatura deste mês. Por isso:

- **`Data`** = data de vencimento da fatura (`Data de Vencimento` lida do
  próprio arquivo). Uma data só para a fatura inteira, que é como a planilha
  agrega o mês.
- **A data real da compra** fica preservada no fim da descrição: `{Em 17/Jul}`.
- **`Pago`** = `x` sempre, porque a fatura já foi paga quando você exporta.

Como a data vem do arquivo e não de `datetime.now()`, reprocessar um extrato
antigo continua gerando o mesmo resultado.

## Um CSV só, pronto pra colar

Todos os extratos do `input/` viram **um arquivo só** em `output/`, nomeado
pelo período que cobre — `fatura_2026-08.csv` para um extrato,
`faturas_2025-04_a_2026-08.csv` para vários. O nome carrega o período de
propósito: rodar de novo no mês seguinte não sobrescreve o anterior.

Use `--output-file meu_nome.csv` para escolher o nome, ou `--split` para voltar
a um CSV por extrato. Trocar de modo apaga o arquivo do outro modo, para você
nunca colar na planilha o resultado de uma execução antiga.

### Ordenação

1. **Data** (vencimento da fatura) — cada fatura vira um bloco contíguo
2. **Categoria** — A→Z, ignorando acentos; **sem categoria vai para o fim** de
   cada bloco
3. **Data da compra** — a de `{Em 15/Jul}`, não a do vencimento

O ano da compra não aparece na descrição, então é reconstruído: o ano mais
recente que não passa do vencimento. Isso ordena parcelas antigas corretamente
— `{Em 21/Aug}` numa fatura de abril/2025 é agosto de **2024**. Conferido
contra as 331 linhas dos quatro extratos: zero divergências.

## Instalação

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Uso

```bash
python main.py                              # input/ -> output/fatura_AAAA-MM.csv
python main.py --split                      # um CSV por extrato
python main.py --output-file agosto.csv     # nome fixo para o CSV combinado
python main.py --report-file review.txt     # salva o relatório de revisão
python main.py --no-interactive             # não abre o categorizador no fim
python main.py --strict                     # sai com erro se algo ficar sem categoria
python main.py --encoding utf-8-sig         # se o Excel comer os acentos
```

Opções: `--input`, `--output`, `--output-file`, `--split`, `--rules`,
`--report-file`, `--encoding`, `--strict`, `--no-interactive`.

## O relatório

Toda execução imprime, por arquivo:

```
Extrato Agosto Sicredi.xls  ->  Extrato Agosto Sicredi.csv
  Vencimento .......... 10/08/2026   (coluna Data = 08/10/2026)
  Lançamentos ......... 104 exportados, 1 descartados
  Débitos ............. R$    15,580.27  vs. fatura R$    15,580.27   [OK]
  Créditos ............ R$    14,157.90  vs. fatura R$    14,157.90   [OK]
  Descartados (pagamento da fatura anterior):
    - Pag Fat Deb Cc  R$ -13,928.90
  Revisar (25):
    [SEM CATEGORIA] Loja Do Japones          R$      25.00
    [genérico     ] AMAZON BR                R$      60.61  -> Casa

==============================================================================
327 lançamento(s) de 4 fatura(s)  ->  output/faturas_2025-04_a_2026-08.csv
```

- **Débitos / Créditos** conferem a soma lida contra os totais que a própria
  fatura declara. Se der `DIVERGE`, algum lançamento não foi lido — não cole na
  planilha antes de olhar.
- **`SEM CATEGORIA`** = não casou com nada; o `categorize.py` vai perguntar.
- **`MARKETPLACE`** = Amazon / Mercado Livre. Sai com categoria **vazia de
  propósito** e o `categorize.py` não pergunta — você preenche na planilha,
  compra a compra.

## `categorize.py` — o conhecimento que cresce

Resolve os lançamentos que ficaram sem categoria e **grava a resposta no
`categories.yml`**, para que no mês seguinte o mesmo estabelecimento já saia
classificado.

```bash
python categorize.py            # sessão interativa
python categorize.py --list     # só lista os pendentes, sem perguntar
```

Também roda **automaticamente no fim do `main.py`** quando aparece
estabelecimento novo — inclusive rodando pelo PyCharm. Desligue com
`--no-interactive`. Sem entrada disponível (cron, pipe fechado) ele não trava:
avisa e sai.

Ele agrupa por estabelecimento, não por linha: `SUPERMERCADOS ALVORA` aparece
23 vezes numa fatura e é perguntado **uma** vez. O número da transação é
ignorado no agrupamento, então `UNITED01624563906420` e `...431` são o mesmo
estabelecimento — e a palavra-chave gravada (`UNITED`) casa com as próximas
compras, em vez de valer só para aquele número.

```
──────────────────────────────────────────────────────────────────────────────
[2/3]  ATEMPORAL
    1 lançamento(s) · R$ 117.16 · Extrato Maio Sicredi.csv
    [Cartão] Atemporal {Em 12/Apr}

      1) Ajuste                  2) Alimentação             3) Assinaturas
      ...
    n) nova categoria    d) não sei (nunca mais perguntar)    p) pular    q) sair
```

| Resposta | O que faz |
|---|---|
| `<número>` | Escolhe a categoria e grava a palavra-chave em `palavras` |
| `n` | Cria uma categoria nova (já aparece na lista do próximo item) |
| `d` | Vai para `desconhecidos` — **nunca mais é perguntado** |
| `p` | Pula; volta a perguntar na próxima execução |
| `q` | Sai salvando o que já foi decidido |

Depois de cada decisão os CSVs são recategorizados e reordenados na hora.
Categorias que você editou à mão no CSV são preservadas — só o que está vazio
é preenchido.

O YAML é editado **por inserção de linha**, não por reserialização: seus
comentários, sua ordem e sua formatação ficam intactos. Toda gravação é
reconferida relendo o arquivo; se não tiver funcionado, o script avisa em vez
de seguir em silêncio.

## Categorização

Todas as regras ficam em **`categories.yml`** — o `main.py` não precisa ser
tocado para adicionar categoria ou palavra-chave. O arquivo é comentado; em
resumo:

| Bloco | O que faz |
|---|---|
| `configuracao.categorias` | Lista oferecida pelo `categorize.py` |
| `excluir` | Descarta a linha (pagamento da fatura anterior) |
| `regras` | Lista ordenada, regex, primeira que casar vence |
| `palavras` | Categoria → trechos; vence o trecho mais longo |
| `marketplaces` | Categoria vazia de propósito; não pergunta, mas avisa |
| `desconhecidos` | Categoria vazia; não pergunta e não avisa |

O que não casar com nada fica com **Categoria vazia** — nunca com um chute.

### Por que marketplace é um caso à parte

Amazon e Mercado Livre vendem ração, monitor, livro e panela. Gravar uma
palavra-chave para `MERCADOLIVRE` fixaria uma categoria que estaria errada na
maioria das compras — por isso esses ficam em `marketplaces`, saem em branco e
o `categorize.py` nem pergunta: não existe resposta única para gravar.

Sub-lojas que dão para identificar ficam em `regras`, que roda **antes**, e
ganham do genérico:

```yaml
regras:
  - padrao: "MERCADOLIVRE (LIVROS|LEITURA)"
    categoria: Educação
  - padrao: "MERCADOLIVRE GROW"
    categoria: Hobby
  - padrao: "AMAZON ?PRIME"
    categoria: Assinaturas
```

Nos quatro extratos isso salva 9 linhas do branco: as parcelas da Esplan e da
TechShop (Eletrônicos), os livros, o Grow (Hobby), o Amazon Prime
(Assinaturas) e o Empório (Alimentação).

A descrição é normalizada antes de comparar: maiúsculas, sem acentos,
pontuação vira espaço, e `CamelCase` é separado (`OggiSantaRita` →
`OGGI SANTA RITA`). O casamento também ignora espaços, então `GRELHA GRILL`
acha `GrelhaGrill`.

Para criar uma categoria nova, basta acrescentar a chave em `palavras`:

```yaml
palavras:
  Viagem:
    - AIRBNB
    - BOOKING
    - LATAM
```

### Viagem é uma data, não um estabelecimento

Palavra-chave não resolve viagem. O restaurante da esquina e o restaurante de
Gramado casam com o mesmo `RESTAURANTE`; o que os separa é **quando** você
comprou. Por isso a viagem se declara por **período**, no portal, depois de
subir as faturas:

1. Você informa um ou mais intervalos (ida e volta). Os seletores ficam presos
   ao intervalo real de compras do lote — não dá para marcar uma viagem que as
   faturas nem cobrem.
2. Tudo que foi **comprado** dentro de um intervalo aparece numa etapa própria,
   já marcado. A data comparada é a da compra (`{Em 15/Jul}`), não a do
   vencimento — uma parcela de 2024 não vira despesa de viagem por cair na
   fatura de agosto de 2026.
3. Você desmarca o que não é viagem (o jogo comprado no eShop no meio do
   passeio), e o resto é confirmado.

O confirmado vira `Viagem` na coluna Categoria, e **a categoria real vai para a
descrição**, entre parênteses, logo antes da data da compra:

```
08/10/2026,Viagem,[Cartão] B91 Supremo Pizzaria (Alimentação) {Em 15/Jul},142.90,
```

Assim a planilha continua respondendo "quanto gastei em comida naquela viagem?"
sem perder o total da viagem.

> Vale só na importação de fatura. A recategorização promete devolver o arquivo
> com **só** a coluna Categoria alterada, e a viagem escreve dentro da
> descrição — os dois contratos não cabem juntos.

## Lançamentos negativos

O **pagamento da fatura anterior** (`PAGAMENTO DEBITO`, `Pag Fat Deb Cc`) é
descartado — não é despesa, e você lança à mão como "Cartão de crédito".

Os outros negativos que aparecem na fatura — estorno de anuidade
(`Credito Anuidade Car`) e devolução de compra (`Devolucao de Compras`) — saem
como **Ajuste**, com o valor negativo, no fim do bloco da categoria. Renda Fixa
e Renda Variável não vêm da fatura; são lançadas direto na planilha.

## Seções lidas

O parser lê **todas** as seções de lançamento do extrato:

- `Despesas no Brasil`
- `Despesas Internacionais` — o valor em R$ entra no CSV como qualquer outro
  lançamento (o valor em US$ é ignorado, já que a planilha trabalha em reais)

## Dados sensíveis

`input/`, `output/` e `documents/` contêm número de conta e histórico
financeiro completo. Confirme que estão no `.gitignore` antes de qualquer push.
