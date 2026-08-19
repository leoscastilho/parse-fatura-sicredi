/**
 * Testes do front. Rodam no `npm test`, que o Dockerfile executa ANTES do
 * build — um teste vermelho derruba a imagem em vez de virar bug em produção.
 *
 * O foco é o que dá para quebrar sem perceber: a troca de tema por banco, o
 * lote do marketplace que não pode sobrescrever decisão manual, e o filtro de
 * extensão que evita mandar um .csv para o perfil do Sicredi.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { applyTheme } from '../theme'
import MarketplaceStep from '../components/MarketplaceStep'
import UploadStep from '../components/UploadStep'
import CategorySelect from '../components/CategorySelect'
import RecategorizeStep, { ChangesSummary } from '../components/RecategorizeStep'
import TravelStep from '../components/TravelStep'
import FinalReview from '../components/FinalReview'
import UnmappedStep from '../components/UnmappedStep'

// ---------------------------------------------------------------- tema

describe('applyTheme', () => {
  beforeEach(() => document.documentElement.removeAttribute('style'))

  it('reescreve as CSS custom properties do banco escolhido', () => {
    applyTheme({ primaria: '#820AD1', escura: '#1B0C24', clara: '#E9D5F7' })
    const style = document.documentElement.style
    expect(style.getPropertyValue('--verde')).toBe('#820AD1')
    expect(style.getPropertyValue('--verde-escuro')).toBe('#1B0C24')
    // A borda acompanha o tom claro para o contorno não destoar.
    expect(style.getPropertyValue('--border')).toBe('#E9D5F7')
  })

  it('ignora tema ausente sem estourar', () => {
    expect(() => applyTheme(null)).not.toThrow()
  })

  it('não apaga variáveis que o tema não define', () => {
    document.documentElement.style.setProperty('--amarelo', '#FFCD00')
    applyTheme({ primaria: '#820AD1' })
    expect(document.documentElement.style.getPropertyValue('--amarelo')).toBe('#FFCD00')
  })
})

// ---------------------------------------------------------------- marketplace

const LINHAS = [
  { line_id: '0:1', merchant: 'AMAZON', descricao: '[Cartão] Amazon Br {Em 8/Jul}',
    valor: 59.13, statement: 'agosto.xls' },
  { line_id: '0:2', merchant: 'MERCADOLIVRE', descricao: '[Cartão] Mercadolivre {Em 9/Jul}',
    valor: 103.14, statement: 'agosto.xls' },
  { line_id: '0:3', merchant: 'AMAZON', descricao: '[Cartão] Amazon Mktplc {Em 10/Jul}',
    valor: 19.9, statement: 'agosto.xls' },
]

function montarMarketplace({ atribuicoes = {} } = {}) {
  const estado = new Map(Object.entries(atribuicoes))
  const setAssignment = vi.fn()
  const setManyAssignments = vi.fn()
  render(
    <MarketplaceStep
      session={{ marketplace_items: LINHAS }}
      categories={['Casa', 'Hobby', 'Outros']}
      getAssignment={(scope, target) => estado.get(target) || null}
      setAssignment={setAssignment}
      setManyAssignments={setManyAssignments}
      onNext={() => {}}
    />,
  )
  return { setAssignment, setManyAssignments }
}

describe('MarketplaceStep', () => {
  it('aplica a categoria em lote só nas linhas sem categoria', async () => {
    const { setManyAssignments } = montarMarketplace({
      atribuicoes: { '0:2': { scope: 'line', target: '0:2', categoria: 'Hobby' } },
    })

    // 3 linhas, 1 já preenchida -> o botão oferece as outras 2.
    const botao = screen.getByRole('button', { name: /Aplicar Outros em 2/ })
    await userEvent.click(botao)

    expect(setManyAssignments).toHaveBeenCalledTimes(1)
    const enviado = setManyAssignments.mock.calls[0][0]
    expect(enviado.map((a) => a.target)).toEqual(['0:1', '0:3'])
    expect(enviado.every((a) => a.patch.categoria === 'Outros')).toBe(true)
    // A decisão manual não aparece no lote.
    expect(enviado.map((a) => a.target)).not.toContain('0:2')
  })

  it('diz no botão QUAL categoria será aplicada', () => {
    // Antes o botão só dizia "Aplicar em 3" e o Outros entrava sem aviso.
    montarMarketplace()
    expect(screen.getByRole('button', { name: /Aplicar Outros em 3/ })).toBeEnabled()
  })

  it('o botão acompanha a categoria escolhida', async () => {
    montarMarketplace()
    const seletor = screen.getAllByRole('combobox')[0]
    await userEvent.selectOptions(seletor, 'Hobby')
    expect(screen.getByRole('button', { name: /Aplicar Hobby em 3/ })).toBeEnabled()
  })

  it('não deixa sair da tela com linha em branco', () => {
    // O contrato mudou: antes dava para continuar deixando buraco no CSV.
    // Agora o próprio botão de continuar preenche, e diz com o quê.
    montarMarketplace()
    expect(screen.getByRole('button', { name: /Continuar e aplicar Outros em 3/ }))
      .toBeEnabled()
    expect(screen.getByText(/esta\s+tela não deixa passar linha em branco/i))
      .toBeInTheDocument()
  })

  it('continuar aplica o lote e só depois avança', async () => {
    const setManyAssignments = vi.fn()
    const onNext = vi.fn()
    render(
      <MarketplaceStep
        session={{ marketplace_items: LINHAS }}
        categories={['Casa', 'Hobby', 'Outros']}
        getAssignment={() => null}
        setAssignment={vi.fn()}
        setManyAssignments={setManyAssignments}
        onNext={onNext}
      />)
    await userEvent.click(
      screen.getByRole('button', { name: /Continuar e aplicar Outros em 3/ }))

    expect(setManyAssignments).toHaveBeenCalledTimes(1)
    expect(setManyAssignments.mock.calls[0][0]).toHaveLength(3)
    expect(onNext).toHaveBeenCalled()
  })

  it('sem categoria escolhida, continuar fica travado', async () => {
    montarMarketplace()
    await userEvent.selectOptions(screen.getAllByRole('combobox')[0], '')
    expect(screen.getByRole('button', { name: /Continuar/ })).toBeDisabled()
  })

  it('com tudo preenchido o botão volta a ser só "Continuar"', () => {
    montarMarketplace({ atribuicoes: {
      '0:1': { categoria: 'Casa' }, '0:2': { categoria: 'Casa' },
      '0:3': { categoria: 'Casa' },
    } })
    expect(screen.getByRole('button', { name: 'Continuar' })).toBeEnabled()
    expect(screen.getByText(/Todas as linhas têm categoria/)).toBeInTheDocument()
  })

  it('limpar tudo fica desabilitado quando não há nada preenchido', () => {
    montarMarketplace()
    expect(screen.getByRole('button', { name: /Limpar tudo/ })).toBeDisabled()
  })

  it('limpar tudo remove as atribuições de todas as linhas', async () => {
    const { setManyAssignments } = montarMarketplace({
      atribuicoes: { '0:1': { scope: 'line', target: '0:1', categoria: 'Casa' } },
    })
    await userEvent.click(screen.getByRole('button', { name: /Limpar tudo/ }))
    const enviado = setManyAssignments.mock.calls[0][0]
    expect(enviado).toHaveLength(3)
    expect(enviado.every((a) => a.patch === null)).toBe(true)
  })
})

// ---------------------------------------------------------------- upload

describe('UploadStep', () => {
  const sicredi = { nome: 'Sicredi', extensoes: ['.xls', '.xlsx'], validado: true }
  const nubank = { nome: 'Nubank', extensoes: ['.csv'], validado: false,
                   pede_vencimento: true }

  it('descarta arquivos que o perfil do banco não aceita', async () => {
    const onUpload = vi.fn()
    const { container } = render(
      <UploadStep onUpload={onUpload} busy={false} banco={sicredi} />)

    const input = container.querySelector('input[type="file"]')
    await userEvent.upload(input, [
      new File(['x'], 'fatura.csv', { type: 'text/csv' }),
      new File(['x'], 'fatura.xls', { type: 'application/vnd.ms-excel' }),
    ])

    expect(screen.getByText('fatura.xls')).toBeInTheDocument()
    expect(screen.queryByText('fatura.csv')).not.toBeInTheDocument()
  })

  it('avisa quando o perfil do banco não foi validado', () => {
    render(<UploadStep onUpload={vi.fn()} busy={false} banco={nubank} />)
    expect(screen.getByText(/não foi validado/i)).toBeInTheDocument()
  })

  it('exige a data de vencimento nos bancos que não a trazem no arquivo', async () => {
    const onUpload = vi.fn()
    const { container } = render(
      <UploadStep onUpload={onUpload} busy={false} banco={nubank} />)

    await userEvent.upload(container.querySelector('input[type="file"]'),
                           new File(['x'], 'nu.csv', { type: 'text/csv' }))

    const processar = screen.getByRole('button', { name: /Processar/ })
    expect(processar).toBeDisabled()

    const data = container.querySelector('input[type="date"]')
    await userEvent.type(data, '2026-08-10')
    expect(processar).toBeEnabled()

    await userEvent.click(processar)
    expect(onUpload).toHaveBeenCalledWith(expect.any(Array), '2026-08-10')
  })
})

// ---------------------------------------------------------------- select

describe('CategorySelect', () => {
  it('mostra uma categoria que ainda não está na lista como "(nova)"', () => {
    render(<CategorySelect value="Churrascaria" categories={['Casa']}
                           onChange={() => {}} />)
    const select = screen.getByRole('combobox')
    expect(within(select).getByText('Churrascaria (nova)')).toBeInTheDocument()
  })

  it('permite voltar para vazio', async () => {
    const onChange = vi.fn()
    render(<CategorySelect value="Casa" categories={['Casa', 'Lazer']}
                           onChange={onChange} />)
    await userEvent.selectOptions(screen.getByRole('combobox'), '')
    expect(onChange).toHaveBeenCalledWith('')
  })
})


// ---------------------------------------------------------------- recategorizar

describe('RecategorizeStep', () => {
  it('aceita só .csv', async () => {
    const { container } = render(<RecategorizeStep onUpload={vi.fn()} busy={false} />)
    await userEvent.upload(container.querySelector('input[type="file"]'), [
      new File(['x'], 'saida.csv', { type: 'text/csv' }),
      new File(['x'], 'extrato.xls', { type: 'application/vnd.ms-excel' }),
    ])
    expect(screen.getByText('saida.csv')).toBeInTheDocument()
    expect(screen.queryByText('extrato.xls')).not.toBeInTheDocument()
  })

  it('diz que só a coluna Categoria muda', () => {
    render(<RecategorizeStep onUpload={vi.fn()} busy={false} />)
    expect(screen.getByText(/só a coluna Categoria muda/i)).toBeInTheDocument()
  })
})

describe('ChangesSummary', () => {
  const sessao = (changes) => ({
    changes,
    unchanged: 397,
    source_files: [{ name: 'historico.csv', rows: 399, total: 44618.19 }],
  })

  const montar = (changes) => render(
    <ChangesSummary session={sessao(changes)} getAssignment={() => null}
                    setAssignment={vi.fn()} setManyAssignments={vi.fn()}
                    onNext={vi.fn()} />)

  it('lista as mudanças com o de/para e a regra que casou', () => {
    montar([{ line_id: '0:12', descricao: '[Cartão] Target 000208 {Em 1/Feb}',
              valor: 23.91, de: '', para: 'Casa', matched: 'TARGET' }])

    expect(screen.getByText('[Cartão] Target 000208 {Em 1/Feb}')).toBeInTheDocument()
    expect(screen.getByText('Casa')).toBeInTheDocument()
    expect(screen.getByText('TARGET')).toBeInTheDocument()
    // "de" vazio precisa aparecer como vazio, não sumir da tabela.
    expect(screen.getByText(/— vazia —/)).toBeInTheDocument()
  })

  it('quando nada muda, diz que o arquivo já estava em dia', () => {
    montar([])
    expect(screen.getByText(/já estava em dia/i)).toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).toBeNull()
  })
})

// ---------------------------------------------------------------- viagem

const VIAGEM_ITENS = [
  { line_id: '0:2', purchase_date: '2026-07-02', valor: 270.51, categoria: 'Alimentação',
    descricao: '[Cartão] Supermercados Alvora {Em 2/Jul}', viagem: true },
  { line_id: '0:5', purchase_date: '2026-07-03', valor: 59.13, categoria: '',
    descricao: '[Cartão] Amazon Br {Em 3/Jul}', viagem: true },
]

function montarViagem({
  ranges = [], items = VIAGEM_ITENS, warnings = [], rejected = new Set(),
  atribuicoes = {}, purchase_range = { inicio: '2026-06-26', fim: '2026-07-08' },
} = {}) {
  const onRangesChange = vi.fn()
  const onToggle = vi.fn()
  const setAssignment = vi.fn()
  const onNext = vi.fn()
  render(
    <TravelStep
      session={{ purchase_range }}
      categories={['Alimentação', 'Hobby', 'Outros']}
      ranges={ranges}
      items={items}
      warnings={warnings}
      rejected={rejected}
      onRangesChange={onRangesChange}
      getAssignment={(scope, target) => atribuicoes[target] || null}
      setAssignment={setAssignment}
      onToggle={onToggle}
      onNext={onNext}
      busy={false}
    />,
  )
  return { onRangesChange, onToggle, setAssignment, onNext }
}

describe('TravelStep', () => {
  it('limita os seletores ao intervalo de COMPRAS do lote', () => {
    // O erro que isto previne: marcar a viagem sobre o vencimento da fatura,
    // que é uma data só e não tem nada a ver com quando se comprou.
    const { container } = montarViagem()
    const [ida, volta] = document.querySelectorAll('input[type="date"]')
    expect(ida).toHaveAttribute('min', '2026-06-26')
    expect(ida).toHaveAttribute('max', '2026-07-08')
    expect(volta).toHaveAttribute('max', '2026-07-08')
    expect(screen.getByText('26/06/2026')).toBeInTheDocument()
  })

  it('adiciona um período e limpa o formulário', async () => {
    const { onRangesChange } = montarViagem()
    const [ida, volta] = document.querySelectorAll('input[type="date"]')

    await userEvent.type(ida, '2026-07-02')
    await userEvent.type(volta, '2026-07-05')
    await userEvent.type(screen.getByPlaceholderText(/Gramado/), 'Gramado')
    await userEvent.click(screen.getByRole('button', { name: /Adicionar período/ }))

    expect(onRangesChange).toHaveBeenCalledWith([
      { inicio: '2026-07-02', fim: '2026-07-05', rotulo: 'Gramado' },
    ])
  })

  it('recusa a volta antes da ida sem chamar a API', async () => {
    const { onRangesChange } = montarViagem()
    const [ida, volta] = document.querySelectorAll('input[type="date"]')
    await userEvent.type(ida, '2026-07-05')
    await userEvent.type(volta, '2026-07-02')
    await userEvent.click(screen.getByRole('button', { name: /Adicionar período/ }))

    expect(screen.getByText(/não pode ser antes da ida/i)).toBeInTheDocument()
    expect(onRangesChange).not.toHaveBeenCalled()
  })

  it('remover manda a lista SEM o período — a chamada é substitutiva', async () => {
    const { onRangesChange } = montarViagem({
      ranges: [
        { inicio: '2026-07-02', fim: '2026-07-05', rotulo: 'Gramado' },
        { inicio: '2026-07-07', fim: '2026-07-08', rotulo: '' },
      ],
    })
    await userEvent.click(screen.getAllByRole('button', { name: 'remover' })[0])
    expect(onRangesChange).toHaveBeenCalledWith([
      { inicio: '2026-07-07', fim: '2026-07-08', rotulo: '' },
    ])
  })

  it('vem tudo marcado por padrão e desmarcar avisa o pai', async () => {
    const { onToggle } = montarViagem()
    const caixas = screen.getAllByRole('checkbox')
    expect(caixas.every((c) => c.checked)).toBe(true)

    await userEvent.click(caixas[0])
    expect(onToggle).toHaveBeenCalledWith('0:2')
  })

  it('desconta do total o que foi desmarcado', () => {
    montarViagem({ rejected: new Set(['0:2']) })
    // O resumo soma as CONFIRMADAS, não as candidatas: desmarcar a Alvora
    // (270,51) deixa só a Amazon (59,13) indo para Viagem.
    const resumo = screen.getByText(/de 2 confirmada/).closest('p')
    expect(resumo).toHaveTextContent('1 de 2 confirmada')
    expect(resumo).toHaveTextContent(/R\$\s*59,13/)
  })

  it('oferece escolher a categoria real quando a linha está em branco', () => {
    // Sem categoria real, a linha viraria "Viagem" sem nenhuma pista do que
    // foi comprado — o parêntese ficaria vazio.
    montarViagem()
    const [alvora, amazon] = screen.getAllByRole('row').slice(1)  // pula o cabeçalho
    expect(within(alvora).queryByRole('combobox')).toBeNull()
    expect(within(alvora).getByText('Alimentação')).toBeInTheDocument()
    expect(within(amazon).getByRole('combobox')).toBeInTheDocument()
  })

  it('usa a categoria decidida no marketplace, não a da regra', () => {
    montarViagem({
      atribuicoes: { '0:5': { scope: 'line', target: '0:5', categoria: 'Hobby' } },
    })
    // Já resolvida no marketplace: vira texto, não dropdown.
    const [, amazon] = screen.getAllByRole('row').slice(1)
    expect(within(amazon).queryByRole('combobox')).toBeNull()
    expect(within(amazon).getByText('Hobby')).toBeInTheDocument()
  })

  it('mostra os avisos do backend', () => {
    montarViagem({ warnings: ['O período 01/01 a 05/01 não tem nenhuma compra.'] })
    expect(screen.getByText(/não tem nenhuma compra/)).toBeInTheDocument()
  })

  it('sem período nenhum, deixa seguir em frente', async () => {
    const { onNext } = montarViagem({ items: [] })
    expect(screen.getByText(/Adicione um período acima/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Continuar' }))
    expect(onNext).toHaveBeenCalled()
  })

  it('lote sem data de compra alguma não trava a etapa', async () => {
    // Sem limites os seletores ficam livres em vez de a etapa sumir: o usuário
    // ainda pode registrar o período, e o backend avisa se não pegar nada.
    const { onNext } = montarViagem({ purchase_range: null, items: [] })
    const [ida] = document.querySelectorAll('input[type="date"]')
    expect(ida).not.toHaveAttribute('min')
    await userEvent.click(screen.getByRole('button', { name: 'Continuar' }))
    expect(onNext).toHaveBeenCalled()
  })
})

// ---------------------------------------------------------------- App

/**
 * O App inteiro nunca era renderizado por teste nenhum, e foi assim que uma
 * `const` lida antes da declaração (temporal dead zone) sobreviveu: os
 * componentes passavam todos, e a aplicação estourava em branco no navegador.
 * Estes testes montam o App de verdade — é o que fecha essa classe de erro.
 */
