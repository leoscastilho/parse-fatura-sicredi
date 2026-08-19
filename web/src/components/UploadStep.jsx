import { useRef, useState } from 'react'
import TravelRanges from './TravelRanges'

const brl = (v) => v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })

export default function UploadStep({
  onUpload, busy, banco, travelRanges = [], onTravelRangesChange,
}) {
  const [files, setFiles] = useState([])
  const [dragging, setDragging] = useState(false)
  const [vencimento, setVencimento] = useState('')
  const inputRef = useRef(null)

  const extensoes = banco?.extensoes || ['.xls', '.xlsx']
  const precisaVencimento = Boolean(banco?.pede_vencimento)
  const pronto = files.length > 0 && (!precisaVencimento || vencimento)

  function accept(list) {
    // Filtra pelas extensões do banco escolhido: soltar um .csv na tela do
    // Sicredi só produziria um 415 do servidor.
    setFiles([...list].filter((f) =>
      extensoes.some((ext) => f.name.toLowerCase().endsWith(ext))))
  }

  return (
    <section className="card">
      <h2>Extratos do cartão — {banco?.nome || '…'}</h2>
      <p className="muted">
        Pode mandar vários de uma vez — todos viram um CSV só, com cada fatura
        num bloco. Este banco aceita <code>{extensoes.join(', ')}</code>.
      </p>

      {banco && !banco.validado && (
        <div className="alert warn">
          O perfil de leitura do {banco.nome} ainda não foi validado contra uma
          fatura real. Confira os totais antes de colar na planilha.
        </div>
      )}

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
        <input
          ref={inputRef}
          type="file"
          accept={extensoes.join(',')}
          multiple
          hidden
          onChange={(e) => accept(e.target.files)}
        />
        {files.length === 0
          ? <span>Arraste os arquivos aqui ou clique para escolher</span>
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

      {precisaVencimento && (
        <div className="toolbar">
          <label className="small">
            Data de vencimento da fatura{' '}
            <input type="date" value={vencimento}
                   onChange={(e) => setVencimento(e.target.value)} />
          </label>
          <span className="muted small">
            O {banco.nome} não traz essa data no arquivo, e ela é a coluna
            <code>Data</code> do CSV.
          </span>
        </div>
      )}

      {/* A viagem se declara aqui porque é agora que você lembra dela — não
          depois de revisar 130 estabelecimentos. Ainda não há intervalo de
          compras para limitar os seletores (as faturas nem foram lidas), então
          a validação acontece do outro lado, na etapa Viagem. */}
      {onTravelRangesChange && (
        <details className="viagem-upload" open={travelRanges.length > 0}>
          <summary>
            Viajou neste período?
            {travelRanges.length > 0 && (
              <span className="badge">{travelRanges.length}</span>
            )}
          </summary>
          <TravelRanges
            ranges={travelRanges}
            onChange={onTravelRangesChange}
            busy={busy}
          />
        </details>
      )}

      <button className="primary" disabled={!pronto || busy}
              onClick={() => onUpload(files, vencimento)}>
        {busy ? 'Processando…' : 'Processar'}
      </button>
    </section>
  )
}
