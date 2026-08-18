# Backlog — parse-fatura-sicredi

Rascunho das issues. Cada uma tem **contexto**, **o que fazer** e **pronto quando**,
para você conseguir pegar qualquer uma daqui a dois meses sem reconstruir o
raciocínio.

Ordem sugerida de ataque:

1. `#1 auth` — bloqueia o deploy na nuvem, e é a única com risco real de dados
2. `#2 CI` — barato, e protege tudo que vier depois
3. `#5 troca de banco` e `#6/#7 ordenação` — bugs/UX que você já sentiu
4. o resto por interesse

---

## 1. Autenticação antes de qualquer exposição pública

**Prioridade: alta — bloqueia a #4.** `seguranca`, `bloqueador`

Hoje o portal não tem autenticação nenhuma. Na LAN isso é aceitável; exposto na
internet não é: o CSV gerado contém **seu histórico completo de gastos**, e o
`/config/import` aceita substituir a configuração inteira sem credencial.

O `nginx` já é o ponto de entrada, então a opção mais barata é `auth_basic` com
um `.htpasswd` montado por volume. Se quiser algo melhor, um proxy com OIDC
(oauth2-proxy, Authelia) ou simplesmente deixar tudo atrás da Tailscale e nunca
publicar porta.

**Fazer**
- [ ] Decidir entre basic auth no nginx, proxy OIDC, ou só Tailscale/WireGuard
- [ ] Se HTTP: obrigar HTTPS, porque basic auth em claro é teatro
- [ ] Rate limit no `/upload` e no `/config/import`
- [ ] Documentar no README que sem isso a app não deve sair da LAN

**Pronto quando** um `curl` sem credencial em `/config` e `/export` devolver 401.

---

## 2. CI no GitHub Actions

**Prioridade: alta.** `infra`

Já existem 114 testes (100 backend + 14 front), mas eles só rodam no
`docker build`. Um push com o código quebrado passa despercebido até a próxima
imagem — e o build é justamente o momento em que você menos quer descobrir.

**Fazer**
- [ ] Workflow rodando `pytest tests/ -q` e `npm test` em cada push e PR
- [ ] Rodar `docker build` dos dois Dockerfiles no CI (pega erro de layer/COPY)
- [ ] Badge de status no README
- [ ] Dependabot ou Renovate — as versões estão fixadas em `requirements.txt`
      de propósito, e sem robô elas apodrecem em silêncio

**Pronto quando** um PR com teste quebrado ficar vermelho antes do merge.

---

## 3. Formato de entrada/saída com editor visual, não só YAML

**Prioridade: média.** `ux`

Hoje as abas "Formato de entrada" e "Formato de saída" são um `<textarea>` com
YAML cru. Funciona para quem escreveu o schema; não funciona para configurar um
banco novo daqui a seis meses.

A ideia é inverter: **formulário como interface, YAML como resultado**. Os
campos são poucos e conhecidos (rótulo do vencimento, nome das colunas,
separador decimal, formato de data), e o YAML gerado continua sendo o que é
commitado — nada de guardar configuração em banco.

**Fazer**
- [ ] Formulário por estratégia (`excel_secoes`, `csv_simples`) gerando o YAML
- [ ] Modo "avançado" que mostra e edita o YAML cru, para o que o form não cobrir
- [ ] Preview lado a lado: arquivo cru → colunas detectadas → linha do CSV final
- [ ] Ao subir um extrato desconhecido, **sugerir** o mapeamento de colunas a
      partir do cabeçalho encontrado
- [ ] Mostrar o diff do YAML antes de salvar

**Pronto quando** der para configurar um banco novo sem digitar YAML.

---

## 4. Deploy na nuvem

**Prioridade: média — depende da #1.** `infra`

Hoje roda em Docker no Proxmox. Para acessar de fora:

- **Depende de #1**, sem exceção.
- Segredos: `FATURA_GITHUB_TOKEN` não pode virar env var em painel compartilhado;
  usar o cofre do provedor ou Docker secret.
