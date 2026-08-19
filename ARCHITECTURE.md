# Arquitetura — v2 (FastAPI + React)

## O problema central: tirar o `input()`

A CLI podia parar e perguntar. Um servidor HTTP não pode. Cada pergunta do
terminal virou **estado persistido + um request posterior**:

| CLI (v1)                          | Web (v2)                                     |
|-----------------------------------|----------------------------------------------|
| `input("categoria? ")`            | `/upload` devolve `unmapped_items`; o React pergunta |
| grava no YAML na hora             | `/update-mapping` grava no YAML de trabalho da transação |
| reescreve o CSV a cada resposta   | `/preview` recalcula o dataset inteiro, idempotente |
| `to_csv()` no fim                 | `/export` faz streaming do mesmo dataset      |

O que amarra tudo é o `transaction_id`.

## Modelos: um só, do parsing ao download

```
.xls ──read_statement──▶ Entry ──classify_statement──▶ ClassifiedLine ──▶ CSV
                       (dataclass)                     (dataclass, canônico)
                                                              │
                                                     LineItem.from_core
                                                              ▼
                                                     LineItem (pydantic) ──▶ JSON
```

`ClassifiedLine` é o modelo canônico e **não muda de forma em lugar nenhum**.
`LineItem` é o espelho Pydantic dele, campo a campo, usado só na borda HTTP
para validação e serialização. `LineItem.from_core()` / `.to_core()` são a
única ponte, e são triviais justamente porque os campos são idênticos.

**`line_id` (`"<índice do extrato>:<linha>"`) é determinístico.** O React
devolve atribuições referenciando o mesmo id que recebeu, então o backend não
guarda mapa de tradução nenhum: só aplica overrides sobre as linhas do SQLite.

### Decisões vivem separadas das linhas

```
transactions
├── lines_json        ← o que foi LIDO. imutável durante a transação.
└── assignments_json  ← o que o USUÁRIO decidiu. sobrescrito a cada passo.
```

`_apply_assignments()` combina os dois e devolve **cópias novas**. Consequências:

* `/preview` é idempotente — chamar dez vezes dá o mesmo resultado;
* um erro de atribuição nunca corrompe o que veio do extrato;
* dá para voltar uma etapa no wizard sem reprocessar o `.xls`.

Precedência: **linha > estabelecimento > regra**. É o que permite o marketplace
funcionar (mesma Amazon, Casa numa compra e Hobby na seguinte) sem quebrar o
agrupamento das outras telas.

### Os quatro estados

`LineState` existe porque "sem categoria" não é uma coisa só:

| estado        | categoria | pergunta? | avisa? | por quê |
|---------------|-----------|-----------|--------|---------|
| `auto`        | definida  | não       | não    | casou com regra/palavra-chave |
| `unmapped`    | vazia     | **sim**   | sim    | nada casou; grava palavra-chave |
| `marketplace` | vazia     | não*      | sim    | varia a cada compra; decisão por linha |
| `ignored`     | vazia     | não       | não    | você já disse que não sabe |

\* o marketplace é perguntado **por linha**, e a resposta não vira palavra-chave.

## Viagem: a classificação que é uma janela de tempo

O restaurante da esquina e o restaurante de Gramado casam com a mesma
palavra-chave. O que os separa é **quando** a compra foi feita, e nenhuma regra
do `categories.yml` sabe expressar isso. Daí um eixo separado.

```
transactions
├── travel_json           ← os PERÍODOS (input do usuário)
└── travel_rejected_json  ← as exceções desmarcadas na confirmação
```

Três decisões que sustentam o resto:

* **A marca `viagem` não é gravada em `lines_json`.** Ela é derivada dos
  períodos a cada leitura (`mark_travel` dentro de `_lines_of`). É o que
  preserva a imutabilidade das linhas lidas e torna reeditar um período
  trivialmente idempotente: apagou o período, sumiu a marca, sem nada a
  desfazer. `POST /travel` é **substitutivo** pelo mesmo motivo — remover é
  mandar a lista sem o item, e não existe DELETE.
* **A data comparada é a da COMPRA**, nunca a do vencimento. Uma parcela
  comprada em 21/08/2024 numa fatura que vence em 10/08/2026 não é despesa de
  uma viagem em agosto de 2026. É o único ponto em que esta implementação e uma
  que olhasse a coluna `Data` divergem, e há um teste dedicado a ele.
* **A conversão roda por último**, depois de `_apply_assignments`. A categoria
  que vai para o parêntese é a FINAL — com marketplace e correções manuais já
  resolvidos —, não o chute da regra.

O resultado: `Categoria` vira `Viagem` e a categoria real entra na descrição,
logo antes do `{Em 15/Jul}`:

```
[Cartão] B91 Supremo Pizzaria (Alimentação) {Em 15/Jul}
```

Assim a planilha continua respondendo "quanto gastei em comida naquela viagem?".
A anotação é idempotente (refazer o `/preview` não empilha parênteses) e, sem
categoria real, a linha vira `Viagem` **sem** parêntese em vez de ganhar um
rótulo inventado.

**Não vale na recategorização** (`409`): aquele fluxo promete devolver o arquivo
com só a coluna Categoria alterada, e a viagem escreve dentro da descrição. Os
dois contratos não cabem juntos.

## Autenticação no GitHub

* **Fine-grained PAT**, `Contents: Read and write`, **um repositório só**.
  Não é classic token com escopo `repo` — esse daria acesso a todos os
  privados, e este container precisa de um arquivo.
* Entra por `FATURA_GITHUB_TOKEN`, de um `.env` fora da imagem e fora do git,
  ou de um Docker secret. **Nunca `ARG`/`ENV` no Dockerfile** — ficaria gravado
  numa layer e apareceria em `docker history`.
* No processo é `SecretStr`: não sai em log, traceback, `repr()` nem resposta.
* Nenhum endpoint recebe, devolve ou ecoa o token.
* Ausente = app funciona inteiro, só não publica.

**Concorrência.** O SHA do `categories.yml` é lido no `/upload` e guardado na
transação. No commit, comparamos com o SHA atual: se você editou o arquivo no
Mac e deu push durante a revisão, o commit é recusado com **409** em vez de
sobrescrever seu trabalho.

**Comentários.** O YAML enviado é sempre produzido por `core/yaml_edit.py`, que
**insere linhas** no texto original. Um `yaml.dump` apagaria todos os seus
comentários (`# ? chocolateria — histórico oscila…`) e reordenaria o arquivo.
Toda edição é reconferida com `yaml.safe_load` antes de ser aceita.

## Por que não tem container de banco

Estado transacional em **SQLite num volume**. Um usuário, uma sessão por vez.
Vantagens sobre Redis aqui: um container a menos no Proxmox, e a revisão
sobrevive a um restart do container — perder 30 minutos de trabalho porque o
Redis reiniciou sem persistência seria pior que o "problema" que ele resolve.
TTL de 24h aplicado na leitura **e** varrido no startup.

Se um dia precisar de mais de um worker: troque por Postgres **antes** de subir
`--workers`, porque o WAL do SQLite não gosta de escritores concorrentes.
