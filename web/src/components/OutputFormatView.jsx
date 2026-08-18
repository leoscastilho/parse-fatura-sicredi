import { useEffect, useState } from 'react'
import * as api from '../api'

/**
 * Formato de SAÍDA — compartilhado por todos os bancos.
 *
 * O destino é sempre a mesma planilha, então este arquivo não é por banco. A
 * pré-visualização mostra uma linha montada com o schema atual: dá para ver o
 * efeito de mudar o modelo da descrição sem processar uma fatura.
 */
export default function OutputFormatView({ onError }) {
  const [yamlText, setYamlText] = useState('')
  const [original, setOriginal] = useState('')
  const [exemplo, setExemplo] = useState(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState(null)

  useEffect(() => { load() }, [])

  async function load() {
    try {
      const cfg = await api.getConfig()
      setYamlText(cfg.output_yaml)
      setOriginal(cfg.output_yaml)
      setExemplo(cfg.output_exemplo)
    } catch (e) { onError(e.message) }
  }

  const alterado = yamlText !== original

  async function salvar() {
    setBusy(true)
    try {
      await api.saveOutput(yamlText)
      setOriginal(yamlText)
      setNote('Formato salvo.')
      await load()
    } catch (e) {
      onError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <section className="card">
        <h2>Formato de saída <span className="count">compartilhado</span></h2>
        <p className="muted">
          Vale para todos os bancos: o CSV vai sempre para a mesma planilha.
          Mudou a planilha? Muda aqui — não no código.
        </p>

        <textarea
          className="yaml-editor"
          spellCheck={false}
          value={yamlText}
          onChange={(e) => setYamlText(e.target.value)}
          rows={22}
        />

        <div className="toolbar">
          <button className="ghost" disabled={!alterado || busy} onClick={salvar}>
            {busy ? 'Salvando…' : 'Salvar formato'}
          </button>
          {alterado && (
            <button className="link" onClick={() => setYamlText(original)}>
              descartar alterações
            </button>
          )}
          <div className="grow" />
          {note && <span className="muted small">{note}</span>}
        </div>
      </section>

      {exemplo && (
        <section className="card">
          <h2>Como fica uma linha</h2>
          <p className="muted">
            Montada com o formato salvo, a partir de uma compra fictícia de
            R$ 270,51 no Supermercados Alvora em 15/jul, parcela 3/5.
          </p>
          <table className="grid compact">
            <thead>
              <tr>{Object.keys(exemplo).map((k) => <th key={k}>{k}</th>)}</tr>
            </thead>
            <tbody>
              <tr>{Object.entries(exemplo).map(([k, v]) => <td key={k}>{v}</td>)}</tr>
            </tbody>
          </table>
        </section>
      )}
    </>
  )
}
