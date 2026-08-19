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
import {
  BarrasDivergentes, BarrasEmpilhadas, LineChart, brlCompacto, eixoContinuo, escala,
} from '../components/charts'
import Residuos from '../components/Residuos'

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
        setManyAssignments={vi.fn()}
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
    expect(screen.getByText(/1 de 2 resolvido/)).toBeInTheDocument()
    expect(screen.getAllByText(/1 sem categoria/).length).toBeGreaterThan(0)
  })

  it('não deixa continuar deixando estabelecimento em branco', () => {
    // Mesma regra do Marketplace: o botão diz o que vai aplicar.
    montarNovos()
    expect(screen.getByRole('button', { name: /Continuar e aplicar Outros em 2/ }))
      .toBeEnabled()
  })

  it('continuar aplica o lote só nos pendentes e avança', async () => {
    const setManyAssignments = vi.fn()
    const onNext = vi.fn()
    render(
      <UnmappedStep
        session={{ transaction_id: 't1', unmapped_items: NOVOS }}
        categories={['Casa', 'Alimentação', 'Outros']}
        getAssignment={(scope, target) =>
          target === 'BRASEIRO' ? { categoria: 'Alimentação' } : null}
        setAssignment={vi.fn()}
        setManyAssignments={setManyAssignments}
        onCategoriesChanged={vi.fn()}
        onNext={onNext}
        onError={vi.fn()}
      />)

    await userEvent.click(
      screen.getByRole('button', { name: /Continuar e aplicar Outros em 1/ }))

    const enviado = setManyAssignments.mock.calls[0][0]
    expect(enviado.map((a) => a.target)).toEqual(['LOJA XPTO'])
    expect(enviado[0].patch.categoria).toBe('Outros')
    // Nada para persistir (ninguém marcou lembrar) -> segue direto.
    expect(onNext).toHaveBeenCalled()
  })

  it('com tudo resolvido o botão vira só "Continuar"', () => {
    montarNovos({ atribuicoes: {
      BRASEIRO: { categoria: 'Alimentação' },
      'LOJA XPTO': { categoria: '', mark_unknown: true },
    } })
    expect(screen.getByRole('button', { name: 'Continuar' })).toBeEnabled()
  })

  it('o preenchimento em lote não vira regra no categories.yml', () => {
    // "Outros" não é conhecimento sobre o estabelecimento: vale para esta
    // fatura e não deve ser gravado como palavra-chave.
    montarNovos()
    expect(screen.getByText(/não vira regra no/)).toBeInTheDocument()
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

// ---------------------------------------------------------------- análise

describe('charts — eixo contínuo', () => {
  it('preenche os meses ausentes para o buraco aparecer', () => {
    // Sem isto, jan e abr ficam lado a lado no eixo e a linha os liga como se
    // fossem meses consecutivos — o gráfico mente sobre o tempo.
    const preenchido = eixoContinuo([
      { periodo: '2024-01', gasto: 10 },
      { periodo: '2024-04', gasto: 40 },
    ])
    expect(preenchido.map((p) => p.periodo)).toEqual([
      '2024-01', '2024-02', '2024-03', '2024-04'])
    expect(preenchido[1].gasto).toBeUndefined()
    expect(preenchido[3].gasto).toBe(40)
  })

  it('atravessa a virada de ano', () => {
    const preenchido = eixoContinuo([
      { periodo: '2023-11' }, { periodo: '2024-02' }])
    expect(preenchido.map((p) => p.periodo)).toEqual([
      '2023-11', '2023-12', '2024-01', '2024-02'])
  })

  it('não mexe numa série de um ponto só', () => {
    expect(eixoContinuo([{ periodo: '2024-01' }])).toHaveLength(1)
  })
})

describe('charts — formato curto do eixo', () => {
  it('abrevia sem perder o "R$"', () => {
    // "R$ 2.000.000" não cabe na margem e o navegador cortava o R, deixando
    // "$ 2.000.000" — outra moeda, não uma abreviação.
    expect(brlCompacto(2_000_000)).toBe('R$ 2 mi')
    expect(brlCompacto(200_000)).toBe('R$ 200 mil')
    expect(brlCompacto(0)).toBe('R$ 0')
    expect(brlCompacto(-1500)).toBe('-R$ 2 mil')
  })
})

/**
 * Resposta mínima do /analytics — só o que a tela precisa para montar.
 *
 * É uma FUNÇÃO, não um objeto: cada teste altera um pedaço com spread, e uma
 * constante compartilhada viraria estado entre testes na primeira vez que
 * alguém empurrasse num array aninhado.
 */
const RESPOSTA_BASE = () => ({
  arquivo: 'all.csv',
  resumo: {
    periodo_inicio: '2024-01', periodo_fim: '2024-06', meses_com_dado: 6,
    total_gasto: 2_179_679, media_mensal: 1000, total_receita: 0,
    total_investido: 0, gasto_no_cartao: 0, custo_fixo_mensal: 0,
    gasto_nao_detalhado: 0, meses_nao_detalhados: [],
    excluido: { artefato: 0, carregamento: 0 },
  },
  serie_mensal: [{ periodo: '2024-01', gasto: 1000, receita: 0,
                   no_cartao: 0, nao_detalhado: 0 }],
  por_categoria: [{ categoria: 'Casa', total: 1000 }],
  categoria_por_periodo: { categorias: ['Casa'], periodos: [] },
  sazonalidade: { anos: [2024], celulas: [], media_por_mes: [] },
  top_estabelecimentos: [], recorrentes: [], anomalias: [],
  variacao_recente: [], concentracao: { top_10: 0.4, mediana: 300 },
  saude: { total_lancamentos: 18, avisos: [], meses_faltando: [],
           sem_categoria: { quantidade: 0, total: 0, exemplos: [] },
           sem_data: { quantidade: 0, total: 0, exemplos: [] },
           meses_que_nao_fecham: [], total_meses_que_nao_fecham: 0,
           pares_que_se_anulam: [], dupla_contagem: [] },
  intervalo_disponivel: { inicio: '2012-01', fim: '2024-06' },
  filtro: { inicio: null, fim: null },
})

describe('AnalyticsView', () => {
  const RESPOSTA = {
    arquivo: 'all.csv',
    resumo: {
      periodo_inicio: '2024-01', periodo_fim: '2024-06', meses_com_dado: 6,
      total_gasto: 6000, media_mensal: 1000, total_receita: 12000,
      total_investido: 0, gasto_no_cartao: 500, custo_fixo_mensal: 800,
      gasto_nao_detalhado: 1200, meses_nao_detalhados: ['2024-05', '2024-06'],
      excluido: { artefato: 0, carregamento: 3000 },
    },
    serie_mensal: Array.from({ length: 6 }, (_, i) => ({
      periodo: `2024-0${i + 1}`, gasto: 1000, receita: 2000,
      no_cartao: 0, nao_detalhado: 0, carregamento: 0, investimento: 0, lancamentos: 3,
    })),
    por_categoria: [{ categoria: 'Casa', total: 4000, lancamentos: 6, media: 666,
                      share: 0.66, no_cartao: 0 }],
    categoria_por_periodo: { categorias: ['Casa'], periodos: [] },
    sazonalidade: { anos: [2024], celulas: [], media_por_mes: [] },
    top_estabelecimentos: [{ descricao: 'Aluguel', total: 4000, lancamentos: 6,
                             categoria: 'Casa' }],
    recorrentes: [{ descricao: 'Aluguel', categoria: 'Casa', meses: 6,
                    primeiro: '2024-01', ultimo: '2024-06', ativo: true,
                    mediana: 800, total: 4800, variacao: 0.2 }],
    anomalias: [],
    anual: [{ ano: 2024, gasto: 6000, receita: 12000, meses_com_dado: 6,
              media_mensal: 1000, variacao: null }],
    variacao_recente: [],
    concentracao: { lancamentos: 18, top_10: 0.4, top_50: 0.9, top_1_pct: 0.1,
                    mediana: 300 },
    saude: {
      total_lancamentos: 18, avisos: [], meses_faltando: [],
      sem_categoria: { quantidade: 0, total: 0, exemplos: [] },
      sem_data: { quantidade: 0, total: 0, exemplos: [] },
      meses_que_nao_fecham: [], total_meses_que_nao_fecham: 0,
      pares_que_se_anulam: [], dupla_contagem: [],
    },
    // O intervalo do ARQUIVO, que o backend calcula antes de recortar — é o que
    // limita o seletor de datas. `filtro` é o recorte em vigor.
    intervalo_disponivel: { inicio: '2019-01', fim: '2024-06' },
    filtro: { inicio: null, fim: null },
  }

  async function montarAnalise(resposta = RESPOSTA, onError = vi.fn()) {
    vi.resetModules()
    const analytics = vi.fn().mockResolvedValue(resposta)
    vi.doMock('../api', () => ({ analytics }))
    const { default: View } = await import('../components/AnalyticsView')
    render(<View onError={onError} />)
    const input = document.querySelector('input[type=file]')
    await userEvent.upload(input, new File(['a'], 'all.csv', { type: 'text/csv' }))
    await screen.findByText('all.csv')
    return { analytics }
  }

  it('começa pedindo o arquivo e avisa que nada é gravado', async () => {
    vi.resetModules()
    vi.doMock('../api', () => ({ analytics: vi.fn() }))
    const { default: View } = await import('../components/AnalyticsView')
    render(<View onError={vi.fn()} />)
    expect(screen.getByText(/Nada é gravado/)).toBeInTheDocument()
  })

  it('mostra o que ficou FORA da conta de gasto', async () => {
    // O número de cima tem que ser auditável: sem isto, "gasto total" é mágico
    // e ninguém sabe que R$ 3.000 de Poupança foram excluídos de propósito.
    await montarAnalise()
    expect(screen.getByText(/Fora da conta de gasto/)).toBeInTheDocument()
    expect(screen.getByText(/R\$\s*3\.000,00/)).toBeInTheDocument()
  })

  it('denuncia o gasto que está num balde genérico', async () => {
    await montarAnalise()
    const aviso = screen.getByText(/está em "Cartão de crédito"/)
    // Vírgula decimal: a interface é em português.
    expect(aviso.textContent).toContain('20,0% do gasto')
    expect(aviso.textContent).toContain('2 meses')
  })

  it('a saúde dos dados vem ANTES dos gráficos', async () => {
    // Ler um painel sem saber o que está faltando é pior do que não ler.
    await montarAnalise()
    const titulos = [...document.querySelectorAll('h2')].map((h) => h.textContent)
    expect(titulos[0]).toMatch(/Antes de acreditar/)
  })

  it('quando não há nada suspeito, diz isso', async () => {
    await montarAnalise({
      ...RESPOSTA,
      resumo: { ...RESPOSTA.resumo, gasto_nao_detalhado: 0, meses_nao_detalhados: [] },
    })
    expect(screen.getByText(/Nada suspeito/)).toBeInTheDocument()
  })

  it('grita quando a mesma compra pode estar contada duas vezes', async () => {
    await montarAnalise({
      ...RESPOSTA,
      saude: { ...RESPOSTA.saude,
               dupla_contagem: [{ periodo: '2024-03', itens: 300, fatura: 3000 }] },
    })
    expect(screen.getByText(/contada duas\s+vezes/)).toBeInTheDocument()
  })

  it('oferece a tabela — os tons claros da paleta exigem essa alternativa', async () => {
    await montarAnalise()
    const alternar = screen.getByRole('checkbox', { name: /Ver como tabela/ })
    await userEvent.click(alternar)
    expect(screen.getAllByRole('columnheader').some((c) => c.textContent === 'Mês'))
      .toBe(true)
  })

  it('mostra o custo já comprometido', async () => {
    await montarAnalise()
    expect(screen.getByText(/já está comprometido antes de qualquer decisão/))
      .toBeInTheDocument()
  })
})

describe('AnalyticsView — recorte de período fixo no topo', () => {
  const RESPOSTA = {
    arquivo: 'all.csv',
    resumo: {
      periodo_inicio: '2024-01', periodo_fim: '2024-06', meses_com_dado: 6,
      total_gasto: 6000, media_mensal: 1000, total_receita: 12000,
      total_investido: 0, gasto_no_cartao: 0, custo_fixo_mensal: 800,
      gasto_nao_detalhado: 0, meses_nao_detalhados: [],
      excluido: { artefato: 0, carregamento: 0 },
    },
    serie_mensal: [{ periodo: '2024-01', gasto: 1000, receita: 2000,
                     no_cartao: 0, nao_detalhado: 0 }],
    por_categoria: [{ categoria: 'Casa', total: 6000 }],
    categoria_por_periodo: { categorias: ['Casa'], periodos: [] },
    sazonalidade: { anos: [2024], celulas: [], media_por_mes: [] },
    top_estabelecimentos: [], recorrentes: [], anomalias: [],
    variacao_recente: [], concentracao: { top_10: 0.4, mediana: 300 },
    saude: { total_lancamentos: 18, avisos: [], meses_faltando: [],
             sem_categoria: { quantidade: 0, total: 0, exemplos: [] },
             sem_data: { quantidade: 0, total: 0, exemplos: [] },
             meses_que_nao_fecham: [], total_meses_que_nao_fecham: 0,
             pares_que_se_anulam: [], dupla_contagem: [] },
    intervalo_disponivel: { inicio: '2019-03', fim: '2024-06' },
    filtro: { inicio: null, fim: null },
  }

  async function montar(resposta = RESPOSTA, onError = vi.fn()) {
    vi.resetModules()
    const analytics = vi.fn().mockResolvedValue(resposta)
    vi.doMock('../api', () => ({ analytics }))
    const { default: View } = await import('../components/AnalyticsView')
    render(<View onError={onError} />)
    await userEvent.upload(document.querySelector('input[type=file]'),
                           new File(['a'], 'all.csv', { type: 'text/csv' }))
    await screen.findByText('all.csv')
    return { analytics }
  }

  it('conta os presets a partir do último mês COM DADO, não de hoje', async () => {
    // Um arquivo exportado em 2024 e aberto em 2026 devolveria "último ano"
    // vazio se a âncora fosse o relógio — e o clique num botão perfeitamente
    // razoável viraria um erro no lugar do gráfico.
    const { analytics } = await montar()
    await userEvent.click(screen.getByRole('button', { name: '1 ano' }))
    expect(analytics).toHaveBeenLastCalledWith(expect.any(File),
                                               { inicio: '2023-07', fim: '2024-06' })
  })

  it('pedir mais período do que o arquivo tem devolve o arquivo inteiro', async () => {
    const { analytics } = await montar()
    await userEvent.click(screen.getByRole('button', { name: '5 anos' }))
    // 60 meses antes de jun/24 seria jul/19, mas o arquivo começa em mar/19:
    // pedir 5 anos de um arquivo de 3 não é erro, é pedir tudo.
    expect(analytics).toHaveBeenLastCalledWith(expect.any(File),
                                               { inicio: '2019-07', fim: '2024-06' })
    await userEvent.click(screen.getByRole('button', { name: '2 anos' }))
    expect(analytics).toHaveBeenLastCalledWith(expect.any(File),
                                               { inicio: '2022-07', fim: '2024-06' })
  })

  it('"1 mês" é um mês, não zero', async () => {
    const { analytics } = await montar()
    await userEvent.click(screen.getByRole('button', { name: '1 mês' }))
    expect(analytics).toHaveBeenLastCalledWith(expect.any(File),
                                               { inicio: '2024-06', fim: '2024-06' })
  })

  it('"Tudo" limpa o recorte em vez de mandar as pontas do arquivo', async () => {
    // Mandar as datas exatas funcionaria, mas prende a análise ao que o arquivo
    // tinha na primeira leitura. Vazio é "sem recorte", que é o que se quer.
    const { analytics } = await montar()
    await userEvent.click(screen.getByRole('button', { name: '1 ano' }))
    await userEvent.click(screen.getByRole('button', { name: 'Tudo' }))
    expect(analytics).toHaveBeenLastCalledWith(expect.any(File),
                                               { inicio: '', fim: '' })
  })

  it('o seletor de datas não deixa pedir mês que o arquivo não cobre', async () => {
    await montar()
    const de = screen.getByLabelText('Início do período')
    const ate = screen.getByLabelText('Fim do período')
    expect(de).toHaveAttribute('min', '2019-03')
    expect(ate).toHaveAttribute('max', '2024-06')
  })

  it('o recorte vai para o SERVIDOR — é lá que tudo é recalculado', async () => {
    // Se o filtro fosse aplicado no cliente depois de agregar, a média mensal e
    // o custo fixo continuariam sendo os do arquivo inteiro ao lado de gráficos
    // do período. Este teste prova que a resposta nova substitui os números.
    vi.resetModules()
    const analytics = vi.fn()
      .mockResolvedValueOnce(RESPOSTA)
      .mockResolvedValueOnce({
        ...RESPOSTA,
        resumo: { ...RESPOSTA.resumo, media_mensal: 4242, custo_fixo_mensal: 99 },
        filtro: { inicio: '2023-07', fim: '2024-06' },
      })
    vi.doMock('../api', () => ({ analytics }))
    const { default: View } = await import('../components/AnalyticsView')
    render(<View onError={vi.fn()} />)
    await userEvent.upload(document.querySelector('input[type=file]'),
                           new File(['a'], 'all.csv', { type: 'text/csv' }))
    await screen.findByText('all.csv')
    expect(screen.getByText('R$ 1.000,00')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '1 ano' }))
    expect(await screen.findByText('R$ 4.242,00')).toBeInTheDocument()
    expect(screen.getAllByText('R$ 99,00').length).toBeGreaterThan(0)
  })

  it('recorte vazio não joga o arquivo fora', async () => {
    // O backend responde 400 dizendo o que o arquivo cobre. Voltar para a
    // dropzone obrigaria a subir o CSV de novo por causa de um clique.
    vi.resetModules()
    const onError = vi.fn()
    const analytics = vi.fn()
      .mockResolvedValueOnce(RESPOSTA)
      .mockRejectedValueOnce(new Error('nenhum lançamento entre 2024-06 e 2024-06'))
    vi.doMock('../api', () => ({ analytics }))
    const { default: View } = await import('../components/AnalyticsView')
    render(<View onError={onError} />)
    await userEvent.upload(document.querySelector('input[type=file]'),
                           new File(['a'], 'all.csv', { type: 'text/csv' }))
    await screen.findByText('all.csv')

    await userEvent.click(screen.getByRole('button', { name: '1 mês' }))
    expect(onError).toHaveBeenCalledWith(expect.stringContaining('nenhum lançamento'))
    expect(screen.getByText('all.csv')).toBeInTheDocument()
    expect(document.querySelector('.periodo-barra')).toBeInTheDocument()
  })

  it('trocar de arquivo zera o recorte', async () => {
    const { analytics } = await montar()
    await userEvent.click(screen.getByRole('button', { name: '1 ano' }))
    await userEvent.click(screen.getByRole('button', { name: 'Outro arquivo' }))
    await userEvent.upload(document.querySelector('input[type=file]'),
                           new File(['b'], 'outro.csv', { type: 'text/csv' }))
    expect(analytics).toHaveBeenLastCalledWith(expect.any(File), {})
  })
})

