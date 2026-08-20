/**
 * Quem pegou esta compra: o período, e como chamá-lo na tela.
 *
 * Vive fora dos componentes porque DUAS telas precisam da mesma resposta — a
 * etapa Viagem, que mostra a coluna "Qual viagem", e a aba Novos, que mostra a
 * dica embaixo da descrição. Duas cópias da mesma regra divergiriam no dia em
 * que uma delas resolvesse o empate de outro jeito, e a divergência apareceria
 * como "a tela A diz Peru e a tela B diz Chile" sem ninguém saber qual está
 * certa.
 */

/**
 * O primeiro período que contém a data — a MESMA regra do backend
 * (`core/travel.py::range_of`).
 *
 * Períodos sobrepostos: vence o primeiro da lista. Resolver diferente aqui
 * mostraria na tela um nome de viagem que o arquivo não vai levar.
 *
 * As datas são ISO (`AAAA-MM-DD`), então comparar as strings é comparar as
 * datas — sem `new Date`, que leria a string como UTC e deslocaria o dia para
 * quem está em fuso negativo.
 */
export const periodoDe = (iso, ranges = []) =>
  (iso && ranges.find((r) => r.inicio <= iso && iso <= r.fim)) || null

const diaMes = (iso) => {
  const [, m, d] = (iso || '').split('-')
  return d ? `${d}/${m}` : iso
}

/**
 * Como chamar um período na tela: o nome, ou a janela quando não tem nome.
 *
 * Célula vazia seria pior do que a data: com dois períodos abertos, o que essa
 * informação responde é "qual dos dois", e `24/10 → 30/10` responde isso tão
 * bem quanto um nome que ninguém digitou.
 */
export const rotuloDoPeriodo = (periodo) =>
  !periodo ? '' : (periodo.rotulo || `${diaMes(periodo.inicio)} → ${diaMes(periodo.fim)}`)

/**
 * `line_id -> rótulo da viagem`, a partir do que o backend devolveu em /travel.
 *
 * `items` já vem filtrado pelo backend (só o que caiu dentro de algum período),
 * então não há data para reconferir aqui — só descobrir QUAL período pegou cada
 * um, que é o que o backend não devolve.
 */
export function viagensPorLinha(items = [], ranges = []) {
  const mapa = new Map()
  for (const item of items) {
    const periodo = periodoDe(item.purchase_date, ranges)
    if (periodo) mapa.set(item.line_id, rotuloDoPeriodo(periodo))
  }
  return mapa
}
