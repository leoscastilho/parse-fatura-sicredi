import { useCallback, useEffect, useMemo, useState } from 'react'
import * as api from './api'
import { CategoriasFixas } from './components/CategorySelect'
import UploadStep from './components/UploadStep'
import UnmappedStep from './components/UnmappedStep'
import AutoReviewStep from './components/AutoReviewStep'
import MarketplaceStep from './components/MarketplaceStep'
import FinalReview from './components/FinalReview'
import RulesView from './components/RulesView'
import OutputFormatView from './components/OutputFormatView'
import ConfigBundle from './components/ConfigBundle'
import RecategorizeStep, { ChangesSummary } from './components/RecategorizeStep'
import TravelStep from './components/TravelStep'
import AnalyticsView from './components/AnalyticsView'
import { applyTheme, resetTheme } from './theme'
import { viagensPorLinha } from './viagens'
import { TODOS, TitularFiltro, opcoesDeTitular } from './titulares'

const STEPS = [
  { id: 'upload', label: 'Upload' },
  // Só aparece na recategorização: mostra o diff antes de qualquer revisão.
  { id: 'changes', label: 'Mudanças', apenas: 'recategorizacao' },
  { id: 'unmapped', label: 'Novos' },
  { id: 'auto', label: 'Revisão' },
  { id: 'marketplace', label: 'Marketplace' },
  // Depois do marketplace de propósito: a categoria que vai para o parêntese é
  // a final, já com as decisões daquela etapa. Vale nos DOIS modos — a viagem
  // de 2019 só é lembrada quando o histórico inteiro está na tela, e prender a
  // marcação ao mês em que a fatura chegou era dar uma única chance a ela.
  { id: 'viagem', label: 'Viagem' },
  { id: 'final', label: 'Conferir e exportar' },
]

// As decisões vivem num Map com chave "escopo:alvo". Sempre que precisamos
// falar com o backend, viram um array — o formato que /validate, /preview e
// /export esperam. Uma estrutura só, do primeiro clique ao download.
const keyOf = (scope, target) => `${scope}:${target}`

/**
 * Leva a rolagem para o começo da etapa seguinte.
 *
 * Quem confirma o último item de uma lista de 130 está no rodapé da página. Sem
 * isto, a etapa nova abre com a rolagem onde estava — ou seja, já no fim dela,
 * olhando para o próprio botão "Continuar" sem ter visto o que a tela pede.
 * `?.` porque o jsdom dos testes não implementa rolagem.
 */
function aoTopo() {
  window.scrollTo?.({ top: 0, behavior: 'smooth' })
}

