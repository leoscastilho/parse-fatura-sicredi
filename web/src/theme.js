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

/**
 * O tema de LOTE MISTO — a cor de banco nenhum, de propósito.
 *
 * Com faturas de bancos diferentes na mesma leva, pintar a tela com a cor de um
 * deles seria escolher um sem motivo: metade das linhas na tela é do outro, e a
 * cor passaria a dizer algo falso sobre o que está carregado.
 *
 * Não dá para resolver isso com `resetTheme`: o `:root` da folha ainda é o
 * verde Sicredi de quando o portal lia um banco só, então "voltar ao padrão"
 * num lote Sicredi + BTG seria exatamente pintar a tela de Sicredi. Daí uma
 * paleta própria — com as MESMAS chaves de um `tema:` de banco, para passar
 * pelo `applyTheme` sem nenhum caso especial.
 */
export const TEMA_NEUTRO = {
  primaria: '#465061',
  escura: '#232A36',
  clara: '#D6DBE3',
  suave: '#F0F2F6',
  destaque: '#E4C767',
  neutra: '#5F6874',
  aviso: '#6B4A12',
  erro: '#E60050',
  fundo: '#F5F6F8',
  texto: '#232A36',
  // A marca também não vira inicial de banco nenhum: volta ao "$" do portal.
  inicial: '$',
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
