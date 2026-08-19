import { useState } from 'react'
import * as api from '../api'
import CategorySelect from './CategorySelect'

const brl = (v) => v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })

/**
 * Estabelecimentos que nenhuma regra reconheceu.
 *
 * Cada linha é um ESTABELECIMENTO, não um lançamento — decidir "Braseiro =
 * Alimentação" resolve as N compras dele de uma vez.
 *
 * "Salvar no mapeamento" é o que faz o conhecimento crescer: grava a
 * palavra-chave no categories.yml, e no mês que vem ele já sai classificado.
 * Antes de salvar, o /validate mostra o estrago — se a palavra-chave for larga
 * demais ela rouba lançamentos que já estavam certos em outra categoria.
 */
export default function UnmappedStep({
  session, categories, getAssignment, setAssignment,
  onCategoriesChanged, onNext, onError,
}) {
  const [impacts, setImpacts] = useState({})
  const [saving, setSaving] = useState(false)
  const [esconderResolvidos, setEsconderResolvidos] = useState(false)
  const items = session.unmapped_items

  // "Resolvido" inclui o "não sei": decidir que não se sabe também é decidir,
  // e a linha não deve continuar pedindo atenção.
  const resolvido = (item) => {
    const a = getAssignment('merchant', item.merchant)
    return Boolean(a?.categoria || a?.mark_unknown)
  }
  const pendentes = items.filter((i) => !resolvido(i))
  const visiveis = esconderResolvidos ? pendentes : items

  async function checkImpact(item) {
    const assignment = getAssignment('merchant', item.merchant)
    if (!assignment?.categoria) return
    try {
      const result = await api.validate(session.transaction_id, [assignment])
      setImpacts((current) => ({
        ...current,
        [item.merchant]: {
          issues: result.issues,
          impact: result.impacts[0] || null,
        },
      }))
    } catch (e) {
      onError(e.message)
    }
  }

  async function persistAll() {
    const toPersist = items
      .map((item) => getAssignment('merchant', item.merchant))
      .filter((a) => a && (a.persist_keyword || a.mark_unknown || a.mark_marketplace))

    if (!toPersist.length) return onNext()

    setSaving(true)
    try {
      await api.updateMapping(session.transaction_id, toPersist)
      const fresh = await api.getCategories()
      onCategoriesChanged(fresh.categories)
      onNext()
    } catch (e) {
      onError(e.message)
    } finally {
      setSaving(false)
    }
  }

  if (!items.length) {
    return (
      <section className="card">
        <h2>Novos estabelecimentos</h2>
        <p className="muted">Nenhum. Todas as regras cobriram esta fatura.</p>
        <button className="primary" onClick={onNext}>Continuar</button>
      </section>
    )
  }

  return (
    <section className="card">
      <h2>Novos estabelecimentos <span className="count">{items.length}</span></h2>
      <p className="muted">
        Nada no <code>categories.yml</code> reconheceu estes. Ordenados por valor,
        do que mais pesa para o que menos pesa.
        {items.length > pendentes.length && (
          <> <strong>{items.length - pendentes.length} de {items.length} resolvido(s).</strong></>
        )}
      </p>

      <div className="toolbar">
        <label className="checkbox">
          <input type="checkbox" checked={esconderResolvidos}
                 onChange={(e) => setEsconderResolvidos(e.target.checked)} />
          Esconder os que já preenchi
        </label>
        <span className="muted small">{pendentes.length} sem categoria</span>
      </div>

      <table className="grid">
        <thead>
          <tr>
            <th>Estabelecimento</th>
            <th className="right">Lanç.</th>
            <th className="right">Total</th>
            <th>Categoria</th>
            <th>Salvar no mapeamento</th>
          </tr>
        </thead>
        <tbody>
          {visiveis.map((item) => {
            const assignment = getAssignment('merchant', item.merchant)
            const feedback = impacts[item.merchant]
            const unknown = assignment?.mark_unknown

            return (
              <tr key={item.merchant} className={unknown ? 'muted-row' : ''}>
                <td>
                  <strong>{item.merchant}</strong>
                  <div className="samples">{item.samples[0]}</div>
                </td>
                <td className="right">{item.count}</td>
                <td className="right money">{brl(item.total)}</td>
                <td>
                  <CategorySelect
                    value={assignment?.categoria || ''}
                    categories={categories}
                    onChange={(categoria) =>
                      setAssignment('merchant', item.merchant, {
                        categoria, mark_unknown: false,
                      })}
                  />
                </td>
                <td>
                  <label className="checkbox">
                    <input
                      type="checkbox"
                      disabled={!assignment?.categoria}
                      checked={Boolean(assignment?.persist_keyword)}
                      onChange={(e) => {
                        setAssignment('merchant', item.merchant, {
                          persist_keyword: e.target.checked ? item.merchant : null,
                        })
                        if (e.target.checked) checkImpact(item)
                        else setImpacts((c) => ({ ...c, [item.merchant]: null }))
                      }}
                    />
                    lembrar
                  </label>

                  {assignment?.persist_keyword && (
                    <input
                      className="keyword"
                      value={assignment.persist_keyword}
                      onChange={(e) =>
                        setAssignment('merchant', item.merchant, {
                          persist_keyword: e.target.value,
                        })}
                      onBlur={() => checkImpact(item)}
                      aria-label="palavra-chave"
                    />
                  )}

                  <button
                    className="link small"
                    onClick={() => setAssignment('merchant', item.merchant, {
                      categoria: '', mark_unknown: true, persist_keyword: null,
                    })}
                  >
                    não sei
                  </button>

                  {feedback?.issues?.map((issue, i) => (
                    <div key={i} className={`inline-note ${issue.severity}`}>
                      {issue.message}
                    </div>
                  ))}
                  {feedback?.impact?.reclassified_away?.length > 0 && (
                    <details className="inline-note warning">
                      <summary>
                        rouba {feedback.impact.reclassified_away.length} lançamento(s)
                      </summary>
                      <ul>
                        {feedback.impact.reclassified_away.slice(0, 8).map((row, i) => (
                          <li key={i}>{row.de} → {row.para}: {row.descricao}</li>
                        ))}
                      </ul>
                    </details>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>

      {esconderResolvidos && visiveis.length === 0 && (
        <p className="muted">Tudo resolvido nesta etapa.</p>
      )}

      <button className="primary" onClick={persistAll} disabled={saving}>
        {saving ? 'Gravando mapeamento…' : 'Continuar'}
      </button>
      <p className="muted small">
        O que você marcar como “lembrar” vai para o <code>categories.yml</code> e
        é publicado num commit único no fim, junto com o export.
      </p>
    </section>
  )
}
