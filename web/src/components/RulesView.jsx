import { useEffect, useMemo, useState } from 'react'
import * as api from '../api'
import CategorySelect from './CategorySelect'
import RegexRules from './RegexRules'

/**
 * Revisão das regras existentes — o `categories.yml` como tela.
 *
 * O foco não é "listar o arquivo" (para isso o editor de texto serve melhor).
 * É mostrar o que o arquivo NÃO consegue mostrar sozinho:
 *
 *   • chutes  — entradas comentadas com `# ?`, esperando sua confirmação;
 *   • redundantes — trechos que um mais curto da mesma categoria já cobre;
 *   • conflitos — trechos que vencem outros de categoria diferente.
 *
 * Toda edição é gravada por inserção/remoção de linha, então seus comentários
 * e sua ordem continuam intactos.
 */
const FILTERS = [
  { id: 'todos', label: 'Todos' },
  { id: 'flagged', label: 'Chutes (# ?)' },
  { id: 'redundant', label: 'Redundantes' },
  { id: 'overrides', label: 'Conflitos' },
]

const BLOCK_LABEL = {
  palavras: 'palavra-chave',
  palavras_genericas: 'genérica',
  marketplaces: 'marketplace',
  desconhecidos: 'desconhecido',
  excluir: 'excluir',
}

