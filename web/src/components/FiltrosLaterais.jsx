import { useState } from 'react'
import { brlExato, rotuloPeriodo } from './charts'

/**
 * Janelas prontas. `meses: 1` é ESTE mês, não "um mês atrás": a conta é
 * `recuar(ultimo, meses - 1)`, então 1 devolve o próprio mês e 12 devolve os
 * doze que terminam nele.
 */
export const JANELAS = [
  { id: 'tudo', rotulo: 'Todo o histórico', meses: null },
  { id: '1m', rotulo: 'Este mês', meses: 1 },
  { id: '3m', rotulo: 'Últimos 3 meses', meses: 3 },
  { id: '6m', rotulo: 'Últimos 6 meses', meses: 6 },
  { id: '12m', rotulo: 'Últimos 12 meses', meses: 12 },
  { id: '24m', rotulo: 'Últimos 24 meses', meses: 24 },
  { id: '60m', rotulo: 'Últimos 60 meses', meses: 60 },
]

/** Recua `n` meses de um "AAAA-MM". */
export function recuar(periodo, n) {
  let ano = Number(periodo.slice(0, 4))
  let mes = Number(periodo.slice(5, 7)) - n
  while (mes <= 0) { mes += 12; ano -= 1 }
  return `${ano}-${String(mes).padStart(2, '0')}`
}

export const primeiroDia = (periodo) => `${periodo.slice(0, 7)}-01`

/**
 * Último dia do mês, de verdade — 28, 29, 30 ou 31.
 *
 * `new Date(ano, mes, 0)` é o dia zero do mês SEGUINTE, que o JavaScript
 * resolve como o último do anterior. Sai de graça e acerta fevereiro bissexto,
 * que uma tabela fixa erraria a cada quatro anos.
 */
export function ultimoDia(periodo) {
  const ano = Number(periodo.slice(0, 4))
  const mes = Number(periodo.slice(5, 7))
  return `${periodo.slice(0, 7)}-${String(new Date(ano, mes, 0).getDate()).padStart(2, '0')}`
}

/** Quantos meses há entre dois "AAAA-MM", inclusive nas duas pontas. */
export const contarMeses = (a, b) =>
  (Number(b.slice(0, 4)) - Number(a.slice(0, 4))) * 12
  + Number(b.slice(5, 7)) - Number(a.slice(5, 7)) + 1

/**
 * Completa um "AAAA-MM" com o dia da ponta, para o `input[type=date]` aceitar.
 *
 * O campo de data exige dia; o dado, não. Das 6.717 linhas do histórico dele,
 * ZERO trazem data com dia legível — a coluna Data é `Feb-12` ou `Aug`, e o que
 * é confiável são as colunas numéricas de Mês e Ano. Então o dia aqui é um
 * detalhe do widget, não do recorte: o servidor lê os sete primeiros caracteres
 * e trata as duas pontas como meses inteiros.
 */
const comDia = (valor, padrao, dia) =>
  ((valor || '').length > 7 ? valor : dia(valor || padrao))

