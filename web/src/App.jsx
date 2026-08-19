import { useCallback, useEffect, useMemo, useState } from 'react'
import * as api from './api'
import UploadStep from './components/UploadStep'
import UnmappedStep from './components/UnmappedStep'
import AutoReviewStep from './components/AutoReviewStep'
import MarketplaceStep from './components/MarketplaceStep'
import FinalReview from './components/FinalReview'
import RulesView from './components/RulesView'
import BankPicker from './components/BankPicker'
import InputFormatView from './components/InputFormatView'
import OutputFormatView from './components/OutputFormatView'
import ConfigBundle from './components/ConfigBundle'
import RecategorizeStep, { ChangesSummary } from './components/RecategorizeStep'
import TravelStep from './components/TravelStep'
import AnalyticsView from './components/AnalyticsView'
import { applyTheme } from './theme'

const STEPS = [
  { id: 'upload', label: 'Upload' },
  // Só aparece na recategorização: mostra o diff antes de qualquer revisão.
  { id: 'changes', label: 'Mudanças', apenas: 'recategorizacao' },
  { id: 'unmapped', label: 'Novos' },
  { id: 'auto', label: 'Revisão' },
  { id: 'marketplace', label: 'Marketplace' },
  // Depois do marketplace de propósito: a categoria que vai para o parêntese é
  // a final, já com as decisões daquela etapa. Fora da recategorização, que
  // promete não tocar a descrição.
  { id: 'viagem', label: 'Viagem', apenas: 'fatura' },
  { id: 'final', label: 'Conferir e exportar' },
]

// As decisões vivem num Map com chave "escopo:alvo". Sempre que precisamos
// falar com o backend, viram um array — o formato que /validate, /preview e
// /export esperam. Uma estrutura só, do primeiro clique ao download.
const keyOf = (scope, target) => `${scope}:${target}`

