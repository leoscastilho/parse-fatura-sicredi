import { useEffect, useRef, useState } from 'react'
import * as api from '../api'
import TravelRanges from './TravelRanges'

const diaMesAno = (iso) => {
  const [a, m, d] = (iso || '').split('-')
  return d ? `${d}/${m}/${a}` : iso
}

/**
 * O primeiro nome — o padrão do rótulo que vai para a planilha.
 *
 * "Rhyesla Siqueira" vira "Rhyesla" porque a descrição já é longa e o nome
 * inteiro empurraria o resto para fora da coluna. É só o PADRÃO: o campo é
 * editável, e duas pessoas com o mesmo primeiro nome se resolvem lá.
 */
export const primeiroNome = (completo) => (completo || '').trim().split(/\s+/)[0] || ''

/**
 * O mapa de titulares no formato que o backend espera: `Completo=Rótulo`.
 *
 * VAI SÓ QUEM LEVA MARCA. "Eu" simplesmente não entra na lista — para o
 * servidor, nome ausente e rótulo vazio são a mesma coisa (`apelidos.get(nome,
 * "")`), então mandar o par vazio seria carregar uma distinção que ninguém do
 * outro lado consegue ler.
 *
 * COM MENOS DE DUAS PESSOAS O MAPA É VAZIO, e a guarda não é decorativa: sem
 * ela, uma fatura de um titular só cujo nome não bate com o "Associado" — outro
 * banco, ou o campo ausente — deixaria `eu` vazio, o rótulo cairia no primeiro
 * nome e TODA linha do arquivo levaria a marca da única pessoa que existe. É a
 * mesma condição que esconde o seletor, escrita onde a decisão acontece.
 */
export const formTitulares = (titulares, eu, apelidos) => (
  titulares.length < 2 ? '' : titulares
    .filter((nome) => nome !== eu && (apelidos[nome] || '').trim())
    .map((nome) => `${nome}=${apelidos[nome].trim()}`)
    .join('\n'))

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
  // Conta conjunta: os nomes que aparecem na coluna de titular do extrato, quem
  // deles sou eu, e o rótulo que cada um dos OUTROS leva para a planilha.
  const [titulares, setTitulares] = useState([])
  const [eu, setEu] = useState('')
  const [apelidos, setApelidos] = useState({})
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
        if (cancelado) return
        setLimites(r.purchase_range || null)
        const nomes = r.titulares || []
        setTitulares(nomes)
        // A sugestão vem do "Associado" impresso na fatura — o banco já diz de
        // quem é a conta. Confirmar é mais rápido que procurar o próprio nome.
        //
        // Sem validar contra `nomes`: quem garante que a sugestão está na lista
        // é o servidor, que devolve `null` quando o "Associado" não aparece nos
        // lançamentos. Repetir a checagem aqui daria dois donos para a mesma
        // regra e um galho que nenhum teste consegue alcançar.
        setEu(r.eu_sugerido || '')
        setApelidos(Object.fromEntries(nomes.map((n) => [n, primeiroNome(n)])))
      } catch {
        // Conveniência, não pré-requisito: falhou, o editor volta a ficar solto
        // e a validação real continua acontecendo no processamento.
        if (!cancelado) { setLimites(null); setTitulares([]) }
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

      {/* CONTA CONJUNTA. Só aparece com mais de um nome na fatura: com um só
          não há o que perguntar, e perguntar assim mesmo seria uma etapa a
          mais em toda importação para responder sempre a mesma coisa. */}
      {titulares.length > 1 && (
        <div className="titulares">
          <strong className="small">Quem é você nesta fatura?</strong>
          <p className="muted small">
            As compras dos outros ganham o nome no fim da descrição — as suas
            ficam como estão. Marcar as próprias seria escrever o mesmo nome em
            quase toda linha do arquivo para não distinguir nada.
          </p>

          {titulares.map((nome) => (
            <div className="titular" key={nome}>
              <label className="checkbox">
                <input type="radio" name="titular-eu" value={nome}
                       checked={eu === nome} disabled={busy}
                       onChange={() => setEu(nome)} />
                Esse sou eu
              </label>

              {eu === nome ? (
                <div className="grow">
                  <span className="muted small">{nome} — sem marca na descrição</span>
                </div>
              ) : (
                <div className="grow">
                  <input type="text" value={apelidos[nome] ?? ''} disabled={busy}
                         aria-label={`Nome de ${nome} na planilha`}
                         onChange={(e) => setApelidos((atuais) =>
                           ({ ...atuais, [nome]: e.target.value }))} />
                  {/* O nome completo fica embaixo, em cinza: é a referência de
                      quem é quem, mas quem vai para o arquivo é o de cima. */}
                  <span className="muted small">{nome}</span>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      <button className="primary" disabled={!pronto || busy}
              onClick={() => onUpload(files, vencimento,
                                      formTitulares(titulares, eu, apelidos))}>
        {busy ? 'Processando…' : 'Processar'}
      </button>
    </section>
  )
}
