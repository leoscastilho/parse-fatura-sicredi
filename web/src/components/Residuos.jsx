import { useState } from 'react'
import { BarrasDivergentes, DIVERGENTE, LineChart, brlExato, rotuloPeriodo } from './charts'

const CORTES = [
  { rotulo: 'R$ 100', valor: 100 },
  { rotulo: 'R$ 500', valor: 500 },
  { rotulo: 'R$ 1.000', valor: 1000 },
  { rotulo: 'Tudo', valor: 0 },
]

/**
 * Soma corrida do resíduo, mês a mês, do primeiro ao último mês que não fecha.
 *
 * Mês que fecha entra como zero — não some da série. É essa continuidade que
 * transforma uma lista de sessenta e nove desencontros numa curva: se ela
 * oscila em torno do zero, é lançamento caindo no mês vizinho; se ela DESCE e
 * não volta, tem dinheiro que saiu e nunca foi explicado por lançamento nenhum.
 */
function acumular(meses) {
  if (!meses.length) return []
  const porPeriodo = new Map(meses.map((m) => [m.periodo, m.saldo]))
  const ordenados = [...meses].map((m) => m.periodo).sort()
  const fim = ordenados[ordenados.length - 1]

  let ano = Number(ordenados[0].slice(0, 4))
  let mes = Number(ordenados[0].slice(5))
  let soma = 0
  const saida = []
  for (;;) {
    const periodo = `${ano}-${String(mes).padStart(2, '0')}`
    soma += porPeriodo.get(periodo) || 0
    saida.push({ periodo, acumulado: Math.round(soma * 100) / 100 })
    if (periodo === fim) break
    mes += 1
    if (mes === 13) { ano += 1; mes = 1 }
  }
  return saida
}

/**
 * Os meses que não fecham pela identidade
 * `receita − gasto − investimento − poupança + resgate ≈ 0`.
 *
 * O painel de saúde dizia só "69 meses não fecham", e esse número é inútil por
 * um motivo medido: a MEDIANA do resíduo é R$ 117. Sessenta e poucos desses
 * meses são centavos de arredondamento, e listá-los junto com o mês de
 * R$ 72 mil esconde exatamente os sete que têm história. Por isso aqui a
 * primeira coisa é um corte de valor, e a segunda é o acumulado — a lista crua
 * vem por último, quando já se sabe onde olhar.
 *
 * O SINAL diz o que fazer, e é por isso que ele ganha cor própria:
 *   • para a esquerda  saiu mais do que entrou — gasto pago com dinheiro que
 *                      não está lançado, quase sempre um "Resgate da poupança"
 *                      que faltou registrar;
 *   • para a direita   entrou mais do que saiu e a sobra não foi parada em
 *                      lugar nenhum — falta a linha de Poupança.
 */
