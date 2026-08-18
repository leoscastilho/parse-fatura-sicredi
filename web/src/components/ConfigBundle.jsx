import { useRef, useState } from 'react'
import * as api from '../api'

/**
 * Pacote de configuração — o que torna o portal portátil.
 *
 * Baixa `categories.yml`, `output.yml` e os perfis de banco num .zip; sobe de
 * volta o mesmo pacote. É o caminho para outra pessoa usar isto com as regras
 * dela sem que o servidor guarde nada: as regras são um arquivo, não uma
 * tabela num banco de dados.
 *
 * O import valida TUDO antes de gravar QUALQUER coisa — um perfil quebrado no
 * pacote não pode deixar metade da config nova e metade da antiga.
 */
export default function ConfigBundle({ onError, onImported }) {
  const [previa, setPrevia] = useState(null)
  const [arquivo, setArquivo] = useState(null)
  const [busy, setBusy] = useState(false)
  const inputRef = useRef(null)

  async function inspecionar(file) {
    if (!file) return
    setArquivo(file)
    setPrevia(null)
    setBusy(true)
    try {
      setPrevia((await api.importConfig(file, true)).conteudo)
    } catch (e) {
      onError(e.message)
      setArquivo(null)
    } finally {
      setBusy(false)
    }
  }

  async function aplicar() {
    setBusy(true)
    try {
      await api.importConfig(arquivo, false)
      setPrevia(null)
      setArquivo(null)
      if (inputRef.current) inputRef.current.value = ''
      onImported?.()
    } catch (e) {
      onError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <section className="card">
        <h2>Exportar configuração</h2>
        <p className="muted">
          Um .zip com as regras de categoria, o formato de saída e todos os
          perfis de banco. É o backup — e é o que outra pessoa precisaria para
          rodar isto com as regras dela.
        </p>
        <button className="primary" onClick={() => api.exportConfig().catch((e) => onError(e.message))}>
          Baixar .zip
        </button>
      </section>

      <section className="card">
        <h2>Importar configuração</h2>
        <p className="muted">
          O pacote é conferido inteiro antes de qualquer gravação. Você vê o que
          tem dentro e confirma.
        </p>

        <input ref={inputRef} type="file" accept=".zip"
               onChange={(e) => inspecionar(e.target.files?.[0])} />

        {previa && (
          <>
            <div className="alert ok">
              <strong>Pacote válido.</strong> Nada foi gravado ainda.
            </div>
            <table className="grid compact">
              <tbody>
                <tr>
                  <td>categories.yml</td>
                  <td>
                    {previa['categories.yml'].categorias} categorias,{' '}
                    {previa['categories.yml'].palavras} palavras-chave,{' '}
                    {previa['categories.yml'].regras} regras regex
                  </td>
                </tr>
                {previa['output.yml']?.colunas && (
                  <tr>
                    <td>output.yml</td>
                    <td>{previa['output.yml'].colunas.join(', ')}</td>
                  </tr>
                )}
                {Object.entries(previa.banks || {}).map(([id, b]) => (
                  <tr key={id}>
                    <td>banks/{id}.yml</td>
                    <td>
                      {b.nome} · {b.estrategia}
                      {b.validado ? '' : ' · placeholder'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <button className="primary" disabled={busy} onClick={aplicar}>
              {busy ? 'Aplicando…' : 'Substituir a configuração atual'}
            </button>
          </>
        )}
      </section>
    </>
  )
}