export default function RulesView({ onError, onCategoriesChanged, onFlaggedChanged }) {
  const [data, setData] = useState(null)
  const [filter, setFilter] = useState('todos')
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState(null)

  const [newValue, setNewValue] = useState('')
  const [newCategory, setNewCategory] = useState('')

  useEffect(() => { load() }, [])

  async function load() {
    try {
      const result = await api.getRules()
      setData(result)
      onFlaggedChanged?.(result.flagged_count)
    } catch (e) {
      onError(e.message)
    }
  }

  async function apply(operations, message) {
    setBusy(true)
    setNote(null)
    try {
      const result = await api.editRules(operations, false)
      setData((current) => ({ ...current, ...result }))
      onFlaggedChanged?.(result.flagged_count)
      onCategoriesChanged?.(result.categories)
      setNote(message)
    } catch (e) {
      onError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const visible = useMemo(() => {
    if (!data) return []
    const needle = query.trim().toLowerCase()
    return data.entries.filter((entry) => {
      if (filter === 'flagged' && !entry.flagged) return false
      if (filter === 'redundant' && !entry.redundant_with.length) return false
      if (filter === 'overrides' && !entry.overrides.length) return false
      if (!needle) return true
      return (
        entry.value.toLowerCase().includes(needle) ||
        (entry.categoria || '').toLowerCase().includes(needle) ||
        entry.comment.toLowerCase().includes(needle)
      )
    })
  }, [data, filter, query])

  if (!data) return <section className="card"><p>Carregando regras…</p></section>

  const counts = {
    todos: data.entries.length,
    flagged: data.entries.filter((e) => e.flagged).length,
    redundant: data.entries.filter((e) => e.redundant_with.length).length,
    overrides: data.entries.filter((e) => e.overrides.length).length,
  }

  return (
    <>
      <section className="card">
        <h2>Regras <span className="count">{data.entries.length}</span></h2>
        <p className="muted">
          Vale <strong>trecho mais longo vence</strong>. As edições entram por
          inserção e remoção de linha, então seus comentários e sua ordem no{' '}
          <code>categories.yml</code> continuam como estão.
        </p>

        {counts.flagged > 0 && (
          <div className="alert warn">
            <strong>{counts.flagged} entrada(s) marcada(s) com <code># ?</code></strong> —
            chutes esperando sua confirmação. Três saídas, e só uma mantém o
            mapeamento como está:
            <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
              <li><strong>confirmar</strong> — o chute estava certo. Tira o{' '}
                <code># ?</code> e não muda mais nada.</li>
              <li><strong>trocar a categoria</strong> — corrige o destino e salva na hora.</li>
              <li><strong>apagar</strong> — remove a palavra-chave do arquivo.</li>
            </ul>
          </div>
        )}

        <div className="toolbar">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              className={filter === f.id ? 'ghost active' : 'ghost'}
              style={filter === f.id
                ? { borderColor: 'var(--verde)', color: 'var(--verde-escuro)' }
                : undefined}
              onClick={() => setFilter(f.id)}
            >
              {f.label} ({counts[f.id]})
            </button>
          ))}
          <input
            className="search grow"
            type="search"
            placeholder="buscar trecho, categoria ou comentário…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>

        {note && <div className="alert ok">{note}</div>}

        <table className="grid">
          <thead>
            <tr>
              <th>Trecho</th>
              <th>Onde</th>
              <th>Observações</th>
              <th>Ações</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((entry) => (
              <tr key={`${entry.block}|${entry.categoria}|${entry.value}|${entry.line}`}>
                <td>
                  <strong className="mono">{entry.value}</strong>
                  {entry.comment && <div className="samples">{entry.comment}</div>}
                </td>
                <td>
                  {entry.categoria
                    ? <span className="tag block">{entry.categoria}</span>
                    : <span className="tag block">{BLOCK_LABEL[entry.block]}</span>}
                  <div className="muted small">linha {entry.line}</div>
                </td>
                <td>
                  {entry.flagged && <span className="tag flag">chute</span>}
                  {entry.redundant_with.length > 0 && (
                    <div className="inline-note">
                      <span className="tag redundant">redundante</span>
                      já coberto por {entry.redundant_with.join(', ')}
                    </div>
                  )}
                  {entry.overrides.length > 0 && (
                    <div className="inline-note">
                      <span className="tag override">vence</span>
                      sobre {entry.overrides.join(', ')}
                    </div>
                  )}
                </td>
                <td>
                  {entry.flagged && (
                    <button
                      className="ghost confirm"
                      disabled={busy}
                      title="mantém a palavra-chave e a categoria; só tira a marca # ?"
                      onClick={() => apply(
                        [{ op: 'confirm', block: entry.block,
                           categoria: entry.categoria, value: entry.value }],
                        `“${entry.value}” confirmado em ${entry.categoria || entry.block}.`,
                      )}
                    >
                      confirmar
                    </button>
                  )}
                  {entry.block === 'palavras' && (
                    <CategorySelect
                      value={entry.categoria}
                      categories={data.categories}
                      onChange={(destino) => {
                        if (!destino || destino === entry.categoria) return
                        apply(
                          [{ op: 'move', block: 'palavras', categoria: entry.categoria,
                             new_categoria: destino, value: entry.value }],
                          `“${entry.value}” movido para ${destino}.`,
                        )
                      }}
                    />
                  )}
                  <button
                    className="link small"
                    disabled={busy}
                    onClick={() => apply(
                      [{ op: 'remove', block: entry.block,
                         categoria: entry.categoria, value: entry.value }],
                      `“${entry.value}” removido.`,
                    )}
                  >
                    apagar
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {!visible.length && <p className="muted">Nada com esse filtro.</p>}
      </section>

      <section className="card">
        <h2>Adicionar palavra-chave</h2>
        <div className="toolbar">
          <input
            type="text"
            className="grow"
            placeholder="trecho (será normalizado: maiúsculas, sem acento)"
            value={newValue}
            onChange={(e) => setNewValue(e.target.value)}
          />
          <CategorySelect
            value={newCategory}
            categories={data.categories}
            placeholder="— categoria —"
            onChange={setNewCategory}
          />
          <button
            className="ghost"
            disabled={busy || !newValue.trim() || !newCategory}
            onClick={() => {
              apply(
                [{ op: 'add', block: 'palavras', categoria: newCategory, value: newValue.trim() }],
                `“${newValue.trim()}” adicionado em ${newCategory}.`,
              )
              setNewValue('')
            }}
          >
            Adicionar
          </button>
        </div>
      </section>

      <RegexRules categories={data.categories} onError={onError} />
    </>
  )
}