- Estado: o SQLite vive num volume. Em provedor com disco efêmero
  (Cloud Run, Fly sem volume) a transação morre no meio da revisão. Ou monta
  volume, ou troca por Postgres — o `Store` já isola isso num lugar só.
- Custo: sobe uma imagem de ~350MB + nginx; qualquer VPS de 1GB dá conta.

**Fazer**
- [ ] Escolher destino (VPS + Traefik, Fly.io, Railway…)
- [ ] Volume persistente para `/data` ou migrar o `Store` para Postgres
- [ ] HTTPS com certificado automático
- [ ] Backup do `config/` — hoje o GitHub já é isso, se o token estiver ativo

**Pronto quando** der para importar uma fatura pelo celular, autenticado.

---

## 5. Trocar de banco no meio da importação perde os dados sem avisar

**Prioridade: alta — é um bug.** `bug`, `ux`

O seletor de banco está na barra de cima e vale para a sessão inteira. Se você
trocar no meio de uma revisão, o tema muda mas a transação continua sendo a do
banco anterior — e os perfis de leitura são incompatíveis, então o estado fica
mentindo.

Hoje o seletor é apenas desabilitado durante a etapa `importar`, o que esconde o
problema em vez de resolver: você não consegue trocar, e também não entende por quê.

**Fazer**
- [ ] Reabilitar o seletor durante a importação
- [ ] Ao trocar com transação aberta, abrir um diálogo: *"Trocar para Nubank
      descarta a fatura carregada e as N decisões desta sessão. Continuar?"*
- [ ] Confirmando: limpa as atribuições, volta para a etapa de upload, aplica o
      tema novo
- [ ] Cancelando: mantém o banco anterior selecionado (o `<select>` tem que
      voltar sozinho, senão fica dessincronizado do estado)
- [ ] Teste cobrindo os dois caminhos

**Pronto quando** trocar de banco nunca deixar a tela num estado inconsistente.

---

## 6. Revisão: ordenar por total decrescente

**Prioridade: média.** `ux`

A aba "Já classificados" ordena por valor absoluto, mas a de "Novos" e a de
marketplace deveriam seguir a mesma regra, e a ordenação deveria ser explícita
na interface em vez de implícita no backend.

**Fazer**
- [ ] Ordenar por **Total desc** em todas as telas de revisão
- [ ] Cabeçalho de coluna clicável para reordenar (total, quantidade, nome)
- [ ] Manter a ordem escolhida ao voltar de outra etapa

**Pronto quando** as três telas abrirem com o maior valor no topo.

---

## 7. "Total por categoria" ordenado por valor decrescente

**Prioridade: baixa — rápido.** `ux`

Na etapa "Conferir e exportar", o resumo por categoria sai em ordem alfabética
(`sorted(by_category.items())` no `/preview`). O que interessa ali é onde o
dinheiro foi, não o alfabeto.

**Fazer**
- [ ] Ordenar por valor desc (`Alimentação R$ 12.844` no topo)
- [ ] Barra proporcional ao lado do valor, para leitura instantânea
- [ ] Mostrar % do total da fatura

**Pronto quando** a maior categoria for a primeira linha.

---

## 8. Reabrir uma revisão perdida

**Prioridade: média.** `ux`, `bug`

O `transaction_id` só existe no estado do React. Fechou a aba, recarregou sem
querer, o navegador matou a página no celular — a revisão inteira se perde, mesmo
com a transação viva no SQLite por 24h.

**Fazer**
- [ ] Guardar o `transaction_id` no `localStorage`
- [ ] Ao abrir, se houver transação válida, oferecer *"Continuar a revisão de
      ontem (2 faturas, 12 pendências)?"*
- [ ] `GET /transactions` listando as ativas com resumo
- [ ] Botão de descartar explicitamente

**Pronto quando** dar F5 no meio da revisão não custar nada.

---

## 9. Uma fatura de cada banco na mesma exportação

**Prioridade: média.** `feature`

