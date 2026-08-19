import { useRef, useState } from 'react'
import * as api from '../api'
import { gravarFiltros, lerFiltros } from '../filtrosSalvos'
import {
  BarrasEmpilhadas, BarrasH, DIVERGENTE, Heatmap, Legenda, LineChart, SERIES,
  TabelaDados, brlExato, eixoContinuo, rotuloPeriodo,
} from './charts'
import FiltrosLaterais from './FiltrosLaterais'
import Reservas from './Reservas'
import Residuos from './Residuos'

const PRESETS = [
  { id: '5a', rotulo: '5 anos', meses: 60 },
  { id: '2a', rotulo: '2 anos', meses: 24 },
  { id: '1a', rotulo: '1 ano', meses: 12 },
  { id: '6m', rotulo: '6 meses', meses: 6 },
  { id: '1m', rotulo: '1 mês', meses: 1 },
  { id: 'tudo', rotulo: 'Tudo', meses: null },
]

/** Recua `n` meses de um "YYYY-MM". */
export function recuar(periodo, n) {
  let ano = Number(periodo.slice(0, 4))
  let mes = Number(periodo.slice(5)) - n
  while (mes <= 0) { mes += 12; ano -= 1 }
  return `${ano}-${String(mes).padStart(2, '0')}`
}

/** Quantos meses há entre dois "YYYY-MM", inclusive nas duas pontas. */
export const contarMeses = (a, b) =>
  (Number(b.slice(0, 4)) - Number(a.slice(0, 4))) * 12
  + Number(b.slice(5)) - Number(a.slice(5)) + 1

/** Mediana simples — usada para decidir se um mês é excepcional. */
const mediana = (xs) => {
  const s = [...xs].sort((a, b) => a - b)
  if (!s.length) return 0
  const m = Math.floor(s.length / 2)
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2
}

// toFixed() é insensível a locale e devolve "20.0%" — ponto decimal numa
// interface em português. O separador tem que ser vírgula.
const pct = (v) =>
  `${((v ?? 0) * 100).toLocaleString('pt-BR', { minimumFractionDigits: 1,
                                                maximumFractionDigits: 1 })}%`

/**
 * Análise do histórico inteiro a partir de um CSV.
 *
 * É a única aba que só LÊ: não há transação, nada é gravado, nada é publicado.
 * Por isso ela não tem etapas nem botão de continuar — você solta o arquivo e
 * olha.
 *
 * Cada painel responde uma pergunta, nesta ordem:
 *   1. Dá para confiar nestes números?      (saúde dos dados, antes de tudo)
 *   2. Quanto entra, quanto sai, quando?    (série mensal)
 *   3. Para onde vai?                       (categorias, estabelecimentos)
 *   4. O que já está comprometido?          (recorrentes)
 *   5. O que fugiu do normal?               (anomalias)
 */
