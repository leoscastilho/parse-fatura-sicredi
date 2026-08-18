import { useEffect, useState } from 'react'
import * as api from '../api'

const brl = (v) => v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })

/**
 * Tela final: chama /preview e mostra exatamente as linhas que vão para o CSV,
 * na mesma ordem em que serão gravadas. O que você vê aqui é o arquivo.
 */
export default function FinalReview({ session, assignmentList, onError }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [exporting, setExporting] = useState(false)
  const [done, setDone] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api.preview(session.transaction_id, assignmentList)
      .then((result) => { if (!cancelled) setData(result) })
      .catch((e) => onError(e.message))
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [session.transaction_id, assignmentList, onError])

  async function download() {
    setExporting(true)
    try {
      setDone(await api.exportCsv(session.transaction_id, assignmentList, true))
    } catch (e) {
      onError(e.message)
    } finally {
      setExporting(false)
    }
  }

  if (loading && !data) return <section className="card"><p>Montando o resultado…</p></section>
  if (!data) return null

  const notReconciled = session.statements.filter((s) => !s.reconciles)

  return (
    <section className="card">
      <h2>Conferir e exportar</h2>

      <div className="summary">
        <div><span className="k">Linhas</span><span className="v">{data.rows.length}</span></div>
        <div><span className="k">Total</span><span className="v">{brl(data.total)}</span></div>
        <div><span className="k">Sem categoria</span><span className="v">{data.remaining_blank}</span></div>
        <div><span className="k">Arquivo</span><span className="v mono">{data.filename}</span></div>
      </div>

      {notReconciled.length > 0 && (
        <div className="alert error">
          Estas faturas não fecharam com o total declarado — confira antes de
          colar na planilha: {notReconciled.map((s) => s.name).join(', ')}
        </div>
      )}

      <details className="totals">
        <summary>Total por categoria</summary>
        <table className="grid compact">
          <tbody>
            {Object.entries(data.by_category).map(([categoria, valor]) => (
              <tr key={categoria}>
                <td>{categoria}</td>
                <td className="right money">{brl(valor)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>

      <div className="scroll">
        <table className="grid compact sticky">
          <thead>
            <tr>
              <th>Data</th><th>Categoria</th><th>Descrição</th>
              <th className="right">Valor (R$)</th><th>Pago</th>
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row) => (
              <tr key={row.line_id} className={row.categoria ? '' : 'blank'}>
                <td className="mono">{row.data}</td>
                <td>{row.categoria || <span className="muted">—</span>}</td>
                <td>{row.descricao}</td>
                <td className="right money">{row.valor.toFixed(2)}</td>
                <td>{row.pago}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <button className="primary" onClick={download} disabled={exporting}>
        {exporting ? 'Gerando…' : 'Baixar CSV'}
      </button>

      {done && (
        <div className="alert ok">
          <strong>{done.filename}</strong> baixado.
          {done.commit
            ? <> Mapeamento publicado: <a href={done.commit} target="_blank" rel="noreferrer">ver commit</a></>
            : <> Nenhuma mudança de mapeamento para publicar.</>}
        </div>
      )}
    </section>
  )
}
