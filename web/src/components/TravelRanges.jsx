import { useRef, useState } from 'react'
import { juntarPeriodos, lerPeriodosCsv, textoDoArquivo } from '../travelCsv'
import { dataCurta } from '../viagens'

const porExtenso = dataCurta

/**
 * Editor de períodos de viagem — o mesmo nas duas telas onde aparece.
 *
 * Na tela de upload ele vem SEM limites: as faturas ainda não foram lidas, e
 * portanto não se sabe o intervalo de compras. Depois do processamento a etapa
 * Viagem mostra o mesmo editor já com `limites`, prendendo os seletores ao
 * período que as faturas realmente cobrem.
 *
 * Ele nunca guarda estado próprio de períodos: a lista vive no App, que é
 * quem fala com o backend. Aqui só existe o rascunho do período em digitação.
 */
export default function TravelRanges({
  ranges, onChange, limites = null, warnings = [], busy = false, compacto = false,
  preVoo = true,
}) {
  const [inicio, setInicio] = useState('')
  const [fim, setFim] = useState('')
  const [rotulo, setRotulo] = useState('')
  const [erro, setErro] = useState(null)
  const [importado, setImportado] = useState(null)
  const arquivoRef = useRef(null)

  function adicionar() {
    if (!inicio || !fim) return setErro('Escolha as duas datas.')
    if (fim < inicio) return setErro('A volta não pode ser antes da ida.')
    setErro(null)
    onChange([...ranges, { inicio, fim, rotulo: rotulo.trim() }])
    setInicio('')
    setFim('')
    setRotulo('')
  }

  /**
   * Importa um CSV `start_date,end_date,trip_name`.
   *
   * Lido aqui, no navegador — na tela de upload ainda não existe transação
   * para o backend guardar nada. O que sai é a mesma lista do editor manual,
   * então o resto do fluxo não sabe que este caminho existe.
   */
  async function importar(arquivo) {
    if (!arquivo) return
    setErro(null)
    let texto = ''
    try {
      texto = await textoDoArquivo(arquivo)
    } catch (e) {
      return setErro(`Não consegui ler ${arquivo.name}: ${e.message}`)
    }
    const { ranges: lidos, erros } = lerPeriodosCsv(texto)
    // O input guarda o último arquivo escolhido: sem limpar, escolher o MESMO
    // arquivo de novo (depois de corrigi-lo) não dispara `change` nenhum e a
    // tela fica parada, parecendo que a correção não adiantou.
    if (arquivoRef.current) arquivoRef.current.value = ''

    if (!lidos.length) {
      setImportado({ adicionados: 0, repetidos: 0, erros, nome: arquivo.name })
      return
    }
    const { lista, adicionados, repetidos } = juntarPeriodos(ranges, lidos)
    setImportado({ adicionados, repetidos, erros, nome: arquivo.name })
    if (adicionados) onChange(lista)
  }

  const remover = (i) => onChange(ranges.filter((_, idx) => idx !== i))

  return (
    <>
      {!compacto && (
        <p className="muted">
          Tudo que for <strong>comprado</strong> dentro de um destes períodos
          entra como <strong>Viagem</strong>, e a categoria real vai para a
          descrição, entre parênteses. A data comparada é a da compra
          (<code>{'{Em 15/Jul}'}</code>), não a do vencimento da fatura.
        </p>
      )}

      {/* Três estados, e a diferença entre os dois últimos importa: "não
          consegui ler" é uma falha e "não tentei" não é. Na recategorização o
          arquivo pode cobrir cinco anos e nem é lido antes de processar —
          dizer que a leitura falhou seria acusar um erro que não houve. */}
      <p className="muted small">
        {limites
          ? <>As compras deste lote vão de <strong>{porExtenso(limites.inicio)}</strong>{' '}
             a <strong>{porExtenso(limites.fim)}</strong> — os seletores só
             oferecem datas dentro disso, porque viagem fora do intervalo não
             pegaria compra nenhuma.</>
          : preVoo
            ? <>Não consegui ler as datas das compras deste lote, então os
               seletores ficam soltos. Dá para ajustar depois, na etapa
               <strong> Viagem</strong>.</>
            : <>Os seletores ficam soltos: o arquivo ainda não foi lido e pode
               cobrir anos. Depois de processar, a etapa <strong>Viagem</strong>{' '}
               mostra o intervalo real e o que cada período pegou.</>}
      </p>

      <div className="toolbar">
        <label className="field">
          <span className="small">Ida</span>
          <input type="date" value={inicio}
                 min={limites?.inicio} max={limites?.fim}
                 onChange={(e) => setInicio(e.target.value)} />
        </label>
        <label className="field">
          <span className="small">Volta</span>
          <input type="date" value={fim}
                 min={inicio || limites?.inicio} max={limites?.fim}
                 onChange={(e) => setFim(e.target.value)} />
        </label>
        <label className="field grow">
          <span className="small">Nome (opcional)</span>
          <input type="text" value={rotulo} placeholder="Gramado, feriado de maio…"
                 onChange={(e) => setRotulo(e.target.value)} />
        </label>
        <button className="ghost" onClick={adicionar} disabled={busy}>
          Adicionar período
        </button>
      </div>

      <div className="toolbar">
        <span className="small grow">
          Ou traga a lista pronta: um CSV de{' '}
          <code>start_date,end_date,trip_name</code>, uma viagem por linha. As
          datas podem vir como <code>2026-07-15</code> ou{' '}
          <code>15/07/2026</code>, com ou sem cabeçalho.
        </span>
        {/* O `<input type=file>` cru desenha um botão do sistema operacional
            no meio de uma linha de botões do portal. Escondê-lo atrás de um
            label é o jeito de a aparência ser a mesma sem perder o controle
            nativo — o input continua no DOM, focável e rotulado. */}
        <label className={`ghost como-botao ${busy ? 'desabilitado' : ''}`}>
          Importar CSV
          <input ref={arquivoRef} type="file" accept=".csv,text/csv"
                 aria-label="Importar períodos de um CSV"
                 disabled={busy}
                 onChange={(e) => importar(e.target.files?.[0])} />
        </label>
      </div>

      {importado && (
        <div className={`alert ${importado.erros.length ? 'warn' : 'ok'}`}>
          <strong>{importado.nome}</strong>:{' '}
          {importado.adicionados
            ? `${importado.adicionados} período(s) adicionado(s)`
            : 'nenhum período novo'}
          {importado.repetidos > 0 && `, ${importado.repetidos} já estava(m) na lista`}
          {importado.erros.length > 0 && (
            // As linhas recusadas vêm uma a uma, com o número: um "3 linhas
            // ignoradas" mandaria procurar quais no arquivo inteiro.
            <ul className="small">
              {importado.erros.map((e) => <li key={e}>{e}</li>)}
            </ul>
          )}
        </div>
      )}

      {erro && <div className="alert error">{erro}</div>}

      {ranges.length === 0 ? (
        <p className="muted small">
          Nenhum período — nada vira Viagem.
        </p>
      ) : (
        <ul className="file-list">
          {ranges.map((r, i) => (
            <li key={`${r.inicio}-${r.fim}-${i}`}>
              <span>
                {/* Com o ano: são 57 viagens entre 2018 e 2026 nesta
                    lista, e `15/12 → 16/12` não diz qual Sorocaba é. */}
                <strong>{dataCurta(r.inicio)} → {dataCurta(r.fim)}</strong>
                {r.rotulo && <span className="muted"> · {r.rotulo}</span>}
              </span>
              <button className="link" onClick={() => remover(i)} disabled={busy}>
                remover
              </button>
            </li>
          ))}
        </ul>
      )}

      {warnings.map((aviso) => (
        <div className="alert warn" key={aviso}>{aviso}</div>
      ))}
    </>
  )
}
