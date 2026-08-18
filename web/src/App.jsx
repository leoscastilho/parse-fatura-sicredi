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
import { applyTheme } from './theme'

const STEPS = [
  { id: 'upload', label: 'Upload' },
  { id: 'unmapped', label: 'Novos' },
  { id: 'auto', label: 'Revisão' },
  { id: 'marketplace', label: 'Marketplace' },
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
    setBusy(true)
    setError(null)
    try {
      const data = await api.upload(files, bankId, vencimento)
      setSession(data)
      setAssignments(new Map())
      setStep('unmapped')
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  function restart() {
    setSession(null)
    setAssignments(new Map())
    setStep('upload')
    setError(null)
    api.getCategories().then((d) => setCategories(d.categories)).catch(() => {})
  }

  const stepIndex = STEPS.findIndex((s) => s.id === step)
  const counts = session && {
    unmapped: session.unmapped_items.length,
    marketplace: session.marketplace_items.length,
  }

  const TITULOS = {
    importar: 'Importar fatura',
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

          {section === 'importar' && (
            <>
              {session && (
                <div className="session-bar">
                  <span className="muted small">
                    {session.statements.length} fatura(s) de{' '}
                    {bancoAtual?.nome || '—'} carregada(s)
                  </span>
                  <button className="ghost" onClick={restart}>Começar de novo</button>
                </div>
              )}

              <nav className="steps" aria-label="Etapas">
                {STEPS.map((s, i) => (
                  <button
                    key={s.id}
                    className={`step ${s.id === step ? 'active' : ''} ${i < stepIndex ? 'done' : ''}`}
                    disabled={!session && s.id !== 'upload'}
                    onClick={() => session && setStep(s.id)}
                  >
                    <span className="num">{i + 1}</span>
                    {s.label}
                    {counts && s.id === 'unmapped' && counts.unmapped > 0 && (
                      <span className="badge">{counts.unmapped}</span>
                    )}
                    {counts && s.id === 'marketplace' && counts.marketplace > 0 && (
                      <span className="badge">{counts.marketplace}</span>
                    )}
                  </button>
                ))}
              </nav>

              {step === 'upload' && (
                <UploadStep onUpload={handleUpload} busy={busy} banco={bancoAtual} />
              )}

              {step === 'unmapped' && session && (
                <UnmappedStep
                  session={session}
                  categories={categories}
                  getAssignment={getAssignment}
                  setAssignment={setAssignment}
                  assignmentList={assignmentList}
                  onCategoriesChanged={setCategories}
                  onNext={() => setStep('auto')}
                  onError={setError}
                />
              )}

              {step === 'auto' && session && (
                <AutoReviewStep
                  session={session}
                  categories={categories}
                  getAssignment={getAssignment}
                  setAssignment={setAssignment}
                  onNext={() => setStep('marketplace')}
                />
              )}

              {step === 'marketplace' && session && (
                <MarketplaceStep
                  session={session}
                  categories={categories}
                  getAssignment={getAssignment}
                  setAssignment={setAssignment}
                  setManyAssignments={setManyAssignments}
                  onNext={() => setStep('final')}
                />
              )}

              {step === 'final' && session && (
                <FinalReview
                  session={session}
                  assignmentList={assignmentList}
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