/** O balde de quem não tem `<nome>` na descrição. Ver `SEM_TITULAR` no back. */
export const SEM_TITULAR = '<sem marca>'

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
  disponiveis, intervalo, filtro, preset, semCategorias, semLinhas,
  semTitulares = [], rotulos = {}, busy, aoAplicar,
}) {
  const [aberta, setAberta] = useState(false)
  const [quantasLinhas, setQuantasLinhas] = useState(10)

  const categorias = disponiveis?.categorias || []
  const lancamentos = disponiveis?.lancamentos || []
  // Vem vazio quando há um balde só — numa fatura de uma pessoa, ou num
  // histórico anterior à marcação existir, isolar titular não isola nada.
  const titulares = disponiveis?.titulares || []

  // --- período ---------------------------------------------------------------
  //
  // O recorte MORA AQUI e não numa barra à parte porque ele é um filtro como os
  // outros: some com dinheiro dos painéis. Tê-lo fora da barra fazia a bolha
  // mentir — dizia "nenhum filtro" numa tela recortada em seis meses.
  const { inicio: primeiro, fim: ultimo } = intervalo || {}
  const temPeriodo = Boolean(primeiro && ultimo)
  const inicioAtual = temPeriodo ? comDia(filtro?.inicio, primeiro, primeiroDia) : ''
  const fimAtual = temPeriodo ? comDia(filtro?.fim, ultimo, ultimoDia) : ''
  // O que conta como recorte é o que VAI para o servidor: "Todo o histórico"
  // manda as duas pontas vazias, e é isso que distingue os dois casos sem
  // depender de comparar datas com as bordas do arquivo.
  const recortado = Boolean(filtro?.inicio || filtro?.fim)

  const ativos = semCategorias.length + semLinhas.length + semTitulares.length
    + (recortado ? 1 : 0)

  const escolherPeriodo = (inicio, fim, id) =>
    aoAplicar({ semCategorias, semLinhas, semTitulares, inicio, fim, preset: id })

  /**
   * As janelas contam a partir do último mês COM LANÇAMENTO, não de hoje.
   *
   * Um arquivo exportado em março e aberto em setembro devolveria "últimos 6
   * meses" vazio se a âncora fosse o relógio — e o usuário veria um erro no
   * lugar do gráfico por ter escolhido uma opção perfeitamente razoável.
   */
  const escolherJanela = (id) => {
    const janela = JANELAS.find((j) => j.id === id)
    if (!janela?.meses) return escolherPeriodo('', '', 'tudo')
    // Pedir 5 anos de um arquivo de 3 não é erro, é pedir o arquivo inteiro.
    const de = recuar(ultimo, janela.meses - 1)
    return escolherPeriodo(primeiroDia(de < primeiro ? primeiro : de),
                           ultimoDia(ultimo), janela.id)
  }

  const alternarCategoria = (categoria) =>
    aoAplicar({
      semCategorias: semCategorias.includes(categoria)
        ? semCategorias.filter((c) => c !== categoria)
        : [...semCategorias, categoria],
      semLinhas,
      semTitulares,
    })

  const alternarTitular = (id) =>
    aoAplicar({
      semCategorias,
      semLinhas,
      semTitulares: semTitulares.includes(id)
        ? semTitulares.filter((t) => t !== id)
        : [...semTitulares, id],
    })

  const alternarLinha = (id, descricao) =>
    aoAplicar({
      semCategorias,
      semTitulares,
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
              {/* "estão sem eles" só descrevia exclusão. Com o recorte aqui
                  dentro, o filtro também pode ser um intervalo — e "sem o
                  período" não quer dizer nada. */}
              {ativos} filtro(s) em vigor — <strong>todos</strong> os painéis
              desta aba já saem com eles aplicados.{' '}
              <button className="link" disabled={busy}
                      onClick={() => aoAplicar({ semCategorias: [], semLinhas: [],
                                                 semTitulares: [],
                                                 inicio: '', fim: '', preset: 'tudo' })}>
                trazer tudo de volta
              </button>
            </div>
          ) : (
            <p className="muted small">
              Tudo dentro. Remova o que distorce e os painéis se reajustam —
              média, custo fixo e anomalias são recalculados, não fatiados.
            </p>
          )}

          {temPeriodo && (
            <>
              <h3>Período</h3>
              {/* Os campos vêm ANTES da lista de janelas: a janela é um atalho
                  para preencher os dois, e o que vale é sempre o que está
                  escrito neles. Escolher "Últimos 6 meses" e depois empurrar
                  uma ponta é o uso normal, não a exceção. */}
              <div className="filtros-periodo">
                <label>
                  de
                  <input type="date" value={inicioAtual}
                         min={primeiroDia(primeiro)} max={fimAtual}
                         disabled={busy} aria-label="Início do período"
                         onChange={(e) => e.target.value
                           && escolherPeriodo(e.target.value, fimAtual, 'custom')} />
                </label>
                <label>
                  até
                  <input type="date" value={fimAtual}
                         min={inicioAtual} max={ultimoDia(ultimo)}
                         disabled={busy} aria-label="Fim do período"
                         onChange={(e) => e.target.value
                           && escolherPeriodo(inicioAtual, e.target.value, 'custom')} />
                </label>
              </div>

              <select className="filtros-janela" value={preset || 'tudo'} disabled={busy}
                      aria-label="Janela de tempo"
                      onChange={(e) => escolherJanela(e.target.value)}>
                {/* "Personalizado" só existe depois de mexer nos campos: uma
                    opção que ninguém pode escolher não merece lugar na lista
                    enquanto não descreve o estado atual. */}
                {preset === 'custom' && <option value="custom">Personalizado</option>}
                {JANELAS.map((j) => (
                  <option key={j.id} value={j.id}>{j.rotulo}</option>
                ))}
              </select>

              <p className="muted small">
                {busy ? 'Recalculando…'
                      : `${contarMeses(inicioAtual, fimAtual)} meses`}
                {' — o recorte é mensal, então o dia serve só para apontar o mês.'}
                {' O arquivo cobre '}
                {rotuloPeriodo(primeiro)} a {rotuloPeriodo(ultimo)}.
              </p>
            </>
          )}

          {/* TITULARES vem antes das categorias porque separa PESSOAS, não
              assuntos: numa conta conjunta, "para onde vai o dinheiro" tem duas
              respostas diferentes, e somá-las esconde as duas. É o filtro de
              maior efeito, então é o primeiro. */}
          {titulares.length > 1 && (
            <>
              <h3>De quem é a compra</h3>
              <p className="muted small">
                O nome sai do <code>&lt;…&gt;</code> no fim da descrição, posto
                na importação. Desmarque para tirar essa pessoa de todos os
                painéis — ou desmarque todas as outras para ver só ela.
              </p>
              <div className="filtros-lista">
                {titulares.map((t) => {
                  const id = t.titular || SEM_TITULAR
                  return (
                    <label className="checkbox filtros-item" key={id}>
                      <input type="checkbox" disabled={busy}
                             checked={!semTitulares.includes(id)}
                             onChange={() => alternarTitular(id)} />
                      <span className="grow">
                        {t.titular || '(sem marca)'}
                        {/* Abreviado para a linha caber: com "lançamento(s)"
                            por extenso, o nome e o valor quebravam em duas. */}
                        <span className="muted small">{' '}· {t.lancamentos} lanç.</span>
                      </span>
                      <span className="money small">{brlExato(t.total)}</span>
                    </label>
                  )
                })}
              </div>
            </>
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