describe('App', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.doMock('../api', () => ({
      getCategories: vi.fn().mockResolvedValue({ categories: ['Alimentação'] }),
      getRules: vi.fn().mockResolvedValue({ flagged_count: 0 }),
      getConfig: vi.fn().mockResolvedValue({
        banks: [{ id: 'sicredi', nome: 'Sicredi', extensoes: ['.xls'], validado: true,
                  tema: { primaria: '#3FA110' } }],
        banco_padrao: 'sicredi',
      }),
      upload: vi.fn(), recategorize: vi.fn(), travel: vi.fn(),
      preview: vi.fn(), exportCsv: vi.fn(),
    }))
  })

  async function montarApp() {
    const { default: App } = await import('../App')
    return render(<App />)
  }

  it('renderiza sem estourar', async () => {
    await montarApp()
    expect(await screen.findByRole('heading', { name: 'Importar fatura' }))
      .toBeInTheDocument()
  })

  it('mostra a etapa de Viagem no fluxo de fatura', async () => {
    await montarApp()
    expect(await screen.findByRole('button', { name: /Viagem/ })).toBeInTheDocument()
  })

  it('esconde a etapa de Viagem na recategorização', async () => {
    // A recategorização promete não tocar a descrição, e a viagem escreve nela.
    await montarApp()
    await userEvent.click(screen.getByRole('button', { name: 'Recategorizar CSV' }))
    expect(screen.queryByRole('button', { name: /^Viagem/ })).toBeNull()
    expect(await screen.findByRole('button', { name: /Mudanças/ })).toBeInTheDocument()
  })
})

