import { useEffect, useRef, useState } from 'react'
import * as api from '../api'
import TravelRanges from './TravelRanges'

const diaMesAno = (iso) => {
  const [a, m, d] = (iso || '').split('-')
  return d ? `${d}/${m}/${a}` : iso
}

export default function UploadStep({
  onUpload, busy, banco, travelRanges = [], onTravelRangesChange,
}) {
  const [files, setFiles] = useState([])
  const [dragging, setDragging] = useState(false)
  const [vencimento, setVencimento] = useState('')
  // Intervalo de COMPRAS do lote, lido antes do processamento. `null` = ainda
  // não sei (ou não deu para saber), e aí o editor de viagem fica solto.
  const [limites, setLimites] = useState(null)
  const [lendoPeriodo, setLendoPeriodo] = useState(false)
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

  // Pré-voo: as faturas são lidas assim que escolhidas, só para saber de quando
  // a quando vão as compras. Sem isto os seletores de data ficam soltos e dá
  // para marcar uma viagem de 2019 num lote de julho de 2026 — erro que só
  // apareceria do outro lado, depois de todo o trabalho de revisão.
  //
  // Depende do vencimento além dos arquivos porque em banco que não traz a data
  // no arquivo é ela que ancora o ano da compra: "{Em 15/Jul}" não diz o ano.
  useEffect(() => {
    if (!files.length) return setLimites(null)
    let cancelado = false
    // Espera a digitação parar: `input[type=date]` dispara onChange a cada
    // pedaço da data em alguns navegadores, e cada disparo reenvia os arquivos.
    const timer = setTimeout(async () => {
      setLendoPeriodo(true)
      try {
        const r = await api.uploadPeriodo(files, banco?.id || '', vencimento)
        if (!cancelado) setLimites(r.purchase_range || null)
      } catch {
        // Conveniência, não pré-requisito: falhou, o editor volta a ficar solto
        // e a validação real continua acontecendo no processamento.
        if (!cancelado) setLimites(null)
      } finally {
        if (!cancelado) setLendoPeriodo(false)
      }
    }, 300)
    return () => { cancelado = true; clearTimeout(timer) }
  }, [files, banco?.id, vencimento])

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
          depois de revisar 130 estabelecimentos. E a pergunta nomeia as datas
          do lote: "viajou neste período?" sem dizer qual período obriga a
          conferir a fatura noutra janela para responder. */}
      {onTravelRangesChange && files.length > 0 && (
        <details className="viagem-upload" open={travelRanges.length > 0}>
          <summary>
            {lendoPeriodo ? 'Lendo as datas das compras…'
              : limites
                ? `Viajou entre ${diaMesAno(limites.inicio)} e ${diaMesAno(limites.fim)}?`
                : 'Viajou neste período?'}
            {travelRanges.length > 0 && (
              <span className="badge">{travelRanges.length}</span>
            )}
          </summary>
          <TravelRanges
            ranges={travelRanges}
            onChange={onTravelRangesChange}
            limites={limites}
            busy={busy || lendoPeriodo}
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
