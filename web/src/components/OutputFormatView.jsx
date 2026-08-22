import { useEffect, useState } from 'react'
import * as api from '../api'

/**
 * Formato de saída — PAINEL, não editor.
 *
 * Havia aqui um `<textarea>` com o YAML e um botão de salvar. Ele criava uma
 * segunda verdade sobre o mesmo `output.yml`: bastava editar o arquivo com esta
 * tela aberta para o "Salvar" devolver o formato antigo por cima do novo, sem
 * nada avisando. Quem escreve o formato é o arquivo; esta tela conta o que ele
 * diz.
 *
 * E conta derivando: todo nome de coluna, toda forma de marca e o exemplo vêm
 * do `output_doc`, que o backend monta a partir do schema em vigor e dos mesmos
 * templates que escrevem a descrição. Um painel com o formato redigitado à mão
 * mentiria na primeira mudança — que é como quase toda documentação morre.
 */

// As quatro famílias de delimitador, e o que cada uma quer dizer. É a chave de
// leitura da coluna Descrição: sabendo isto, uma linha da planilha se lê sem
// consultar nada.
const FAMILIAS = [
  { d: '[ ]', t: 'de onde veio', x: 'a etiqueta fixa e, num lote misto, o banco' },
  { d: '( )', t: 'complemento do lançamento', x: 'a parcela e, em viagem, a categoria real' },
  { d: '{ }', t: 'marca do portal', x: 'a data da compra e o nome da viagem' },
  { d: '< >', t: 'pessoa', x: 'quem passou o cartão, numa conta conjunta' },
]

/**
 * A qual das quatro famílias a marca pertence — pelo delimitador que ela ABRE.
 *
 * O estabelecimento não tem delimitador nenhum, e cai no `txt`: pintá-lo com a
 * cor de outra família (era o que acontecia quando o padrão era `< >`) faria a
 * legenda ali em cima mentir sobre o próprio exemplo logo abaixo dela.
 */
const familia = (forma) => (
  { '[': 'col', '(': 'par', '{': 'cha', '<': 'ang' }[forma[0]] || 'txt')

/**
 * Parte a descrição de exemplo nos pedaços que as marcas ocupam.
 *
 * Serve só para pintar: a busca é pelas formas que o BACKEND mandou, então o
 * destaque acompanha o formato em vigor em vez de depender de um regex escrito
 * aqui — que seria a quinta cópia da mesma regra.
 */
export function anotar(texto, marcas) {
  const formas = marcas
    .map((m) => m.forma)
    .filter((f) => f && texto.includes(f))
    // Da maior para a menor: "[Cartão-BTG]" contém "[Cartão]", e casar a menor
    // primeiro partiria a maior no meio.
    .sort((a, b) => b.length - a.length)

  let pedacos = [{ txt: texto, marca: null }]
  for (const forma of formas) {
    const proximo = []
    for (const p of pedacos) {
      if (p.marca || !p.txt.includes(forma)) { proximo.push(p); continue }
      const i = p.txt.indexOf(forma)
      if (i > 0) proximo.push({ txt: p.txt.slice(0, i), marca: null })
      proximo.push({ txt: forma, marca: forma })
      const resto = p.txt.slice(i + forma.length)
      if (resto) proximo.push({ txt: resto, marca: null })
    }
    pedacos = proximo
  }
  return pedacos
}

