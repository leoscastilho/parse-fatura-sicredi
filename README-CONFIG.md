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

O formato de entrada **não se edita pelo portal**. Ele descreve como o banco
exporta, que é fato do banco e não preferência de quem usa — e um fato que,
quando muda, muda para todo mundo ao mesmo tempo. Editável, cada instalação
podia acabar com um leitor diferente. (O formato de SAÍDA continua editável na
tela, e pelo motivo simétrico: ele descreve a SUA planilha.)

1. Copie `config/banks/nubank.yml` para `config/banks/<id>.yml`.
2. Ajuste `id`, `nome`, `tema` e o bloco `leitura`.
3. Rode a suíte contra um extrato real — é o `test_le_extrato_*` que prova que
   o perfil funciona, e é onde o arquivo de exemplo fica versionado.
4. Quando os totais fecharem, mude `validado: true`.

Estratégias de leitura disponíveis:

| `estrategia`        | Para que serve                                            |
|---------------------|-----------------------------------------------------------|
| `excel_secoes`      | Planilha com blocos, cada um com cabeçalho e "Valor Total" |
| `csv_com_preambulo` | CSV com um bloco de `rótulo;valor` antes da tabela         |
| `csv_simples`       | Uma tabela, cabeçalho na primeira linha                    |

Um formato novo é uma função nova em `core/statement.py` e um `estrategia:`
novo no YAML — nada mais no sistema precisa saber que ela existe.

### Mais de um formato no mesmo banco

Um banco pode exportar de vários jeitos. O Sicredi exporta dois — planilha
`.xls` pelo site, `.csv` pelo aplicativo — e eles não se parecem. Nesse caso
`leitura` ganha uma lista `formatos:`, cada item com a sua `estrategia` e as
suas `extensoes`:

```yaml
leitura:
  formatos:
    - id: site
      estrategia: excel_secoes
      extensoes: [".xls", ".xlsx"]
      # …
    - id: app
      estrategia: csv_com_preambulo
      extensoes: [".csv"]
      # …
```

**O portal escolhe pela extensão e nunca pergunta.** Quem baixou o arquivo já
sabe de onde ele veio; ter que contar isso à tela seria transferir ao usuário
uma distinção que o nome do arquivo resolve sozinho. A lista de extensões que a
dropzone anuncia é a união de todos os formatos.

Sem a lista, `leitura` inteiro é um formato só — que é o caso do Nubank.

## Recategorizar um CSV antigo

As regras melhoram todo mês; os CSVs já exportados ficaram com a categorização
de quando foram gerados. A aba **Recategorizar CSV** passa o motor atual por
cima deles.

A entrada é o próprio formato de saída, então não há configuração nova: o
estabelecimento sai de dentro de `Descrição`. Dá para subir vários arquivos,
inclusive o histórico todo junto.

**Formatos antigos funcionam.** Nada obriga a descrição a seguir o padrão de
hoje: sem `[Cartão]`, sem `{Em 15/Jul}`, com espaços sobrando — tudo é lido e
classificado igual, e a descrição volta **exatamente** como entrou. O
`{Em 15/Jul}` só serviria para ordenar, e a recategorização não reordena.

Colunas a mais que a planilha antiga tinha (`Mês`, `Ano`, `Filtro`) voltam
intactas, na mesma posição. A linha original é reescrita célula por célula com
UMA troca, então nem a formatação do número muda: `270.50` continua `270.50` em
vez de virar `270.5`. O único requisito é ter as colunas `Data`, `Categoria`,
`Descrição`, `Valor (R$)` e `Pago` — em qualquer ordem.

O compromisso, fixado em teste: **só a coluna `Categoria` muda**. Mesmas linhas,
mesma ordem, mesmos valores — para o diff contra o arquivo antigo mostrar
exatamente as reclassificações e mais nada.

Onde a regra não opina (marketplace, desconhecido, sem regra), a categoria que
já estava no arquivo é **mantida**. Zerar essas linhas descartaria as decisões
manuais acumuladas. Onde a regra opina e discorda do arquivo, a regra vence — é
para isso que você está reprocessando — mas toda troca aparece na tela
"Mudanças" antes de qualquer coisa ser exportada, porque uma regra nova pode
desfazer um ajuste que você fez à mão na planilha.

Depois disso a revisão é idêntica à de uma fatura: novos estabelecimentos,
marketplace linha a linha, conferência e exportação.

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
