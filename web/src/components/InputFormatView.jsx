import { useEffect, useState } from 'react'
import * as api from '../api'

/**
 * Formato de ENTRADA — configurado por banco.
 *
 * O que muda entre bancos é só isto: onde está a data de vencimento, como se
 * chamam as colunas, como o número é escrito. Tudo isso é dado, não código —
 * por isso a tela edita o YAML do perfil direto.
 *
 * O botão "testar com um arquivo" é a parte que importa: um perfil pode estar
 * perfeitamente bem-formado e ainda assim apontar para uma coluna que não
 * existe. A única forma honesta de saber é rodar contra uma fatura real, e o
 * teste não grava nada.
 */
export default function InputFormatView({ bankId, banks, onError, onBanksChanged }) {
  const [yamlText, setYamlText] = useState('')
  const [original, setOriginal] = useState('')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState(null)
  const [resultado, setResultado] = useState(null)
  const [vencimento, setVencimento] = useState('')

  const banco = banks.find((b) => b.id === bankId)

  useEffect(() => {
    if (!bankId) return
    setResultado(null)
    setNote(null)
    api.getBank(bankId)
      .then((d) => { setYamlText(d.yaml_text); setOriginal(d.yaml_text) })
      .catch((e) => onError(e.message))
  }, [bankId, onError])

  const alterado = yamlText !== original

  async function salvar() {
    setBusy(true)
    try {
      await api.saveBank(bankId, yamlText)
      setOriginal(yamlText)
      setNote('Perfil salvo.')
      onBanksChanged?.()
    } catch (e) {
      onError(e.message)
    } finally {
      setBusy(false)
    }
  }

  async function testar(file) {
    if (!file) return
    setBusy(true)
    setResultado(null)
    try {
      // Manda o YAML da tela, não o do disco: dá para testar antes de salvar.
      setResultado(await api.testBank(bankId, file, { yaml_text: yamlText, vencimento }))
    } catch (e) {
      onError(e.message)
    } finally {
      setBusy(false)
    }
  }

  if (!banco) return <section className="card"><p>Escolha um banco na barra de cima.</p></section>

  return (
    <>
      <section className="card">
        <h2>
          Formato de entrada — {banco.nome}{' '}
          <span className="count">{banco.estrategia}</span>
        </h2>
        <p className="muted">
          Aceita {banco.extensoes.join(', ')}.
          {banco.pede_vencimento
            ? ' Este banco não traz a data de vencimento no arquivo, então o portal pergunta no upload.'
            : ' A data de vencimento é lida do próprio arquivo.'}
        </p>

        {!banco.validado && (
          <div className="alert warn">
            <strong>Perfil não validado.</strong> Foi escrito sem uma fatura real
            na mão. Teste com um arquivo aqui embaixo, confira os totais e mude{' '}
            <code>validado: true</code> no YAML quando estiver convencido.
          </div>
        )}

        <textarea
          className="yaml-editor"
          spellCheck={false}
          value={yamlText}
          onChange={(e) => setYamlText(e.target.value)}
          rows={22}
        />

        <div className="toolbar">
          <button className="ghost" disabled={!alterado || busy} onClick={salvar}>
            {busy ? 'Salvando…' : 'Salvar perfil'}
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

      <section className="card">
        <h2>Testar com um arquivo</h2>
        <p className="muted">
          Roda o perfil da caixa acima (mesmo sem salvar) contra um extrato de
          verdade. Nada é gravado.
        </p>

        <div className="toolbar">
          {banco.pede_vencimento && (
            <label className="small">
              Vencimento{' '}
              <input type="date" value={vencimento}
                     onChange={(e) => setVencimento(e.target.value)} />
            </label>
          )}
          <input
            type="file"
            accept={banco.extensoes.join(',')}
            onChange={(e) => testar(e.target.files?.[0])}
          />
        </div>

        {resultado && (
          <>
            <div className={`alert ${resultado.confere ? 'ok' : 'warn'}`}>
              <strong>{resultado.lancamentos} lançamento(s) lidos.</strong>{' '}
              Débitos R$ {resultado.debitos.toFixed(2)}
              {resultado.declarado_debitos
                ? ` (a fatura declara R$ ${resultado.declarado_debitos.toFixed(2)})`
                : ' — este formato não declara totais, então não há o que conferir'}
              {resultado.vencimento && ` · vencimento ${resultado.vencimento}`}
              {!resultado.confere && ' — NÃO BATE, algum lançamento não foi interpretado'}
            </div>

            {resultado.amostra.length > 0 && (
              <table className="grid compact">
                <thead>
                  <tr><th>Descrição gerada</th><th>Categoria</th><th className="right">Valor</th></tr>
                </thead>
                <tbody>
                  {resultado.amostra.map((row) => (
                    <tr key={row.line_id}>
                      <td>{row.descricao}</td>
                      <td>{row.categoria || <span className="muted">—</span>}</td>
                      <td className="right money">{row.valor.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        )}
      </section>
    </>
  )
}
