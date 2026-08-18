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

  it('avisa que continuar deixa as linhas em branco, sem preencher nada', () => {
    montarMarketplace()
    expect(screen.getByText(/SEM categoria/)).toBeInTheDocument()
    expect(screen.getByText(/Nada é preenchido automaticamente/)).toBeInTheDocument()
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
