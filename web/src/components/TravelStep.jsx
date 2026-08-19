import { useMemo } from 'react'
import CategorySelect from './CategorySelect'
import TravelRanges from './TravelRanges'

const brl = (v) => v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })

const diaMes = (iso) => {
  const [, m, d] = (iso || '').split('-')
  return d ? `${d}/${m}` : iso
}

/**
 * Viagem — a única classificação que é uma JANELA DE TEMPO, não um
 * estabelecimento.
 *
 * O restaurante da esquina e o restaurante de Gramado casam com a mesma
 * palavra-chave. O que os separa é a data da compra, e nenhuma regra de
 * `categories.yml` consegue expressar isso. Por isso esta etapa existe.
 *
 * Duas coisas que a tela deixa explícitas porque erram silenciosamente:
 *
 *   * A data comparada é a da COMPRA (`{Em 15/Jul}`), não a do vencimento da
 *     fatura. Os seletores são limitados ao intervalo real de compras do lote,
 *     então não dá para marcar uma viagem que a fatura nem cobre.
 *   * Estar na janela é SUGESTÃO. Comprar um jogo no eShop no meio da viagem
 *     não é despesa de viagem — por isso cada linha vem marcada mas
 *     desmarcável, e não reclassificada em silêncio.
 *
 * O que é confirmado vira `Viagem` na coluna Categoria e ganha a categoria real
 * entre parênteses na descrição, logo antes do `{Em 15/Jul}`. Assim a planilha
 * continua respondendo "quanto gastei em comida naquela viagem?".
 */
export default function TravelStep({
  session, categories, ranges, items, warnings, rejected,
  onRangesChange, getAssignment, setAssignment, onToggle, onNext, busy,
}) {
  const limites = session.purchase_range

  const confirmadas = useMemo(
    () => items.filter((i) => !rejected.has(i.line_id)),
    [items, rejected],
  )
  const totalConfirmado = confirmadas.reduce((s, i) => s + i.valor, 0)

  return (
    <>
      <section className="card">
        <h2>Períodos de viagem</h2>
        <TravelRanges
          ranges={ranges}
          onChange={onRangesChange}
          limites={limites}
          warnings={warnings}
          busy={busy}
        />
      </section>

      <section className="card">
        <h2>
          Confirmar as compras da viagem <span className="count">{items.length}</span>
        </h2>

        {items.length === 0 ? (
          <p className="muted">
            {ranges.length
              ? 'Nenhuma compra caiu nos períodos acima.'
              : 'Adicione um período acima para ver o que ele pega.'}
          </p>
        ) : (
          <>
            <p className="muted">
              {confirmadas.length} de {items.length} confirmada(s) ·{' '}
              <strong>{brl(totalConfirmado)}</strong> indo para Viagem. Desmarque
              o que não é despesa de viagem — a linha volta inteira à categoria
              dela, sem parêntese nenhum.
            </p>

            <div className="scroll">
              <table className="grid compact sticky">
                <thead>
                  <tr>
                    <th>Viagem?</th>
                    <th>Compra</th>
                    <th>Lançamento</th>
                    <th className="right">Valor</th>
                    <th>Categoria real</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => {
                    const marcada = !rejected.has(item.line_id)
                    // A categoria que vai para o parêntese é a FINAL: se o
                    // usuário resolveu esta linha no marketplace, é a dele.
                    const manual = getAssignment('line', item.line_id)?.categoria
                    const real = manual || item.categoria
                    return (
                      <tr key={item.line_id} className={marcada ? '' : 'blank'}>
                        <td>
                          <input
                            type="checkbox"
                            checked={marcada}
                            aria-label={`Viagem: ${item.descricao}`}
                            onChange={() => onToggle(item.line_id)}
                          />
                        </td>
                        <td className="mono">{diaMes(item.purchase_date)}</td>
                        <td>{item.descricao}</td>
                        <td className="right money">{brl(item.valor)}</td>
                        <td>
                          {real ? (
                            <span>{real}</span>
                          ) : (
                            // Sem categoria real não há o que anexar. Resolver
                            // aqui evita uma linha "Viagem" sem nenhuma pista
                            // do que foi comprado.
                            <CategorySelect
                              value={manual || ''}
                              categories={categories}
                              onChange={(categoria) =>
                                setAssignment('line', item.line_id, { categoria })}
                              placeholder="— sem categoria —"
                            />
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}

        <button className="primary" onClick={onNext}>Continuar</button>
      </section>
    </>
  )
}