describe('AnalyticsView — meses excepcionais medidos contra a vizinhança', () => {
  // Uma série que cresce dez vezes em 14 anos, como a dele: R$ 1.000/mês no
  // começo, R$ 40.000/mês no fim, e UM mês de R$ 658 mil no meio do período
  // caro. Contra a mediana da série inteira, todo mês recente vira "pico" e o
  // gráfico apaga justamente o período que interessa.
  const serie = [
    ...Array.from({ length: 60 }, (_, i) => ({
      periodo: `20${12 + Math.floor(i / 12)}-${String((i % 12) + 1).padStart(2, '0')}`,
      gasto: 1000, receita: 1000, no_cartao: 0, nao_detalhado: 0,
    })),
    ...Array.from({ length: 24 }, (_, i) => ({
      periodo: `20${22 + Math.floor(i / 12)}-${String((i % 12) + 1).padStart(2, '0')}`,
      gasto: i === 12 ? 658_000 : 40_000, receita: 40_000,
      no_cartao: 0, nao_detalhado: 0,
    })),
  ]

  async function montar() {
    vi.resetModules()
    vi.doMock('../api', () => ({ analytics: vi.fn().mockResolvedValue({
      ...RESPOSTA_BASE(), serie_mensal: serie,
    }) }))
    const { default: View } = await import('../components/AnalyticsView')
    render(<View onError={vi.fn()} />)
    await userEvent.upload(document.querySelector('input[type=file]'),
                           new File(['a'], 'all.csv', { type: 'text/csv' }))
    await screen.findByText('all.csv')
  }

  it('tira o mês de R$ 658 mil e SÓ ele', async () => {
    await montar()
    expect(screen.getByText(/1 mês\(es\) excepcionais/)).toBeInTheDocument()
    expect(screen.getByText(/Fora do gráfico/).textContent)
      .toMatch(/R\$\s*658\.000,00/)
  })

  it('mês caro de uma época cara não é pico', async () => {
    // Os 23 meses de R$ 40 mil são 40x a mediana global (R$ 1.000) e rotina na
    // própria vizinhança. Medir contra a série inteira marcaria os 24.
    await montar()
    expect(screen.queryByText(/24 mês\(es\) excepcionais/)).not.toBeInTheDocument()
  })

  it('o rodapé não vira lista de vinte meses', async () => {
    vi.resetModules()
    // Uma série plana com dez picos: o rodapé antigo listava todos e ocupava
    // mais linhas do que o gráfico que ele explicava.
    const plana = Array.from({ length: 60 }, (_, i) => ({
      periodo: `20${12 + Math.floor(i / 12)}-${String((i % 12) + 1).padStart(2, '0')}`,
      gasto: i % 6 === 0 ? 500_000 : 1000, receita: 0,
      no_cartao: 0, nao_detalhado: 0,
    }))
    vi.doMock('../api', () => ({ analytics: vi.fn().mockResolvedValue({
      ...RESPOSTA_BASE(), serie_mensal: plana,
    }) }))
    const { default: View } = await import('../components/AnalyticsView')
    render(<View onError={vi.fn()} />)
    await userEvent.upload(document.querySelector('input[type=file]'),
                           new File(['a'], 'all.csv', { type: 'text/csv' }))
    await screen.findByText('all.csv')
    const rodape = screen.getByText(/Fora do gráfico/).textContent
    expect(rodape).toMatch(/e mais \d+/)
    expect(rodape.match(/R\$/g).length).toBeLessThanOrEqual(4)
  })
})