// -------------------------------------------------- recategorizar: confirmar

const MUDANCAS = [
  { line_id: '0:1', descricao: 'Conta de luz', valor: 180.5, de: 'Outros',
    para: 'Casa', matched: 'LUZ' },
  { line_id: '0:2', descricao: 'Remédio Joyner', valor: 42.0, de: 'Cachorro',
    para: 'Saúde', matched: 'REMEDIO' },
]

function montarChanges({ atribuicoes = {} } = {}) {
  const setAssignment = vi.fn()
  const setManyAssignments = vi.fn()
  render(
    <ChangesSummary
      session={{ changes: MUDANCAS, unchanged: 397,
                 source_files: [{ name: 'historico.csv', rows: 399, total: 1 }] }}
      getAssignment={(scope, target) => atribuicoes[target] || null}
      setAssignment={setAssignment}
      setManyAssignments={setManyAssignments}
      onNext={vi.fn()}
    />,
  )
  return { setAssignment, setManyAssignments }
}

describe('ChangesSummary — confirmar mudança a mudança', () => {
  it('mostra de → para e a regra que casou', () => {
    montarChanges()
    const [luz] = screen.getAllByRole('row').slice(1)
    expect(within(luz).getByText('Outros')).toBeInTheDocument()
    expect(within(luz).getByText('Casa')).toBeInTheDocument()
    expect(within(luz).getByText('LUZ')).toBeInTheDocument()
  })

  it('tudo começa aceito', () => {
    montarChanges()
    expect(screen.getAllByRole('checkbox').every((c) => c.checked)).toBe(true)
    expect(screen.getByText(/2 de 2 serão aplicadas/)).toBeInTheDocument()
  })

  it('recusar devolve a categoria de ORIGEM, não limpa a linha', async () => {
    // O erro que isto previne: recusar virar "sem categoria" e apagar anos de
    // decisão manual da planilha.
    const { setAssignment } = montarChanges()
    await userEvent.click(screen.getAllByRole('checkbox')[1])
    expect(setAssignment).toHaveBeenCalledWith('line', '0:2',
      { categoria: 'Cachorro', persist_keyword: null })
  })

  it('reaceitar remove a atribuição em vez de fixar a nova categoria', async () => {
    const { setAssignment } = montarChanges({
      atribuicoes: { '0:1': { scope: 'line', target: '0:1', categoria: 'Outros' } },
    })
    await userEvent.click(screen.getAllByRole('checkbox')[0])
    expect(setAssignment).toHaveBeenCalledWith('line', '0:1', null)
  })

  it('conta e soma só o que continua aceito', () => {
    montarChanges({
      atribuicoes: { '0:1': { scope: 'line', target: '0:1', categoria: 'Outros' } },
    })
    expect(screen.getByText(/1 de 2 serão aplicadas/)).toBeInTheDocument()
    expect(screen.getByText(/1 recusada/)).toBeInTheDocument()
  })

  it('recusar todas manda a categoria de origem de cada linha', async () => {
    const { setManyAssignments } = montarChanges()
    await userEvent.click(screen.getByRole('button', { name: 'Recusar todas' }))
    expect(setManyAssignments).toHaveBeenCalledWith([
      { scope: 'line', target: '0:1', patch: { categoria: 'Outros', persist_keyword: null } },
      { scope: 'line', target: '0:2', patch: { categoria: 'Cachorro', persist_keyword: null } },
    ])
  })

  it('aceitar todas limpa as atribuições', async () => {
    const { setManyAssignments } = montarChanges({
      atribuicoes: { '0:1': { scope: 'line', target: '0:1', categoria: 'Outros' } },
    })
    await userEvent.click(screen.getByRole('button', { name: 'Aceitar todas' }))
    expect(setManyAssignments.mock.calls[0][0].every((a) => a.patch === null)).toBe(true)
  })
})

