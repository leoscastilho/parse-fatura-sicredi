/**
 * Como uma viagem se chama na tela, e qual viagem pegou cada linha.
 *
 * QUEM DECIDE QUAL VIAGEM É O BACKEND. Cada `LineItem` chega com
 * `viagem_periodo` já resolvido, e este módulo só cuida da apresentação. Isso é
 * deliberado: a resposta depende de duas regras — a fixação à mão vence a data,
 * e entre períodos sobrepostos vence o primeiro da lista — e uma segunda
 * implementação delas em JavaScript significaria a tela dizendo "Peru" e o
 * arquivo saindo "Ferroão", sem ninguém saber qual dos dois está certo.
 *
 * A identidade de um período é a JANELA (`inicio|fim`), não o nome: é o que
 * permite renomear uma viagem sem soltar as linhas penduradas nela, e é a mesma
 * chave que a importação de CSV usa para não duplicar período.
 */

export const chaveDoPeriodo = (periodo) =>
  periodo ? `${periodo.inicio}|${periodo.fim}` : ''

/**
 * `2018-12-15` -> `15/12/2018`. COM O ANO, e isso não é preciosismo.
 *
 * O `dd/mm` de antes nasceu quando um lote era uma fatura, cobrindo um mês: ali
 * o ano era óbvio e só ocupava espaço. Com o histórico inteiro na tela e 57
 * viagens entre 2018 e 2026, `15/12 → 16/12` não diz qual Sorocaba é — e o
 * usuário passa a desconfiar que o ano foi perdido no meio do caminho, o que
 * não aconteceu: a comparação sempre foi em ISO completo.
 */
export const dataCurta = (iso) => {
  const [a, m, d] = (iso || '').split('-')
  return d ? `${d}/${m}/${a}` : iso
}

/**
 * O nome da viagem, ou a janela quando ela não tem nome.
 *
 * Vazio seria pior do que a data: com dois períodos abertos, o que esta
 * informação responde é "qual dos dois", e `24/10 → 30/10` responde isso tão
 * bem quanto um nome que ninguém digitou.
 */
export const rotuloDoPeriodo = (periodo) =>
  !periodo ? '' : (periodo.rotulo || `${dataCurta(periodo.inicio)} → ${dataCurta(periodo.fim)}`)

/**
 * `line_id -> rótulo da viagem`, para as telas que acontecem ANTES da etapa
 * Viagem — onde a marca ainda não está na descrição.
 */
export function viagensPorLinha(items = []) {
  const mapa = new Map()
  for (const item of items) {
    if (item.viagem_periodo) mapa.set(item.line_id, rotuloDoPeriodo(item.viagem_periodo))
  }
  return mapa
}
