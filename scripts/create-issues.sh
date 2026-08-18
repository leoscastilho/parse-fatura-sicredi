#!/usr/bin/env bash
# Cria o backlog no GitHub. Rode do diretório do repositório.
#
#   gh auth status || gh auth login
#   bash create-issues.sh
#
# Idempotente por título: se já existir uma issue aberta com o mesmo título,
# ela é pulada — dá para rodar de novo depois de acrescentar itens.

set -euo pipefail

REPO="${REPO:-leoscastilho/parse-fatura-sicredi}"

criar_label() {
  gh label create "$1" --repo "$REPO" --color "$2" --description "$3" 2>/dev/null \
    || echo "  label '$1' já existe"
}

echo "== labels =="
criar_label bug         d73a4a "Comportamento errado"
criar_label ux          a2eeef "Interface e fluxo"
criar_label feature     0e8a16 "Funcionalidade nova"
criar_label infra       5319e7 "Build, CI, deploy"
criar_label seguranca   b60205 "Segurança"
criar_label dados       fbca04 "Regras e dados de categorização"
criar_label bloqueador  000000 "Bloqueia outra issue"

criar_issue() {
  local titulo="$1" labels="$2" corpo="$3"
  if gh issue list --repo "$REPO" --state open --search "$titulo in:title" \
       --json title --jq '.[].title' | grep -qxF "$titulo"; then
    echo "  já existe: $titulo"
    return
  fi
  gh issue create --repo "$REPO" --title "$titulo" --label "$labels" --body "$corpo"
}

echo
echo "== issues =="

criar_issue "Autenticação antes de expor o portal publicamente" "seguranca,bloqueador" \
'O portal não tem autenticação. Na LAN tudo bem; exposto, não: o CSV gerado contém o histórico completo de gastos, e `/config/import` substitui a configuração inteira sem credencial.

O nginx já é a porta de entrada, então `auth_basic` com `.htpasswd` por volume é o caminho mais barato. Alternativas: proxy OIDC (oauth2-proxy, Authelia) ou nunca publicar porta e acessar por Tailscale.

**Fazer**
- [ ] Escolher entre basic auth, proxy OIDC ou só Tailscale/WireGuard
- [ ] Exigir HTTPS se for basic auth (em claro não protege nada)
- [ ] Rate limit em `/upload` e `/config/import`
- [ ] README deixando claro que sem isso a app não sai da LAN

**Pronto quando** `curl` sem credencial em `/config` e `/export` devolver 401.

Bloqueia o deploy na nuvem.'

criar_issue "CI: rodar os testes no GitHub Actions" "infra" \
'Existem 114 testes (100 backend + 14 front), mas só rodam no `docker build`. Um push quebrado passa despercebido até a próxima imagem.

**Fazer**
- [ ] Workflow com `pytest tests/ -q` e `npm test` em push e PR
- [ ] `docker build` dos dois Dockerfiles no CI (pega erro de layer/COPY)
- [ ] Badge no README
- [ ] Dependabot ou Renovate: as versões estão fixadas de propósito em `requirements.txt` e sem robô apodrecem em silêncio

**Pronto quando** um PR com teste quebrado ficar vermelho antes do merge.'

criar_issue "Editor visual para os formatos de entrada e saída" "ux" \
'As abas "Formato de entrada" e "Formato de saída" são um `<textarea>` com YAML cru. Funciona para quem escreveu o schema; não funciona para configurar um banco novo daqui a seis meses.

Inverter: **formulário como interface, YAML como resultado**. Os campos são poucos e conhecidos (rótulo do vencimento, nome das colunas, separador decimal, formato de data). O YAML gerado continua sendo o que é commitado — nada de configuração em banco de dados.

