import { useMemo } from 'react'
import CategorySelect from './CategorySelect'
import TravelRanges from './TravelRanges'

const brl = (v) => v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })

const diaMes = (iso) => {
  const [, m, d] = (iso || '').split('-')
  return d ? `${d}/${m}` : iso
}

/**
 * Qual período pegou esta linha — o PRIMEIRO que contém a data da compra.
 *
 * O empate é resolvido igual ao backend (`travel.range_of`): períodos que se
 * sobrepõem, vence o primeiro da lista. Resolver diferente aqui mostraria na
 * tela um nome de viagem que o arquivo não vai levar.
 *
 * As datas são ISO (`AAAA-MM-DD`), então comparar as strings é comparar as
 * datas — sem `new Date`, que interpretaria a string como UTC e deslocaria o
 * dia para quem está em fuso negativo.
 */
const periodoDe = (iso, ranges) =>
  ranges.find((r) => r.inicio <= iso && iso <= r.fim) || null

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
                    <th>Qual viagem</th>
                    <th>Compra</th>
                    <th>Lançamento</th>
                    <th className="right">Valor</th>
                    <th>Categoria real</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => {
                    const marcada = !rejected.has(item.line_id)
                    // A categoria que vai para o parêntese é a FINAL, na mesma
                    // ordem de precedência que o backend usa em
                    // `_apply_assignments`: decisão de LINHA (marketplace) vence
                    // decisão de ESTABELECIMENTO (Novos), que vence a da regra.
                    //
                    // Olhar só o escopo `line` era o bug: a categoria escolhida
                    // na aba "Novos" fica em `merchant`, então a linha chegava
                    // aqui em branco e pedia para escolher de novo. O export
                    // saía certo — quem estava errado era esta tela, o que é
                    // pior, porque manda refazer um trabalho já feito.
                    //
                    // A escolha é do OBJETO decisão, não do campo: é assim que
                    // `_apply_assignments` resolve (`by_line or by_merchant`),
                    // e escolher pelo campo faria uma decisão de linha com
                    // categoria vazia cair de volta na do estabelecimento.
                    const decisao = getAssignment('line', item.line_id)
                      || getAssignment('merchant', item.merchant)
                    const manual = decisao?.categoria
                    const real = manual || item.categoria
                    const periodo = periodoDe(item.purchase_date, ranges)
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
                        <td>
                          {/* Sem nome, mostra a janela em vez de célula vazia:
                              com dois períodos abertos ao mesmo tempo, o que
                              esta coluna responde é "qual dos dois pegou esta
                              linha" — e a data responde isso tão bem quanto o
                              nome que ninguém digitou. */}
                          {periodo?.rotulo
                            ? periodo.rotulo
                            : periodo
                              ? <span className="muted mono">
                                  {diaMes(periodo.inicio)}–{diaMes(periodo.fim)}
                                </span>
                              : <span className="muted">—</span>}
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