// -------------------------------------------- conferir: filtrar só o que mudou

describe('FinalReview — filtro "só o que mudou"', () => {
  const LINHAS_PREVIEW = [
    { line_id: '0:1', data: '07/10/2026', categoria: 'Casa', categoria_anterior: 'Outros',
      descricao: 'Conta de luz', valor: 180.5, pago: 'x' },
    { line_id: '0:2', data: '07/10/2026', categoria: 'Alimentação',
      categoria_anterior: 'Alimentação', descricao: 'Mercado da semana',
      valor: 312.4, pago: 'x' },
    { line_id: '0:3', data: '07/10/2026', categoria: 'Transporte',
      categoria_anterior: 'Transporte', descricao: 'Pedagio', valor: 12.0, pago: 'x' },
  ]

  async function montarFinal(modo) {
    vi.resetModules()
    vi.doMock('../api', () => ({
      preview: vi.fn().mockResolvedValue({
        rows: LINHAS_PREVIEW, total: 504.9, by_category: { Casa: 180.5 },
        remaining_blank: 0, filename: 'saida.csv',
      }),
      exportCsv: vi.fn(),
    }))
    const { default: Final } = await import('../components/FinalReview')
    render(<Final session={{ transaction_id: 't1', modo, statements: [] }}
                  assignmentList={[]} onError={vi.fn()} />)
    await screen.findByText('Conferir e exportar')
  }

  it('na recategorização mostra as colunas De e Para', async () => {
    await montarFinal('recategorizacao')
    expect(screen.getByRole('columnheader', { name: 'De' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Para' })).toBeInTheDocument()
  })

  it('começa filtrado: num histórico grande o que interessa é o que mudou', async () => {
    await montarFinal('recategorizacao')
    const filtro = screen.getByRole('checkbox',
      { name: /só as linhas que mudaram/i })
    expect(filtro).toBeChecked()
    expect(screen.getByText('Conta de luz')).toBeInTheDocument()
    expect(screen.queryByText('Mercado da semana')).toBeNull()
    expect(screen.getByText(/1 de 3/)).toBeInTheDocument()
  })

  it('desligar o filtro traz o arquivo inteiro de volta', async () => {
    await montarFinal('recategorizacao')
    await userEvent.click(screen.getByRole('checkbox',
      { name: /só as linhas que mudaram/i }))
    expect(screen.getByText('Mercado da semana')).toBeInTheDocument()
    expect(screen.getByText('Pedagio')).toBeInTheDocument()
  })

  it('na importação normal não há filtro nem coluna De', async () => {
    // Numa fatura nova não existe "categoria anterior" — a coluna seria vazia
    // e o filtro esconderia tudo.
    await montarFinal('fatura')
    expect(screen.queryByRole('checkbox',
      { name: /só as linhas que mudaram/i })).toBeNull()
    expect(screen.queryByRole('columnheader', { name: 'De' })).toBeNull()
    expect(screen.getByText('Mercado da semana')).toBeInTheDocument()
  })
})

// ------------------------------------------------ novos: esconder resolvidos

describe('UnmappedStep', () => {
  const NOVOS = [
    { merchant: 'BRASEIRO', count: 3, total: 210.5, samples: ['[Cartão] Braseiro'],
      statements: ['ago.xls'] },
    { merchant: 'LOJA XPTO', count: 1, total: 42, samples: ['[Cartão] Loja Xpto'],
      statements: ['ago.xls'] },
  ]

  function montarNovos({ atribuicoes = {} } = {}) {
    render(
      <UnmappedStep
        session={{ transaction_id: 't1', unmapped_items: NOVOS }}
        categories={['Casa', 'Alimentação']}
        getAssignment={(scope, target) => atribuicoes[target] || null}
        setAssignment={vi.fn()}
        onCategoriesChanged={vi.fn()}
        onNext={vi.fn()}
        onError={vi.fn()}
      />)
  }

  const filtro = () =>
    screen.getByRole('checkbox', { name: /Esconder os que já preenchi/i })

  it('começa mostrando tudo', () => {
    montarNovos()
    expect(filtro()).not.toBeChecked()
    expect(screen.getByText('BRASEIRO')).toBeInTheDocument()
    expect(screen.getByText('LOJA XPTO')).toBeInTheDocument()
  })

  it('esconde os que já têm categoria', async () => {
    montarNovos({ atribuicoes: { BRASEIRO: { categoria: 'Alimentação' } } })
    await userEvent.click(filtro())
    expect(screen.queryByText('BRASEIRO')).toBeNull()
    expect(screen.getByText('LOJA XPTO')).toBeInTheDocument()
  })

  it('"não sei" também conta como resolvido', async () => {
    // Decidir que não se sabe é decidir: a linha não deve continuar pedindo
    // atenção só porque ficou sem categoria.
    montarNovos({ atribuicoes: { BRASEIRO: { categoria: '', mark_unknown: true } } })
    await userEvent.click(filtro())
    expect(screen.queryByText('BRASEIRO')).toBeNull()
  })

  it('conta quantos faltam', () => {
    montarNovos({ atribuicoes: { BRASEIRO: { categoria: 'Alimentação' } } })
    expect(screen.getByText(/1 sem categoria/)).toBeInTheDocument()
    expect(screen.getByText(/1 de 2 resolvido/)).toBeInTheDocument()
  })
})

// ------------------------------------------ períodos de viagem no upload

describe('TravelRanges na tela de upload', () => {
  const sicredi = { nome: 'Sicredi', extensoes: ['.xls'], validado: true }

  it('deixa registrar a viagem antes de processar', async () => {
    // É agora que se lembra da viagem — não depois de revisar 130
    // estabelecimentos.
    const onTravelRangesChange = vi.fn()
    render(<UploadStep onUpload={vi.fn()} busy={false} banco={sicredi}
                       travelRanges={[]} onTravelRangesChange={onTravelRangesChange} />)

    await userEvent.click(screen.getByText(/Viajou neste período/))
    const [ida, volta] = document.querySelectorAll('input[type="date"]')
    await userEvent.type(ida, '2026-07-02')
    await userEvent.type(volta, '2026-07-05')
    await userEvent.click(screen.getByRole('button', { name: /Adicionar período/ }))

    expect(onTravelRangesChange).toHaveBeenCalledWith([
      { inicio: '2026-07-02', fim: '2026-07-05', rotulo: '' }])
  })

  it('sem faturas lidas ainda, os seletores não têm limite', () => {
    render(<UploadStep onUpload={vi.fn()} busy={false} banco={sicredi}
                       travelRanges={[]} onTravelRangesChange={vi.fn()} />)
    const [ida] = document.querySelectorAll('input[type="date"]')
    expect(ida).not.toHaveAttribute('min')
    expect(ida).not.toHaveAttribute('max')
  })

  it('não aparece quando o pai não oferece o controle', () => {
    render(<UploadStep onUpload={vi.fn()} busy={false} banco={sicredi} />)
    expect(screen.queryByText(/Viajou neste período/)).toBeNull()
  })
})

// -------------------------------------------- totais em ordem decrescente

describe('FinalReview — total por categoria', () => {
  it('lista do maior gasto para o menor, não em ordem alfabética', async () => {
    // A pergunta que se faz abrindo isto é "onde foi parar o dinheiro". Em
    // ordem alfabética a resposta ficava no meio da lista.
    vi.resetModules()
    vi.doMock('../api', () => ({
      preview: vi.fn().mockResolvedValue({
        rows: [], total: 0, remaining_blank: 0, filename: 'x.csv',
        by_category: { Alimentação: 312.4, Casa: 1180.5, Transporte: 90.2,
                       Ajuste: -75 },
      }),
      exportCsv: vi.fn(),
    }))
    const { default: Final } = await import('../components/FinalReview')
    render(<Final session={{ transaction_id: 't1', modo: 'fatura', statements: [] }}
                  assignmentList={[]} onError={vi.fn()} />)
    await screen.findByText('Conferir e exportar')

    const totais = screen.getByText('Total por categoria').closest('details')
    const ordem = [...totais.querySelectorAll('tbody tr td:first-child')]
      .map((td) => td.textContent)
    expect(ordem).toEqual(['Casa', 'Alimentação', 'Transporte', 'Ajuste'])
  })
})

// ------------------------------------------------ navegação entre etapas

describe('App — navegação travada até confirmar', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.doMock('../api', () => ({
      getCategories: vi.fn().mockResolvedValue({ categories: ['Casa'] }),
      getRules: vi.fn().mockResolvedValue({ flagged_count: 0 }),
      getConfig: vi.fn().mockResolvedValue({
        banks: [{ id: 'sicredi', nome: 'Sicredi', extensoes: ['.xls'],
                  validado: true, tema: { primaria: '#3FA110' } }],
        banco_padrao: 'sicredi',
      }),
      upload: vi.fn(), recategorize: vi.fn(), travel: vi.fn(),
      preview: vi.fn(), exportCsv: vi.fn(),
    }))
  })

  async function montarApp() {
    const { default: App } = await import('../App')
    render(<App />)
    await screen.findByRole('heading', { name: 'Importar fatura' })
  }

  it('sem fatura, só a etapa de upload responde', async () => {
    await montarApp()
    expect(screen.getByRole('button', { name: /1\s*Upload/ })).toBeEnabled()
    for (const etapa of ['Novos', 'Revisão', 'Marketplace', 'Viagem']) {
      expect(screen.getByRole('button', { name: new RegExp(`\\d\\s*${etapa}`) }))
        .toBeDisabled()
    }
  })

  it('as etapas à frente explicam por que estão travadas', async () => {
    await montarApp()
    expect(screen.getByRole('button', { name: /\d\s*Revisão/ }))
      .toHaveAttribute('title', expect.stringMatching(/Conclua a etapa anterior/))
  })
})
