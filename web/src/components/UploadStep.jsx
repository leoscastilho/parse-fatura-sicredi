import { useEffect, useRef, useState } from 'react'
import * as api from '../api'
import TravelRanges from './TravelRanges'

const diaMesAno = (iso) => {
  const [a, m, d] = (iso || '').split('-')
  return d ? `${d}/${m}/${a}` : iso
}

/**
 * O primeiro nome — o padrão do rótulo que vai para a planilha.
 *
 * "Rhyesla Siqueira" vira "Rhyesla" porque a descrição já é longa e o nome
 * inteiro empurraria o resto para fora da coluna. É só o PADRÃO: o campo é
 * editável, e duas pessoas com o mesmo primeiro nome se resolvem lá.
 */
export const primeiroNome = (completo) => (completo || '').trim().split(/\s+/)[0] || ''

/**
 * O mapa de titulares no formato que o backend espera: `Completo=Rótulo`.
 *
 * VAI SÓ QUEM LEVA MARCA. "Eu" simplesmente não entra na lista — para o
 * servidor, nome ausente e rótulo vazio são a mesma coisa (`apelidos.get(nome,
 * "")`), então mandar o par vazio seria carregar uma distinção que ninguém do
 * outro lado consegue ler.
 *
 * COM MENOS DE DUAS PESSOAS O MAPA É VAZIO, e a guarda não é decorativa: sem
 * ela, uma fatura de um titular só cujo nome não bate com o "Associado" — outro
 * banco, ou o campo ausente — deixaria `eu` vazio, o rótulo cairia no primeiro
 * nome e TODA linha do arquivo levaria a marca da única pessoa que existe. É a
 * mesma condição que esconde o seletor, escrita onde a decisão acontece.
 */
export const formTitulares = (titulares, eu, apelidos) => (
  titulares.length < 2 ? '' : titulares
    .filter((nome) => nome !== eu && (apelidos[nome] || '').trim())
    .map((nome) => `${nome}=${apelidos[nome].trim()}`)
    .join('\n'))

/**
 * As senhas no formato que o backend espera: `indice=senha`, uma por linha.
 *
 * A chave é a POSIÇÃO do arquivo no lote, não o nome — `=` é caractere legal em
 * nome de arquivo, e o par `chave=valor` teria de escapá-lo. Aqui dentro o mapa
 * é por NOME, que é o que sobrevive a acrescentar ou tirar um arquivo da lista:
 * a tradução para índice acontece só na hora de enviar.
 *
 * Manda a senha de todo arquivo que tem uma digitada, protegido ou não. O
 * servidor só usa a dos que estão cifrados, e filtrar aqui exigiria manter uma
 * segunda cópia da lista de protegidos só para isso.
 */
export const formSenhas = (files, senhas) => files
  .map((f, i) => [i, senhas[f.name]])
  .filter(([, s]) => s)
  .map(([i, s]) => `${i}=${s}`)
  .join('\n')