O `/upload` recebe **um** `banco` para todos os arquivos. Se num mês você tem
Sicredi e Nubank, precisa rodar duas vezes e juntar os CSVs à mão — o que
contraria a decisão de "todos os extratos viram um CSV só".

**Fazer**
- [ ] Aceitar arquivos de bancos diferentes no mesmo upload (detectar por
      extensão + tentativa de parse, ou perguntar por arquivo)
- [ ] `Statement` já carrega `bank_id`; propagar até o CSV se você quiser a coluna
- [ ] Ordenação continua por vencimento → categoria → data da compra

**Pronto quando** subir Sicredi + Nubank juntos produzir um CSV só, correto.

---

## 10. Detectar fatura duplicada no upload

**Prioridade: média — evita erro caro.** `bug`, `dados`

Nada impede subir o mesmo extrato duas vezes. O resultado é um CSV com tudo
dobrado, e o erro só aparece depois de colado na planilha — quando dá muito mais
trabalho desfazer.

**Fazer**
- [ ] Avisar quando dois arquivos do mesmo upload tiverem o mesmo vencimento e
      o mesmo total declarado
- [ ] Guardar hash das faturas já exportadas e avisar em uploads futuros
- [ ] Aviso, não bloqueio — pode existir motivo legítimo

**Pronto quando** subir o mesmo arquivo duas vezes gerar um alerta claro.

---

## 11. Marketplace: aproveitar as decisões dos meses anteriores

**Prioridade: média — a maior economia de tempo do fluxo.** `feature`

Marketplace é decidido linha a linha e **nada** é lembrado. São ~70 linhas por
mês (R$ 9.795 nas cinco faturas) resolvidas do zero toda vez.

Boa parte é repetição: mesma loja, valores parecidos, mesma categoria de sempre.
Dá para sugerir sem gravar palavra-chave (que continuaria errado).

**Fazer**
- [ ] Guardar as decisões de linha por (estabelecimento, valor, mês) num
      histórico local — arquivo, não banco
- [ ] Sugerir a categoria mais usada para aquele marketplace, como
      pré-seleção editável, deixando claro que é sugestão
- [ ] Usar o `documents/financeiro_pessoal_aug_2026.csv` (6.7 mil linhas) para
      alimentar a sugestão inicial
- [ ] Nunca gravar isso como palavra-chave em `categories.yml`

**Pronto quando** a maioria das linhas de marketplace vier pré-preenchida e
você só corrigir as exceções.

---

## 12. Sugerir categoria para estabelecimento novo a partir do histórico

**Prioridade: baixa.** `feature`

Mesma ideia da #11, aplicada à aba "Novos". Você tem 6.716 lançamentos
categorizados em `documents/`. Um estabelecimento novo quase sempre parece com
algo que já existe lá.

**Fazer**
- [ ] Indexar o histórico por token normalizado → categoria mais frequente
- [ ] Na aba "Novos", mostrar *"parecido com Padaria Brasil → Alimentação (36×)"*
- [ ] Sugestão nunca é aplicada sozinha

**Pronto quando** a maioria dos novos vier com uma sugestão razoável.

---

## 13. Validar o perfil do Nubank contra uma fatura real

**Prioridade: baixa.** `dados`

`config/banks/nubank.yml` está `validado: false` — foi escrito sem uma fatura na
mão. O portal avisa, mas ele nunca foi exercitado com um arquivo de verdade.

**Fazer**
- [ ] Exportar uma fatura real do app do Nubank
- [ ] Testar em "Formato de entrada" → *testar com um arquivo*
- [ ] Conferir nome das colunas, formato de data e como vêm as parcelas
      (o perfil assume que o Nubank põe "Parcela 2/5" dentro do título)
- [ ] Ajustar e virar `validado: true`
- [ ] Adicionar um CSV de exemplo (anonimizado) às fixtures dos testes

**Pronto quando** os totais fecharem e a flag estiver `true`.

---

## 14. Limpeza do `categories.yml`

