import { useRef, useState } from 'react'

const brl = (v) => v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })

/**
 * Recategorizar um CSV que já saiu daqui.
 *
 * O caso de uso: as regras melhoram todo mês, e os CSVs antigos ficaram com a
 * categorização de quando foram gerados. Aqui você joga o motor atual por cima
 * deles.
 *
 * A entrada é o próprio formato de saída, então o portal já sabe ler tudo: o
 * estabelecimento está dentro da descrição e a data da compra está no
 * `{Em 15/Jul}`. Sai o mesmo arquivo, mesmas linhas, mesma ordem — só a coluna
 * Categoria muda.
 */
export default function RecategorizeStep({ onUpload, busy }) {
  const [files, setFiles] = useState([])
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef(null)

  function accept(list) {
    setFiles([...list].filter((f) => f.name.toLowerCase().endsWith('.csv')))
  }

  return (
    <section className="card">
      <h2>Recategorizar um CSV existente</h2>
      <p className="muted">
        Suba um CSV no formato de saída. Ele passa pelas regras{' '}
        <strong>atuais</strong> e volta igual — mesmas linhas, mesma ordem,{' '}
        <strong>só a coluna Categoria muda</strong>. Pode mandar vários,
        inclusive um arquivo com o histórico todo junto.
      </p>
      <p className="muted small">
        Não precisa ser um arquivo gerado por aqui. Exportações antigas em que a
        descrição não tem <code>[Cartão]</code> nem <code>{'{Em 15/Jul}'}</code>{' '}
        funcionam do mesmo jeito, e colunas a mais (<code>Mês</code>,{' '}
        <code>Ano</code>) voltam intactas. Basta ter as colunas{' '}
        <code>Data</code>, <code>Categoria</code>, <code>Descrição</code>,{' '}
        <code>Valor (R$)</code> e <code>Pago</code>.
      </p>

      <div
        className={`dropzone ${dragging ? 'over' : ''}`}
        onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); accept(e.dataTransfer.files) }}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => e.key === 'Enter' && inputRef.current?.click()}
      >
        <input ref={inputRef} type="file" accept=".csv" multiple hidden
               onChange={(e) => accept(e.target.files)} />
        {files.length === 0
          ? <span>Arraste os CSVs aqui ou clique para escolher</span>
          : <span>{files.length} arquivo(s) selecionado(s)</span>}
      </div>

      {files.length > 0 && (
        <ul className="file-list">
          {files.map((f) => (
            <li key={f.name}>
              <span>{f.name}</span>
              <span className="muted">{(f.size / 1024).toFixed(0)} KB</span>
            </li>
          ))}
        </ul>
      )}

      <button className="primary" disabled={!files.length || busy}
              onClick={() => onUpload(files)}>
        {busy ? 'Reprocessando…' : 'Recategorizar'}
      </button>

      <p className="muted small">
        Onde a regra não tem opinião — marketplace, desconhecido, sem regra — a
        categoria que já estava no arquivo é mantida. Nada é zerado, e a
        descrição não é tocada.
      </p>
    </section>
  )
}

/**
 * O diff do reprocessamento — e cada mudança aceitável ou recusável aqui mesmo.
 *
 * Antes esta tela era só leitura e mandava "reverter nas próximas etapas". Mas
 * as próximas agrupam por estabelecimento e não sabem qual era a categoria
 * ANTES — que é justamente a informação que decide. Recusar é uma atribuição de
 * LINHA fixando a categoria de origem: a mesma máquina do marketplace, sem
 * caminho novo no backend.
 */
