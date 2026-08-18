# Configuração — onde mora cada coisa

```
config/
├── categories.yml     regras de categorização — COMPARTILHADAS entre bancos
├── output.yml         formato do CSV — COMPARTILHADO
└── banks/
    ├── sicredi.yml    perfil de leitura + tema (validado)
    └── nubank.yml     placeholder (validado: false)
```

**Por que YAML e não um banco de dados.** As regras são configuração, não dados:
mudam devagar, precisam de revisão, e o histórico importa. Como arquivo, elas
vêm de graça com `git diff`, `git blame` e pull request. Como tabela, exigiriam
um container a mais, migração e backup — e você perderia os comentários.

**Por que não JSON.** JSON não tem comentário, e é neles que vivem os `# ?` que
marcam os chutes de categorização esperando sua confirmação. Toda a edição pelo
portal é feita por inserção/remoção de LINHA justamente para preservá-los: um
`yaml.dump` devolveria o arquivo ordenado alfabeticamente e sem uma única nota.

## Categorias são compartilhadas entre bancos

"Supermercados Alvora" é o mesmo estabelecimento no Sicredi ou no Nubank. Um
conjunto só de palavras-chave significa que o conhecimento acumulado vale para
qualquer cartão que você adicionar depois.

O que é por banco é só a LEITURA: onde está a data de vencimento, como se
chamam as colunas, se o número usa vírgula ou ponto.

## Adicionar um banco

1. Copie `config/banks/nubank.yml` para `config/banks/<id>.yml`.
2. Ajuste `id`, `nome`, `tema` e o bloco `leitura`.
3. Na aba **Formato de entrada**, escolha o banco, cole o YAML e use
   **testar com um arquivo** — ele roda contra um extrato real sem gravar nada.
4. Quando os totais fecharem, mude `validado: true`.

Estratégias de leitura disponíveis:

| `estrategia`   | Para que serve                                            |
|----------------|-----------------------------------------------------------|
| `excel_secoes` | Planilha com blocos, cada um com cabeçalho e "Valor Total" |
| `csv_simples`  | Uma tabela, cabeçalho na primeira linha                    |

Um formato novo é uma função nova em `core/statement.py` e um `estrategia:`
novo no YAML — nada mais no sistema precisa saber que ela existe.

## Levar a configuração embora

**Configuração → Exportar** baixa um `.zip` com tudo. **Importar** valida o
pacote inteiro antes de gravar qualquer coisa (e mostra o que tem dentro antes
de você confirmar).

É esse par que permite outra pessoa usar o portal com as regras dela sem que o
servidor guarde nada dela: as regras viajam no arquivo, não numa tabela.

## Testes

```bash
python -m pytest tests/ -q     # 91 testes do backend
cd web && npm test             # 12 testes do front
```

Os dois rodam dentro do `docker build`: um estágio `test` no Dockerfile do
backend e um `RUN npm test` no do front. Teste vermelho = imagem não sai.

A suíte é hermética — gera os próprios extratos em vez de depender de
`input/*.xls`, que é gitignored e não entra na imagem. Um teste extra roda
contra os extratos reais quando eles existem, mas nada obrigatório depende disso.