export default function AnalyticsView({ onError }) {
  const [dados, setDados] = useState(null)
  const [busy, setBusy] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [tabela, setTabela] = useState(false)
  const [comPicos, setComPicos] = useState(false)
  // O arquivo fica na mão para poder ser reenviado a cada mudança de período: o
  // recorte é aplicado ANTES de agregar, no servidor, então média mensal, custo
  // fixo e anomalias são recalculados em vez de fatiados. Filtrar no cliente,
  // depois de agregar, mostraria os números do arquivo inteiro ao lado dos
  // gráficos do período — e eles não bateriam.
  const [arquivo, setArquivo] = useState(null)
  const [preset, setPreset] = useState('tudo')
  // Exclusões da barra lateral. Viajam para o servidor junto com o recorte,
  // pelo mesmo motivo: o que importa é o número recalculado, não o fatiado.
  // Nascem do que ficou salvo no navegador — a identidade do lançamento é de
  // conteúdo, então reordenar a planilha não invalida nada (ver filtrosSalvos).
  const salvos = useRef(lerFiltros())
  const [semCategorias, setSemCategorias] = useState(salvos.current.semCategorias)
  const [semLinhas, setSemLinhas] = useState(salvos.current.semLinhas)
  const [rotulos, setRotulos] = useState(salvos.current.rotulos)
  const [excluidas, setExcluidas] = useState([])
  const [composicao, setComposicao] = useState(null)   // null = decide sozinho
  const [porAnoNaSazonalidade, setPorAnoNaSazonalidade] = useState(true)
  // Séries escondidas no gráfico de tendência. É só olhar: não muda número
  // nenhum e não vai para o servidor, ao contrário dos filtros da barra.
  const [ocultas, setOcultas] = useState([])
  const inputRef = useRef(null)

  async function enviar(novoArquivo, filtro = {}, novoPreset,
                        { podeDesistirDosFiltros = false, rotulos: agora } = {}) {
    if (!novoArquivo) return
    setBusy(true)
    try {
      const resposta = await api.analytics(novoArquivo, filtro)
      setDados(resposta)
      setArquivo(novoArquivo)
      setSemCategorias(filtro.semCategorias || [])
      setSemLinhas(filtro.semLinhas || [])
      // Os rótulos vêm por PARÂMETRO, não do estado: `setRotulos` só vale no
      // próximo render, e ler o estado aqui gravaria o mapa de antes do clique
      // — o cache ficava sempre um passo atrás e, na prática, vazio.
      gravarFiltros({ semCategorias: filtro.semCategorias || [],
                      semLinhas: filtro.semLinhas || [], rotulos: agora || rotulos })
      if (novoPreset) setPreset(novoPreset)
    } catch (e) {
      // Filtro salvo de OUTRA planilha pode não casar com esta e derrubar o
      // primeiro carregamento — e aí a aba parece quebrada, quando o problema é
      // uma preferência velha. Tenta de novo limpo e diz o que aconteceu, em
      // vez de deixar a dropzone recusando o arquivo para sempre.
      if (podeDesistirDosFiltros
          && ((filtro.semCategorias || []).length || (filtro.semLinhas || []).length)) {
        onError(`${e.message} — os filtros salvos não valem para este arquivo,`
                + ' então abri sem eles.')
        return enviar(novoArquivo, { ...filtro, semCategorias: [], semLinhas: [] },
                      novoPreset)
      }
      // Recorte vazio devolve 400 com o texto do que o arquivo cobre. Manter os
      // dados anteriores é o que impede a tela de virar dropzone de novo e
      // obrigar a subir o arquivo outra vez por causa de um clique.
      onError(e.message)
    } finally {
      setBusy(false)
    }
  }

  // Toda mudança de filtro reenvia o lote inteiro: recorte + exclusões. Mandar
  // só o que mudou faria o servidor recalcular sobre um estado que a tela não
  // tem — e os dois se desencontrariam no primeiro erro.
  const filtroAtual = () => ({
    inicio: dados?.filtro?.inicio || '', fim: dados?.filtro?.fim || '',
    semCategorias, semLinhas,
  })

  function escolherPeriodo(inicio, fim, id) {
    enviar(arquivo, { ...filtroAtual(), inicio, fim }, id)
  }

  function aplicarFiltros({ semCategorias: cats, semLinhas: linhas, rotulos: novos }) {
    // O rótulo é cache de exibição: sem ele, um lançamento excluído que caiu
    // fora do período em vigor não teria como aparecer na barra — e filtro que
    // não dá para ver é filtro que não dá para desfazer.
    const mesclados = novos ? { ...rotulos, ...novos } : rotulos
    if (novos) setRotulos(mesclados)
    enviar(arquivo, { ...filtroAtual(), semCategorias: cats, semLinhas: linhas },
           undefined, { rotulos: mesclados })
  }

  function novoArquivo(f) {
    setPreset('tudo')
    setExcluidas([])
    setComposicao(null)
    // Os filtros salvos valem para o arquivo novo: as identidades são de
    // conteúdo, então reexportar a mesma planilha mantém todas elas.
    enviar(f, { semCategorias, semLinhas }, undefined,
           { podeDesistirDosFiltros: true })
  }

  if (!dados) {
    return (
      <section className="card">
        <h2>Análise do histórico</h2>
        <p className="muted">
          Solte aqui o CSV com todos os seus lançamentos — o histórico inteiro,
          não uma fatura. Pode ser o arquivo cru que o Google Sheets baixa, com
          as colunas de formatação e tudo: ele é limpo na leitura.
        </p>
        <p className="muted small">
          Nada é gravado. O arquivo é lido, virado em números e esquecido.
        </p>

        <div className={`dropzone ${dragging ? 'over' : ''}`}
             onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
             onDragLeave={() => setDragging(false)}
             onDrop={(e) => { e.preventDefault(); setDragging(false)
                              novoArquivo(e.dataTransfer.files[0]) }}
             onClick={() => inputRef.current?.click()}
             role="button" tabIndex={0}
             onKeyDown={(e) => e.key === 'Enter' && inputRef.current?.click()}>
          <input ref={inputRef} type="file" accept=".csv" hidden
                 onChange={(e) => novoArquivo(e.target.files[0])} />
          <span>{busy ? 'Analisando…' : 'Arraste o CSV aqui ou clique para escolher'}</span>
        </div>
      </section>
    )
  }

  const { resumo, saude, serie_mensal, por_categoria, categoria_por_periodo,
          sazonalidade, top_estabelecimentos, recorrentes, anomalias,
          variacao_recente, concentracao, intervalo_disponivel } = dados

  const ativos = recorrentes.filter((r) => r.ativo)

  // PICOS. A compra da casa foi R$ 658 mil num mês cuja vizinhança gasta R$ 40
  // mil. Um ponto assim achata os outros contra o chão e o gráfico passa a
  // mostrar o evento único em vez da tendência. Meses excepcionais saem da
  // LINHA por padrão (continuam na tabela e no rodapé) e voltam com o toggle.
  // Isto é correção de ESCALA, não de período: o recorte de tempo já é global.
  //
  // A comparação é com a VIZINHANÇA, não com a mediana da série inteira. Num
  // histórico de 14 anos em que o gasto cresceu dez vezes, a mediana global
  // fica presa nos anos baratos do começo: ela marcava 15 meses como
  // excepcionais, e a maioria era rotina de 2025 — o gráfico apagava justamente
  // o período mais recente. Contra os 13 meses ao redor sobram 3, que são os
  // eventos de verdade.
  const picos = serie_mensal.filter((m, i) => {
    const vizinhanca = serie_mensal.slice(Math.max(0, i - 6), i + 7)
      .map((x) => x.gasto).filter((v) => v > 0)
    const referencia = mediana(vizinhanca)
    return referencia > 0 && (m.gasto > referencia * 8 || m.receita > referencia * 8)
  })
  const serieDoGrafico = eixoContinuo(
    comPicos || !picos.length
      ? serie_mensal
      : serie_mensal.filter((m) => !picos.some((p) => p.periodo === m.periodo)))
  const naoDetalhadoPct = resumo.total_gasto ? resumo.gasto_nao_detalhado / resumo.total_gasto : 0

  // --- para onde vai o dinheiro, com as categorias que o usuário tirou fora ---
  const visiveis = por_categoria.filter((c) => !excluidas.includes(c.categoria))
  const totalVisivel = visiveis.reduce((s, c) => s + c.total, 0)
  const mostradas = visiveis.slice(0, 12)
  // "Fora de escala" é medido contra a MEDIANA das categorias mostradas, não
  // contra a média: a média já vem contaminada pelo próprio outlier. Casa dá
  // 13x a mediana; a segunda colocada dá 3,6x — o corte em 4x separa as duas.
  const medianaCategoria = mediana(mostradas.map((c) => c.total))
  const foraDeEscala = mostradas.filter((c) => c.total >= medianaCategoria * 4)

  // --- composição: por ano ou por mês ---------------------------------------
  const porAno = (() => {
    const balde = new Map()
    for (const p of categoria_por_periodo.periodos) {
      const ano = p.periodo.slice(0, 4)
      const acc = balde.get(ano) || categoria_por_periodo.categorias.map(() => 0)
      p.valores.forEach((v, i) => { acc[i] += v })
      balde.set(ano, acc)
    }
    return [...balde.entries()].map(([periodo, valores]) => ({ periodo, valores }))
  })()
  // Com dois anos na tela, uma barra por ano são duas barras: não é gráfico, é
  // uma comparação. A partir daí o mês é a unidade útil — por isso o padrão
  // segue o recorte, e o botão continua lá para discordar.
  const mesesNoRecorte = resumo.periodo_inicio && resumo.periodo_fim
    ? contarMeses(resumo.periodo_inicio, resumo.periodo_fim) : 0
  const porMes = mesesNoRecorte <= 24
  const modoComposicao = composicao ?? (porMes ? 'mes' : 'ano')
  // Por MÊS a pilha sofre do mesmo mal da linha: out/25 tem R$ 658 mil e os
  // outros 23 meses viram um risco no chão. Por ANO o pico se dilui no total do
  // ano, então lá ele fica — tirar seria mentir sobre quanto aquele ano custou.
  const composicaoSerie = modoComposicao === 'ano'
    ? porAno
    : categoria_por_periodo.periodos.filter(
      (p) => comPicos || !picos.some((k) => k.periodo === p.periodo))
  const picosNaComposicao = modoComposicao === 'mes' && !comPicos
    && categoria_por_periodo.periodos.length !== composicaoSerie.length

  // --- tendência por categoria ----------------------------------------------
  //
  // A COR SAI DO ÍNDICE ORIGINAL da categoria, não da posição entre as
  // visíveis. É a regra que impede a legenda de virar um jogo de memória:
  // esconder "Casa" não pode repintar Construção de azul.
  const tendencia = (() => {
    const nomes = categoria_por_periodo.categorias
    const series = nomes.map((nome, i) => ({
      chave: `c${i}`, rotulo: nome || '(sem categoria)', cor: SERIES[i % SERIES.length],
    }))
    const pontos = eixoContinuo(categoria_por_periodo.periodos.map((p) => {
      const ponto = { periodo: p.periodo }
      p.valores.forEach((v, i) => { ponto[`c${i}`] = v })
      return ponto
    }))
    return {
      pontos,
      legenda: series.map((s) => ({ rotulo: s.rotulo, cor: s.cor })),
      visiveis: series.filter((s) => !ocultas.includes(s.rotulo)),
    }
  })()

  return (
    <>
      <BarraDePeriodo disponivel={intervalo_disponivel} filtro={dados.filtro}
                      preset={preset} busy={busy} aoEscolher={escolherPeriodo} />

      <FiltrosLaterais disponiveis={dados.disponiveis} busy={busy}
                       semCategorias={semCategorias} semLinhas={semLinhas}
                       rotulos={rotulos} aoAplicar={aplicarFiltros} />

      {/* 1. Dá para confiar nestes números? Vem antes de qualquer gráfico —
             ler um painel sem saber o que está faltando é pior que não ler. */}
      <SaudeDosDados saude={saude} resumo={resumo} naoDetalhadoPct={naoDetalhadoPct} />

      <section className="card">
        <div className="toolbar">
          <div className="grow">
            <h2 style={{ margin: 0 }}>{dados.arquivo}</h2>
            <span className="muted small">
              {rotuloPeriodo(resumo.periodo_inicio)} a {rotuloPeriodo(resumo.periodo_fim)} ·{' '}
              {resumo.meses_com_dado} meses com lançamento ·{' '}
              {saude.total_lancamentos.toLocaleString('pt-BR')} linhas
            </span>
          </div>
          <label className="checkbox">
            <input type="checkbox" checked={tabela}
                   onChange={(e) => setTabela(e.target.checked)} />
            Ver como tabela
          </label>
          <button className="ghost" onClick={() => { setDados(null); setArquivo(null) }}>
            Outro arquivo
          </button>
        </div>

        <div className="summary">
          <div><span className="k">Gasto total</span>
               <span className="v">{brlExato(resumo.total_gasto)}</span></div>
          <div><span className="k">Média por mês</span>
               <span className="v">{brlExato(resumo.media_mensal)}</span></div>
          <div><span className="k">Custo fixo mensal</span>
               <span className="v">{brlExato(resumo.custo_fixo_mensal)}</span></div>
          <div><span className="k">Receita total</span>
               <span className="v">{brlExato(resumo.total_receita)}</span></div>
        </div>

        <p className="muted small">
          Fora da conta de gasto, de propósito:{' '}
          <strong>{brlExato(resumo.excluido.carregamento)}</strong> de
          Poupança/Resgate (o mesmo dinheiro indo e voltando entre meses, que é
          como você zera o mês) e{' '}
          <strong>{brlExato(resumo.excluido.artefato)}</strong> de saldo. Somar
          isso dobraria o orçamento.
        </p>
      </section>

      {/* 2. Quanto entra, quanto sai, quando? */}
      <section className="card">
        <h2>Gasto e receita, mês a mês</h2>

        {picos.length > 0 && (
          <div className="toolbar">
            <label className="checkbox grow">
              <input type="checkbox" checked={comPicos}
                     onChange={(e) => setComPicos(e.target.checked)} />
              Incluir os {picos.length} mês(es) excepcionais
            </label>
          </div>
        )}

        {tabela ? (
          <TabelaDados
            colunas={['Mês', 'Gasto', 'Receita', 'No cartão', 'Não detalhado']}
            linhas={serie_mensal.map((m) => [rotuloPeriodo(m.periodo), m.gasto,
                                             m.receita, m.no_cartao, m.nao_detalhado])} />
        ) : (
          <>
            <LineChart pontos={serieDoGrafico} series={[
              { chave: 'gasto', rotulo: 'Gasto' },
              { chave: 'receita', rotulo: 'Receita' },
            ]} />
            <div className="viz-legenda">
              <span><span className="viz-dot" style={{ background: SERIES[0] }} />Gasto</span>
              <span><span className="viz-dot" style={{ background: SERIES[1] }} />Receita</span>
            </div>
          </>
        )}

        <p className="muted small">
          Buraco na linha é mês sem lançamento nenhum — não é mês de gasto zero.
          {picos.length > 0 && !comPicos && (
            // No máximo quatro: a lista completa chegou a ocupar quatro linhas
            // de rodapé, mais texto do que o gráfico que ela explica.
            <> Fora do gráfico para não achatar o resto:{' '}
              {picos.slice(0, 4)
                    .map((p) => `${rotuloPeriodo(p.periodo)} (${brlExato(p.gasto)})`)
                    .join(', ')}
              {picos.length > 4 && ` e mais ${picos.length - 4}`}.</>
          )}
        </p>
      </section>

      {/* 3. Para onde vai? */}
      <section className="card">
        <h2>Para onde vai o dinheiro</h2>
        {/* Nada de citar "Casa" aqui: o texto tem que continuar verdadeiro
            depois que Casa for tirada, e com o recorte de 2 anos a maior
            categoria é outra. O número vem do dado, ou não vem. */}
        <p className="muted small">
          {por_categoria.length} categorias no período. Clique numa barra para
          tirá-la do gráfico
          {foraDeEscala.length > 0 && medianaCategoria > 0 && (
            <> — {foraDeEscala[0].categoria || '(sem categoria)'} sozinha vale{' '}
              {Math.round(foraDeEscala[0].total / medianaCategoria)}x a mediana
              das outras, e enquanto ela está aí as demais dividem o pedaço que
              sobra da régua</>
          )}.
        </p>

        {(excluidas.length > 0 || foraDeEscala.length > 0) && (
          <div className="toolbar">
            {excluidas.length > 0 && (
              <div className="chips grow">
                <span className="muted small">Fora do gráfico:</span>
                {excluidas.map((c) => (
                  <button key={c} type="button" className="chip"
                          aria-label={`Trazer ${c || '(sem categoria)'} de volta`}
                          onClick={() => setExcluidas(excluidas.filter((x) => x !== c))}>
                    {c || '(sem categoria)'} <span aria-hidden>×</span>
                  </button>
                ))}
                <button type="button" className="ghost"
                        onClick={() => setExcluidas([])}>Trazer todas de volta</button>
              </div>
            )}
            {foraDeEscala.length > 0 && (
              <button type="button" className="ghost"
                      onClick={() => setExcluidas([...excluidas,
                                                   ...foraDeEscala.map((c) => c.categoria)])}>
                Tirar {foraDeEscala.map((c) => c.categoria || '(sem categoria)').join(', ')}
                {' '}— {foraDeEscala.length > 1 ? 'estão' : 'está'} fora de escala
              </button>
            )}
          </div>
        )}

        <BarrasH
          itens={mostradas.map((c) => ({
            rotulo: c.categoria || '(sem categoria)', valor: c.total,
            // O nome CRU vai junto: o rótulo pode ser "(sem categoria)", que é
            // texto de tela, e excluir por ele não casaria com nada.
            categoria: c.categoria,
          }))}
          total={totalVisivel}
          aoClicar={(item) => setExcluidas([...excluidas, item.categoria])} />

        <p className="muted small">
          {excluidas.length > 0
            ? <>As porcentagens são sobre <strong>{brlExato(totalVisivel)}</strong>, o
                gasto que sobrou depois de tirar {excluidas.length} categoria(s) —
                não sobre o total do período ({brlExato(resumo.total_gasto)}).</>
            : <>As porcentagens são sobre o gasto do período,{' '}
                <strong>{brlExato(resumo.total_gasto)}</strong>.</>}
        </p>
      </section>

      <section className="card">
        <div className="toolbar">
          <h2 className="grow" style={{ margin: 0 }}>
            Composição do gasto por {modoComposicao === 'mes' ? 'mês' : 'ano'}
          </h2>
          <div className="viz-janelas">
            <button type="button"
                    className={`ghost ${modoComposicao === 'mes' ? 'ativo' : ''}`}
                    onClick={() => setComposicao('mes')}>Por mês</button>
            <button type="button"
                    className={`ghost ${modoComposicao === 'ano' ? 'ativo' : ''}`}
                    onClick={() => setComposicao('ano')}>Por ano</button>
          </div>
        </div>
        {tabela ? (
          <TabelaDados
            colunas={[modoComposicao === 'mes' ? 'Mês' : 'Ano',
                      ...categoria_por_periodo.categorias]}
            linhas={composicaoSerie.map((p) => [
              modoComposicao === 'mes' ? rotuloPeriodo(p.periodo) : p.periodo,
              ...p.valores,
            ])} />
        ) : (
          <BarrasEmpilhadas periodos={composicaoSerie}
                            categorias={categoria_por_periodo.categorias}
                            formatoRotulo={modoComposicao === 'mes' ? rotuloPeriodo
                                                                    : (p) => p} />
        )}
        {picosNaComposicao && (
          <p className="muted small">
            Os mesmos {picos.length} mês(es) excepcionais estão fora daqui — a
            caixa de seleção lá em cima traz os dois gráficos de volta.
          </p>
        )}
      </section>

      {/* Tendência: a mesma pergunta da composição, mas ao longo do tempo em
          vez de empilhada. Empilhado responde "quanto foi o mês"; sobreposto
          responde "esta categoria está subindo?", que é o que não dava para
          ver. Clicar na legenda esconde a série — sem isso, uma categoria dez
          vezes maior que as outras achata todas contra o chão. */}
      {tendencia.pontos.length > 1 && (
        <section className="card">
          <div className="toolbar">
            <h2 className="grow" style={{ margin: 0 }}>Tendência por categoria</h2>
            {ocultas.length > 0 && (
              <button className="ghost" onClick={() => setOcultas([])}>
                Mostrar as {ocultas.length} escondidas
              </button>
            )}
          </div>
          <p className="muted small">
            Gasto de cada categoria, mês a mês, no período escolhido. Clique na
            legenda para esconder uma — as que ficam mantêm a cor, então a
            legenda não precisa ser relida a cada clique.
          </p>

          {tabela ? (
            <TabelaDados
              colunas={['Mês', ...tendencia.visiveis.map((s) => s.rotulo)]}
              linhas={tendencia.pontos.map((p) => [
                rotuloPeriodo(p.periodo),
                ...tendencia.visiveis.map((s) => p[s.chave] ?? 0),
              ])} />
          ) : tendencia.visiveis.length === 0 ? (
            <p className="muted">
              Todas escondidas — clique numa da legenda para trazê-la de volta.
            </p>
          ) : (
            <LineChart pontos={tendencia.pontos} series={tendencia.visiveis} />
          )}

          <Legenda itens={tendencia.legenda} ocultas={ocultas}
                   aoClicar={(rotulo) => setOcultas((atuais) =>
                     atuais.includes(rotulo)
                       ? atuais.filter((c) => c !== rotulo)
                       : [...atuais, rotulo])} />
          <p className="muted small">
            As {categoria_por_periodo.categorias.length} maiores categorias do
            período; o resto entra em “Outras”. Os filtros da barra lateral
            valem aqui como em todo o resto — esconder na legenda é só para
            olhar, e não muda número nenhum.
          </p>
        </section>
      )}

      <section className="card">
        <div className="toolbar">
          <h2 className="grow" style={{ margin: 0 }}>Sazonalidade</h2>
          {/* Ligado por padrão: com a régua única, 2012 a 2024 ficam todos
              abaixo de 10% do máximo e o mapa responde "os anos recentes são
              mais caros" — que já se sabia — em vez de "dezembro é caro". */}
          <label className="checkbox">
            <input type="checkbox" checked={porAnoNaSazonalidade}
                   onChange={(e) => setPorAnoNaSazonalidade(e.target.checked)} />
            Comparar dentro de cada ano
          </label>
        </div>
        <p className="muted small">
          Gasto de cada mês, ano a ano. Quanto mais escuro, mais caro.
        </p>
        <Heatmap anos={sazonalidade.anos} celulas={sazonalidade.celulas}
                 normalizar={porAnoNaSazonalidade} />
      </section>

      <section className="card">
        <h2>Onde você mais gasta</h2>
        <BarrasH itens={top_estabelecimentos.slice(0, 12).map((e) => ({
          rotulo: `${e.descricao} · ${e.lancamentos}x`, valor: e.total,
        }))} />
        <p className="muted small">
          As 10 maiores linhas do histórico concentram{' '}
          <strong>{pct(concentracao.top_10)}</strong> do gasto; a mediana de um
          lançamento é <strong>{brlExato(concentracao.mediana)}</strong>.
        </p>
      </section>

      {/* 4. O que já está comprometido? */}
      <section className="card">
        <h2>Custos recorrentes <span className="count">{ativos.length}</span></h2>
        <p className="muted">
          O que se repete mês após mês soma{' '}
          <strong>{brlExato(resumo.custo_fixo_mensal)}</strong> por mês — é o
          quanto do orçamento já está comprometido antes de qualquer decisão.
        </p>
        <div className="scroll">
          <table className="grid compact sticky">
            <thead>
              <tr>
                <th>Recorrente</th><th>Categoria</th>
                <th className="right">Hoje</th><th className="right">Histórica</th>
                <th className="right">Meses</th>
                <th className="right">Variação</th><th>Período</th>
              </tr>
            </thead>
            <tbody>
              {ativos.slice(0, 20).map((r) => (
                <tr key={r.descricao}>
                  <td>{r.descricao}</td>
                  <td className="muted">{r.categoria}</td>
                  <td className="right money">{brlExato(r.mediana_recente)}</td>
                  <td className="right money muted">{brlExato(r.mediana)}</td>
                  <td className="right">{r.meses}</td>
                  <td className={`right ${r.variacao > 0.15 ? 'alta' : ''}`}>
                    {r.variacao > 0 ? '+' : ''}{(r.variacao * 100).toFixed(0)}%
                  </td>
                  <td className="muted small">
                    {rotuloPeriodo(r.primeiro)} → {rotuloPeriodo(r.ultimo)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="muted small">
          <strong>Hoje</strong> é a mediana dos últimos 12 meses;{' '}
          <strong>histórica</strong> é a da série inteira. As duas são
          diferentes porque preço sobe: somar a histórica subestimaria o
          comprometido — a luz entraria a R$ 172 quando hoje é R$ 500. A
          variação compara o primeiro terço da série com o último.
        </p>
      </section>

      {/* 5. O que fugiu do normal? */}
      {anomalias.length > 0 && (
        <section className="card">
          <h2>Meses fora da curva <span className="count">{anomalias.length}</span></h2>
          <p className="muted">
            Cada linha compara a categoria com a <strong>própria</strong> mediana
            histórica. R$ 800 é rotina em Casa e gritante em Cachorro.
          </p>
          <div className="scroll">
            <table className="grid compact sticky">
              <thead>
                <tr>
                  <th>Mês</th><th>Categoria</th><th className="right">Gasto</th>
                  <th className="right">Mediana</th><th className="right">Excesso</th>
                </tr>
              </thead>
              <tbody>
                {anomalias.map((a) => (
                  <tr key={`${a.periodo}-${a.categoria}`}>
                    <td className="mono">{rotuloPeriodo(a.periodo)}</td>
                    <td>{a.categoria || '(sem categoria)'}</td>
                    <td className="right money">{brlExato(a.total)}</td>
                    <td className="right money muted">{brlExato(a.mediana)}</td>
                    <td className="right money alta">+{brlExato(a.excesso)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {variacao_recente.length > 0 && (
        <section className="card">
          <h2>O que explicou a variação do último ano</h2>
          <BarrasH
            itens={variacao_recente.map((v) => ({
              rotulo: v.categoria || '(sem categoria)', valor: v.delta,
              cor: v.delta >= 0 ? DIVERGENTE.negativo : DIVERGENTE.positivo,
            }))} />
          <p className="muted small">
            Laranja subiu, azul caiu. É a diferença entre “gastei mais” e saber
            de onde veio.
          </p>
        </section>
      )}

      {/* 6. O dinheiro que só muda de lugar. Fica depois do gasto porque não é
             gasto — mas antes dos resíduos porque é o que explica boa parte
             deles: o carry é justamente o mecanismo que faz o mês fechar. */}
      <Reservas reservas={dados.reservas} totalInvestido={resumo.total_investido} />

      {/* 7. O que não fecha — por último, porque é trabalho de correção, não
             leitura. Mas com painel próprio: enfiado numa linha do alerta de
             saúde, "69 meses não fecham" não dizia nem quais nem quanto. */}
      <Residuos saude={saude} />
    </>
  )
}

/**
 * O recorte de tempo, fixo no topo. TODO painel desta aba deriva dele.
 *
 * Os presets contam a partir do último mês COM LANÇAMENTO, não de hoje. Um
 * arquivo exportado em março e aberto em setembro devolveria "últimos 6 meses"
 * vazio se a âncora fosse o relógio — e o usuário veria um erro no lugar do
 * gráfico por ter clicado num botão perfeitamente razoável.
 */
function BarraDePeriodo({ disponivel, filtro, preset, busy, aoEscolher }) {
  const { inicio: primeiro, fim: ultimo } = disponivel || {}
  if (!primeiro || !ultimo) return null

  const inicioAtual = filtro?.inicio || primeiro
  const fimAtual = filtro?.fim || ultimo

  const aplicarPreset = (p) => {
    if (!p.meses) return aoEscolher('', '', p.id)
    // `max` com o começo do arquivo: pedir 5 anos de um arquivo de 3 não é erro,
    // é pedir o arquivo inteiro.
    const inicio = recuar(ultimo, p.meses - 1)
    aoEscolher(inicio < primeiro ? primeiro : inicio, ultimo, p.id)
  }

  return (
    <div className="periodo-barra">
      <span className="periodo-rotulo">Período</span>

      <div className="viz-janelas">
        {PRESETS.map((p) => (
          <button key={p.id} type="button" disabled={busy}
                  className={`ghost ${preset === p.id ? 'ativo' : ''}`}
                  onClick={() => aplicarPreset(p)}>{p.rotulo}</button>
        ))}
      </div>

      <label className="periodo-de">
        de
        <input type="month" value={inicioAtual} min={primeiro} max={fimAtual}
               disabled={busy} aria-label="Início do período"
               onChange={(e) => aoEscolher(e.target.value, fimAtual, 'custom')} />
      </label>
      <label className="periodo-ate">
        até
        <input type="month" value={fimAtual} min={inicioAtual} max={ultimo}
               disabled={busy} aria-label="Fim do período"
               onChange={(e) => aoEscolher(inicioAtual, e.target.value, 'custom')} />
      </label>

      <span className="muted small grow">
        {busy ? 'Recalculando…'
              : `${contarMeses(inicioAtual, fimAtual)} meses · o arquivo cobre
                 ${rotuloPeriodo(primeiro)} a ${rotuloPeriodo(ultimo)}`}
      </span>
    </div>
  )
}

/** Painel de saúde — o que pode estar torto antes de você acreditar no resto. */
function SaudeDosDados({ saude, resumo, naoDetalhadoPct }) {
  const problemas = []

  if (saude.dupla_contagem?.length) {
    problemas.push({
      nivel: 'error',
      texto: `${saude.dupla_contagem.length} mês(es) têm a fatura lançada em linha
              única E os itens dela detalhados — a mesma compra contada duas
              vezes: ${saude.dupla_contagem.map((d) => d.periodo).join(', ')}.`,
    })
  }
  if (resumo.gasto_nao_detalhado > 0) {
    problemas.push({
      nivel: 'warn',
      texto: `${brlExato(resumo.gasto_nao_detalhado)} (${pct(naoDetalhadoPct)} do gasto)
              está em "Cartão de crédito" — a fatura inteira numa linha só, em
              ${resumo.meses_nao_detalhados.length} meses. É gasto real, mas a
              divisão por categoria não enxerga o que tem dentro. Passar esses
              meses pelo Recategorizar abriria o balde.`,
    })
  }
  if (saude.sem_categoria.quantidade) {
    problemas.push({
      nivel: 'warn',
      texto: `${saude.sem_categoria.quantidade} lançamentos sem categoria somam
              ${brlExato(saude.sem_categoria.total)}: ${saude.sem_categoria.exemplos.slice(0, 3).join(', ')}.`,
    })
  }
  if (saude.pares_que_se_anulam?.length) {
    problemas.push({
      nivel: 'warn',
      texto: `Lançamento no mês errado: ${saude.pares_que_se_anulam
        .map((p) => `${p.a} e ${p.b} diferem por ${brlExato(p.valor)} em sentidos opostos`)
        .join('; ')}.`,
    })
  }
  if (saude.meses_faltando.length) {
    problemas.push({
      nivel: 'info',
      texto: `${saude.meses_faltando.length} meses sem lançamento nenhum
              (${saude.meses_faltando[0]} a ${saude.meses_faltando[saude.meses_faltando.length - 1]}).
              Os gráficos deixam buraco em vez de desenhar zero.`,
    })
  }
  // "69 meses não fecham" é um número que não faz ninguém agir: a mediana do
  // resíduo é R$ 117, então quase todos são arredondamento. O que vale dizer
  // aqui é quantos são GRANDES e quanto somam; o resto tem painel próprio.
  const grandes = (saude.meses_que_nao_fecham || []).filter((m) => Math.abs(m.saldo) >= 500)
  if (grandes.length) {
    const liquido = grandes.reduce((s, m) => s + m.saldo, 0)
    problemas.push({
      nivel: 'warn',
      texto: `${grandes.length} de ${saude.total_meses_que_nao_fecham} meses não
              fecham por mais de R$ 500 — juntos, ${brlExato(liquido)} sem
              contrapartida lançada. O maior é ${brlExato(grandes[0].saldo)} em
              ${rotuloPeriodo(grandes[0].periodo)}. Detalhe no fim da página.`,
    })
  } else if (saude.total_meses_que_nao_fecham) {
    problemas.push({
      nivel: 'info',
      texto: `${saude.total_meses_que_nao_fecham} meses não fecham por centavos —
              arredondamento, não lançamento faltando.`,
    })
  }
  saude.avisos.forEach((a) => problemas.push({ nivel: 'info', texto: a }))

  if (!problemas.length) {
    return (
      <section className="card">
        <div className="alert ok">
          Nada suspeito: todo lançamento tem categoria e data, e os meses fecham.
        </div>
      </section>
    )
  }

  return (
    <section className="card">
      <h2>Antes de acreditar nos números</h2>
      {problemas.map((p, i) => (
        <div key={i} className={`alert ${p.nivel === 'error' ? 'error'
                                        : p.nivel === 'warn' ? 'warn' : ''}`}>
          {p.texto}
        </div>
      ))}
    </section>
  )
}
