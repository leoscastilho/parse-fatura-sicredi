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
  session, categories, getAssignment, setAssignment, setManyAssignments,
  onCategoriesChanged, onNext, onError,
}) {
  const [impacts, setImpacts] = useState({})
  const [saving, setSaving] = useState(false)
  const [esconderResolvidos, setEsconderResolvidos] = useState(false)
  const [categoriaLote, setCategoriaLote] = useState('Outros')
  const items = session.unmapped_items

  // A categoria em vigor, na mesma precedência do backend: o que o usuário
  // escolheu agora vence o que já vinha no arquivo.
  //
  // `item.categoria` só é preenchida na RECATEGORIZAÇÃO — numa fatura nova,
  // "não mapeado" quer dizer que nenhuma regra opinou e a categoria é vazia.
  // Ignorá-la era o bug: um arquivo com 400 linhas já categorizadas à mão, que
  // as regras simplesmente não reconhecem, chegava aqui todo em branco, e o
  // "Continuar e aplicar Outros" apagava anos de decisão de uma vez.
  const categoriaDe = (item) =>
    getAssignment('merchant', item.merchant)?.categoria || item.categoria || ''

  // "Resolvido" inclui o "não sei": decidir que não se sabe também é decidir,
  // e a linha não deve continuar pedindo atenção.
  const resolvido = (item) => {
    const a = getAssignment('merchant', item.merchant)
    return Boolean(categoriaDe(item) || a?.mark_unknown)
  }
  const pendentes = items.filter((i) => !resolvido(i))
  const visiveis = esconderResolvidos ? pendentes : items
  // Quantos chegaram já preenchidos pelo arquivo, sem o usuário ter tocado.
  const doArquivo = items.filter(
    (i) => i.categoria && !getAssignment('merchant', i.merchant)?.categoria).length

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

  // Mesma regra do Marketplace: esta tela não deixa passar estabelecimento sem
  // categoria. O que sobrou recebe a categoria do seletor, e o botão diz qual.
  // Sem isso, "continuar" mandava estabelecimentos vazios para o CSV em
  // silêncio.
  //
  // A lista enviada é montada AQUI, não lida do estado depois de um
  // `setManyAssignments`: o React só aplica o novo estado no próximo render, e
  // ler no mesmo tick mandaria a lista velha para o backend.
  async function continuar() {
    const doLote = pendentes.map((item) => ({
      scope: 'merchant', target: item.merchant,
      categoria: categoriaLote, mark_unknown: false,
    }))

    if (doLote.length) {
      setManyAssignments(doLote.map((a) => ({
        scope: a.scope, target: a.target,
        patch: { categoria: a.categoria, mark_unknown: false },
      })))
    }

    // Só o que o usuário mandou lembrar (ou marcou como desconhecido) vai para
    // o YAML. O preenchimento em lote vale para esta fatura e não vira regra:
    // "Outros" não é conhecimento sobre o estabelecimento.
    const aPersistir = items
      .map((item) => getAssignment('merchant', item.merchant))
      .filter((a) => a && (a.persist_keyword || a.mark_unknown || a.mark_marketplace))

    if (!aPersistir.length) return onNext()

    setSaving(true)
    try {
      await api.updateMapping(session.transaction_id, aPersistir)
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
        {doArquivo > 0 && (
          <> {doArquivo} já tinham categoria no arquivo e ficam como estão —
            marque “esconder os que já preenchi” para ver só os que faltam.</>
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
                    value={categoriaDe(item)}
                    categories={categories}
                    
                    onChange={(categoria) =>
                      setAssignment('merchant', item.merchant, {
                        categoria, mark_unknown: false,
                      })}
                  />
                  {item.categoria && !assignment?.categoria && (
                    <div className="muted small">já vinha no arquivo</div>
                  )}
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

      <div className="toolbar">
        <div className="grow">
          <strong className="small">
            Preencher os {pendentes.length} sem categoria com{' '}
            <span className="destaque">{categoriaLote || '— escolha ao lado —'}</span>
          </strong>
          <div className="muted small">
            vale só para esta fatura · não vira regra no{' '}
            <code>categories.yml</code>
          </div>
        </div>
        <CategorySelect
          value={categoriaLote}
          categories={categories}
          placeholder="— escolher —"
          onChange={setCategoriaLote}
        />
      </div>

      <button className="primary" onClick={continuar}
              disabled={saving || (pendentes.length > 0 && !categoriaLote)}>
        {saving
          ? 'Gravando mapeamento…'
          : pendentes.length > 0
            ? `Continuar e aplicar ${categoriaLote || '…'} em ${pendentes.length}`
            : 'Continuar'}
      </button>
      <p className="muted small">
        O que você marcar como “lembrar” vai para o <code>categories.yml</code>{' '}
        assim que você continuar, e é publicado no GitHub junto com o export.
        {pendentes.length > 0 && (
          <> Os {pendentes.length} sem categoria recebem{' '}
            {categoriaLote || 'a categoria escolhida acima'} — esta tela não
            deixa passar estabelecimento em branco.</>
        )}
      </p>
    </section>
  )
}