**Fazer**
- [ ] Formulário por estratégia (`excel_secoes`, `csv_simples`) gerando o YAML
- [ ] Modo avançado com o YAML cru, para o que o formulário não cobrir
- [ ] Preview lado a lado: arquivo cru → colunas detectadas → linha final do CSV
- [ ] Sugerir o mapeamento de colunas a partir do cabeçalho de um arquivo novo
- [ ] Mostrar o diff do YAML antes de salvar

**Pronto quando** der para configurar um banco novo sem digitar YAML.'

criar_issue "Deploy na nuvem" "infra" \
'Hoje roda em Docker no Proxmox. Para acessar de fora:

- **Depende da issue de autenticação**, sem exceção.
- Segredos: `FATURA_GITHUB_TOKEN` não pode virar env var em painel compartilhado — usar cofre do provedor ou Docker secret.
- Estado: o SQLite vive num volume. Em provedor com disco efêmero (Cloud Run, Fly sem volume) a transação morre no meio da revisão. Ou monta volume, ou troca por Postgres — o `Store` já isola isso num lugar só.
- Custo: imagem de ~350MB + nginx; qualquer VPS de 1GB dá conta.

**Fazer**
- [ ] Escolher destino (VPS + Traefik, Fly.io, Railway…)
- [ ] Volume persistente para `/data` ou migrar o `Store` para Postgres
- [ ] HTTPS com certificado automático
- [ ] Backup do `config/` (o GitHub já cobre, se o token estiver ativo)

**Pronto quando** der para importar uma fatura pelo celular, autenticado.'

criar_issue "Trocar de banco no meio da importação perde os dados sem avisar" "bug,ux" \
'O seletor de banco vale para a sessão inteira. Trocar no meio de uma revisão muda o tema, mas a transação continua sendo a do banco anterior — e os perfis de leitura são incompatíveis, então o estado passa a mentir.

Hoje o seletor é apenas desabilitado durante a importação, o que esconde o problema: não dá para trocar, e também não fica claro por quê.

**Fazer**
- [ ] Reabilitar o seletor durante a importação
- [ ] Com transação aberta, abrir diálogo: "Trocar para Nubank descarta a fatura carregada e as N decisões desta sessão. Continuar?"
- [ ] Confirmando: limpa as atribuições, volta para o upload, aplica o tema novo
- [ ] Cancelando: o `<select>` volta sozinho para o banco anterior (senão dessincroniza do estado)
- [ ] Teste cobrindo os dois caminhos

**Pronto quando** trocar de banco nunca deixar a tela inconsistente.'

criar_issue "Ordenar as telas de revisão por total decrescente" "ux" \
'"Já classificados" ordena por valor absoluto, mas "Novos" e marketplace deveriam seguir a mesma regra — e a ordenação deveria ser explícita na interface, não implícita no backend.

**Fazer**
- [ ] Total desc em todas as telas de revisão
- [ ] Cabeçalho de coluna clicável para reordenar (total, quantidade, nome)
- [ ] Manter a ordem escolhida ao voltar de outra etapa

**Pronto quando** as três telas abrirem com o maior valor no topo.'

criar_issue "Total por categoria ordenado por valor, não por alfabeto" "ux" \
'Na etapa "Conferir e exportar" o resumo por categoria sai em ordem alfabética (`sorted(by_category.items())` no `/preview`). O que interessa ali é para onde o dinheiro foi.

**Fazer**
- [ ] Ordenar por valor desc (Alimentação R$ 12.844 no topo)
- [ ] Barra proporcional ao lado do valor
- [ ] Mostrar % do total da fatura

**Pronto quando** a maior categoria for a primeira linha.'

criar_issue "Reabrir uma revisão perdida (F5 fecha tudo)" "ux,bug" \
'O `transaction_id` só existe no estado do React. Fechou a aba ou recarregou sem querer e a revisão se perde — mesmo com a transação viva no SQLite por 24h.

