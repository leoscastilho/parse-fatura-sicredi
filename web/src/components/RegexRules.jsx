import { useEffect, useState } from 'react'
import * as api from '../api'
import CategorySelect from './CategorySelect'

/**
 * Regras ordenadas (regex) — a caixa separada.
 *
 * Elas vivem em outro bloco porque obedecem a outra lógica: nas palavras-chave
 * vence o trecho mais longo, aqui vence a PRIMEIRA que casar. Misturar as duas
 * na mesma tabela seria mentir sobre como o motor decide.
 *
 * O campo de teste existe porque a comparação roda sobre a descrição
 * normalizada, não sobre o texto cru — "iFood Clube" vira "I FOOD CLUBE", e um
 * `^IFOOD` que parece certo não casa. Sem testar, não dá para saber.
 */
export default function RegexRules({ categories, onError }) {
  const [rules, setRules] = useState([])
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState(null)
  const [novo, setNovo] = useState({ padrao: '', categoria: '', comment: '' })
  const [amostra, setAmostra] = useState('')
  const [teste, setTeste] = useState(null)

  useEffect(() => { load() }, [])

  async function load() {
    try { setRules(await api.getRegexRules()) } catch (e) { onError(e.message) }
  }

  async function apply(operations) {
    setBusy(true)
    try {
      setRules((await api.editRegexRules(operations)).rules)
      setEditing(null)
    } catch (e) {
      onError(e.message)
    } finally {
      setBusy(false)
    }
  }

  async function runTest(padrao) {
    if (!padrao) return setTeste(null)
    try {
      setTeste(await api.testRegex(padrao, amostra ? [amostra] : []))
    } catch (e) {
      onError(e.message)
    }
  }

  return (
    <section className="card regex-card">
      <h2>Regras ordenadas (regex) <span className="count">{rules.length}</span></h2>
      <p className="muted">
        Aqui vence a <strong>primeira que casar</strong>, de cima para baixo — por
        isso a ordem é editável. Use para os casos específicos que precisam ganhar
        de uma palavra-chave genérica.
      </p>

      <table className="grid compact">
        <thead>
          <tr>
            <th style={{ width: 40 }}>#</th>
            <th>Padrão</th>
            <th style={{ width: 190 }}>Categoria</th>
            <th style={{ width: 150 }}>Ordem</th>
            <th style={{ width: 110 }}>Ações</th>
          </tr>
        </thead>
        <tbody>
          {rules.map((rule, i) => {
            const emEdicao = editing?.index === rule.index
            return (
              <tr key={rule.index}>
                <td className="mono muted">{i + 1}</td>
                <td>
                  {emEdicao ? (
                    <input
                      type="text"
                      className="mono"
                      style={{ width: '100%' }}
                      value={editing.padrao}
                      onChange={(e) => setEditing({ ...editing, padrao: e.target.value })}
                      onBlur={() => runTest(editing.padrao)}
                    />
                  ) : (
                    <code>{rule.padrao}</code>
                  )}
                  {rule.comment && !emEdicao && <div className="samples">{rule.comment}</div>}
                </td>
                <td>
                  {emEdicao ? (
                    <CategorySelect
                      value={editing.categoria}
                      categories={categories}
                      onChange={(categoria) => setEditing({ ...editing, categoria })}
                    />
                  ) : rule.categoria}
                </td>
                <td>
                  <button className="ghost" disabled={busy || i === 0}
                          onClick={() => apply([{ op: 'move', index: rule.index, delta: -1 }])}>
                    ↑
                  </button>{' '}
                  <button className="ghost" disabled={busy || i === rules.length - 1}
                          onClick={() => apply([{ op: 'move', index: rule.index, delta: 1 }])}>
                    ↓
                  </button>
                </td>
                <td>
                  {emEdicao ? (
                    <>
                      <button className="ghost" disabled={busy}
                              onClick={() => apply([{ op: 'update', index: rule.index,
                                                      padrao: editing.padrao,
                                                      categoria: editing.categoria }])}>
                        salvar
                      </button>{' '}
                      <button className="link small" onClick={() => setEditing(null)}>
                        cancelar
                      </button>
                    </>
                  ) : (
                    <>
                      <button className="link small" onClick={() => { setEditing({ ...rule }); runTest(rule.padrao) }}>
                        editar
                      </button>
                      <button className="link small" disabled={busy}
                              onClick={() => apply([{ op: 'remove', index: rule.index }])}>
                        apagar
                      </button>
                    </>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>

      <div className="toolbar">
        <input
          type="text"
          className="mono grow"
          placeholder="novo padrão, ex.: ^IFOOD\s*\*"
          value={novo.padrao}
          onChange={(e) => setNovo({ ...novo, padrao: e.target.value })}
          onBlur={() => runTest(novo.padrao)}
        />
        <CategorySelect
          value={novo.categoria}
          categories={categories}
          placeholder="— categoria —"
          onChange={(categoria) => setNovo({ ...novo, categoria })}
        />
        <button
          className="ghost"
          disabled={busy || !novo.padrao.trim() || !novo.categoria}
          onClick={async () => {
            await apply([{ op: 'add', padrao: novo.padrao.trim(),
                           categoria: novo.categoria, comment: novo.comment }])
            setNovo({ padrao: '', categoria: '', comment: '' })
          }}
        >
          Adicionar no fim
        </button>
      </div>

      <div className="toolbar">
        <input
          type="text"
          className="grow"
          placeholder="testar com uma descrição, ex.: iFood Clube"
          value={amostra}
          onChange={(e) => setAmostra(e.target.value)}
        />
        <button className="ghost"
                onClick={() => runTest(editing?.padrao || novo.padrao)}>
          Testar
        </button>
      </div>

      {teste && (
        <div className={`alert ${teste.valido ? 'ok' : 'error'}`}>
          {!teste.valido ? (
            <>regex inválido: <code>{teste.erro}</code></>
          ) : teste.resultados.length === 0 ? (
            'regex válido. Escreva uma descrição acima para testar o casamento.'
          ) : (
            teste.resultados.map((r, i) => (
              <div key={i}>
                <code>{r.amostra}</code> normaliza para <code>{r.normalizado}</code> →{' '}
                {r.casa
                  ? <strong>casa em “{r.trecho}”</strong>
                  : <strong>não casa</strong>}
              </div>
            ))
          )}
        </div>
      )}
    </section>
  )
}
