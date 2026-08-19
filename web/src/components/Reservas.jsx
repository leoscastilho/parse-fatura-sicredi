import { BarrasH, LineChart, brlExato, rotuloPeriodo } from './charts'

/**
 * O que existe dentro de `Poupança` / `Resgate Poupança`.
 *
 * As duas categorias guardam TRÊS mecanismos diferentes, e só a descrição os
 * separa. Somados, davam um "saldo da poupança" de -R$ 349 mil — um número que
 * não descreve nada e que fazia a aba parecer errada. Separados, cada um
 * responde uma pergunta:
 *
 *   carry      o mês zerando. Sai de um mês, entra no seguinte, saldo zero por
 *              definição — e por isso vira uma corrente CONFERÍVEL.
 *   reserva    a caixinha com objetivo. É aqui que existe saldo de verdade.
 *   aplicação  dinheiro voltando de investimento, cujo aporte está em outra
 *              categoria. Aparece à parte para não ser lido como poupança.
 */
export default function Reservas({ reservas, totalInvestido }) {
  if (!reservas) return null
  const { grupos, objetivos, saldo_mensal: saldoMensal, corrente } = reservas
  const temAlgo = grupos.carry.movimentos || grupos.reserva.movimentos
                  || grupos.aplicacao.movimentos
  if (!temAlgo) return null

  const problemas = [...(corrente.quebrados || []), ...(corrente.sem_origem || [])]
  const aindaAplicado = (totalInvestido || 0) - grupos.aplicacao.resgatado

  return (
    <section className="card">
      <h2>Poupança: o que é saldo do mês e o que é reserva</h2>
      <p className="muted">
        <strong>Poupança</strong> e <strong>Resgate Poupança</strong> guardam
        coisas diferentes na mesma categoria, e o que as separa é a descrição.
        Somadas, o saldo não quer dizer nada; separadas, cada uma responde uma
        pergunta.
      </p>

      <div className="summary">
        <div>
          <span className="k">Guardado em reserva</span>
          <span className="v">{brlExato(grupos.reserva.saldo)}</span>
        </div>
        <div>
          <span className="k">Carry entre meses</span>
          <span className="v">{brlExato(grupos.carry.guardado)}</span>
        </div>
        <div>
          <span className="k">Voltou de aplicação</span>
          <span className="v">{brlExato(grupos.aplicacao.resgatado)}</span>
        </div>
      </div>

      {/* A corrente do carry: a checagem que só existe porque o carry foi
          separado da caixinha. Misturado com "PS5", não há como saber qual
          poupança devia reaparecer no mês seguinte. */}
      {corrente.elos > 0 && (
        problemas.length === 0 ? (
          <div className="alert ok">
            Os {corrente.elos} elos do saldo fecham: tudo que saiu de um mês
            entrou no seguinte, pelo mesmo valor.
          </div>
        ) : (
          <div className="alert warn">
            {corrente.quebrados.length > 0 && (
              <>Saiu de um mês e não entrou no seguinte:{' '}
                {corrente.quebrados.map((q) => (
                  `${rotuloPeriodo(q.de)} → ${rotuloPeriodo(q.para)} `
                  + `(saiu ${brlExato(q.saiu)}, entrou ${brlExato(q.entrou)})`
                )).join('; ')}. </>
            )}
            {corrente.sem_origem.length > 0 && (
              <>Entrou sem ter saído do mês anterior:{' '}
                {corrente.sem_origem.map((o) => (
                  `${rotuloPeriodo(o.periodo)} (${brlExato(o.entrou)})`
                )).join('; ')}. </>
            )}
            São as duas pontas do mesmo lançamento — corrigir uma conserta o par.
            Os outros {corrente.elos - corrente.quebrados.length} elos fecham.
          </div>
        )
      )}

      {saldoMensal.length > 1 && (
        <>
          <h3>Quanto você tem guardado</h3>
          <p className="muted small">
            Só a reserva com objetivo: o carry sai da conta porque ele zera todo
            mês, e o resgate de aplicação sai porque o aporte dele está em{' '}
            <strong>Investimento</strong>, não aqui.
          </p>
          <LineChart pontos={saldoMensal}
                     series={[{ chave: 'saldo', rotulo: 'Reserva' }]} />
        </>
      )}

      {objetivos.length > 0 && (
        <>
          <h3>Para onde você guarda</h3>
          <BarrasH itens={objetivos.slice(0, 12).map((o) => ({
            rotulo: `${o.objetivo} · ${o.movimentos}x`, valor: o.total,
          }))} />
          <p className="muted small">
            É o quanto <strong>entrou</strong> em cada objetivo, não o que
            sobrou nele. Saldo por objetivo não dá para calcular com os dados
            como estão: os depósitos nomeiam o objetivo (“Documentos Veículos”)
            e os resgates quase nunca usam o mesmo nome (“Resgate manutenção
            carro”). Um saldo pareado assim pareceria exato e estaria errado. Se
            quiser esse número, o caminho é escrever o mesmo objetivo nos dois
            lados a partir de agora.
          </p>
        </>
      )}

      {grupos.aplicacao.resgatado > 0 && (
        <p className="muted small">
          <strong>{brlExato(grupos.aplicacao.resgatado)}</strong> lançados como
          resgate de poupança são, na verdade, aplicação voltando (Sicredi,
          Rico, Investimento). Contra{' '}
          <strong>{brlExato(totalInvestido || 0)}</strong> aportados em
          Investimento, sobram <strong>{brlExato(aindaAplicado)}</strong>{' '}
          aplicados. Enquanto os dois ficavam no mesmo balde, a poupança
          aparecia com saldo negativo.
        </p>
      )}
    </section>
  )
}
