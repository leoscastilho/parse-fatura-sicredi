/**
 * Aplica o tema do banco escolhido nas CSS custom properties.
 *
 * Todo o CSS usa `var(--primaria)`, `var(--primaria-escura)` etc. Trocar o banco é
 * só reescrever essas variáveis no `:root` — nenhum componente precisa saber
 * que existe mais de um banco, e não há classe `.tema-nubank` espalhada pelo
 * código.
 */
const MAP = {
  primaria: '--primaria',
  escura: '--primaria-escura',
  clara: '--clara',
  suave: '--primaria-suave',
  destaque: '--destaque',
  neutra: '--neutra',
  aviso: '--aviso',
  erro: '--erro',
  fundo: '--bg',
  texto: '--text',
}

export function applyTheme(tema) {
  if (!tema) return
  const root = document.documentElement
  for (const [chave, variavel] of Object.entries(MAP)) {
    if (tema[chave]) root.style.setProperty(variavel, tema[chave])
  }
  // A borda acompanha o tom claro da marca para o contorno não destoar.
  if (tema.clara) root.style.setProperty('--border', tema.clara)
}

/**
 * Volta ao GRAFITE do portal — o `:root` da folha, que é a cor de banco nenhum.
 *
 * Chamado nos dois casos em que nenhuma cor de banco descreve a tela: sem lote
 * (o portal não é de banco nenhum até alguém soltar um arquivo) e com bancos
 * DIFERENTES no mesmo lote (pintar de um deles diria "Sicredi" numa tela em que
 * metade das linhas é do BTG).
 *
 * Remover a propriedade inline é o que devolve a palavra à folha. Sobrescrever
 * com uma paleta fixa aqui — que foi como isto nasceu — criava um segundo
 * neutro para manter em sincronia com o do CSS, e os dois divergiriam no dia em
 * que alguém mexesse num só.
 */
export function resetTheme() {
  const root = document.documentElement
  for (const variavel of Object.values(MAP)) root.style.removeProperty(variavel)
  root.style.removeProperty('--border')
}
