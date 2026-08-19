import { useEffect, useState } from 'react'
import * as api from '../api'

const brl = (v) => v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })

/**
 * Tela final: chama /preview e mostra exatamente as linhas que vão para o CSV,
 * na mesma ordem em que serão gravadas. O que você vê aqui é o arquivo.
 */
export default function FinalReview({
  session, assignmentList, travelRejected = [], onError,
}) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [exporting, setExporting] = useState(false)
  const [done, setDone] = useState(null)
  // Num histórico de milhares de linhas, o que interessa conferir são as
  // poucas que a regra tocou — então o filtro já começa ligado.
  const [soAlteradas, setSoAlteradas] = useState(true)

  // Serializado só para a lista de dependências: o array chega recriado a cada
  // render do App, e usá-lo direto refaria o /preview em loop infinito.
  const rejeitadasJson = JSON.stringify(travelRejected)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api.preview(session.transaction_id, assignmentList, JSON.parse(rejeitadasJson))
      .then((result) => { if (!cancelled) setData(result) })
      .catch((e) => onError(e.message))
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [session.transaction_id, assignmentList, rejeitadasJson, onError])

  async function download() {
    setExporting(true)
    try {
      setDone(await api.exportCsv(
        session.transaction_id, assignmentList, true, travelRejected))
    } catch (e) {
      onError(e.message)
    } finally {
      setExporting(false)
    }
  }

  if (loading && !data) return <section className="card"><p>Montando o resultado…</p></section>
  if (!data) return null

  const notReconciled = session.statements.filter((s) => !s.reconciles)

  // Na recategorização cada linha carrega a categoria que estava no arquivo.
  // É o que permite marcar o que mudou — e filtrar por isso, porque num
  // histórico de 6 mil linhas o que interessa conferir são as poucas que a
  // regra tocou.
  const recategorizando = session.modo === 'recategorizacao'
  const mudou = (row) =>
    row.categoria_anterior !== null && row.categoria !== row.categoria_anterior
  const alteradas = recategorizando ? data.rows.filter(mudou).length : 0
  const linhas = (recategorizando && soAlteradas)
    ? data.rows.filter(mudou)
    : data.rows

  return (
    <section className="card">
      <h2>Conferir e exportar</h2>

      <div className="summary">
        <div><span className="k">Linhas</span><span className="v">{data.rows.length}</span></div>
        <div><span className="k">Total</span><span className="v">{brl(data.total)}</span></div>
        <div><span className="k">Sem categoria</span><span className="v">{data.remaining_blank}</span></div>
        {recategorizando
          ? <div><span className="k">Categoria alterada</span><span className="v">{alteradas}</span></div>
          : <div><span className="k">Arquivo</span><span className="v mono">{data.filename}</span></div>}
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
            {/* Do maior para o menor: a pergunta que se faz aqui é "onde foi
                parar o dinheiro", e a resposta está no topo. Em ordem
                alfabética ela ficava no meio da lista. */}
            {Object.entries(data.by_category)
              .sort((a, b) => b[1] - a[1])
              .map(([categoria, valor]) => (
                <tr key={categoria}>
                  <td>{categoria}</td>
                  <td className="right money">{brl(valor)}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </details>

      {recategorizando && (
        <div className="toolbar">
          <label className="checkbox">
            <input type="checkbox" checked={soAlteradas}
                   onChange={(e) => setSoAlteradas(e.target.checked)} />
            Mostrar só as linhas que mudaram de categoria
          </label>
          <span className="muted small">
            {alteradas} de {data.rows.length}
          </span>
        </div>
      )}

      <div className="scroll">
        <table className="grid compact sticky">
          <thead>
            <tr>
              <th>Data</th>
              {recategorizando && <th>De</th>}
              <th>{recategorizando ? 'Para' : 'Categoria'}</th>
              <th>Descrição</th>
              <th className="right">Valor (R$)</th><th>Pago</th>
            </tr>
          </thead>
          <tbody>
            {linhas.map((row) => {
              const trocou = recategorizando && mudou(row)
              return (
                <tr key={row.line_id}
                    className={`${row.categoria ? '' : 'blank'} ${trocou ? 'changed' : ''}`}>
                  <td className="mono">{row.data}</td>
                  {recategorizando && (
                    <td className="muted">
                      {row.categoria_anterior || <span className="muted">— vazia —</span>}
                    </td>
                  )}
                  <td>
                    {trocou
                      ? <strong>{row.categoria || '— vazia —'}</strong>
                      : (row.categoria || <span className="muted">—</span>)}
                  </td>
                  <td>{row.descricao}</td>
                  <td className="right money">{row.valor.toFixed(2)}</td>
                  <td>{row.pago}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {recategorizando && soAlteradas && alteradas === 0 && (
        <p className="muted">
          Nenhuma linha muda de categoria — o arquivo sai idêntico ao que entrou.
        </p>
      )}

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