**Fazer**
- [ ] Guardar o `transaction_id` no `localStorage`
- [ ] Ao abrir, oferecer "Continuar a revisão de ontem (2 faturas, 12 pendências)?"
- [ ] `GET /transactions` listando as ativas com resumo
- [ ] Botão de descartar explicitamente

**Pronto quando** dar F5 no meio da revisão não custar nada.'

criar_issue "Aceitar faturas de bancos diferentes no mesmo upload" "feature" \
'O `/upload` recebe **um** `banco` para todos os arquivos. Com Sicredi e Nubank no mesmo mês é preciso rodar duas vezes e juntar os CSVs à mão — o que contraria a decisão de "todos os extratos viram um CSV só".

**Fazer**
- [ ] Aceitar arquivos de bancos diferentes no mesmo upload (detectar por extensão + tentativa de parse, ou perguntar por arquivo)
- [ ] `Statement` já carrega `bank_id`; propagar até o CSV se quiser a coluna
- [ ] Ordenação continua vencimento → categoria → data da compra

**Pronto quando** subir Sicredi + Nubank juntos produzir um CSV só, correto.'

criar_issue "Detectar fatura duplicada no upload" "bug,dados" \
'Nada impede subir o mesmo extrato duas vezes. O CSV sai com tudo dobrado e o erro só aparece depois de colado na planilha, quando dá muito mais trabalho desfazer.

**Fazer**
- [ ] Avisar quando dois arquivos do mesmo upload tiverem o mesmo vencimento e o mesmo total declarado
- [ ] Guardar hash das faturas já exportadas e avisar em uploads futuros
- [ ] Aviso, não bloqueio — pode existir motivo legítimo

**Pronto quando** subir o mesmo arquivo duas vezes gerar um alerta claro.'

criar_issue "Marketplace: sugerir categoria a partir dos meses anteriores" "feature" \
'Marketplace é decidido linha a linha e nada é lembrado. São ~70 linhas por mês (R$ 9.795 nas cinco faturas) resolvidas do zero toda vez, e boa parte é repetição.

Dá para sugerir sem gravar palavra-chave (que continuaria errado, já que a categoria muda a cada compra).

**Fazer**
- [ ] Guardar decisões de linha por (estabelecimento, valor, mês) num histórico local — arquivo, não banco
- [ ] Pré-selecionar a categoria mais usada para aquele marketplace, editável e marcada como sugestão
- [ ] Alimentar a sugestão inicial com `documents/financeiro_pessoal_aug_2026.csv` (6.7 mil linhas)
- [ ] Nunca gravar isso como palavra-chave em `categories.yml`

**Pronto quando** a maioria das linhas de marketplace vier pré-preenchida.'

criar_issue "Sugerir categoria para estabelecimento novo usando o histórico" "feature" \
'São 6.716 lançamentos já categorizados em `documents/`. Um estabelecimento novo quase sempre parece com algo que já está lá.

**Fazer**
- [ ] Indexar o histórico por token normalizado → categoria mais frequente
- [ ] Na aba "Novos", mostrar "parecido com Padaria Brasil → Alimentação (36×)"
- [ ] Sugestão nunca é aplicada sozinha

**Pronto quando** a maioria dos novos vier com uma sugestão razoável.'

criar_issue "Validar o perfil do Nubank contra uma fatura real" "dados" \
'`config/banks/nubank.yml` está `validado: false` — foi escrito sem uma fatura na mão. O portal avisa, mas o perfil nunca foi exercitado com arquivo de verdade.

**Fazer**
- [ ] Exportar uma fatura real do app
- [ ] Testar em "Formato de entrada" → testar com um arquivo
- [ ] Conferir colunas, formato de data e como vêm as parcelas (o perfil assume "Parcela 2/5" dentro do título)
- [ ] Ajustar e virar `validado: true`
- [ ] Adicionar um CSV de exemplo anonimizado às fixtures

**Pronto quando** os totais fecharem e a flag estiver `true`.'

