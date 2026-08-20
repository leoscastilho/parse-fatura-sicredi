import { useMemo, useState } from 'react'
import CategorySelect from './CategorySelect'
import { passaGrupo, useTitularFiltro } from '../titulares'

const brl = (v) => v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })

/**
 * Revisão do que o script já classificou.
 *
 * Deliberadamente NÃO é uma tela sequencial. São ~135 estabelecimentos e ~290
 * lançamentos: confirmar um a um seria mais lento que a CLI. Aqui tudo aparece
 * de uma vez, já preenchido, e você só toca no que está errado — o caso comum
 * é passar direto.
 *
 * O agrupamento é por estabelecimento: "Supermercados Alvora, 28x, R$ 5.496,94"
 * é uma linha, não 28.
 */
export default function AutoReviewStep({
  session, categories, getAssignment, setAssignment, onNext,
}) {
  const [query, setQuery] = useState('')
  const [onlyChanged, setOnlyChanged] = useState(false)
  // O filtro por titular é PURAMENTE VISUAL, e por isso ele entra no ÚLTIMO
  // momento possível: só nas linhas que a tabela desenha.
  //
  // Aplicá-lo em `items` teria parecido igual e mudado o comportamento — os
  // contadores, o "quantos faltam" e principalmente o preenchimento em lote
  // passariam a enxergar meio lote. "Continuar e aplicar Outros em 4" deixaria
  // os estabelecimentos da outra pessoa em branco, e a promessa desta tela ("não
  // deixa passar estabelecimento em branco") pararia de valer em silêncio.
  const filtroTitular = useTitularFiltro()
  const items = session.auto_classified_items

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return items.filter((item) => {
      if (!passaGrupo(filtroTitular, item.titulares)) return false
      if (onlyChanged && !getAssignment('merchant', item.merchant)) return false
      if (!needle) return true
      return (
        item.merchant.toLowerCase().includes(needle) ||
        item.categoria.toLowerCase().includes(needle)
      )
    })
  }, [items, query, onlyChanged, getAssignment, filtroTitular])

  const changed = items.filter((i) => getAssignment('merchant', i.merchant)).length
  const lines = items.reduce((sum, i) => sum + i.count, 0)

  return (
    <section className="card">
      <h2>
        Já classificados <span className="count">{items.length}</span>
      </h2>
      <p className="muted">
        {lines} lançamentos agrupados em {items.length} estabelecimentos. Já vêm
        preenchidos — mexa só no que estiver errado.
        {changed > 0 && <> <strong>{changed} alterado(s).</strong></>}
      </p>

      <div className="toolbar">
        <input
          className="search"
          type="search"
          placeholder="filtrar por estabelecimento ou categoria…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <label className="checkbox">
          <input
            type="checkbox"
            checked={onlyChanged}
            onChange={(e) => setOnlyChanged(e.target.checked)}
          />
          só os que eu mudei
        </label>
      </div>

      <table className="grid">
        <thead>
          <tr>
            <th>Estabelecimento</th>
            <th className="right">Lanç.</th>
            <th className="right">Total</th>
            <th>Categoria</th>
            <th>Regra</th>
          </tr>
        </thead>
        <tbody>
          {visible.map((item) => {
            const override = getAssignment('merchant', item.merchant)
            const current = override?.categoria ?? item.categoria
            const dirty = Boolean(override)

            return (
              <tr key={`${item.merchant}|${item.categoria}`} className={dirty ? 'dirty' : ''}>
                <td>
                  <strong>{item.merchant}</strong>
                  <div className="samples">{item.samples[0]}</div>
                </td>
                <td className="right">{item.count}</td>
                <td className="right money">{brl(item.total)}</td>
                <td>
                  <CategorySelect
                    value={current}
                    categories={categories}
                    
                    onChange={(categoria) => {
                      if (categoria === item.categoria) {
                        setAssignment('merchant', item.merchant, null)
                      } else {
                        setAssignment('merchant', item.merchant, { categoria })
                      }
                    }}
                  />
                  {dirty && (
                    <button
                      className="link small"
                      onClick={() => setAssignment('merchant', item.merchant, null)}
                    >
                      desfazer ({item.categoria})
                    </button>
                  )}
                </td>
                <td className="rule">{item.matched}</td>
              </tr>
            )
          })}
        </tbody>
      </table>

      {!visible.length && <p className="muted">Nada com esse filtro.</p>}

      <button className="primary" onClick={onNext}>Continuar</button>
    </section>
  )
}
