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

export function upload(files, banco = '', vencimento = '') {
  const form = new FormData()
  for (const file of files) form.append('files', file)
  form.append('banco', banco)
  form.append('vencimento', vencimento)
  return json('/upload', { method: 'POST', body: form })
}

// --- configuração (bancos, formato de entrada e saída) ---------------------

export const getConfig = () => json('/config')
export const getBank = (id) => json(`/config/bank/${encodeURIComponent(id)}`)

export const saveBank = (id, yaml_text) =>
  json('/config/bank', asJson({ id, yaml_text }))

export const saveOutput = (yaml_text) =>
  json('/config/output', asJson({ yaml_text }))

export function testBank(bank_id, file, { yaml_text = '', vencimento = '' } = {}) {
  const form = new FormData()
  form.append('bank_id', bank_id)
  form.append('yaml_text', yaml_text)
  form.append('vencimento', vencimento)
  form.append('file', file)
  return json('/config/bank/test', { method: 'POST', body: form })
}

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

export const preview = (transaction_id, assignments) =>
  json('/preview', asJson({ transaction_id, assignments }))

// O /export devolve bytes, não JSON: monta um object URL e dispara o download.
export async function exportCsv(transaction_id, assignments, commit_mapping = true) {
  const response = await request('/export', asJson({ transaction_id, assignments, commit_mapping }))

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
