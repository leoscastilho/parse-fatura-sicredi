/**
 * Aplica o tema do banco escolhido nas CSS custom properties.
 *
 * Todo o CSS usa `var(--verde)`, `var(--verde-escuro)` etc. Trocar o banco é
 * só reescrever essas variáveis no `:root` — nenhum componente precisa saber
 * que existe mais de um banco, e não há classe `.tema-nubank` espalhada pelo
 * código.
 */
const MAP = {
  primaria: '--verde',
  escura: '--verde-escuro',
  clara: '--neutro-claro',
  suave: '--verde-suave',
  destaque: '--amarelo',
  neutra: '--neutro-escuro',
  aviso: '--marrom',
  erro: '--magenta',
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
 * Volta ao tema do próprio CSS, o que estiver escrito no `:root` da folha.
 *
 * Existe porque o banco deixou de ser uma escolha permanente e passou a ser
 * detectado por arquivo: entre um lote e o seguinte a tela precisa esquecer o
 * roxo do Nubank, senão "Começar de novo" mantém a cara do banco anterior até
 * alguém soltar outro arquivo — e a cor passa a mentir sobre o que está
 * carregado. Remover a propriedade inline é o que devolve a palavra à folha;
 * sobrescrever com um valor fixo criaria um terceiro tema para manter.
 */
export function resetTheme() {
  const root = document.documentElement
  for (const variavel of Object.values(MAP)) root.style.removeProperty(variavel)
  root.style.removeProperty('--border')
}
