// Cliente HTTP. Sempre caminho relativo sob /api — em dev o Vite faz proxy,
// em produção o nginx faz. O front nunca sabe o host do backend, então a mesma
// imagem sobe em qualquer lugar sem rebuild.

const BASE = '/api'

async function request(path, options = {}) {
  const response = await fetch(`${BASE}${path}`, options)
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      if (body.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* resposta sem corpo JSON */
    }
    throw new Error(detail)
  }
  return response
}

async function json(path, options) {
  return (await request(path, options)).json()
}

const asJson = (body) => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const getCategories = () => json('/categories')

export const getRules = () => json('/rules')

export const editRules = (operations, commit = false) =>
  json('/rules/edit', asJson({ operations, commit }))

// `senha` é a senha DO ARQUIVO, não do portal — o BTG manda a fatura cifrada.
// Ela sobe no formulário, decifra no servidor e morre ali: não fica guardada
// aqui, não volta em resposta nenhuma e não entra no banco da transação.
export function upload(files, vencimento = '', titulares = '', senha = '') {
  const form = new FormData()
  for (const file of files) form.append('files', file)
  form.append('vencimento', vencimento)
  // `Nome Completo=Rótulo`, uma linha por pessoa. Lado direito vazio = sou eu,
  // e a linha não recebe marca nenhuma.
  form.append('titulares', titulares)
  form.append('senha', senha)
  return json('/upload', { method: 'POST', body: form })
}

// Pré-voo: de quando a quando vão as COMPRAS deste lote, e nada além disso.
// Não abre transação e não grava nada — serve para a pergunta "viajou neste
// período?" poder nomear as datas antes do processamento, e para descobrir que
// um arquivo do lote está cifrado antes de tentar processá-lo.
export function uploadPeriodo(files, vencimento = '', senha = '') {
  const form = new FormData()
  for (const file of files) form.append('files', file)
  form.append('vencimento', vencimento)
  form.append('senha', senha)
  return json('/upload/periodo', { method: 'POST', body: form })
}

// Recategorizar CSVs que já saíram daqui: mesma revisão, origem diferente.
export function recategorize(files) {
  const form = new FormData()
  for (const file of files) form.append('files', file)
  return json('/recategorize', { method: 'POST', body: form })
}

// --- análise do histórico ---------------------------------------------------

// Sem transaction_id: é leitura pura, nada fica guardado do outro lado.
export function analytics(files, {
  inicio = '', fim = '', semCategorias = [], semLinhas = [], semTitulares = [],
} = {}) {
  const form = new FormData()
  // Vários arquivos são de PESSOAS diferentes (a análise do casal): nada é
  // deduplicado, duas linhas idênticas em arquivos diferentes são dois gastos.
  for (const file of [].concat(files)) form.append('files', file)
  // Recorte e exclusões vão para o servidor porque TODA métrica é recalculada
  // sobre eles. Filtrar no cliente, depois de agregar, daria média mensal e
  // custo fixo do arquivo inteiro ao lado de gráficos do período — números que
  // não batem.
  form.append('inicio', inicio)
  form.append('fim', fim)
  // Uma por linha, não separadas por vírgula: nome de categoria e descrição de
  // lançamento têm vírgula com frequência, e o separador não pode estar no dado.
  form.append('sem_categorias', semCategorias.join('\n'))
  form.append('sem_linhas', semLinhas.join('\n'))
  // O balde "sem marca" viaja como `<sem marca>`: linha vazia seria descartada
  // junto com o espaço em branco, e é justamente esse balde que precisa sair
  // para isolar uma pessoa.
  form.append('sem_titulares', semTitulares.join('\n'))
  return json('/analytics', { method: 'POST', body: form })
}

// --- configuração (bancos e formato de saída) ------------------------------
//
// O formato de ENTRADA não se edita: ele descreve como o banco exporta, que é
// fato do banco, não preferência de quem usa. O de SAÍDA descreve a planilha
// de quem usa, e por isso continua aqui.

export const getConfig = () => json('/config')
export const saveOutput = (yaml_text) =>
  json('/config/output', asJson({ yaml_text }))

// --- regras ordenadas (regex) ---------------------------------------------

export const getRegexRules = () => json('/rules/regex')

export const editRegexRules = (operations, commit = false) =>
  json('/rules/regex', asJson({ operations, commit }))

export const testRegex = (padrao, amostras) =>
  json('/rules/regex/test', asJson({ padrao, amostras }))

// --- pacote de configuração ------------------------------------------------

export async function exportConfig() {
  const response = await request('/config/export')
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = (response.headers.get('content-disposition') || '')
    .match(/filename="([^"]+)"/)?.[1] || 'config.zip'
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

export function importConfig(file, dryRun = false) {
  const form = new FormData()
  form.append('file', file)
  form.append('dry_run', String(dryRun))
  return json('/config/import', { method: 'POST', body: form })
}

export const validate = (transaction_id, assignments) =>
  json('/validate', asJson({ transaction_id, assignments }))

export const updateMapping = (transaction_id, assignments, commit_now = false) =>
  json('/update-mapping', asJson({ transaction_id, assignments, commit_now }))

// --- viagens ---------------------------------------------------------------

// Substitutivo: a lista enviada VIRA a lista de períodos. Remover um período é
// mandar a lista sem ele — não existe DELETE aqui.
// `pinned` (`line_id -> chave do período`) segue a mesma regra: o mapa enviado
// VIRA o mapa guardado, então despendurar uma linha é mandar o mapa sem ela.
export const travel = (transaction_id, ranges, pinned = {}) =>
  json('/travel', asJson({ transaction_id, ranges, pinned }))

export const preview = (transaction_id, assignments, travel_rejected = []) =>
  json('/preview', asJson({ transaction_id, assignments, travel_rejected }))

// O /export devolve bytes, não JSON: monta um object URL e dispara o download.
export async function exportCsv(
  transaction_id, assignments, commit_mapping = true, travel_rejected = [],
) {
  const response = await request('/export', asJson({
    transaction_id, assignments, commit_mapping, travel_rejected,
  }))

  const disposition = response.headers.get('content-disposition') || ''
  const filename = (disposition.match(/filename="([^"]+)"/) || [])[1] || 'fatura.csv'

  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)

  return { filename, commit: response.headers.get('x-mapping-commit') || null }
}