export default function Residuos({ saude }) {
  const [corte, setCorte] = useState(500)
  const [tudo, setTudo] = useState(false)

  const meses = saude.meses_que_nao_fecham || []
  if (!meses.length) return null

  const relevantes = meses.filter((m) => Math.abs(m.saldo) >= corte)
  const ruido = meses.length - relevantes.length
  const liquido = meses.reduce((s, m) => s + m.saldo, 0)
  const faltando = relevantes.filter((m) => m.saldo < 0)
  const sobrando = relevantes.filter((m) => m.saldo > 0)
  const serie = acumular(meses)
  const mostrados = tudo ? relevantes : relevantes.slice(0, 12)

  return (
    <section className="card">
      <h2>Meses que não fecham <span className="count">{meses.length}</span></h2>
      <p className="muted">
        A conta de cada mês é <strong>receita − gasto − investimento − poupança
        + resgate</strong>. Ela deveria dar zero: é assim que você empurra
        dinheiro de um mês para o outro. Onde não dá, ou falta um lançamento ou
        um lançamento caiu no mês errado.
      </p>

      <div className="summary">
        <div>
          <span className="k">Buraco líquido no período</span>
          <span className="v" style={{ color: liquido < 0 ? DIVERGENTE.negativo
                                                          : DIVERGENTE.positivo }}>
            {brlExato(liquido)}
          </span>
        </div>
        <div>
          <span className="k">Meses sem resgate</span>
          <span className="v">{faltando.length}</span>
        </div>
        <div>
          <span className="k">Meses com sobra solta</span>
          <span className="v">{sobrando.length}</span>
        </div>
        <div>
          <span className="k">Arredondamento</span>
          <span className="v">{ruido}</span>
        </div>
      </div>

      <div className="toolbar">
        <span className="muted small">Ignorar resíduo abaixo de</span>
        <div className="viz-janelas">
          {CORTES.map((c) => (
            <button key={c.rotulo} type="button"
                    className={`ghost ${corte === c.valor ? 'ativo' : ''}`}
                    onClick={() => setCorte(c.valor)}>{c.rotulo}</button>
          ))}
        </div>
        <span className="muted small grow">
          {ruido > 0 && `${ruido} mês(es) abaixo do corte ficaram de fora — juntos dão
                         ${brlExato(meses.filter((m) => Math.abs(m.saldo) < corte)
                                         .reduce((s, m) => s + m.saldo, 0))}.`}
        </span>
      </div>

      <h3>Para onde o buraco foi crescendo</h3>
      <p className="muted small">
        Soma corrida do resíduo. Linha andando de lado é lançamento no mês
        vizinho — se anula sozinho. Degrau que desce e não volta é dinheiro que
        saiu sem entrada registrada, e é aí que está o que vale corrigir.
      </p>
      <LineChart pontos={serie}
                 series={[{ chave: 'acumulado', rotulo: 'Resíduo acumulado' }]} />

      {relevantes.length > 0 && (
        <>
          <h3>Os meses que puxam a conta</h3>
          <BarrasDivergentes itens={mostrados.map((m) => ({
            rotulo: rotuloPeriodo(m.periodo),
            valor: m.saldo,
            nota: m.tipo === 'falta_resgate' ? 'faltou resgate' : 'sobra sem destino',
          }))} />
          <div className="viz-legenda">
            <span>
              <span className="viz-dot" style={{ background: DIVERGENTE.negativo }} />
              Saiu sem entrada registrada
            </span>
            <span>
              <span className="viz-dot" style={{ background: DIVERGENTE.positivo }} />
              Entrou e não foi para lugar nenhum
            </span>
          </div>
          {relevantes.length > 12 && (
            <button type="button" className="ghost" onClick={() => setTudo(!tudo)}>
              {tudo ? 'Mostrar só os 12 maiores'
                    : `Ver os ${relevantes.length} acima do corte`}
            </button>
          )}

          <div className="scroll">
            <table className="grid compact sticky">
              <thead>
                <tr>
                  <th>Mês</th>
                  <th className="right">Receita</th>
                  <th className="right">Gasto</th>
                  <th className="right">Poupança</th>
                  <th className="right">Resgate</th>
                  <th className="right">Resíduo</th>
                  <th>O que provavelmente falta</th>
                </tr>
              </thead>
              <tbody>
                {mostrados.map((m) => (
                  <tr key={m.periodo}>
                    <td className="mono">{rotuloPeriodo(m.periodo)}</td>
                    <td className="right money muted">{brlExato(m.receita)}</td>
                    <td className="right money muted">{brlExato(m.gasto)}</td>
                    <td className="right money muted">{brlExato(m.poupanca)}</td>
                    <td className="right money muted">{brlExato(m.resgate)}</td>
                    <td className="right money"
                        style={{ color: m.saldo < 0 ? DIVERGENTE.negativo
                                                    : DIVERGENTE.positivo }}>
                      {brlExato(m.saldo)}
                    </td>
                    <td className="small">
                      {m.tipo === 'falta_resgate'
                        ? `um "Resgate da poupança" de ${brlExato(-m.saldo)}`
                        : `uma "Poupança" de ${brlExato(m.saldo)}`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {saude.pares_que_se_anulam?.length > 0 && (
        <div className="alert">
          Estes pares se cancelam — não é dinheiro sumido, é um lançamento no mês
          errado, e corrigir um conserta os dois:{' '}
          {saude.pares_que_se_anulam
            .map((p) => `${rotuloPeriodo(p.a)} ↔ ${rotuloPeriodo(p.b)} (${brlExato(p.valor)})`)
            .join('; ')}.
        </div>
      )}

      <p className="muted small">
        Meses em que só houve gasto — nenhuma receita, poupança ou resgate — não
        entram nesta conta: sem nenhum dos dois lados a identidade não tem o que
        comparar.
      </p>
    </section>
  )
}
