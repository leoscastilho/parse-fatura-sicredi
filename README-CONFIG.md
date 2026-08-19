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
