import { createContext, useContext } from 'react'

/**
 * As categorias que dizem para onde o dinheiro se MOVEU — Renda Fixa, Renda
 * Variável, Resgate Poupança, Poupança, Investimento.
 *
 * POR QUE CONTEXTO E NÃO PROP. Seis seletores em quatro telas atribuem
 * categoria a uma compra. Como prop, esquecer UM deixava aquela tela oferecendo
 * `Poupança` para uma despesa de fatura — e nada quebrava: a tela renderiza, o
 * seletor funciona, o erro só aparece meses depois, na soma da planilha. Um
 * teste de mutação mostrou exatamente isso: apagar a prop em três dos quatro
 * passos não derrubava teste nenhum.
 *
 * Pelo contexto, a lista chega sozinha em todo seletor da árvore, inclusive nos
 * editores de regra: lá a pergunta é "para onde esta regra aponta", e apontar
 * uma palavra-chave para `Poupança` cria exatamente o problema que esta lista
 * existe para evitar, só que para sempre em vez de uma linha.
 *
 * Não existe escape hatch, e não precisa: a guarda `c !== value` abaixo mantém
 * a fixa visível onde ela JÁ está. Uma regra que hoje aponta para `Poupança`
 * continua legível e continua podendo sair de lá — o que não dá é para entrar.
 */
export const CategoriasFixas = createContext([])

// Um <select> nativo: teclado, busca por digitação e acessibilidade de graça.
// Numa tela com 135 dropdowns, isso importa mais que um combobox bonito.

/**
 * Seletor de categoria.
 *
 * As fixas somem da lista de escolha porque a pergunta desta tela é outra: o
 * que esta compra foi. Nenhuma compra no cartão é "Renda Fixa", e uma escolhida
 * por engano inverte o sinal da linha na planilha, onde essas categorias são
 * somadas e todo o resto é subtraído.
 *
 * ESCONDER NÃO PODE APAGAR. Se a linha JÁ está numa fixa — 825 delas no
 * histórico dele —, a opção continua na lista e continua selecionada. Um
 * <select> controlado cujo `value` não existe entre as opções cai para vazio no
 * primeiro render e leva a categoria junto, em silêncio; é o mesmo bug que já
 * apareceu aqui quando "Novos" achava que nada estava resolvido.
 */
export default function CategorySelect({
  value, categories, onChange, placeholder = '— sem categoria —', id,
}) {
  const fixas = useContext(CategoriasFixas)
  const oculta = (c) => fixas.includes(c) && c !== value
  const visiveis = categories.filter((c) => !oculta(c))
  // "(nova)" é para categoria que não existe no YAML. Uma fixa existe — ela só
  // não estava na lista desta tela —, então não pode ganhar esse rótulo.
  const nova = value && !categories.includes(value)

  return (
    <select
      id={id}
      className={`category-select ${value ? '' : 'empty'}`}
      value={value || ''}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="">{placeholder}</option>
      {nova && <option value={value}>{value} (nova)</option>}
      {visiveis.map((c) => (
        <option key={c} value={c}>{c}</option>
      ))}
    </select>
  )
}