export function ChangesSummary({ session, getAssignment, setAssignment,
                                 setManyAssignments, onNext }) {
  const [aberto, setAberto] = useState(true)
  const mudancas = session.changes || []

  // Agrupadas pela categoria de DESTINO, que é como a decisão acontece: "tudo
  // que virou Casa está certo, tudo que virou Lazer eu quero conferir". Na
  // ordem do arquivo, as 618 mudanças de um histórico inteiro chegam
  // embaralhadas e obrigam a reavaliar a mesma pergunta a cada linha.
  // Desempate pelo maior valor: dentro da mesma categoria, o que pesa vem antes.
  const ordenadas = [...mudancas].sort((a, b) =>
    (a.para || '').localeCompare(b.para || '', 'pt-BR')
    || Math.abs(b.valor) - Math.abs(a.valor))

  // Recusada = há uma atribuição de linha devolvendo a categoria de origem.
  const recusada = (m) => getAssignment('line', m.line_id)?.categoria === m.de
  const aceitas = mudancas.filter((m) => !recusada(m))
  const recusadas = mudancas.length - aceitas.length
  const total = aceitas.reduce((s, m) => s + Math.abs(m.valor), 0)

  const alternar = (m) =>
    setAssignment('line', m.line_id,
      recusada(m) ? null : { categoria: m.de, persist_keyword: null })

  const todas = (aceitar) =>
    setManyAssignments(mudancas.map((m) => ({
      scope: 'line', target: m.line_id,
      patch: aceitar ? null : { categoria: m.de, persist_keyword: null },
    })))

  return (
    <section className="card">
      <h2>
        O que muda <span className="count">{mudancas.length}</span>
      </h2>
      <p className="muted">
        {session.source_files.map((f) => f.name).join(', ')} ·{' '}
        {session.source_files.reduce((s, f) => s + f.rows, 0)} linhas ·{' '}
        <strong>{session.unchanged} sem alteração</strong>
        {aceitas.length > 0 && <> · {brl(total)} trocando de categoria</>}
        {recusadas > 0 && <> · <strong>{recusadas} recusada(s)</strong></>}
      </p>

      {mudancas.length === 0 ? (
        <div className="alert ok">
          Nenhuma categoria muda com as regras atuais. O arquivo já estava em dia.
        </div>
      ) : (
        <>
          <div className="alert warn">
            A regra vence sobre o que estava no arquivo — é por isso que você
            está reprocessando. Desmarque as que você já tinha corrigido à mão:
            a linha volta para a categoria que estava no arquivo.
          </div>

          <div className="toolbar">
            <span className="small grow">
              {aceitas.length} de {mudancas.length} serão aplicadas
            </span>
            <button className="ghost" onClick={() => todas(true)}
                    disabled={recusadas === 0}>
              Aceitar todas
            </button>
            <button className="ghost" onClick={() => todas(false)}
                    disabled={recusadas === mudancas.length}>
              Recusar todas
            </button>
          </div>

          <button className="link" onClick={() => setAberto((v) => !v)}>
            {aberto ? 'esconder' : 'mostrar'} as {mudancas.length} mudanças
          </button>

          {aberto && (
            <div className="scroll">
              <table className="grid compact sticky">
                <thead>
                  <tr>
                    <th>Aplicar?</th>
                    <th>Lançamento</th>
                    <th className="right">Valor</th>
                    <th>De</th>
                    <th>Para</th>
                    <th>Regra</th>
                  </tr>
                </thead>
                <tbody>
                  {ordenadas.map((m, i) => {
                    const fora = recusada(m)
                    // Primeira linha de cada categoria de destino ganha um
                    // filete: é o que faz o agrupamento ser visto sem ler.
                    const abreGrupo = i === 0 || ordenadas[i - 1].para !== m.para
                    return (
                      <tr key={m.line_id}
                          className={`clicavel ${fora ? 'blank' : ''} ${abreGrupo ? 'abre-grupo' : ''}`}
                          // A linha inteira é o alvo. Mirar num quadradinho de
                          // 13px, 618 vezes, é trabalho que a tela criou e não
                          // devolve nada — e o checkbox continua lá para quem
                          // navega por teclado.
                          onClick={() => alternar(m)}>
                        <td>
                          <input type="checkbox" checked={!fora}
                                 aria-label={`Aplicar em ${m.descricao}`}
                                 onChange={() => alternar(m)}
                                 // Sem isto o clique no checkbox conta duas
                                 // vezes — nele e na linha — e nada muda.
                                 onClick={(e) => e.stopPropagation()} />
                        </td>
                        <td>{m.descricao}</td>
                        <td className="right money">{brl(m.valor)}</td>
                        <td>{m.de || <span className="muted">— vazia —</span>}</td>
                        <td>
                          {fora ? <span className="muted">{m.para}</span>
                                : <strong>{m.para}</strong>}
                        </td>
                        <td className="rule">{m.matched}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      <button className="primary" onClick={onNext}>Continuar a revisão</button>
    </section>
  )
}
