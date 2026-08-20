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

  // A categoria em vigor: a escolha do usuário vence a que veio no arquivo.
  //
  // `line.categoria` só chega preenchida na RECATEGORIZAÇÃO — numa fatura nova,
  // marketplace quer dizer que a regra se recusou a opinar e a linha está
  // vazia. Ignorá-la era o bug: as compras de Amazon que você já tinha
  // classificado à mão na planilha voltavam em branco, e o "Continuar e aplicar
  // Outros" as apagava.
  const categoriaDe = (line) =>
    getAssignment('line', line.line_id)?.categoria || line.categoria || ''

  const pending = useMemo(
    () => items.filter((i) => !categoriaDe(i)),
    [items, getAssignment],
  )
  const visible = hideResolved ? pending : items
  const resolved = items.length - pending.length
  // "Limpar tudo" desfaz o que VOCÊ escolheu; a categoria que veio no arquivo
  // não é sua para limpar aqui, então o botão fica apagado quando você ainda
  // não editou nada.
  const editadas = items.filter((i) => getAssignment('line', i.line_id)?.categoria).length
  const total = items.reduce((sum, i) => sum + i.valor, 0)
  const pendingTotal = pending.reduce((sum, i) => sum + i.valor, 0)

  function applyToPending() {
    if (!bulkCategory || !pending.length) return
    setManyAssignments(pending.map((line) => ({
      scope: 'line', target: line.line_id, patch: { categoria: bulkCategory },
    })))
  }

  // Sair daqui com linha em branco é o erro que esta tela existe para evitar:
  // o CSV sai com buracos e o trabalho volta para a planilha. Então o botão de
  // continuar preenche o que faltou, e diz exatamente com o quê.
  function continuar() {
    if (pending.length && bulkCategory) applyToPending()
    onNext()
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
        <button className="ghost danger" onClick={clearAll} disabled={!editadas}>
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
                    value={categoriaDe(line)}
                    categories={categories}
                    placeholder="— deixar em branco —"
                    onChange={(categoria) =>
                      setAssignment('line', line.line_id, categoria ? { categoria } : null)}
                  />
                  {line.categoria && !assignment?.categoria && (
                    <div className="muted small">já vinha no arquivo</div>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>

      {!visible.length && (
        <p className="muted" style={{ marginTop: 14 }}>Tudo preenchido.</p>
      )}

      <button className="primary" onClick={continuar}
              disabled={pending.length > 0 && !bulkCategory}>
        {pending.length > 0
          ? `Continuar e aplicar ${bulkCategory || '…'} em ${pending.length}`
          : 'Continuar'}
      </button>
      <p className="muted small">
        {pending.length > 0
          ? `As ${pending.length} linha(s) sem categoria recebem
             ${bulkCategory || 'a categoria escolhida acima'} ao continuar — esta
             tela não deixa passar linha em branco. O que você já classificou à
             mão não é tocado.`
          : 'Todas as linhas têm categoria.'}
      </p>
    </section>
  )
}
