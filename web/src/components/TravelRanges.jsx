import { useState } from 'react'

const diaMes = (iso) => {
  const [, m, d] = (iso || '').split('-')
  return d ? `${d}/${m}` : iso
}
const porExtenso = (iso) => {
  const [a, m, d] = (iso || '').split('-')
  return d ? `${d}/${m}/${a}` : iso
}

/**
 * Editor de períodos de viagem — o mesmo nas duas telas onde aparece.
 *
 * Na tela de upload ele vem SEM limites: as faturas ainda não foram lidas, e
 * portanto não se sabe o intervalo de compras. Depois do processamento a etapa
 * Viagem mostra o mesmo editor já com `limites`, prendendo os seletores ao
 * período que as faturas realmente cobrem.
 *
 * Ele nunca guarda estado próprio de períodos: a lista vive no App, que é
 * quem fala com o backend. Aqui só existe o rascunho do período em digitação.
 */
export default function TravelRanges({
  ranges, onChange, limites = null, warnings = [], busy = false, compacto = false,
}) {
  const [inicio, setInicio] = useState('')
  const [fim, setFim] = useState('')
  const [rotulo, setRotulo] = useState('')
  const [erro, setErro] = useState(null)

  function adicionar() {
    if (!inicio || !fim) return setErro('Escolha as duas datas.')
    if (fim < inicio) return setErro('A volta não pode ser antes da ida.')
    setErro(null)
    onChange([...ranges, { inicio, fim, rotulo: rotulo.trim() }])
    setInicio('')
    setFim('')
    setRotulo('')
  }

  const remover = (i) => onChange(ranges.filter((_, idx) => idx !== i))

  return (
    <>
      {!compacto && (
        <p className="muted">
          Tudo que for <strong>comprado</strong> dentro de um destes períodos
          entra como <strong>Viagem</strong>, e a categoria real vai para a
          descrição, entre parênteses. A data comparada é a da compra
          (<code>{'{Em 15/Jul}'}</code>), não a do vencimento da fatura.
        </p>
      )}

      <p className="muted small">
        {limites
          ? <>As compras deste lote vão de <strong>{porExtenso(limites.inicio)}</strong>{' '}
             a <strong>{porExtenso(limites.fim)}</strong> — os seletores só
             oferecem datas dentro disso, porque viagem fora do intervalo não
             pegaria compra nenhuma.</>
          : <>Não consegui ler as datas das compras deste lote, então os
             seletores ficam soltos. Dá para ajustar depois, na etapa
             <strong> Viagem</strong>.</>}
      </p>

      <div className="toolbar">
        <label className="field">
          <span className="small">Ida</span>
          <input type="date" value={inicio}
                 min={limites?.inicio} max={limites?.fim}
                 onChange={(e) => setInicio(e.target.value)} />
        </label>
        <label className="field">
          <span className="small">Volta</span>
          <input type="date" value={fim}
                 min={inicio || limites?.inicio} max={limites?.fim}
                 onChange={(e) => setFim(e.target.value)} />
        </label>
        <label className="field grow">
          <span className="small">Nome (opcional)</span>
          <input type="text" value={rotulo} placeholder="Gramado, feriado de maio…"
                 onChange={(e) => setRotulo(e.target.value)} />
        </label>
        <button className="ghost" onClick={adicionar} disabled={busy}>
          Adicionar período
        </button>
      </div>

      {erro && <div className="alert error">{erro}</div>}

      {ranges.length === 0 ? (
        <p className="muted small">
          Nenhum período — nada vira Viagem.
        </p>
      ) : (
        <ul className="file-list">
          {ranges.map((r, i) => (
            <li key={`${r.inicio}-${r.fim}-${i}`}>
              <span>
                <strong>{diaMes(r.inicio)} → {diaMes(r.fim)}</strong>
                {r.rotulo && <span className="muted"> · {r.rotulo}</span>}
              </span>
              <button className="link" onClick={() => remover(i)} disabled={busy}>
                remover
              </button>
            </li>
          ))}
        </ul>
      )}

      {warnings.map((aviso) => (
        <div className="alert warn" key={aviso}>{aviso}</div>
      ))}
    </>
  )
}