export default function UploadStep({
  onUpload, busy, extensoes = ['.xls', '.xlsx', '.csv'],
  onBancosDetectados, travelRanges = [], onTravelRangesChange,
}) {
  const [files, setFiles] = useState([])
  const [dragging, setDragging] = useState(false)
  const [vencimento, setVencimento] = useState('')
  // Intervalo de COMPRAS do lote, lido antes do processamento. `null` = ainda
  // não sei (ou não deu para saber), e aí o editor de viagem fica solto.
  const [limites, setLimites] = useState(null)
  const [lendoPeriodo, setLendoPeriodo] = useState(false)
  // Conta conjunta: os nomes que aparecem na coluna de titular do extrato, quem
  // deles sou eu, e o rótulo que cada um dos OUTROS leva para a planilha.
  const [titulares, setTitulares] = useState([])
  const [eu, setEu] = useState('')
  const [apelidos, setApelidos] = useState({})
  // Os bancos que o pré-voo reconheceu NESTES arquivos. Antes isto era uma
  // dropdown: a pessoa dizia de qual banco era o arquivo que ela mesma tinha
  // acabado de exportar, e errar a escolha dava um erro de parsing sem relação
  // óbvia com a causa. Agora o arquivo responde por si.
  const [bancos, setBancos] = useState([])
  // Os arquivos cifrados que ainda não abriram — o BTG manda a fatura assim.
  // Vem do pré-voo, porque descobrir isso é ler o arquivo: pedir a senha antes
  // de saber se alguém precisa dela seria perguntar a todo mundo por causa de
  // um banco.
  const [protegidos, setProtegidos] = useState([])
  // UMA SENHA POR ARQUIVO, indexada pelo nome: dois cifrados no mesmo lote
  // podem ter chaves diferentes, e uma senha só deixaria um deles sem jeito de
  // abrir.
  //
  // E DOIS mapas de propósito: `senhas` é o que está nos campos, `senhasEnviadas`
  // é o que já foi tentado. Sem a separação, o pré-voo rodaria a cada pausa da
  // digitação e a pessoa veria "a senha não confere" antes de terminar de
  // escrever — um erro sobre um estado que ela ainda não pediu para conferir.
  const [senhas, setSenhas] = useState({})
  const [senhasEnviadas, setSenhasEnviadas] = useState({})
  const inputRef = useRef(null)

  // A pergunta da data só aparece DEPOIS de reconhecer o banco, porque só aí
  // se sabe se ela faz falta: o Sicredi traz o vencimento dentro do arquivo, o
  // Nubank não traz em lugar nenhum.
  const precisaVencimento = bancos.some((b) => b.pede_vencimento)
  // Arquivo cifrado SEGURA o Processar. Sem isso o upload iria adiante lendo só
  // metade do lote e a fatura fecharia com um valor a menos, sem nada na tela
  // apontando para a causa.
  const pronto = files.length > 0 && !protegidos.length
    && (!precisaVencimento || vencimento)
  // Só é erro quando o que está NAQUELE campo é o que foi tentado NAQUELE
  // arquivo: assim que a pessoa começa a corrigir, o aviso sai da frente — e o
  // aviso de um arquivo não aparece por causa da senha errada de outro.
  const senhaNaoConfere = (p) => p.senha_incorreta
    && (senhas[p.nome] ?? '') === (senhasEnviadas[p.nome] ?? '')
  // O que vai para o servidor, e o que o efeito abaixo observa. String em vez
  // do objeto porque `useEffect` compara por identidade: um mapa novo a cada
  // tecla dispararia o pré-voo de novo, que é justamente o que os dois mapas
  // existem para evitar.
  const enviadas = formSenhas(files, senhasEnviadas)
  const podeAbrir = protegidos.some((p) => (senhas[p.nome] || '').trim())

  function accept(list) {
    // Filtra pela união das extensões de todos os bancos: qual deles é, quem
    // decide é o conteúdo, mas um `.pdf` não é fatura de nenhum.
    setFiles([...list].filter((f) =>
      extensoes.some((ext) => f.name.toLowerCase().endsWith(ext))))
  }

  // Pré-voo: as faturas são lidas assim que escolhidas, só para saber de quando
  // a quando vão as compras. Sem isto os seletores de data ficam soltos e dá
  // para marcar uma viagem de 2019 num lote de julho de 2026 — erro que só
  // apareceria do outro lado, depois de todo o trabalho de revisão.
  //
  // Depende do vencimento além dos arquivos porque em banco que não traz a data
  // no arquivo é ela que ancora o ano da compra: "{Em 15/Jul}" não diz o ano.
  useEffect(() => {
    if (!files.length) {
      setBancos([])
      setProtegidos([])
      // As senhas somem junto com os arquivos: são de um lote, não da sessão, e
      // guardá-las depois que o lote saiu de cena é mantê-las em memória por um
      // tempo que não serve para nada.
      setSenhas({}); setSenhasEnviadas({})
      onBancosDetectados?.([])
      return setLimites(null)
    }
    let cancelado = false
    // Espera a digitação parar: `input[type=date]` dispara onChange a cada
    // pedaço da data em alguns navegadores, e cada disparo reenvia os arquivos.
    const timer = setTimeout(async () => {
      setLendoPeriodo(true)
      try {
        const r = await api.uploadPeriodo(files, vencimento, enviadas)
        if (cancelado) return
        setLimites(r.purchase_range || null)
        setBancos(r.bancos || [])
        setProtegidos(r.protegidos || [])
        // Sobe para o App pintar o tema da marca reconhecida.
        onBancosDetectados?.(r.bancos || [])
        const nomes = r.titulares || []
        setTitulares(nomes)
        // A sugestão vem do "Associado" impresso na fatura — o banco já diz de
        // quem é a conta. Confirmar é mais rápido que procurar o próprio nome.
        //
        // Sem validar contra `nomes`: quem garante que a sugestão está na lista
        // é o servidor, que devolve `null` quando o "Associado" não aparece nos
        // lançamentos. Repetir a checagem aqui daria dois donos para a mesma
        // regra e um galho que nenhum teste consegue alcançar.
        setEu(r.eu_sugerido || '')
        setApelidos(Object.fromEntries(nomes.map((n) => [n, primeiroNome(n)])))
      } catch {
        // Conveniência, não pré-requisito: falhou, o editor volta a ficar solto
        // e a validação real continua acontecendo no processamento.
        if (!cancelado) {
          setLimites(null); setTitulares([]); setBancos([]); setProtegidos([])
          onBancosDetectados?.([])
        }
      } finally {
        if (!cancelado) setLendoPeriodo(false)
      }
    }, 300)
    return () => { cancelado = true; clearTimeout(timer) }
  }, [files, vencimento, enviadas])

  return (
    <section className="card">
      <h2>Extratos do cartão</h2>
      <p className="muted">
        Pode mandar vários de uma vez — todos viram um CSV só, com cada fatura
        num bloco, mesmo sendo de bancos diferentes. Aceita{' '}
        <code>{extensoes.join(', ')}</code>, e descobre de qual banco é cada
        arquivo lendo o próprio arquivo.
      </p>

      {/* A confirmação do que foi reconhecido. Com a dropdown, quem escolhia
          sabia; agora a tela é que precisa dizer, senão a detecção acontece em
          silêncio e um erro dela só apareceria lá na frente. */}
      {bancos.length > 0 && (
        <p className="small">
          Reconheci <strong>{bancos.map((b) => b.nome).join(' e ')}</strong>.
        </p>
      )}

      {bancos.filter((b) => !b.validado).map((b) => (
        <div className="alert warn" key={b.id}>
          O perfil de leitura do {b.nome} ainda não foi validado contra uma
          fatura real. Confira os totais antes de colar na planilha.
        </div>
      ))}

      <div
        className={`dropzone ${dragging ? 'over' : ''}`}
        onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); accept(e.dataTransfer.files) }}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => e.key === 'Enter' && inputRef.current?.click()}
      >
        <input
          ref={inputRef}
          type="file"
          accept={extensoes.join(',')}
          multiple
          hidden
          onChange={(e) => accept(e.target.files)}
        />
        {files.length === 0
          ? <span>Arraste os arquivos aqui ou clique para escolher</span>
          : <span>{files.length} arquivo(s) selecionado(s)</span>}
      </div>

      {files.length > 0 && (
        <ul className="file-list">
          {files.map((f) => (
            <li key={f.name}>
              <span>{f.name}</span>
              <span className="muted">{(f.size / 1024).toFixed(0)} KB</span>
            </li>
          ))}
        </ul>
      )}

      {/* SENHA DO ARQUIVO. Só aparece quando algum arquivo do lote está
          cifrado — hoje o BTG, que manda a fatura assim. É a senha que o banco
          definiu para o arquivo, não uma senha do portal, e por isso a tela diz
          isso com todas as letras: perguntar "senha" e mais nada faria a pessoa
          procurar uma que ela nunca cadastrou. */}
      {protegidos.length > 0 && (
        <div className="protegidos">
          <strong className="small">
            {protegidos.length > 1
              ? `${protegidos.length} arquivos deste lote estão protegidos por senha`
              : `${protegidos[0].nome} está protegido por senha`}
          </strong>
          <p className="muted small">
            É a senha <strong>do arquivo</strong>, a que o banco pede para
            abri-lo — serve só para ler a fatura aqui e não fica guardada em
            lugar nenhum.
            {protegidos.length > 1 && ' Cada arquivo tem a sua; os que não estão'
              + ' cifrados já foram lidos e não pedem nada.'}
          </p>

          {protegidos.map((p) => (
            <div className="toolbar" key={p.nome}>
              <label className="small">
                {/* Com um arquivo só, o nome dele já está na frase acima e o
                    rótulo fica sendo o que a pessoa procura ("senha"). Com
                    vários, o rótulo TEM que ser o nome — é a única coisa que
                    diz qual campo é de qual arquivo. */}
                {protegidos.length > 1 ? p.nome : 'Senha do arquivo'}{' '}
                <input
                  type="password"
                  value={senhas[p.nome] ?? ''}
                  autoComplete="off"
                  disabled={busy || lendoPeriodo}
                  onChange={(e) => setSenhas((atuais) =>
                    ({ ...atuais, [p.nome]: e.target.value }))}
                  onKeyDown={(e) => e.key === 'Enter' && podeAbrir
                    && setSenhasEnviadas({ ...senhas })}
                />
              </label>
              {senhaNaoConfere(p) && (
                <span className="inline-note error small">
                  A senha não abre este arquivo.
                </span>
              )}
            </div>
          ))}

          {/* UM botão para todos: com dois arquivos você preenche os dois e
              tenta uma vez. Um botão por campo faria duas leituras do lote
              inteiro para conferir uma senha de cada vez. */}
          <button
            disabled={!podeAbrir || busy || lendoPeriodo}
            onClick={() => setSenhasEnviadas({ ...senhas })}
          >
            {lendoPeriodo ? 'Abrindo…' : 'Abrir'}
          </button>
        </div>
      )}

      {precisaVencimento && (
        <div className="toolbar">
          <label className="small">
            Data de vencimento da fatura{' '}
            <input type="date" value={vencimento}
                   onChange={(e) => setVencimento(e.target.value)} />
          </label>
          <span className="muted small">
            {bancos.filter((b) => b.pede_vencimento).map((b) => b.nome).join(' e ')}{' '}
            não traz essa data no arquivo, e ela é a coluna <code>Data</code> do
            CSV. As faturas do lote que trazem a delas continuam usando a
            própria — a data digitada não sobrescreve nenhuma.
          </span>
        </div>
      )}

      {/* A viagem se declara aqui porque é agora que você lembra dela — não
          depois de revisar 130 estabelecimentos. E a pergunta nomeia as datas
          do lote: "viajou neste período?" sem dizer qual período obriga a
          conferir a fatura noutra janela para responder. */}
      {onTravelRangesChange && files.length > 0 && (
        <details className="viagem-upload" open={travelRanges.length > 0}>
          <summary>
            {lendoPeriodo ? 'Lendo as datas das compras…'
              : limites
                ? `Viajou entre ${diaMesAno(limites.inicio)} e ${diaMesAno(limites.fim)}?`
                : 'Viajou neste período?'}
            {travelRanges.length > 0 && (
              <span className="badge">{travelRanges.length}</span>
            )}
          </summary>
          <TravelRanges
            ranges={travelRanges}
            onChange={onTravelRangesChange}
            limites={limites}
            busy={busy || lendoPeriodo}
          />
        </details>
      )}

      {/* CONTA CONJUNTA. Só aparece com mais de um nome na fatura: com um só
          não há o que perguntar, e perguntar assim mesmo seria uma etapa a
          mais em toda importação para responder sempre a mesma coisa. */}
      {titulares.length > 1 && (
        <div className="titulares">
          <strong className="small">Quem é você nesta fatura?</strong>
          <p className="muted small">
            As compras dos outros ganham o nome no fim da descrição — as suas
            ficam como estão. Marcar as próprias seria escrever o mesmo nome em
            quase toda linha do arquivo para não distinguir nada.
          </p>

          {titulares.map((nome) => (
            <div className="titular" key={nome}>
              <label className="checkbox">
                <input type="radio" name="titular-eu" value={nome}
                       checked={eu === nome} disabled={busy}
                       onChange={() => setEu(nome)} />
                Esse sou eu
              </label>

              {eu === nome ? (
                <div className="grow">
                  <span className="muted small">{nome} — sem marca na descrição</span>
                </div>
              ) : (
                <div className="grow">
                  <input type="text" value={apelidos[nome] ?? ''} disabled={busy}
                         aria-label={`Nome de ${nome} na planilha`}
                         onChange={(e) => setApelidos((atuais) =>
                           ({ ...atuais, [nome]: e.target.value }))} />
                  {/* O nome completo fica embaixo, em cinza: é a referência de
                      quem é quem, mas quem vai para o arquivo é o de cima. */}
                  <span className="muted small">{nome}</span>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      <button className="primary" disabled={!pronto || busy}
              // `eu` vai junto e NÃO para o backend: o servidor só precisa
              // saber quem leva marca, e quem sou eu é justamente quem não
              // leva. Quem quer o nome é o filtro das telas seguintes, para
              // dizer "Leonardo" em vez de "Sem marca".
              onClick={() => onUpload(files, vencimento,
                                      formTitulares(titulares, eu, apelidos), eu,
                                      enviadas)}>
        {busy ? 'Processando…' : 'Processar'}
      </button>
    </section>
  )
}
