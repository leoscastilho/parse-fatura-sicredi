import { createContext, useContext } from 'react'

/**
 * De quem são os lançamentos que aparecem nas tabelas de revisão.
 *
 * PURAMENTE VISUAL. Isto não muda o que é processado, o que é exportado, nem o
 * que qualquer decisão significa: esconder as compras da Rhyesla enquanto eu
 * categorizo as minhas não desfaz nem adia nada dela. É a mesma fatura, com
 * menos coisa na tela de cada vez.
 *
 * CONTEXTO, e não prop, pela lição que a lista de categorias fixas já ensinou
 * aqui: como prop, o filtro precisaria ser repassado em cinco telas, e apagá-lo
 * em três delas não derrubaria teste nenhum — o sintoma seria "o filtro não
 * funciona na aba Marketplace", descoberto meses depois.
 *
 * `TODOS` é `null` e não `""` de propósito: `""` já quer dizer "sem marca", o
 * balde de quem se identificou como "eu" no upload. Confundir os dois faria
 * "todos" mostrar só as minhas compras.
 */
export const TODOS = null

export const TitularFiltro = createContext(TODOS)

export const useTitularFiltro = () => useContext(TitularFiltro)

/**
 * Este lançamento passa pelo filtro?
 *
 * `titular` vem do backend (`LineItem.titular`), extraído do ` <Rhyesla>` no
 * fim da descrição por `core.text.titular_de`. O front não relê a descrição:
 * uma segunda implementação da mesma regra divergiria no dia em que uma das
 * duas passasse a aceitar espaço antes do `<`.
 */
export const passaLinha = (filtro, titular) =>
  filtro === TODOS || (titular || '') === filtro

/**
 * Este ESTABELECIMENTO passa pelo filtro?
 *
 * Basta ter uma linha da pessoa. Um grupo com três compras da Rhyesla e duas
 * minhas aparece nos dois filtros — a decisão ali é do estabelecimento inteiro
 * e vale para todas as linhas dele, então escondê-lo de mim porque ela também
 * comprou lá seria esconder uma decisão que é minha também.
 */
export const passaGrupo = (filtro, titulares) =>
  filtro === TODOS || (titulares || []).includes(filtro)

/**
 * Os nomes que aparecem num lote, na ordem em que o seletor os oferece.
 *
 * `eu` é o nome que a pessoa escolheu como "esse sou eu" na tela de upload —
 * ele não viaja para o backend (só os OUTROS viajam, com o rótulo que levam
 * para a planilha), então chega por aqui, do estado do App. Sem ele o balde
 * sem marca aparece como "Sem marca", que é verdade e é pior.
 */
export function opcoesDeTitular(nomes, eu = '') {
  const distintos = [...new Set(nomes.map((n) => n || ''))]
  if (distintos.length < 2) return []
  const outros = distintos.filter(Boolean).sort((a, b) => a.localeCompare(b, 'pt-BR'))
  return [
    ...(distintos.includes('') ? [{ valor: '', rotulo: eu.trim() || 'Sem marca' }] : []),
    ...outros.map((n) => ({ valor: n, rotulo: n })),
  ]
}
