import { useState } from 'react'
import { brlExato, rotuloPeriodo } from './charts'

/**
 * Barra de filtros — a leitura ao contrário.
 *
 * A pergunta que os painéis respondem é "para onde vai o dinheiro". Num
 * histórico com uma compra de imóvel de R$ 600 mil, essa pergunta tem uma
 * resposta só, e ela é inútil: vai para a casa. O que se quer saber vem de
 * TIRAR — sem a casa, sem a obra, sem aquele pagamento único, como é a rotina?
 *
 * Por isso o padrão é "tudo dentro" e o trabalho é remover, não escolher. E por
 * isso a barra grita quando há filtro em vigor: um painel filtrado que parece
 * um painel completo é a pior tela que este portal poderia ter.
 *
 * As exclusões vão para o SERVIDOR e tudo é recalculado lá, como o recorte de
 * datas. Tirar Casa no cliente, depois de agregar, deixaria a média mensal e o
 * custo fixo com a casa dentro ao lado de gráficos sem ela.
 */
export default function FiltrosLaterais({
  disponiveis, semCategorias, semLinhas, rotulos = {}, busy, aoAplicar,
}) {
  const [aberta, setAberta] = useState(false)
  const [quantasLinhas, setQuantasLinhas] = useState(10)

  const categorias = disponiveis?.categorias || []
  const lancamentos = disponiveis?.lancamentos || []
  const ativos = semCategorias.length + semLinhas.length

  const alternarCategoria = (categoria) =>
    aoAplicar({
      semCategorias: semCategorias.includes(categoria)
        ? semCategorias.filter((c) => c !== categoria)
        : [...semCategorias, categoria],
      semLinhas,
    })

  const alternarLinha = (id, descricao) =>
    aoAplicar({
      semCategorias,
      semLinhas: semLinhas.includes(id)
        ? semLinhas.filter((x) => x !== id)
        : [...semLinhas, id],
      // O rótulo viaja junto para o excluído continuar visível mesmo quando o
      // período muda e ele sai da lista de candidatos.
      rotulos: descricao ? { [id]: descricao } : undefined,
    })

  // "Sempre 10 na tela": os já excluídos não ocupam vaga, então marcar um
  // revela o próximo — a lista não encurta enquanto você trabalha nela.
  const candidatos = lancamentos.filter((l) => !semLinhas.includes(l.id))
  const mostrados = candidatos.slice(0, quantasLinhas)

  // Os chips saem de `semLinhas`, não da lista de candidatos: um lançamento
  // excluído em 2021 não aparece na lista de "últimos 6 meses", e sem o chip
  // não haveria como desfazer essa exclusão sem limpar tudo.
  const excluidos = semLinhas.map((id) => ({
    id,
    descricao: lancamentos.find((l) => l.id === id)?.descricao
      || rotulos[id]
      // Último recurso: a identidade é `período|categoria|descrição|valor`,
      // então a descrição está lá dentro mesmo sem cache nenhum.
      || id.split('|')[2] || id,
  }))

  return (
    <>
      <button className={`filtros-aba ${aberta ? 'aberta' : ''}`}
              onClick={() => setAberta((v) => !v)}
              aria-expanded={aberta}
              title={ativos ? `${ativos} filtro(s) aplicado(s)` : 'Filtros'}>
        Filtros
        {/* A bolha existe para o painel filtrado nunca passar por completo. */}
        {ativos > 0 && <span className="filtros-bolha">{ativos}</span>}
      </button>

      {aberta && (
        <aside className="filtros" aria-label="Filtros dos painéis">
          <div className="toolbar">
            <strong className="grow">Filtros</strong>
            <button className="link" onClick={() => setAberta(false)}>fechar</button>
          </div>

          {ativos > 0 ? (
            <div className="alert warn">
              {ativos} filtro(s) em vigor — <strong>todos</strong> os painéis
              desta aba estão sem eles.{' '}
              <button className="link" disabled={busy}
                      onClick={() => aoAplicar({ semCategorias: [], semLinhas: [] })}>
                trazer tudo de volta
              </button>
            </div>
          ) : (
            <p className="muted small">
              Tudo dentro. Remova o que distorce e os painéis se reajustam —
              média, custo fixo e anomalias são recalculados, não fatiados.
            </p>
          )}

          <h3>Categorias</h3>
          <p className="muted small">
            {categorias.length - semCategorias.length} de {categorias.length} nos painéis.
          </p>
          <div className="filtros-lista">
            {categorias.map((categoria) => (
              <label className="checkbox filtros-item" key={categoria || '(vazia)'}>
                <input type="checkbox" disabled={busy}
                       checked={!semCategorias.includes(categoria)}
                       onChange={() => alternarCategoria(categoria)} />
                {categoria || '(sem categoria)'}
              </label>
            ))}
          </div>

          <h3>Lançamentos avulsos</h3>
          <p className="muted small">
            Os maiores gastos do período, um a um — é aqui que sai a compra
            única que achata o resto.
          </p>

          {excluidos.length > 0 && (
            <div className="chips">
              {excluidos.map((l) => (
                <button key={l.id} className="chip" disabled={busy}
                        aria-label={`Trazer de volta ${l.descricao}`}
                        onClick={() => alternarLinha(l.id)}>
                  {l.descricao.slice(0, 26)} <span aria-hidden>×</span>
                </button>
              ))}
            </div>
          )}

          <div className="filtros-lista">
            {mostrados.map((l) => (
              <label className="checkbox filtros-item" key={l.id}>
                <input type="checkbox" disabled={busy}
                       checked={semLinhas.includes(l.id)}
                       onChange={() => alternarLinha(l.id, l.descricao)} />
                <span className="grow">
                  {l.descricao}
                  <span className="muted small">
                    {' '}· {rotuloPeriodo(l.periodo)} · {l.categoria || '(sem categoria)'}
                  </span>
                </span>
                <span className="money small">{brlExato(l.valor)}</span>
              </label>
            ))}
          </div>

          {candidatos.length > mostrados.length && (
            <button className="ghost" disabled={busy}
                    onClick={() => setQuantasLinhas((n) => n + 10)}>
              Ver mais 10
            </button>
          )}
        </aside>
      )}
    </>
  )
}
