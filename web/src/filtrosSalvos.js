/**
 * Os filtros da aba de Análise, guardados no navegador.
 *
 * POR QUE ISTO É SEGURO CONTRA REORDENAÇÃO
 * ----------------------------------------
 * O que fica salvo não é "a linha 4.312". É a identidade do lançamento, que o
 * backend monta a partir do CONTEÚDO:
 *
 *     2025-10|Casa|Pix da Casa 123 para Gustavo Bianco|600000.00
 *
 * período · categoria · descrição · valor. Nenhum deles depende da posição na
 * planilha, então ordenar por data, por valor ou por categoria, inserir linhas
 * no meio ou reexportar do Sheets não muda nada: o mesmo lançamento continua
 * tendo o mesmo nome. O que QUEBRA a identidade é editar um desses quatro
 * campos — e aí é o certo, porque a linha virou outra coisa e merece uma
 * decisão nova.
 *
 * Dois cuidados que não são óbvios:
 *
 * 1. **Nada é podado na leitura.** Um lançamento excluído que não aparece no
 *    período em vigor continua salvo — senão, filtrar por "últimos 6 meses"
 *    apagaria em silêncio a exclusão de uma compra de 2021, e ela voltaria ao
 *    gráfico quando o período abrisse de novo.
 *
 * 2. **O rótulo anda junto com o id.** É só cache de exibição, mas sem ele um
 *    lançamento excluído fora do período não teria como ser mostrado na barra
 *    — e um filtro que não dá para ver é um filtro que não dá para desfazer.
 */

const CHAVE = 'fatura:analise:filtros:v1'

const VAZIO = { semCategorias: [], semLinhas: [], semTitulares: [], rotulos: {} }

/**
 * `localStorage` pode simplesmente não existir: navegação privada em alguns
 * navegadores, cota estourada, política de site. Salvar filtro é conveniência,
 * então falhar aqui não pode derrubar a aba — o pior caso é começar do zero.
 */
function armazem() {
  try {
    const teste = '__t'
    window.localStorage.setItem(teste, teste)
    window.localStorage.removeItem(teste)
    return window.localStorage
  } catch {
    return null
  }
}

export function lerFiltros() {
  const loja = armazem()
  if (!loja) return { ...VAZIO }
  try {
    const cru = JSON.parse(loja.getItem(CHAVE) || '{}')
    return {
      semCategorias: Array.isArray(cru.semCategorias) ? cru.semCategorias : [],
      semLinhas: Array.isArray(cru.semLinhas) ? cru.semLinhas : [],
      semTitulares: Array.isArray(cru.semTitulares) ? cru.semTitulares : [],
      rotulos: cru.rotulos && typeof cru.rotulos === 'object' ? cru.rotulos : {},
    }
  } catch {
    // Conteúdo corrompido (versão antiga, edição manual): recomeça em branco
    // em vez de deixar a aba sem abrir.
    return { ...VAZIO }
  }
}

export function gravarFiltros({ semCategorias = [], semLinhas = [],
                                semTitulares = [], rotulos = {} }) {
  const loja = armazem()
  if (!loja) return
  try {
    // O mapa de rótulos guarda SÓ o que ainda está excluído. Sem esta poda ele
    // cresceria para sempre com o nome de tudo que já passou pela barra.
    const vivos = Object.fromEntries(
      semLinhas.map((id) => [id, rotulos[id]]).filter(([, r]) => r))
    loja.setItem(CHAVE, JSON.stringify({ semCategorias, semLinhas,
                                         semTitulares, rotulos: vivos }))
  } catch {
    /* cota cheia: o filtro vale para esta sessão e pronto */
  }
}

export function limparFiltros() {
  armazem()?.removeItem(CHAVE)
}
