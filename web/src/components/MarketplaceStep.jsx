import { useMemo, useState } from 'react'
import CategorySelect from './CategorySelect'

const brl = (v) => v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })

/**
 * Marketplaces — a única tela em que a decisão é por LINHA.
 *
 * Amazon e Mercado Livre vendem ração, monitor, livro e panela. Uma
 * palavra-chave para "MERCADOLIVRE" fixaria uma categoria que estaria errada na
 * maioria das compras, então aqui não existe "lembrar": cada lançamento é
 * decidido individualmente, e nada é gravado no YAML.
 *
 * O botão em lote existe porque 70 linhas de Amazon num mês em que você não
 * lembra o que comprou é trabalho que não paga. Ele preenche só as VAZIAS —
 * o que você já classificou à mão não é sobrescrito.
 */
export default function MarketplaceStep({
  session, categories, getAssignment, setAssignment, setManyAssignments, onNext,
}) {
  const [hideResolved, setHideResolved] = useState(false)
  const [bulkCategory, setBulkCategory] = useState('Outros')
  const items = session.marketplace_items

  const pending = useMemo(
    () => items.filter((i) => !getAssignment('line', i.line_id)?.categoria),
    [items, getAssignment],
  )
  const visible = hideResolved ? pending : items
  const resolved = items.length - pending.length
  const total = items.reduce((sum, i) => sum + i.valor, 0)
  const pendingTotal = pending.reduce((sum, i) => sum + i.valor, 0)

  function applyToPending() {
    if (!bulkCategory || !pending.length) return
    setManyAssignments(pending.map((line) => ({
      scope: 'line', target: line.line_id, patch: { categoria: bulkCategory },
    })))
  }

  function clearAll() {
    setManyAssignments(items.map((line) => ({
      scope: 'line', target: line.line_id, patch: null,
    })))
  }

  if (!items.length) {
    return (
      <section className="card">
        <h2>Marketplaces</h2>
        <p className="muted">Nenhuma compra de marketplace nesta fatura.</p>
        <button className="primary" onClick={onNext}>Continuar</button>
      </section>
    )
  }

  return (
    <section className="card">
      <h2>Marketplaces <span className="count">{items.length}</span></h2>
      <p className="muted">
        {brl(total)} em Amazon, Mercado Livre e afins. A categoria muda a cada
        compra, então aqui é <strong>linha a linha</strong> e nada vai para o{' '}
        <code>categories.yml</code>.
        {resolved > 0 && <> <strong>{resolved} de {items.length} preenchido(s).</strong></>}
      </p>

      <div className="toolbar">
        <div className="grow">
          <strong className="small">
            Preencher as {pending.length} sem categoria com{' '}
            <span className="destaque">{bulkCategory || '— escolha ao lado —'}</span>
          </strong>
          <div className="muted small">
            {brl(pendingTotal)} · não sobrescreve o que você já classificou ·
            o padrão é <code>Outros</code>, troque no seletor se quiser outra
          </div>
        </div>

        <CategorySelect
          value={bulkCategory}
          categories={categories}
          placeholder="— escolher —"
          onChange={setBulkCategory}
        />
        <button className="ghost" onClick={applyToPending}
                disabled={!pending.length || !bulkCategory}>
          Aplicar {bulkCategory || '…'} em {pending.length}
        </button>
        <button className="ghost danger" onClick={clearAll} disabled={!resolved}>
          Limpar tudo
        </button>
      </div>

      <label className="checkbox">
        <input
          type="checkbox"
          checked={hideResolved}
          onChange={(e) => setHideResolved(e.target.checked)}
        />
        esconder os que já preenchi
      </label>

      <table className="grid">
        <thead>
          <tr>
            <th>Lançamento</th>
            <th className="right">Valor</th>
            <th>Fatura</th>
            <th>Categoria</th>
          </tr>
        </thead>
        <tbody>
          {visible.map((line) => {
            const assignment = getAssignment('line', line.line_id)
            return (
              <tr key={line.line_id} className={assignment?.categoria ? 'dirty' : ''}>
                <td>{line.descricao}</td>
                <td className="right money">{brl(line.valor)}</td>
                <td className="muted small">{line.statement}</td>
                <td>
                  <CategorySelect
                    value={assignment?.categoria || ''}
                    categories={categories}
                    placeholder="— deixar em branco —"
                    onChange={(categoria) =>
                      setAssignment('line', line.line_id, categoria ? { categoria } : null)}
                  />
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>

      {!visible.length && (
        <p className="muted" style={{ marginTop: 14 }}>Tudo preenchido.</p>
      )}

      <button className="primary" onClick={onNext}>Continuar</button>
      <p className="muted small">
        {pending.length > 0
          ? `Continuar agora deixa ${pending.length} linha(s) SEM categoria — elas saem
             vazias no CSV, no fim do bloco da fatura, e você resolve na planilha.
             Nada é preenchido automaticamente.`
          : 'Todas as linhas têm categoria.'}
      </p>
    </section>
  )
}