export default function App() {
  const [section, setSection] = useState('importar')
  const [sidebarOpen, setSidebarOpen] = useState(true)

  const [step, setStep] = useState('upload')
  const [categories, setCategories] = useState([])
  // As categorias que dizem para onde o dinheiro se MOVEU (Renda Fixa,
  // Poupança, Investimento…). Saem dos seletores que atribuem categoria a uma
  // compra, e só deles: os editores de regra continuam com a lista inteira,
  // senão não haveria como consertar uma regra que já aponta para uma delas.
  const [fixas, setFixas] = useState([])
  const [session, setSession] = useState(null)
  const [assignments, setAssignments] = useState(new Map())
  const [flaggedRules, setFlaggedRules] = useState(0)
  // Viagem: os períodos são o input, `travelItems` é o que o backend diz que
  // eles pegam, e `travelRejected` são as exceções que o usuário desmarcou.
  const [travelRanges, setTravelRanges] = useState([])
  const [travelItems, setTravelItems] = useState([])
  const [travelWarnings, setTravelWarnings] = useState([])
  const [travelRejected, setTravelRejected] = useState(new Set())
  // `line_id -> chave do período`: as compras penduradas na viagem à mão,
  // apesar da data. Passagem e hospedagem são pagas meses antes, e nenhuma
  // janela razoável pega as duas coisas.
  const [travelPinned, setTravelPinned] = useState({})
  const [travelOutros, setTravelOutros] = useState([])
  // Etapas já liberadas. Avançar exige clicar em "Continuar" — pular uma etapa
  // pela barra deixava para trás decisões que a etapa seguinte já consome.
  // Chegando na última, tudo destrava e a navegação vira livre.
  const [liberadas, setLiberadas] = useState(['upload'])
  // Conta conjunta: de quem são os lançamentos que as tabelas mostram. `TODOS`
  // é o padrão, e o filtro não muda nada além do que está na tela.
  const [titularFiltro, setTitularFiltro] = useState(TODOS)
  // Quem eu disse ser, na tela de upload. Só para o seletor ter um nome no
  // lugar de "Sem marca" — não vai para o backend nem para o arquivo.
  const [euNome, setEuNome] = useState('')
  const [banks, setBanks] = useState([])
  // Os bancos que o PRÉ-VOO reconheceu nos arquivos escolhidos. Vazio = nada
  // escolhido ainda, ou a leitura falhou — e aí a tela não afirma banco nenhum.
  const [bancosDoLote, setBancosDoLote] = useState([])
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.getCategories()
      .then((data) => { setCategories(data.categories)
                        setFixas(data.fixed_categories || []) })
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
    } catch (e) {
      setError(`Não consegui carregar os bancos: ${e.message}`)
    }
  }

  // O tema segue o banco DETECTADO no arquivo. Repintar é reescrever as CSS
  // custom properties no `:root`, então nenhum componente precisa saber que
  // existe mais de um banco — e sem lote nenhum a folha volta a mandar.
  //
  // Cor de banco só quando o lote é DE UM banco. Com vários, nenhum deles
  // descreve a tela — pintar do primeiro (que era o que acontecia) escolhia um
  // pela ordem em que os arquivos foram soltos e dizia "Sicredi" numa tela em
  // que metade das linhas é do BTG. Sem lote, idem: o portal não é de banco
  // nenhum até alguém soltar um arquivo.
  //
  // Nos dois casos a saída é a mesma — `resetTheme`, que devolve a palavra ao
  // `:root` da folha, hoje o grafite do portal.
  useEffect(() => {
    const banco = bancosDoLote.length === 1
      && banks.find((b) => b.id === bancosDoLote[0].id)
    if (banco) applyTheme(banco.tema)
    else resetTheme()
  }, [banks, bancosDoLote])

  // O banco deixou de ser escolha e virou leitura: quem responde é o arquivo.
  // A tela mostra o que foi reconhecido em vez de perguntar — ver `detectar`
  // em `core/profiles.py`.
  //
  // Só existe "o banco do lote" quando é UM. Com dois, não há inicial que sirva
  // (nem "B" nem "S" descrevem o que está carregado), e a marca volta ao "$" do
  // portal pela mesma razão que o tema fica neutro.
  const bancoAtual = bancosDoLote.length === 1 ? bancosDoLote[0] : null

  // A união do que TODOS os bancos exportam. A dropzone filtra por isto e o
  // conteúdo decide o resto: um `.pdf` não é fatura de banco nenhum, mas um
  // `.csv` pode ser de qualquer um dos dois.
  const extensoesAceitas = useMemo(
    () => [...new Set(banks.flatMap((b) => b.extensoes || []))].sort(),
    [banks],
  )

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

  // `senha` é a senha DO ARQUIVO — o BTG manda a fatura cifrada. Passa direto
  // para a chamada e não vira estado do App: guardá-la aqui a manteria viva
  // depois do upload, sem que nada mais precise dela.
  async function handleUpload(files, vencimento, titulares = '', eu = '', senha = '') {
    setEuNome(eu)
    await processar(() => api.upload(files, vencimento, titulares, senha), 'unmapped')
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
      setTravelPinned({})
      setTravelOutros([])
      setTitularFiltro(TODOS)
      setLiberadas(['upload', proximaEtapa])
      setStep(proximaEtapa)
      aoTopo()
      // Os períodos digitados na tela de upload só podem ser enviados agora,
      // que existe transação. Falhar aqui não invalida o upload.
      if (travelRanges.length) {
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
    aoTopo()
  }

  function limparViagem() {
    setTravelRanges([])
    setTravelItems([])
    setTravelWarnings([])
    setTravelRejected(new Set())
    setTravelPinned({})
    setTravelOutros([])
  }

  async function enviarPeriodos(transactionId, ranges, pinned = travelPinned) {
    const resposta = await api.travel(transactionId, ranges, pinned)
    setTravelRanges(resposta.ranges)
    setTravelItems(resposta.items)
    setTravelWarnings(resposta.warnings)
    setTravelOutros(resposta.outros || [])
    // O backend PODA: fixação de período que não existe mais é descartada lá,
    // e refletir a poda aqui evita a tela mostrar uma viagem que o arquivo não
    // vai levar. Vale o mesmo raciocínio das rejeições, logo abaixo.
    setTravelPinned(resposta.pinned || {})
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

  // Pendurar (ou despendurar) uma linha numa viagem. Substitutivo como os
  // períodos: o mapa que sobe VIRA o mapa guardado, então tirar é mandar sem.
  async function handleTravelPin(lineId, chave) {
    const proximo = { ...travelPinned }
    if (chave) proximo[lineId] = chave
    else delete proximo[lineId]
    setTravelPinned(proximo)
    if (!session) return
    setBusy(true)
    setError(null)
    try {
      await enviarPeriodos(session.transaction_id, travelRanges, proximo)
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

  /**
   * Os nomes que este lote tem, para o seletor de "de quem são os lançamentos".
   *
   * Sai dos BALDES da sessão, e não de uma rota nova: cada grupo já traz os
   * titulares das linhas dele e cada item de marketplace traz o seu. Com menos
   * de dois nomes distintos `opcoesDeTitular` devolve lista vazia, e o seletor
   * não aparece — cartão de uma pessoa só não tem o que filtrar.
   */
  const titularesDoLote = useMemo(() => {
    if (!session) return []
    const grupos = [...(session.unmapped_items || []), ...(session.auto_classified_items || []),
                    ...(session.ignored_items || [])]
    return opcoesDeTitular([
      ...grupos.flatMap((g) => g.titulares || []),
      ...(session.marketplace_items || []).map((i) => i.titular || ''),
    ], euNome)
  }, [session, euNome])

  // Qual viagem pegou cada linha. Serve às etapas que acontecem ANTES da etapa
  // Viagem: ali a marca ainda não está na descrição, e sem esta dica quem
  // categoriza "Sco Miraflores" não tem como saber que aquilo foi no Peru — a
  // única pista, `{Em 24/Oct}`, exige lembrar de cabeça o que aconteceu no dia.
  // É informativo: quem confirma continua sendo a etapa Viagem.
  const viagemPorLinha = useMemo(() => viagensPorLinha(travelItems), [travelItems])

  function restart() {
    setSession(null)
    setAssignments(new Map())
    setTitularFiltro(TODOS)
    setEuNome('')
    // O lote novo pode ser de outro banco: manter a lista faria a tela seguir
    // roxa de Nubank enquanto espera um arquivo que talvez seja do Sicredi.
    setBancosDoLote([])
    limparViagem()
    setLiberadas(['upload'])
    setStep('upload')
    setError(null)
    api.getCategories()
      .then((d) => { setCategories(d.categories)
                     setFixas(d.fixed_categories || []) })
      .catch(() => {})
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
    saida: 'Formato de saída',
    config: 'Configuração',
  }

  return (
    // A lista das fixas vale para a árvore INTEIRA, e é de propósito que não
    // haja exceção: um seletor que oferece `Poupança` para uma compra é o mesmo
    // erro em qualquer tela. Como prop, ela precisava ser repassada em seis
    // seletores, e apagá-la em três deles não derrubava teste nenhum.
    <CategoriasFixas.Provider value={fixas}>
    <TitularFiltro.Provider value={titularFiltro}>
    <div className="shell">
      <aside className={`sidebar ${sidebarOpen ? '' : 'closed'}`}>
        <div className="brand">
          {/* A inicial do banco reconhecido no lote. Era um "S" fixo de
              Sicredi — que ficava mentindo assim que a tela virava roxa de
              Nubank. Sem lote, volta ao "$": o portal não é de banco nenhum
              até alguém soltar um arquivo. */}
          <span className="mark">{bancoAtual?.tema?.inicial || '$'}</span>
          <span>
            <span className="name">Fatura</span>
            <span className="sub">fatura → planilha</span>
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
        </header>

        <div className="page">
          {error && (
            <div className="alert error" role="alert">
              {error}{' '}
              <button className="link" onClick={() => setError(null)}>fechar</button>
            </div>
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
                      : `${session.statements.length} fatura(s) carregada(s)`
                        + ` de ${bancosDoLote.map((b) => b.nome).join(' e ') || '—'}`}
                  </span>
                  {/* O filtro mora AQUI, e não dentro de cada etapa: ele vale
                      para todas elas e sobrevive à navegação entre elas.
                      Repetido em cada tela, seria cinco controles que precisam
                      concordar entre si. */}
                  {titularesDoLote.length > 0 && (
                    // Sem rótulo visível de propósito: a primeira opção é
                    // "Todos", que já explica o controle, e o `<span>` acima do
                    // seletor o desalinhava do "Começar de novo" ao lado. O
                    // `aria-label` continua nomeando o campo para quem não vê.
                    <select
                      className="titular-filtro"
                      value={titularFiltro === TODOS ? '__todos__' : titularFiltro}
                      aria-label="Mostrar lançamentos de quem"
                      onChange={(e) => setTitularFiltro(
                        e.target.value === '__todos__' ? TODOS : e.target.value)}
                    >
                      <option value="__todos__">Todos</option>
                      {titularesDoLote.map((o) => (
                        <option key={o.valor} value={o.valor}>{o.rotulo}</option>
                      ))}
                    </select>
                  )}
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
                  extensoes={extensoesAceitas}
                  onBancosDetectados={setBancosDoLote}
                  travelRanges={travelRanges}
                  onTravelRangesChange={handleTravelRanges}
                />
              )}

              {step === 'upload' && section === 'recategorizar' && (
                <RecategorizeStep
                  onUpload={handleRecategorize}
                  busy={busy}
                  travelRanges={travelRanges}
                  onTravelRangesChange={handleTravelRanges}
                />
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
                  viagens={viagemPorLinha}
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
                  onNext={() => avancar('viagem')}
                />
              )}

              {step === 'viagem' && session && (
                <TravelStep
                  session={session}
                  categories={categories}
                  ranges={travelRanges}
                  items={travelItems}
                  outros={travelOutros}
                  pinned={travelPinned}
                  warnings={travelWarnings}
                  rejected={travelRejected}
                  onRangesChange={handleTravelRanges}
                  onPin={handleTravelPin}
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
    </TitularFiltro.Provider>
    </CategoriasFixas.Provider>
  )
}