export default function OutputFormatView({ onError }) {
  const [cfg, setCfg] = useState(null)
  const [verYaml, setVerYaml] = useState(false)

  useEffect(() => {
    (async () => {
      try { setCfg(await api.getConfig()) } catch (e) { onError(e.message) }
    })()
  }, [])

  if (!cfg?.output_doc) return <section className="card"><p className="muted">Carregando…</p></section>
  const doc = cfg.output_doc

  return (
    <>
      <section className="card">
        <h2>Formato de saída <span className="count">só leitura</span></h2>
        <p className="muted">
          O CSV que sai daqui vai sempre para a mesma planilha, então o formato é
          um só, para todos os bancos. Ele é definido no arquivo{' '}
          <code>{doc.caminho || 'config/output.yml'}</code> — esta tela mostra o
          que está valendo agora.
        </p>
      </section>

      {/* ------------------------------------------------------- as colunas */}
      <section className="card">
        <h2>As colunas <span className="count">{doc.colunas.length}</span></h2>
        <p className="muted small">
          Nesta ordem, e com estes nomes. Renomear uma coluna no arquivo renomeia
          aqui: o que o portal conhece é o PAPEL de cada uma, não o nome.
        </p>
        <div className="rolagem">
          <table className="grid compact">
            <thead>
              <tr>
                <th>Coluna</th><th>Papel</th><th>O que vai nela</th>
                <th>Tipo</th><th>Exemplo</th>
              </tr>
            </thead>
            <tbody>
              {doc.colunas.map((c) => (
                <tr key={c.papel}>
                  <td><strong>{c.nome}</strong></td>
                  <td><code className="papel">{c.papel}</code></td>
                  <td className="muted">{c.conteudo}</td>
                  <td className="small mono">{c.tipo}</td>
                  <td className="small">{c.exemplo}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* ----------------------------------------------------- a descrição */}
      <section className="card">
        <h2>Como a Descrição é montada</h2>
        <p className="muted small">
          Cada pedaço tem um delimitador próprio, e é por ele que o portal
          reencontra a informação quando o CSV volta pela Recategorização. Por
          isso a forma importa: mudar o delimitador de um pedaço é mudar o que o
          portal consegue reler.
        </p>

        <div className="familias">
          {FAMILIAS.map((f) => (
            <div className="familia" key={f.d}>
              <span className="delim">{f.d}</span>
              <span><strong>{f.t}</strong><br /><span className="muted small">{f.x}</span></span>
            </div>
          ))}
        </div>

        <h3 className="small">Uma linha, marcada</h3>
        <p className="exemplo-anotado">
          {anotar(doc.exemplo_descricao, doc.marcas).map((p, i) => (
            p.marca
              ? <mark key={i} className={`m-${familia(p.marca)}`}>{p.txt}</mark>
              : <span key={i}>{p.txt}</span>
          ))}
        </p>
        <p className="muted small">
          O esqueleto está escrito no arquivo como{' '}
          <code>{doc.modelo}</code>.
        </p>

        <div className="rolagem">
          <table className="grid compact">
            <thead>
              <tr>
                <th>Forma</th><th>O que é</th><th>De onde vem</th>
                <th>Quando aparece</th><th>O portal lê de volta?</th>
              </tr>
            </thead>
            <tbody>
              {doc.marcas.map((m) => (
                <tr key={m.nome}>
                  <td className="mono">{m.forma}</td>
                  <td>
                    <strong>{m.nome}</strong>
                    <br /><span className="muted small">{m.delimitador}</span>
                  </td>
                  <td className="muted small">{m.origem}</td>
                  <td className="muted small">{m.quando}</td>
                  <td className="small">{m.lido}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* -------------------------------------------- ordenação e arquivo */}
      <section className="card">
        <h2>Ordem e arquivo</h2>
        <table className="grid compact">
          <tbody>
            <tr>
              <td style={{ width: 190 }}><strong>Ordenação</strong></td>
              <td>
                {doc.ordenacao.join(' → ')}
                {doc.categoria_vazia_no_fim && (
                  <span className="muted small"> · categoria vazia por último</span>
                )}
              </td>
            </tr>
            <tr>
              <td><strong>Codificação</strong></td>
              <td className="mono small">{doc.encoding}</td>
            </tr>
            <tr>
              <td><strong>Nome do arquivo</strong></td>
              <td className="mono small">
                {doc.nome_um}
                <span className="muted"> · vários meses: </span>
                {doc.nome_varios}
              </td>
            </tr>
          </tbody>
        </table>

        <div className="toolbar">
          <button className="ghost" onClick={() => setVerYaml((v) => !v)}>
            {verYaml ? 'esconder o arquivo' : 'ver o arquivo como está'}
          </button>
        </div>
        {verYaml && (
          <pre className="yaml-leitura">{cfg.output_yaml}</pre>
        )}
      </section>
    </>
  )
}