criar_issue "Limpeza do categories.yml: chutes, redundâncias e um conflito" "dados" \
'A aba "Regras" já lista tudo isto:

- **20 entradas com `# ?`** — chutes esperando confirmação (o botão *confirmar* resolve sem mudar o mapeamento)
- **24 palavras-chave redundantes** — um trecho mais curto da mesma categoria já cobre (`CAFETERIA` engole `BARAO CAFETERIA`, `SUPERMERCADO` engole `SUPERMERCADOS`); apagar não muda resultado
- **1 conflito real**: `RI HAPP` está em **Presentes** e `RI HAPPY`/`RIHAPPY` em **Filha**. A mesma loja cai numa categoria ou noutra dependendo de como a maquininha truncou o nome
- `OTICA` (Saúde) é curta o bastante para casar dentro de `BOTICARIO`

**Fazer**
- [ ] Resolver o conflito Ri Happy
- [ ] Passar pelos 20 chutes
- [ ] Apagar as 24 redundantes
- [ ] Revisar palavras-chave curtas demais

**Pronto quando** o contador de chutes na barra lateral estiver em zero.'

criar_issue "Permitir exigir limite de palavra numa palavra-chave" "feature" \
'O casamento é por substring e não respeita limite de palavra. É proposital e necessário — `UNITED01624563906420` só casa com `UNITED` por causa disso — mas cobra um preço: `CIMENTO` casa dentro de `ESTABELECIMENTO`.

Nos extratos reais são 4 linhas que dependem do comportamento atual e nenhum falso positivo hoje. Não é urgente; é uma bomba-relógio pequena. Os dois comportamentos estão fixados em testes.

**Fazer**
- [ ] Sufixo por palavra-chave para exigir limite: `- CIMENTO!` ou `{valor: CIMENTO, palavra_inteira: true}`
- [ ] Avisar na aba Regras quando uma palavra-chave curta casar dentro de outra
- [ ] Manter substring como padrão

**Pronto quando** der para marcar uma palavra-chave como "palavra inteira" sem mudar as outras.'

criar_issue "Modo convidado: usar o portal sem gravar nada no servidor" "feature" \
'O esqueleto existe: `/config/export` e `/config/import` já levam e trazem a configuração inteira. Falta o modo em que nada é gravado no servidor.

**Fazer**
- [ ] Sessão que carrega a config na transação, sem tocar em `config/`
- [ ] Bloquear `/config/bank`, `/config/output`, `/rules/edit` e GitHub nesse modo
- [ ] Ao exportar o CSV, oferecer também o `.zip` de config atualizado
- [ ] Deixar explícito na interface que nada foi guardado

**Pronto quando** outra pessoa usar o portal de ponta a ponta sem deixar rastro.'

criar_issue "Guardar valor em dólar e vincular o IOF da compra internacional" "feature" \
'A seção internacional é lida, mas o valor em US$ é descartado — só o valor em reais vai para o CSV. O IOF vira lançamento separado (`Imposto`), então o custo real de uma compra internacional fica espalhado.

**Fazer**
- [ ] Guardar o valor em US$ no `Entry` (o campo `international` já existe)
- [ ] Mostrar a cotação implícita na tela de conferência
- [ ] Opcionalmente, ligar o IOF ao lançamento que o gerou

**Pronto quando** der para ver quanto custou de verdade uma compra em dólar.'

criar_issue "Avisar quando o config/ tiver alterações não commitadas" "ux" \
'O portal grava direto no `config/` montado. Sem o token do GitHub as mudanças ficam só no disco, e é fácil esquecer de commitar por semanas.

**Fazer**
- [ ] Mostrar no rodapé quando o `config/` divergir do último commit
- [ ] Botão "publicar agora" que faz o commit da sessão
- [ ] Se o token não estiver configurado, dizer isso explicitamente em vez de falhar em silêncio'

echo
echo "Pronto. Veja em: https://github.com/$REPO/issues"
