import { useMemo, useState } from 'react'
import CategorySelect from './CategorySelect'
import TravelRanges from './TravelRanges'
import { chaveDoPeriodo, dataCurta, rotuloDoPeriodo } from '../viagens'
import { passaLinha, useTitularFiltro } from '../titulares'

const brl = (v) => v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })

// Quantas linhas a gaveta mostra por vez, antes de digitar qualquer coisa.
const LOTE = 10

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
  outros = [], pinned = {}, onPin,
}) {
  const limites = session.purchase_range
  // Filtro PURAMENTE VISUAL. Vale para as duas listas desta tela: esconder as
  // compras da outra pessoa enquanto confiro as minhas não desmarca nem
  // desfaz nada — o que está escondido continua indo para Viagem igual.
  const filtroTitular = useTitularFiltro()
  const [busca, setBusca] = useState('')
  const [quantas, setQuantas] = useState(LOTE)

  const confirmadas = useMemo(
    () => items.filter((i) => !rejected.has(i.line_id)),
    [items, rejected],
  )

  /**
   * A tabela vem ordenada por VIAGEM, e depois pela data da compra.
   *
   * O backend entrega `items` na ordem do arquivo de saída
   * (`ordenacao: [data, categoria, data_compra]` do output.yml), que é a ordem
   * certa para o CSV e a errada para esta tela: as linhas saem agrupadas por
   * categoria, e com dois períodos no mesmo mês — Campo Belo em 06/12 e Dallas
   * em 13/12 — a coluna "Qual viagem" fica pulando e parece bug de ordenação.
   *
   * Aqui a pergunta é "o que entrou nesta viagem?", então a viagem agrupa e a
   * data da compra ordena dentro dela. A ordem do CSV não muda: quem manda no
   * export é o /preview, não isto.
   */
  const ordenadas = useMemo(
    () => items.filter((i) => passaLinha(filtroTitular, i.titular)).sort((a, b) =>
      (a.viagem_periodo?.inicio || '9999').localeCompare(b.viagem_periodo?.inicio || '9999')
      || a.purchase_date.localeCompare(b.purchase_date)
      || a.descricao.localeCompare(b.descricao, 'pt-BR')),
    [items, filtroTitular],
  )
  const totalConfirmado = confirmadas.reduce((s, i) => s + i.valor, 0)
  const penduradas = items.filter((i) => i.viagem_a_mao).length

  /**
   * O que a gaveta oferece: as MAIORES compras fora de viagem, e a busca
   * filtrando o lote inteiro.
   *
   * O corte em 10 vale só para a lista de partida. Passagem e hospedagem são
   * as compras caras da fatura e aparecem no topo sem digitar nada; para o
   * resto existe o campo. Lista vazia até digitar seria mais limpa e exigiria
   * lembrar o nome do estabelecimento, que é justamente o que não se lembra
   * três meses depois.
   */
  const naGaveta = useMemo(
    () => outros.filter((i) => passaLinha(filtroTitular, i.titular)),
    [outros, filtroTitular],
  )
  const achados = useMemo(() => {
    const alvo = busca.trim().toLowerCase()
    if (!alvo) return naGaveta.slice(0, quantas)
    return naGaveta.filter((i) => i.descricao.toLowerCase().includes(alvo))
  }, [naGaveta, busca, quantas])

  // Só a lista de partida é paginada; a busca varre o lote inteiro e mostra
  // tudo que casar. Paginar o resultado da busca esconderia justamente o que o
  // usuário acabou de pedir pelo nome.
  const faltam = busca.trim() ? 0 : naGaveta.length - achados.length

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
                  {ordenadas.map((item) => {
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
                    // Quem resolveu QUAL viagem foi o backend: a fixação à mão
                    // vence a data, e períodos sobrepostos têm desempate. Uma
                    // segunda implementação disso aqui faria a tela dizer Peru
                    // e o arquivo sair Ferroão.
                    const periodo = item.viagem_periodo
                    return (
                      <tr key={item.line_id}
                          className={`${marcada ? '' : 'blank'} ${item.viagem_a_mao ? 'amao' : ''}`}>
                        <td>
                          <input
                            type="checkbox"
                            checked={marcada}
                            aria-label={`Viagem: ${item.descricao}`}
                            onChange={() => onToggle(item.line_id)}
                          />
                        </td>
                        <td>
                          {/* A linha PENDURADA À MÃO edita a viagem aqui —
                              porque pendurar a tira da gaveta lá embaixo, e
                              sem isto errar a viagem era irreversível: a linha
                              subia para cá com o nome errado e não havia como
                              corrigi-la nem devolvê-la. Desmarcar a caixa não
                              resolvia: aquilo exclui da viagem, não desfaz a
                              fixação.

                              Quem foi pega pela DATA continua texto: a viagem
                              dela vem do período, e trocá-la aqui seria mentir
                              sobre o que a janela decidiu. Para essa, o
                              caminho é mexer no período.

                              Sem nome, `rotuloDoPeriodo` devolve a janela em
                              vez de vazio: com dois períodos abertos, o que
                              esta coluna responde é "qual dos dois pegou esta
                              linha" — e a data responde isso tão bem quanto o
                              nome que ninguém digitou. */}
                          {item.viagem_a_mao ? (
                            <select
                              value={pinned[item.line_id] || chaveDoPeriodo(periodo)}
                              disabled={busy}
                              aria-label={`Qual viagem: ${item.descricao}`}
                              onChange={(e) => onPin?.(item.line_id, e.target.value)}
                            >
                              <option value="">— tirar da viagem —</option>
                              {ranges.map((r) => (
                                <option key={chaveDoPeriodo(r)} value={chaveDoPeriodo(r)}>
                                  {rotuloDoPeriodo(r)}
                                </option>
                              ))}
                            </select>
                          ) : periodo
                            ? <span className={periodo.rotulo ? '' : 'muted mono'}>
                                {rotuloDoPeriodo(periodo)}
                              </span>
                            : <span className="muted">—</span>}
                        </td>
                        {/* Com o ano: o `{Em 18/Dec}` da descrição não o tem
                            (é o formato da planilha), e num lote com o
                            histórico inteiro `18/12` não diz de qual ano. */}
                        <td className="mono">{dataCurta(item.purchase_date)}</td>
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

        {/* A GAVETA. Fechada por padrão, porque na maioria das faturas não há
            nada a pendurar — e a tabela de cima é o que interessa. É o mesmo
            `<details>` que a tela de upload usa para "Viajou neste período?",
            então não é padrão novo. */}
        {ranges.length > 0 && (
          <details className="pendurar">
            <summary>
              Comprou algo para a viagem antes dela?
              {penduradas > 0 && <span className="badge">{penduradas}</span>}
            </summary>

            <p className="muted small">
              Passagem, hospedagem e passeio quase sempre são pagos meses antes,
              e a data não conta isso — a passagem de trem do Peru sai em agosto
              e a viagem é em outubro. Ache a compra e diga de qual viagem ela é.
              Alargar o período até agosto arrastaria junto o mês inteiro.
            </p>

            <div className="toolbar">
              <input type="text" className="grow" value={busca}
                     aria-label="Procurar um lançamento para pendurar na viagem"
                     placeholder="filtrar por descrição…"
                     onChange={(e) => setBusca(e.target.value)} />
              <span className="muted small">
                {busca.trim()
                  ? `${achados.length} de ${naGaveta.length} lançamentos`
                  : `as ${achados.length} maiores de ${naGaveta.length}`}
              </span>
            </div>

            {achados.length === 0 ? (
              <p className="muted small">Nada com esse texto neste lote.</p>
            ) : (
              <div className="scroll">
                <table className="grid compact">
                  <tbody>
                    {achados.map((item) => (
                      <tr key={item.line_id}>
                        <td className="mono">{dataCurta(item.purchase_date)}</td>
                        <td>{item.descricao}</td>
                        <td className="right money">{brl(item.valor)}</td>
                        <td>
                          <select
                            value={pinned[item.line_id] || ''}
                            disabled={busy}
                            aria-label={`Pendurar numa viagem: ${item.descricao}`}
                            onChange={(e) => onPin?.(item.line_id, e.target.value)}
                          >
                            <option value="">— nenhuma —</option>
                            {ranges.map((r) => (
                              <option key={chaveDoPeriodo(r)} value={chaveDoPeriodo(r)}>
                                {rotuloDoPeriodo(r)}
                              </option>
                            ))}
                          </select>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {/* Rolar uma lista de 130 linhas para achar a passagem seria pior
                do que buscar por nome — por isso a lista cresce de 10 em 10, e
                o botão diz quantas ainda faltam em vez de "mostrar mais". */}
            {faltam > 0 && (
              <button className="ghost" onClick={() => setQuantas((q) => q + LOTE)}>
                Mostrar mais {Math.min(LOTE, faltam)} · faltam {faltam}
              </button>
            )}
          </details>
        )}

        <button className="primary" onClick={onNext}>Continuar</button>
      </section>
    </>
  )
}