**Prioridade: baixa — mas o portal já mostra tudo.** `dados`

O arquivo acumulou pontas soltas que a aba "Regras" já lista:

- **20 entradas com `# ?`** — chutes meus, esperando confirmação
- **24 palavras-chave redundantes** — um trecho mais curto da mesma categoria já
  cobre (`CAFETERIA` engole `BARAO CAFETERIA`, `SUPERMERCADO` engole
  `SUPERMERCADOS`); apagar não muda resultado nenhum
- **1 conflito real**: `RI HAPP` está em **Presentes** e `RI HAPPY`/`RIHAPPY` em
  **Filha**. A mesma loja cai numa categoria ou noutra dependendo de como a
  maquininha truncou o nome
- `OTICA` (Saúde) é curta o bastante para casar dentro de `BOTICARIO`

**Fazer**
- [ ] Resolver o conflito Ri Happy escolhendo uma categoria
- [ ] Passar pelos 20 chutes com o botão *confirmar*
- [ ] Apagar as 24 redundantes
- [ ] Revisar palavras-chave curtas demais

**Pronto quando** o contador de chutes na barra lateral estiver em zero.

---

## 15. Casamento por substring: tornar o limite de palavra opcional

**Prioridade: baixa.** `feature`

O casamento é por substring e **não** respeita limite de palavra. Isso é
proposital e necessário — `UNITED01624563906420` só casa com `UNITED` por causa
disso — mas cobra um preço: `CIMENTO` casa dentro de `ESTABELECIMENTO`.

Nos seus extratos reais são 4 linhas que dependem do comportamento atual e
nenhum falso positivo hoje. Não é urgente; é uma bomba-relógio pequena.

**Fazer**
- [ ] Sufixo por palavra-chave para exigir limite: `- CIMENTO!` ou
      `{valor: CIMENTO, palavra_inteira: true}`
- [ ] Avisar na aba Regras quando uma palavra-chave curta casar dentro de outra
- [ ] Manter substring como padrão (é o que funciona hoje)

**Pronto quando** der para marcar uma palavra-chave como "palavra inteira" sem
mudar o comportamento das outras.

---

## 16. Modo convidado completo

**Prioridade: baixa.** `feature`

O esqueleto existe: `/config/export` e `/config/import` já levam e trazem a
configuração inteira. Falta o modo em que **nada** é gravado no servidor.

**Fazer**
- [ ] Sessão que carrega a config na transação, sem tocar em `config/`
- [ ] Bloquear `/config/bank`, `/config/output`, `/rules/edit` e GitHub nesse modo
- [ ] Ao exportar o CSV, oferecer também o `.zip` de config atualizado
- [ ] Deixar explícito na interface que nada foi guardado

**Pronto quando** outra pessoa conseguir usar o portal de ponta a ponta sem
deixar rastro no servidor.

---

## 17. Valor em dólar e IOF das compras internacionais

**Prioridade: baixa.** `feature`

A seção internacional é lida, mas o valor em US$ é descartado — só o valor em
reais vai para o CSV. O IOF vira lançamento separado (`Imposto`), então o custo
real de uma compra internacional fica espalhado.

**Fazer**
- [ ] Guardar o valor em US$ no `Entry` (já existe o campo `international`)
- [ ] Mostrar a cotação implícita na tela de conferência
- [ ] Opcionalmente, ligar o IOF ao lançamento que o gerou

**Pronto quando** der para ver quanto custou de verdade uma compra em dólar.

---

## 18. Avisar quando o `config/` tiver alterações não commitadas

**Prioridade: baixa.** `ux`

O portal grava direto no `config/` montado. Sem o token do GitHub, as mudanças
ficam só no disco — e é fácil esquecer de commitar por semanas.

**Fazer**
- [ ] Mostrar no rodapé quando o `config/` divergir do último commit
- [ ] Botão "publicar agora" que faz o commit da sessão
- [ ] Se o token não estiver configurado, dizer isso explicitamente em vez de
      falhar em silêncio