export default function App() {
  const [section, setSection] = useState('importar')
  const [sidebarOpen, setSidebarOpen] = useState(true)

  const [step, setStep] = useState('upload')
  const [categories, setCategories] = useState([])
  const [session, setSession] = useState(null)
  const [assignments, setAssignments] = useState(new Map())
  const [flaggedRules, setFlaggedRules] = useState(0)
  // Viagem: os períodos são o input, `travelItems` é o que o backend diz que
  // eles pegam, e `travelRejected` são as exceções que o usuário desmarcou.
  const [travelRanges, setTravelRanges] = useState([])
  const [travelItems, setTravelItems] = useState([])
  const [travelWarnings, setTravelWarnings] = useState([])
  const [travelRejected, setTravelRejected] = useState(new Set())
  // Etapas já liberadas. Avançar exige clicar em "Continuar" — pular uma etapa
  // pela barra deixava para trás decisões que a etapa seguinte já consome.
  // Chegando na última, tudo destrava e a navegação vira livre.
  const [liberadas, setLiberadas] = useState(['upload'])
  const [banks, setBanks] = useState([])
  const [bankId, setBankId] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.getCategories()
      .then((data) => setCategories(data.categories))
      .catch((e) => setError(`Não consegui carregar as categorias: ${e.message}`))
    api.getRules()
      .then((data) => setFlaggedRules(data.flagged_count))
      .catch(() => {})
    loadBanks()
  }, [])

  async function loadBanks() {
    try {
      const cfg = await api.getConfig()
      setBanks(cfg.banks)
      setBankId((current) => current || cfg.banco_padrao)
    } catch (e) {
      setError(`Não consegui carregar os bancos: ${e.message}`)
    }
  }

  // Trocar de banco repinta a interface inteira: as CSS custom properties são
  // reescritas no :root, então nenhum componente precisa saber que existe mais
  // de um banco.
  useEffect(() => {
    const banco = banks.find((b) => b.id === bankId)
    if (banco) applyTheme(banco.tema)
  }, [banks, bankId])

  const bancoAtual = banks.find((b) => b.id === bankId) || null

  const assignmentList = useMemo(() => [...assignments.values()], [assignments])

  const setAssignment = useCallback((scope, target, patch) => {
    setAssignments((current) => {
      const next = new Map(current)
      const key = keyOf(scope, target)
      if (patch === null) next.delete(key)
      else next.set(key, { scope, target, categoria: '', ...next.get(key), ...patch })
      return next
    })
  }, [])

  // Atribuição em lote — usado pelo "aplicar Outros em tudo" do marketplace.
  const setManyAssignments = useCallback((entries) => {
    setAssignments((current) => {
      const next = new Map(current)
      for (const { scope, target, patch } of entries) {
        const key = keyOf(scope, target)
        if (patch === null) next.delete(key)
        else next.set(key, { scope, target, categoria: '', ...next.get(key), ...patch })
      }
      return next
    })
  }, [])

  const getAssignment = useCallback(
    (scope, target) => assignments.get(keyOf(scope, target)) || null,
    [assignments],
  )

  async function handleUpload(files, vencimento) {
    await processar(() => api.upload(files, bankId, vencimento), 'unmapped')
  }

  async function handleRecategorize(files) {
    await processar(() => api.recategorize(files), 'changes')
  }

  async function processar(chamada, proximaEtapa) {
    setBusy(true)
    setError(null)
    try {
      const sessao = await chamada()
      setSession(sessao)
      setAssignments(new Map())
      setTravelItems([])
      setTravelWarnings([])
      setTravelRejected(new Set())
      setLiberadas(['upload', proximaEtapa])
      setStep(proximaEtapa)
      // Os períodos digitados na tela de upload só podem ser enviados agora,
      // que existe transação. Falhar aqui não invalida o upload.
      if (travelRanges.length && sessao.modo !== 'recategorizacao') {
        await enviarPeriodos(sessao.transaction_id, travelRanges)
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  // Único caminho para frente: cada etapa chama isto no seu "Continuar".
  function avancar(destino) {
    setLiberadas((atuais) =>
      atuais.includes(destino) ? atuais : [...atuais, destino])
    setStep(destino)
  }

  function limparViagem() {
    setTravelRanges([])
    setTravelItems([])
    setTravelWarnings([])
    setTravelRejected(new Set())
  }

  async function enviarPeriodos(transactionId, ranges) {
    const resposta = await api.travel(transactionId, ranges)
    setTravelRanges(resposta.ranges)
    setTravelItems(resposta.items)
    setTravelWarnings(resposta.warnings)
    // Quem deixou de ser candidata deixa de ter rejeição — o backend faz a
    // mesma poda; refletir aqui evita a caixa reaparecer desmarcada.
    const vivas = new Set(resposta.items.map((i) => i.line_id))
    setTravelRejected((atual) => new Set([...atual].filter((id) => vivas.has(id))))
  }

  // Os períodos são SUBSTITUTIVOS: a lista enviada vira a lista do backend.
  // Adicionar e remover são a mesma chamada, o que torna desfazer trivial.
  //
  // Antes de existir transação (tela de upload) a lista fica só no cliente e é
  // enviada assim que o processamento termina.
  async function handleTravelRanges(ranges) {
    if (!session) return setTravelRanges(ranges)
    setBusy(true)
    setError(null)
    try {
      await enviarPeriodos(session.transaction_id, ranges)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const toggleTravel = useCallback((lineId) => {
    setTravelRejected((atual) => {
      const proximo = new Set(atual)
      if (proximo.has(lineId)) proximo.delete(lineId)
      else proximo.add(lineId)
      return proximo
    })
  }, [])

  const travelRejectedList = useMemo(() => [...travelRejected], [travelRejected])

  function restart() {
    setSession(null)
    setAssignments(new Map())
    limparViagem()
    setLiberadas(['upload'])
    setStep('upload')
    setError(null)
    api.getCategories().then((d) => setCategories(d.categories)).catch(() => {})
  }

  // A ORDEM importa: `etapas` é `const`, então lê-la antes da declaração é
  // ReferenceError (temporal dead zone), não `undefined`.
  const modo = session?.modo || (section === 'recategorizar' ? 'recategorizacao' : 'fatura')
  const etapas = STEPS.filter((s) => !s.apenas || s.apenas === modo)
  const stepIndex = etapas.findIndex((s) => s.id === step)
  // Depois de chegar em "Conferir e exportar" não há mais o que proteger: o
  // dataset está montado e voltar para ajustar é justamente o que se quer.
  const navegacaoLivre = liberadas.includes('final')
  const podeIr = (id) => navegacaoLivre || liberadas.includes(id)
  const counts = session && {
    unmapped: session.unmapped_items.length,
    marketplace: session.marketplace_items.length,
    viagem: travelItems.length - travelRejected.size,
  }

  const TITULOS = {
    importar: 'Importar fatura',
    recategorizar: 'Recategorizar CSV',
    analise: 'Análise do histórico',
    regras: 'Regras de categorização',
    entrada: 'Formato de entrada',
    saida: 'Formato de saída',
    config: 'Configuração',
  }

  return (
    <div className="shell">
      <aside className={`sidebar ${sidebarOpen ? '' : 'closed'}`}>
        <div className="brand">
          <span className="mark">S</span>
          <span>
            <span className="name">Fatura</span>
            <span className="sub">Sicredi → planilha</span>
          </span>
        </div>

        <button
          className={`nav-item ${section === 'importar' ? 'active' : ''}`}
          onClick={() => setSection('importar')}
        >
          Importar fatura
        </button>

        <button
          className={`nav-item ${section === 'regras' ? 'active' : ''}`}
          onClick={() => setSection('regras')}
        >
          Regras
          {flaggedRules > 0 && <span className="pill">{flaggedRules}</span>}
        </button>

        <button
          className={`nav-item ${section === 'recategorizar' ? 'active' : ''}`}
          onClick={() => setSection('recategorizar')}
        >
          Recategorizar CSV
        </button>

        <button
          className={`nav-item ${section === 'analise' ? 'active' : ''}`}
          onClick={() => setSection('analise')}
        >
          Análise
        </button>

        <div className="nav-sep" />

        <button
          className={`nav-item ${section === 'entrada' ? 'active' : ''}`}
          onClick={() => setSection('entrada')}
        >
          Formato de entrada
        </button>
        <button
          className={`nav-item ${section === 'saida' ? 'active' : ''}`}
          onClick={() => setSection('saida')}
        >
          Formato de saída
        </button>
        <button
          className={`nav-item ${section === 'config' ? 'active' : ''}`}
          onClick={() => setSection('config')}
        >
          Configuração
        </button>

        <div className="sidebar-foot">v2.1.0</div>
      </aside>

      <div className="content">
        <header className="topbar">
          <button
            className="hamburger"
            aria-label={sidebarOpen ? 'Esconder menu' : 'Mostrar menu'}
            aria-expanded={sidebarOpen}
            onClick={() => setSidebarOpen((v) => !v)}
          >
            <span />
          </button>
          <h1>{TITULOS[section]}</h1>
          <div className="spacer" />
          <BankPicker
            banks={banks}
            value={bankId}
            onChange={setBankId}
            disabled={Boolean(session) && section === 'importar'}
          />
        </header>

        <div className="page">
          {error && (
            <div className="alert error" role="alert">
              {error}{' '}
              <button className="link" onClick={() => setError(null)}>fechar</button>
            </div>
          )}

          {section === 'entrada' && (
            <InputFormatView
              bankId={bankId}
              banks={banks}
              onError={setError}
              onBanksChanged={loadBanks}
            />
          )}

          {section === 'saida' && <OutputFormatView onError={setError} />}

          {section === 'analise' && <AnalyticsView onError={setError} />}

          {section === 'config' && (
            <ConfigBundle onError={setError} onImported={loadBanks} />
          )}

          {section === 'regras' && (
            <RulesView
              onError={setError}
              onCategoriesChanged={setCategories}
              onFlaggedChanged={setFlaggedRules}
            />
          )}

          {(section === 'importar' || section === 'recategorizar') && (
            <>
              {session && (
                <div className="session-bar">
                  <span className="muted small">
                    {session.modo === 'recategorizacao'
                      ? `${session.source_files.reduce((s, f) => s + f.rows, 0)} linha(s) de
                         ${session.source_files.length} arquivo(s) · ${session.changes.length}
                         mudança(s)`
                      : `${session.statements.length} fatura(s) de ${bancoAtual?.nome || '—'} carregada(s)`}
                  </span>
                  <button className="ghost" onClick={restart}>Começar de novo</button>
                </div>
              )}


              <nav className="steps" aria-label="Etapas">
                {etapas.map((s, i) => (
                  <button
                    key={s.id}
                    className={`step ${s.id === step ? 'active' : ''} ${i < stepIndex ? 'done' : ''}`}
                    disabled={(!session && s.id !== 'upload') || !podeIr(s.id)}
                    title={podeIr(s.id) ? undefined
                                        : 'Conclua a etapa anterior para liberar'}
                    onClick={() => session && podeIr(s.id) && setStep(s.id)}
                  >
                    <span className="num">{i + 1}</span>
                    {s.label}
                    {counts && s.id === 'unmapped' && counts.unmapped > 0 && (
                      <span className="badge">{counts.unmapped}</span>
                    )}
                    {counts && s.id === 'marketplace' && counts.marketplace > 0 && (
                      <span className="badge">{counts.marketplace}</span>
                    )}
                    {counts && s.id === 'viagem' && counts.viagem > 0 && (
                      <span className="badge">{counts.viagem}</span>
                    )}
                  </button>
                ))}
              </nav>

              {step === 'upload' && section === 'importar' && (
                <UploadStep
                  onUpload={handleUpload}
                  busy={busy}
                  banco={bancoAtual}
                  travelRanges={travelRanges}
                  onTravelRangesChange={handleTravelRanges}
                />
              )}

              {step === 'upload' && section === 'recategorizar' && (
                <RecategorizeStep onUpload={handleRecategorize} busy={busy} />
              )}

              {step === 'changes' && session && (
                <ChangesSummary
                  session={session}
                  getAssignment={getAssignment}
                  setAssignment={setAssignment}
                  setManyAssignments={setManyAssignments}
                  onNext={() => avancar('unmapped')}
                />
              )}

              {step === 'unmapped' && session && (
                <UnmappedStep
                  session={session}
                  categories={categories}
                  getAssignment={getAssignment}
                  setAssignment={setAssignment}
                  setManyAssignments={setManyAssignments}
                  assignmentList={assignmentList}
                  onCategoriesChanged={setCategories}
                  onNext={() => avancar('auto')}
                  onError={setError}
                />
              )}

              {step === 'auto' && session && (
                <AutoReviewStep
                  session={session}
                  categories={categories}
                  getAssignment={getAssignment}
                  setAssignment={setAssignment}
                  onNext={() => avancar('marketplace')}
                />
              )}

              {step === 'marketplace' && session && (
                <MarketplaceStep
                  session={session}
                  categories={categories}
                  getAssignment={getAssignment}
                  setAssignment={setAssignment}
                  setManyAssignments={setManyAssignments}
                  onNext={() => avancar(modo === 'fatura' ? 'viagem' : 'final')}
                />
              )}

              {step === 'viagem' && session && modo === 'fatura' && (
                <TravelStep
                  session={session}
                  categories={categories}
                  ranges={travelRanges}
                  items={travelItems}
                  warnings={travelWarnings}
                  rejected={travelRejected}
                  onRangesChange={handleTravelRanges}
                  getAssignment={getAssignment}
                  setAssignment={setAssignment}
                  onToggle={toggleTravel}
                  onNext={() => avancar('final')}
                  busy={busy}
                />
              )}

              {step === 'final' && session && (
                <FinalReview
                  session={session}
                  assignmentList={assignmentList}
                  travelRejected={travelRejectedList}
                  onError={setError}
                />
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