describe('AnalyticsView — tirar outlier de "Para onde vai o dinheiro"', () => {
  const COM_OUTLIER = {
    ...RESPOSTA_BASE(),
    por_categoria: [
      { categoria: 'Casa', total: 1_292_685 },
      { categoria: 'Construção', total: 359_093 },
      { categoria: 'Transporte', total: 304_125 },
      { categoria: 'Lazer', total: 137_946 },
      { categoria: 'Alimentação', total: 85_830 },
    ],
  }

  async function montar(resposta = COM_OUTLIER) {
    vi.resetModules()
    vi.doMock('../api', () => ({ analytics: vi.fn().mockResolvedValue(resposta) }))
    const { default: View } = await import('../components/AnalyticsView')
    render(<View onError={vi.fn()} />)
    await userEvent.upload(document.querySelector('input[type=file]'),
                           new File(['a'], 'all.csv', { type: 'text/csv' }))
    await screen.findByText('all.csv')
  }

  it('clicar numa barra tira a categoria do gráfico', async () => {
    await montar()
    await userEvent.click(screen.getByRole('button', { name: /^Casa —/ }))
    expect(screen.queryByRole('button', { name: /^Casa —/ })).not.toBeInTheDocument()
    // e a barra que sobrou volta a ocupar a régua inteira
    expect(screen.getByRole('button', { name: /^Construção —/ })).toBeInTheDocument()
  })

  it('as porcentagens passam a ser sobre o que sobrou', async () => {
    // Manter o denominador antigo faria as fatias visíveis somarem 22% e
    // sugerir que 78% do dinheiro sumiu do gráfico sem explicação.
    await montar()
    await userEvent.click(screen.getByRole('button', { name: /^Casa —/ }))
    expect(screen.getByText(/gasto que sobrou depois de tirar 1 categoria/))
      .toBeInTheDocument()
    // Construção agora é 359093 / 886994 = 40,5%
    expect(screen.getByText('40,5%')).toBeInTheDocument()
  })

  it('oferece tirar o que está fora de escala, nomeando quem é', async () => {
    // Casa vale 13x a mediana das mostradas; a segunda colocada vale 3,6x. O
    // corte em 4x separa as duas — um corte pela MÉDIA não separaria, porque a
    // média já vem contaminada pela própria Casa.
    await montar()
    const botao = screen.getByRole('button', { name: /fora de escala/ })
    expect(botao.textContent).toContain('Casa')
    expect(botao.textContent).not.toContain('Construção')
    await userEvent.click(botao)
    expect(screen.queryByRole('button', { name: /^Casa —/ })).not.toBeInTheDocument()
  })

  it('o que foi tirado fica visível e volta com um clique', async () => {
    await montar()
    await userEvent.click(screen.getByRole('button', { name: /^Casa —/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Trazer Casa de volta' }))
    expect(screen.getByRole('button', { name: /^Casa —/ })).toBeInTheDocument()
  })

  it('sem outlier nenhum, não fica oferecendo remoção', async () => {
    await montar({
      ...RESPOSTA_BASE(),
      por_categoria: [{ categoria: 'Casa', total: 1000 },
                      { categoria: 'Lazer', total: 900 },
                      { categoria: 'Saúde', total: 800 }],
    })
    expect(screen.queryByText(/fora de escala/)).not.toBeInTheDocument()
  })
})

describe('AnalyticsView — composição por ano ou por mês', () => {
  const doze = Array.from({ length: 12 }, (_, i) => ({
    periodo: `2024-${String(i + 1).padStart(2, '0')}`, valores: [100 + i],
  }))

  async function montar(inicio, fim, periodos = doze) {
    vi.resetModules()
    vi.doMock('../api', () => ({ analytics: vi.fn().mockResolvedValue({
      ...RESPOSTA_BASE(),
      resumo: { ...RESPOSTA_BASE().resumo, periodo_inicio: inicio, periodo_fim: fim },
      categoria_por_periodo: { categorias: ['Casa'], periodos },
    }) }))
    const { default: View } = await import('../components/AnalyticsView')
    render(<View onError={vi.fn()} />)
    await userEvent.upload(document.querySelector('input[type=file]'),
                           new File(['a'], 'all.csv', { type: 'text/csv' }))
    await screen.findByText('all.csv')
  }

  it('com dois anos ou menos na tela, o padrão é o mês', async () => {
    // Uma barra por ano quando só há dois anos não é gráfico, é uma comparação.
    await montar('2023-07', '2024-06')
    expect(screen.getByRole('heading', { name: /Composição do gasto por mês/ }))
      .toBeInTheDocument()
  })

  it('com muitos anos, o padrão é o ano', async () => {
    await montar('2012-01', '2024-06')
    expect(screen.getByRole('heading', { name: /Composição do gasto por ano/ }))
      .toBeInTheDocument()
  })

  it('dá para discordar do padrão nos dois sentidos', async () => {
    await montar('2012-01', '2024-06')
    await userEvent.click(screen.getByRole('button', { name: 'Por mês' }))
    expect(screen.getByRole('heading', { name: /por mês/ })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Por ano' }))
    expect(screen.getByRole('heading', { name: /por ano/ })).toBeInTheDocument()
  })

  it('o mês excepcional sai da pilha por mês, mas não da pilha por ano', async () => {
    // Por mês, um mês de R$ 658 mil faz os outros 23 virarem um risco no chão.
    // Por ano ele se dilui no total — tirar seria mentir sobre quanto o ano
    // custou, e o eixo aguenta.
    vi.resetModules()
    const serie = Array.from({ length: 24 }, (_, i) => ({
      periodo: `20${24 + Math.floor(i / 12)}-${String((i % 12) + 1).padStart(2, '0')}`,
      gasto: i === 6 ? 658_000 : 10_000, receita: 0, no_cartao: 0, nao_detalhado: 0,
    }))
    vi.doMock('../api', () => ({ analytics: vi.fn().mockResolvedValue({
      ...RESPOSTA_BASE(),
      resumo: { ...RESPOSTA_BASE().resumo, periodo_inicio: '2024-01', periodo_fim: '2025-12' },
      serie_mensal: serie,
      categoria_por_periodo: {
        categorias: ['Casa'],
        periodos: serie.map((m) => ({ periodo: m.periodo, valores: [m.gasto] })),
      },
    }) }))
    const { default: View } = await import('../components/AnalyticsView')
    render(<View onError={vi.fn()} />)
    await userEvent.upload(document.querySelector('input[type=file]'),
                           new File(['a'], 'all.csv', { type: 'text/csv' }))
    await screen.findByText('all.csv')

    await userEvent.click(screen.getByRole('checkbox', { name: /Ver como tabela/ }))
    // Só a tabela da COMPOSIÇÃO: a de "gasto e receita, mês a mês" continua
    // listando o mês excepcional de propósito — ele sai do gráfico, não do dado.
    const composicao = () => screen.getByRole('heading', { name: /Composição/ })
      .closest('section')
    const linhas = () => [...composicao().querySelectorAll('tbody tr')]
      .map((tr) => [...tr.querySelectorAll('td')].map((td) => td.textContent))

    expect(linhas().map((l) => l[0])).not.toContain('jul/24')
    expect(screen.getByText(/mês\(es\) excepcionais estão fora daqui/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Por ano' }))
    const anos = linhas()
    // 11 meses de R$ 10 mil + o de R$ 658 mil = R$ 768 mil em 2024.
    expect(anos.some((l) => /768\.000,00/.test(l[1] || ''))).toBe(true)
  })

  it('a soma por ano é a soma dos meses daquele ano', async () => {
    await montar('2012-01', '2024-06', [
      { periodo: '2023-11', valores: [10] },
      { periodo: '2023-12', valores: [20] },
      { periodo: '2024-01', valores: [5] },
    ])
    await userEvent.click(screen.getByRole('checkbox', { name: /Ver como tabela/ }))
    const linhas = [...document.querySelectorAll('table')]
      .flatMap((t) => [...t.querySelectorAll('tbody tr')])
      //  : o Intl em pt-BR separa "R$" do número com espaço INQUEBRÁVEL,
      // então comparar com um espaço comum falha por um caractere invisível.
      .map((tr) => [...tr.querySelectorAll('td')]
        .map((td) => td.textContent.replace(/ /g, ' ')))
    expect(linhas).toContainEqual(['2023', 'R$ 30,00'])
    expect(linhas).toContainEqual(['2024', 'R$ 5,00'])
  })
})

describe('Residuos — os meses que não fecham', () => {
  // A forma real do arquivo dele: 69 meses não fecham, mas a mediana do resíduo
  // é R$ 117 e SETE meses concentram tudo. Listar os 69 juntos é o que fazia o
  // painel ser ignorado.
  const MESES = [
    { periodo: '2021-06', saldo: -72139.94, tipo: 'falta_resgate',
      receita: 5000, gasto: 77139.94, poupanca: 0, resgate: 0 },
    { periodo: '2026-08', saldo: 55328.10, tipo: 'sobra_sem_destino',
      receita: 60000, gasto: 4671.90, poupanca: 0, resgate: 0 },
    { periodo: '2019-12', saldo: -18000, tipo: 'falta_resgate',
      receita: 4000, gasto: 22000, poupanca: 0, resgate: 0 },
    { periodo: '2014-11', saldo: 117.42, tipo: 'sobra_sem_destino',
      receita: 3000, gasto: 2882.58, poupanca: 0, resgate: 0 },
    { periodo: '2015-03', saldo: -12.10, tipo: 'falta_resgate',
      receita: 3000, gasto: 3012.10, poupanca: 0, resgate: 0 },
  ]
  const SAUDE = { meses_que_nao_fecham: MESES, total_meses_que_nao_fecham: 5,
                  pares_que_se_anulam: [] }

  it('separa o arredondamento do que é dinheiro de verdade', async () => {
    render(<Residuos saude={SAUDE} />)
    // Dois meses abaixo de R$ 500 saem do gráfico por padrão...
    expect(screen.getByText('Arredondamento').nextSibling.textContent).toBe('2')
    // ...e os grandes ficam, com o sinal separando os dois problemas.
    expect(screen.getByText('Meses sem resgate').nextSibling.textContent).toBe('2')
    expect(screen.getByText('Meses com sobra solta').nextSibling.textContent).toBe('1')
  })

  it('o corte é ajustável — quem quer ver centavo, vê', async () => {
    render(<Residuos saude={SAUDE} />)
    // A linha do acumulado cobre todos os meses (é o ponto dela), então a
    // pergunta certa não é "nov/14 aparece?" e sim "nov/14 virou trabalho?".
    const trabalho = /uma "Poupança" de R\$\s*117,42/
    expect(screen.queryByText(trabalho)).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Tudo' }))
    expect(screen.getByText(trabalho)).toBeInTheDocument()
  })

  it('o líquido soma TODOS os meses, inclusive os abaixo do corte', async () => {
    // O corte é de leitura, não de contabilidade: esconder centavos do gráfico
    // não pode mudar quanto dinheiro está sem explicação.
    render(<Residuos saude={SAUDE} />)
    // -72139,94 + 55328,10 - 18000 + 117,42 - 12,10 = -34706,52
    expect(screen.getByText('-R$ 34.706,52')).toBeInTheDocument()
  })

  it('diz o que fazer com cada mês, em vez de só apontar', async () => {
    render(<Residuos saude={SAUDE} />)
    expect(screen.getByText(/um "Resgate da poupança" de R\$\s*72\.139,94/))
      .toBeInTheDocument()
    expect(screen.getByText(/uma "Poupança" de R\$\s*55\.328,10/)).toBeInTheDocument()
  })

  it('aponta os pares que se cancelam como UM erro, não dois', async () => {
    render(<Residuos saude={{ ...SAUDE,
      pares_que_se_anulam: [{ a: '2024-01', b: '2024-02', valor: 45 }] }} />)
    expect(screen.getByText(/lançamento no mês\s+errado/)).toBeInTheDocument()
    expect(screen.getByText(/jan\/24 ↔ fev\/24/)).toBeInTheDocument()
  })

  it('some quando não há nada para corrigir', () => {
    const { container } = render(
      <Residuos saude={{ meses_que_nao_fecham: [], pares_que_se_anulam: [] }} />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('charts — domínio que desce abaixo do zero', () => {
  it('desenha a parte negativa em vez de achatá-la na linha do zero', () => {
    // O resíduo acumulado dele chega a -R$ 103 mil. Com o piso preso no zero,
    // `y` mapeava só [0, teto] e a curva inteira ficava colada no eixo.
    render(<LineChart pontos={[{ periodo: '2024-01', v: 1000 },
                               { periodo: '2024-02', v: -100000 }]}
                      series={[{ chave: 'v', rotulo: 'Acumulado' }]} />)
    const rotulos = [...document.querySelectorAll('text')].map((t) => t.textContent)
    expect(rotulos.some((r) => r.startsWith('-R$'))).toBe(true)
  })

  it('série toda positiva continua ancorada no zero', () => {
    render(<LineChart pontos={[{ periodo: '2024-01', v: 500 },
                               { periodo: '2024-02', v: 1000 }]}
                      series={[{ chave: 'v', rotulo: 'Gasto' }]} />)
    const rotulos = [...document.querySelectorAll('text')].map((t) => t.textContent)
    expect(rotulos).toContain('R$ 0')
    expect(rotulos.some((r) => r.startsWith('-'))).toBe(false)
  })
})

describe('charts — passo redondo do eixo', () => {
  it('põe as marcas em múltiplos do passo, inclusive abaixo do zero', () => {
    // Dividir [-103 mil, 10 mil] em quatro dava marcas em -148 mil e -95 mil:
    // números que ninguém lê. O que precisa ser redondo é o PASSO.
    const { marcas } = escala(-103736, 10000)
    expect(marcas).toContain(0)
    const passos = marcas.slice(1).map((v, i) => v - marcas[i])
    expect(new Set(passos.map((p) => Math.round(p))).size).toBe(1)
    expect(marcas.every((v) => Number.isInteger(v / passos[0]))).toBe(true)
  })

  it('não desperdiça metade do gráfico arredondando para a casa de cima', () => {
    // R$ 103 mil virava um teto de R$ 200 mil e a curva ficava espremida
    // na metade de baixo do quadro.
    const { teto } = escala(0, 103736)
    expect(teto).toBeLessThan(150000)
    expect(teto).toBeGreaterThanOrEqual(103736)
  })

  it('série toda positiva não ganha piso negativo', () => {
    expect(escala(500, 1000).piso).toBe(0)
  })
})

describe('charts — barras divergentes', () => {
  it('o sinal decide o lado e a cor, não o rótulo', () => {
    const { container } = render(<BarrasDivergentes itens={[
      { rotulo: 'jun/21', valor: -72139.94 },
      { rotulo: 'ago/26', valor: 55328.10 },
    ]} />)
    const [neg, pos] = [...container.querySelectorAll('.viz-barra-preenche')]
    // Negativo cresce para a esquerda a partir do meio; positivo, para a direita.
    expect(neg.style.right).toBe('50%')
    expect(pos.style.left).toBe('50%')
    // Laranja/azul, NÃO vermelho/verde: rodado no validador, o par vermelho e
    // verde dá ΔE 4,1 em deuteranopia — os dois lados viram o mesmo tom para
    // 1 homem em 12, e num gráfico cujo sinal é a informação inteira isso
    // apaga a informação.
    expect(neg.style.background).toMatch(/235, 104, 52|#eb6834/)
    expect(pos.style.background).toMatch(/42, 120, 214|#2a78d6/)
  })
})

describe('charts — rótulos da barra empilhada', () => {
  it('pula rótulos quando há meses demais para caber', () => {
    const periodos = Array.from({ length: 60 }, (_, i) => ({
      periodo: `20${20 + Math.floor(i / 12)}-${String((i % 12) + 1).padStart(2, '0')}`,
      valores: [100],
    }))
    const { container } = render(
      <BarrasEmpilhadas periodos={periodos} categorias={['Casa']} />)
    // 60 rótulos de 7 caracteres em 616px de plot viram borrão. No máximo 16.
    const rotulos = [...container.querySelectorAll('svg text')]
      .filter((t) => /^\d{4}-\d{2}$/.test(t.textContent))
    expect(rotulos.length).toBeLessThanOrEqual(16)
    expect(rotulos.length).toBeGreaterThan(0)
  })

  it('com poucos anos, mostra todos', () => {
    const { container } = render(<BarrasEmpilhadas categorias={['Casa']} periodos={[
      { periodo: '2023', valores: [100] }, { periodo: '2024', valores: [200] }]} />)
    const rotulos = [...container.querySelectorAll('svg text')]
      .filter((t) => /^\d{4}$/.test(t.textContent))
    expect(rotulos.map((t) => t.textContent)).toEqual(['2023', '2024'])
  })
})
