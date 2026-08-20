import { useState } from 'react'

/**
 * Primitivas de gráfico em SVG puro.
 *
 * Sem biblioteca de charts de propósito: são quatro formas, o bundle já entrega
 * tudo em 60 kB, e uma dependência de 300 kB para desenhar retângulos sairia
 * cara num app que o `npm ci` instala do zero a cada build.
 *
 * A PALETA NÃO É ESCOLHA DE GOSTO. Estes oito tons foram validados contra a
 * superfície branca dos cards: banda de luminosidade, piso de croma, separação
 * para daltonismo (protan/deutan/tritan) e contraste. A ORDEM é parte da
 * validação — é ela que garante que duas séries vizinhas numa pilha continuem
 * distinguíveis. Trocar a ordem, inserir um tom no meio ou gerar uma nona cor
 * quebra a garantia, então a nona série vira "Outras" em vez de ganhar um tom
 * inventado.
 *
 * Três tons (aqua, amarelo, magenta) ficam abaixo de 3:1 contra o branco. A
 * regra para isso é rótulo visível ou tabela — por isso toda tela que os usa
 * oferece "ver como tabela", e as barras carregam o valor escrito ao lado.
 */
export const SERIES = [
  '#2a78d6', '#eb6834', '#1baf7a', '#eda100',
  '#e87ba4', '#008300', '#4a3aa7', '#e34948',
]

/**
 * Par divergente: laranja/azul, NÃO vermelho/verde.
 *
 * Vermelho e verde são a escolha instintiva para "faltou / sobrou" e são o erro
 * mais comum em gráfico divergente: rodados no validador contra o branco, o par
 * #d03b3b/#0ca30c dá ΔE 4,1 em deuteranopia — os dois lados viram o MESMO tom
 * para 1 homem em 12, e um gráfico cujo sinal é a informação inteira passa a não
 * ter informação nenhuma. O par abaixo dá ΔE 24,7 no pior caso e passa nas seis
 * checagens; são as posições 1 e 2 da paleta acima, já validadas como vizinhas.
 */
export const DIVERGENTE = { negativo: '#eb6834', positivo: '#2a78d6' }

// Tinta e cromo: recessivos de propósito — a grade não disputa com o dado.
const INK = '#24291F'
const MUTED = '#5A645A'
const GRID = '#E1E0D9'
const AXIS = '#C3C2B7'

export const brl = (v) =>
  (v ?? 0).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL',
                                     maximumFractionDigits: 0 })

/**
 * Formato curto para os rótulos do eixo: "R$ 2 mi", "R$ 500 mil".
 *
 * Por extenso, "R$ 2.000.000" não cabe na margem e o navegador corta o "R" —
 * o eixo passava a exibir "$ 200.000", que num app em reais é uma moeda
 * diferente, não uma abreviação.
 */
export const brlCompacto = (v) => {
  const n = Math.abs(v ?? 0)
  const sinal = (v ?? 0) < 0 ? '-' : ''
  if (n >= 1e6) return `${sinal}R$ ${(n / 1e6).toLocaleString('pt-BR', { maximumFractionDigits: 1 })} mi`
  if (n >= 1e3) return `${sinal}R$ ${(n / 1e3).toLocaleString('pt-BR', { maximumFractionDigits: 0 })} mil`
  return `${sinal}R$ ${n.toLocaleString('pt-BR', { maximumFractionDigits: 0 })}`
}
export const brlExato = (v) =>
  (v ?? 0).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })

const MES_CURTO = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun',
                   'jul', 'ago', 'set', 'out', 'nov', 'dez']

/**
 * Preenche os meses ausentes com `null`.
 *
 * Sem isto o "buraco" nunca aparece: os meses sem lançamento simplesmente não
 * estão no array, então dois pontos distantes anos ficam lado a lado e a linha
 * os liga como se fossem consecutivos. O eixo passa a mentir sobre o tempo.
 */
export function eixoContinuo(pontos, chave = 'periodo') {
  if (pontos.length < 2) return pontos
  const porPeriodo = new Map(pontos.map((p) => [p[chave], p]))
  const num = (p) => [Number(p.slice(0, 4)), Number(p.slice(5))]
  let [ano, mes] = num(pontos[0][chave])
  const [anoFim, mesFim] = num(pontos[pontos.length - 1][chave])
  const saida = []
  while (ano < anoFim || (ano === anoFim && mes <= mesFim)) {
    const alvo = `${ano}-${String(mes).padStart(2, '0')}`
    saida.push(porPeriodo.get(alvo) || { [chave]: alvo })
    mes += 1
    if (mes === 13) { ano += 1; mes = 1 }
  }
  return saida
}

export const rotuloPeriodo = (p) => {
  const [ano, mes] = (p || '').split('-')
  return mes ? `${MES_CURTO[Number(mes) - 1]}/${ano.slice(2)}` : p
}

/**
 * Escala com passo redondo, servindo piso, teto e as marcas já prontas.
 *
 * Arredondar só o TOPO não basta quando o eixo desce abaixo de zero: dividir
 * [-200 mil, 10 mil] em quatro dá marcas em -148 mil e -95 mil, números que
 * ninguém lê. O que precisa ser redondo é o PASSO — daí as marcas caem em
 * múltiplos dele e o zero sempre coincide com uma delas.
 *
 * Arredondar só para a primeira casa significativa também desperdiça metade do
 * gráfico: um máximo de R$ 103 mil virava um teto de R$ 200 mil, e a curva
 * ficava espremida na metade de baixo. A escada 1 / 2 / 2,5 / 5 / 10 dá o passo
 * mais justo que ainda é redondo.
 */
function passoBonito(bruto) {
  if (bruto <= 0) return 1
  const magnitude = 10 ** Math.floor(Math.log10(bruto))
  const n = bruto / magnitude
  const mantissa = n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10
  return mantissa * magnitude
}

export function escala(minimo, maximo, alvo = 5) {
  const baixo = Math.min(0, minimo)
  const alto = Math.max(0, maximo)
  if (alto === baixo) return { piso: 0, teto: 1, marcas: [0, 1] }

  const passo = passoBonito((alto - baixo) / alvo)
  const piso = Math.floor(baixo / passo) * passo
  const teto = Math.ceil(alto / passo) * passo
  const marcas = []
  // O `passo / 2` na condição é folga contra erro de ponto flutuante: sem ele,
  // um acumulado de 0.1 em 0.1 perde a última marca de vez em quando.
  for (let v = piso; v <= teto + passo / 2; v += passo) {
    marcas.push(Math.round(v / passo) * passo)
  }
  return { piso, teto, marcas }
}

function Tooltip({ dados }) {
  if (!dados) return null
  return (
    <div className="viz-tip" style={{ left: dados.x, top: dados.y }}>
      <strong>{dados.titulo}</strong>
      {dados.linhas.map((l) => (
        <div key={l.rotulo}>
          {l.cor && <span className="viz-dot" style={{ background: l.cor }} />}
          {l.rotulo}: <strong>{l.valor}</strong>
        </div>
      ))}
    </div>
  )
}

/**
 * Série temporal. Uma ou mais linhas, mesma unidade, UM eixo.
 *
 * Mês sem lançamento vira BURACO, não zero: o histórico tem 23 meses sem dado
 * (ago/2015 a set/2016) e ligar os pontos por cima deles desenharia uma queda a
 * zero que nunca aconteceu — a mentira mais comum em gráfico de linha.
 */
export function LineChart({ pontos, series, altura = 240, formato = brlCompacto }) {
  const [tip, setTip] = useState(null)
  const L = 88, R = 16, T = 16, B = 28
  const W = 720, H = altura
  const largura = W - L - R, alturaPlot = H - T - B

  const valores = pontos.flatMap((p) => series.map((s) => p[s.chave]).filter((v) => v != null))
  // O piso só desce abaixo de zero quando o dado desce: numa série toda positiva
  // isto é idêntico a ancorar no zero. Sem ele, uma série que vai a -R$ 100 mil
  // (resíduo acumulado) seria desenhada inteira em cima da linha do zero, porque
  // `y` só sabia mapear o intervalo [0, teto].
  const { piso, teto, marcas } = escala(Math.min(0, ...valores), Math.max(0, ...valores))
  const x = (i) => L + (pontos.length === 1 ? largura / 2
                        : (i * largura) / (pontos.length - 1))
  const y = (v) => T + alturaPlot - ((v - piso) / (teto - piso)) * alturaPlot

  // Quebra o caminho onde não há dado — é isto que desenha o buraco.
  const caminho = (chave) => {
    let d = '', caneta = false
    pontos.forEach((p, i) => {
      const v = p[chave]
      if (v == null) { caneta = false; return }
      d += `${caneta ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)} `
      caneta = true
    })
    return d.trim()
  }

  const passo = Math.max(1, Math.ceil(pontos.length / 12))

  return (
    <div className="viz-wrap">
      <svg viewBox={`0 0 ${W} ${H}`} className="viz" role="img"
           aria-label={`Série temporal: ${series.map((s) => s.rotulo).join(', ')}`}>
        {marcas.map((v) => (
          <g key={v}>
            <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} stroke={GRID} strokeWidth="1" />
            <text x={L - 8} y={y(v) + 4} textAnchor="end" fontSize="11" fill={MUTED}>
              {formato(v)}
            </text>
          </g>
        ))}
        <line x1={L} x2={W - R} y1={y(0)} y2={y(0)} stroke={AXIS} strokeWidth="1" />

        {pontos.map((p, i) => i % passo === 0 && (
          <text key={p.periodo} x={x(i)} y={H - 8} textAnchor="middle"
                fontSize="11" fill={MUTED}>{rotuloPeriodo(p.periodo)}</text>
        ))}

        {/* `s.cor` quando vier: a cor tem que seguir a ENTIDADE, não a posição
            na lista. Esconder uma série no gráfico de tendência repintaria
            todas as outras se a cor saísse do índice do array. */}
        {series.map((s, si) => (
          <path key={s.chave} d={caminho(s.chave)} fill="none"
                stroke={s.cor || SERIES[si]} strokeWidth="2"
                strokeLinejoin="round" strokeLinecap="round" />
        ))}

        {/* Faixa de captura por ponto: alvo maior que a marca, como manda a
            regra de interação — mirar num círculo de 4px é hostil. */}
        {pontos.map((p, i) => (
          <rect key={p.periodo} x={x(i) - largura / (pontos.length * 2) - 1} y={T}
                width={largura / pontos.length + 2} height={alturaPlot}
                fill="transparent"
                onMouseEnter={(e) => setTip({
                  x: e.nativeEvent.offsetX + 12, y: e.nativeEvent.offsetY - 8,
                  titulo: rotuloPeriodo(p.periodo),
                  linhas: series.filter((s) => p[s.chave] != null).map((s) => ({
                    rotulo: s.rotulo, valor: brlExato(p[s.chave]),
                    cor: s.cor || SERIES[series.indexOf(s)],
                  })),
                })}
                onMouseLeave={() => setTip(null)} />
        ))}
        {series.map((s, si) => pontos.map((p, i) => p[s.chave] != null && (
          <circle key={`${s.chave}-${i}`} cx={x(i)} cy={y(p[s.chave])} r="2.5"
                  fill={s.cor || SERIES[si]} />
        )))}
      </svg>
      <Tooltip dados={tip} />
    </div>
  )
}

/**
 * Soma corrida de cada série, do começo do período até cada mês.
 *
 * Fora do componente e exportada porque é a única parte disto que tem conta
 * para errar — e conta que só dá para conferir por pixel é conta que não se
 * confere.
 *
 * MÊS SEM LANÇAMENTO SEGURA O TOTAL, não zera. O gráfico de linha desenha
 * buraco nesses meses (ver `LineChart`), e está certo lá: o gasto DAQUELE mês é
 * desconhecido. Aqui a grandeza é outra — "quanto já saiu até aqui" — e essa
 * não volta para zero porque faltou um mês. Ela fica parada, que é o desenho
 * honesto de "nada entrou nesta soma".
 *
 * O `vazio` marca justamente esses meses, para o painel poder dizer quantos
 * platôs da linha são ausência de dado em vez de mês sem gasto. Os 23 meses
 * sem lançamento do histórico dele (ago/15 a set/16) viram um degrau reto de
 * quase dois anos, e sem o aviso ele se lê como economia.
 */
export function acumular(pontos, chaves) {
  const total = Object.fromEntries(chaves.map((c) => [c, 0]))
  return pontos.map((p) => {
    const linha = { periodo: p.periodo, vazio: chaves.every((c) => p[c] == null) }
    for (const c of chaves) {
      if (p[c] != null) total[c] += p[c]
      linha[c] = total[c]
    }
    return linha
  })
}

/**
 * Área empilhada do ACUMULADO por categoria.
 *
 * Responde uma pergunta que nenhum outro painel desta aba responde: não "quanto
 * gastei em maio" nem "a categoria está subindo", e sim **quanto cada categoria
 * já custou no total, e em que ritmo**. A altura da faixa no fim é o total da
 * categoria no período; a inclinação dela em cada trecho é o gasto mensal; a
 * linha de cima é tudo somado.
 *
 * DUAS COISAS QUE A FORMA IMPÕE
 * -----------------------------
 * 1. **A ordem do empilhamento é a do backend — maior embaixo.** A faixa de
 *    baixo é a única com uma base reta; todas as outras herdam o solavanco das
 *    de baixo. Pondo a maior no chão, o ruído que ela injeta nas de cima é
 *    proporcionalmente o menor possível.
 * 2. **A separação entre faixas é um traço da cor do FUNDO, não uma borda
 *    escura.** É o mesmo vão de 2px das barras empilhadas: uma borda com cor
 *    própria vira uma nona série que ninguém pediu.
 *
 * A cor sai de `s.cor` — a identidade da categoria —, nunca do índice na lista
 * de visíveis. Esconder "Casa" na legenda não pode repintar "Construção".
 */
export function AreaEmpilhadaAcumulada({ pontos, series, altura = 280 }) {
  const [tip, setTip] = useState(null)
  const L = 88, R = 16, T = 16, B = 28
  const W = 720, H = altura
  const largura = W - L - R, alturaPlot = H - T - B

  const chaves = series.map((s) => s.chave)
  const linhas = acumular(pontos, chaves)
  if (!linhas.length) return null

  // O topo da pilha no ÚLTIMO mês é o maior valor do gráfico — a soma corrida
  // só cresce, então não há por que varrer a série inteira procurando máximo.
  const ultimo = linhas[linhas.length - 1]
  const { teto, marcas } = escala(0, chaves.reduce((s, c) => s + ultimo[c], 0) || 1)

  const x = (i) => L + (linhas.length === 1 ? largura / 2
                        : (i * largura) / (linhas.length - 1))
  const y = (v) => T + alturaPlot - (v / teto) * alturaPlot

  // Base acumulada de cada faixa: o topo de tudo que está embaixo dela.
  const bases = linhas.map((l) => {
    let soma = 0
    return chaves.map((c) => { const b = soma; soma += l[c]; return b })
  })

  const area = (si) => {
    const topo = linhas.map((l, i) => `${x(i).toFixed(1)} ${y(bases[i][si] + l[chaves[si]]).toFixed(1)}`)
    const base = linhas.map((l, i) => `${x(i).toFixed(1)} ${y(bases[i][si]).toFixed(1)}`).reverse()
    return `M${topo.join(' L')} L${base.join(' L')} Z`
  }

  const passo = Math.max(1, Math.ceil(linhas.length / 12))

  return (
    <div className="viz-wrap">
      <svg viewBox={`0 0 ${W} ${H}`} className="viz" role="img"
           aria-label={`Acumulado por categoria: ${series.map((s) => s.rotulo).join(', ')}`}>
        {marcas.map((v) => (
          <g key={v}>
            <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} stroke={GRID} strokeWidth="1" />
            <text x={L - 8} y={y(v) + 4} textAnchor="end" fontSize="11" fill={MUTED}>
              {brlCompacto(v)}
            </text>
          </g>
        ))}

        {series.map((s, si) => (
          <path key={s.chave} d={area(si)} fill={s.cor || SERIES[si]}
                stroke="#FFF" strokeWidth="1.5" strokeLinejoin="round" />
        ))}

        <line x1={L} x2={W - R} y1={y(0)} y2={y(0)} stroke={AXIS} strokeWidth="1" />

        {linhas.map((l, i) => i % passo === 0 && (
          <text key={l.periodo} x={x(i)} y={H - 8} textAnchor="middle"
                fontSize="11" fill={MUTED}>{rotuloPeriodo(l.periodo)}</text>
        ))}

        {/* Faixa de captura por mês, mais larga que qualquer marca — a regra de
            interação vale igual aqui: mirar numa fatia de 3px seria hostil. */}
        {linhas.map((l, i) => (
          <rect key={l.periodo} x={x(i) - largura / (linhas.length * 2) - 1} y={T}
                width={largura / linhas.length + 2} height={alturaPlot}
                fill="transparent"
                onMouseEnter={(e) => setTip({
                  x: e.nativeEvent.offsetX + 12, y: e.nativeEvent.offsetY - 8,
                  titulo: `até ${rotuloPeriodo(l.periodo)}`,
                  // Maior primeiro: a dica lida de cima para baixo bate com a
                  // pilha lida de cima para baixo só por acaso; ordenar por
                  // valor é o que responde "quem pesa mais" sem contar pixel.
                  linhas: [...series]
                    .map((s) => ({ rotulo: s.rotulo, valor: l[s.chave],
                                   cor: s.cor || SERIES[series.indexOf(s)] }))
                    .sort((a, b) => b.valor - a.valor)
                    .map((x2) => ({ ...x2, valor: brlExato(x2.valor) }))
                    .concat([{ rotulo: 'Total',
                               valor: brlExato(chaves.reduce((s, c) => s + l[c], 0)) }]),
                })}
                onMouseLeave={() => setTip(null)} />
        ))}
      </svg>
      <Tooltip dados={tip} />
    </div>
  )
}

/**
 * Barras horizontais ordenadas por valor.
 *
 * UMA cor só, e isso é regra, não economia: aqui a cor codifica MAGNITUDE, e
 * quem carrega a identidade é o rótulo ao lado — que está sempre escrito.
 * Pintar cada barra de um tom da paleta categórica daria a entender que a cor
 * significa alguma coisa, e a partir da nona barra os tons se repetiriam,
 * sugerindo parentesco entre categorias que não têm nenhum.
 *
 * O valor escrito ao lado também é o que satisfaz a regra de contraste: três
 * tons da paleta ficam abaixo de 3:1 contra o branco e exigem rótulo visível.
 */
export function BarrasH({ itens, total, cor = SERIES[0], formato = brlExato,
                          aoClicar, dicaClique = 'clique para tirar do gráfico' }) {
  const maximo = Math.max(...itens.map((i) => Math.abs(i.valor)), 1)
  return (
    <div className="viz-barras">
      {itens.map((item) => {
        // Uma barra clicável é um BOTÃO, não uma div com onClick: é o que dá
        // foco por teclado, Enter/Espaço e leitura por leitor de tela de graça.
        const Marca = aoClicar ? 'button' : 'div'
        return (
          <Marca className={`viz-barra ${aoClicar ? 'clicavel' : ''}`} key={item.rotulo}
                 {...(aoClicar
                   ? { type: 'button', onClick: () => aoClicar(item),
                       // Sem aria-label o nome acessível vira "Casa R$ 1.292.685,00
                       // 59,3%" — o leitor de tela lê o número e não diz o que o
                       // botão FAZ, que é a única coisa que ele precisava dizer.
                       'aria-label': `${item.rotulo} — ${dicaClique}`,
                       title: `${item.rotulo} — ${dicaClique}` }
                   : {})}>
            <span className="viz-barra-rotulo" title={aoClicar ? undefined : item.rotulo}>
              {item.rotulo}
            </span>
            <span className="viz-barra-trilho">
              <span className="viz-barra-preenche"
                    style={{ width: `${(Math.abs(item.valor) / maximo) * 100}%`,
                             background: item.cor || cor }} />
            </span>
            <span className="viz-barra-valor">
              {formato(item.valor)}
              {total ? (
                <em>{((item.valor / total) * 100).toLocaleString('pt-BR', {
                  minimumFractionDigits: 1, maximumFractionDigits: 1 })}%</em>
              ) : null}
            </span>
          </Marca>
        )
      })}
    </div>
  )
}

/**
 * Barras divergentes a partir de um zero central — o SINAL é a informação.
 *
 * Usada para resíduo mensal: para a esquerda é dinheiro que saiu sem entrada
 * registrada, para a direita é entrada que não foi para lugar nenhum. Ordenar
 * por módulo põe o que importa em cima; a linha do zero é o que deixa comparar
 * os dois lados sem ler número nenhum.
 */
export function BarrasDivergentes({ itens, formato = brlExato, cores = DIVERGENTE }) {
  const maximo = Math.max(...itens.map((i) => Math.abs(i.valor)), 1)
  return (
    <div className="viz-barras">
      {itens.map((item) => {
        const negativo = item.valor < 0
        return (
          <div className="viz-barra" key={item.rotulo}>
            <span className="viz-barra-rotulo" title={item.rotulo}>{item.rotulo}</span>
            <span className="viz-barra-trilho divergente">
              <span className="viz-barra-zero" />
              <span className="viz-barra-preenche"
                    style={{
                      position: 'absolute', top: 0,
                      width: `${(Math.abs(item.valor) / maximo) * 50}%`,
                      [negativo ? 'right' : 'left']: '50%',
                      background: negativo ? cores.negativo : cores.positivo,
                    }} />
            </span>
            <span className="viz-barra-valor">
              {formato(item.valor)}
              {item.nota ? <em>{item.nota}</em> : null}
            </span>
          </div>
        )
      })}
    </div>
  )
}

/**
 * Barras empilhadas por período.
 *
 * O vão de 2px entre segmentos não é enfeite: sem ele, dois tons vizinhos
 * encostados viram uma mancha só para quem tem daltonismo, e a separação
 * validada da paleta deixa de valer.
 */
export function BarrasEmpilhadas({ periodos, categorias, altura = 260,
                                   formatoRotulo = (p) => p }) {
  const [tip, setTip] = useState(null)
  const L = 88, R = 16, T = 16, B = 28
  const W = 720, H = altura
  const largura = W - L - R, alturaPlot = H - T - B

  const totais = periodos.map((p) => p.valores.reduce((s, v) => s + Math.max(0, v), 0))
  const { teto, marcas } = escala(0, Math.max(...totais, 1))
  const larguraBarra = Math.min(46, (largura / periodos.length) * 0.68)
  const x = (i) => L + (i + 0.5) * (largura / periodos.length) - larguraBarra / 2
  const y = (v) => T + alturaPlot - (v / teto) * alturaPlot

  // Com 15 anos cabem 15 rótulos; com 60 meses não cabem 60. Pular de N em N é
  // o que impede "jan/21" de escrever por cima de "fev/21" e virar borrão. O
  // teto é 16 porque é quanto cabe de "jan/21" nos 616px de área de plotagem.
  const passo = Math.max(1, Math.ceil(periodos.length / 16))

  return (
    <div className="viz-wrap">
      <svg viewBox={`0 0 ${W} ${H}`} className="viz" role="img"
           aria-label="Gasto por categoria em cada período">
        {marcas.map((v) => (
          <g key={v}>
            <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} stroke={GRID} strokeWidth="1" />
            <text x={L - 8} y={y(v) + 4} textAnchor="end" fontSize="11" fill={MUTED}>
              {brlCompacto(v)}
            </text>
          </g>
        ))}

        {periodos.map((p, i) => {
          let acumulado = 0
          return (
            <g key={p.periodo}>
              {p.valores.map((v, ci) => {
                if (v <= 0) return null
                const y0 = y(acumulado + v), y1 = y(acumulado)
                acumulado += v
                const h = Math.max(0, y1 - y0 - 2)   // o vão de 2px
                return (
                  <rect key={ci} x={x(i)} y={y0} width={larguraBarra} height={h}
                        fill={SERIES[ci]} rx="2"
                        onMouseEnter={(e) => setTip({
                          x: e.nativeEvent.offsetX + 12, y: e.nativeEvent.offsetY - 8,
                          titulo: `${categorias[ci]} · ${formatoRotulo(p.periodo)}`,
                          linhas: [{ rotulo: 'Total', valor: brlExato(v), cor: SERIES[ci] }],
                        })}
                        onMouseLeave={() => setTip(null)} />
                )
              })}
              {i % passo === 0 && (
                <text x={x(i) + larguraBarra / 2} y={H - 8} textAnchor="middle"
                      fontSize="11" fill={MUTED}>{formatoRotulo(p.periodo)}</text>
              )}
            </g>
          )
        })}
        <line x1={L} x2={W - R} y1={y(0)} y2={y(0)} stroke={AXIS} strokeWidth="1" />
      </svg>
      <Tooltip dados={tip} />
      <Legenda itens={categorias.map((c, i) => ({ rotulo: c, cor: SERIES[i] }))} />
    </div>
  )
}

/**
 * Mapa de calor ano × mês — magnitude, então UMA cor do claro ao escuro.
 *
 * Arco-íris aqui seria erro: matiz não tem ordem natural, e o olho não sabe
 * dizer se laranja é mais ou menos que verde. Luminosidade tem.
 */
const RAMPA = ['#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#184f95', '#0d366b']

export function Heatmap({ anos, celulas, altura = 30, normalizar = false }) {
  const [tip, setTip] = useState(null)
  const mapa = new Map(celulas.map((c) => [`${c.ano}-${c.mes}`, c.total]))

  // O eixo dos anos precisa ser contínuo. 2016 não tem UM lançamento, então o
  // backend não o devolve — e a tabela pulava de 2015 para 2017 como se o ano
  // não existisse, escondendo justamente o buraco que ela deveria mostrar.
  const linhas = anos.length
    ? Array.from({ length: anos[anos.length - 1] - anos[0] + 1 },
                 (_, i) => anos[0] + i)
    : []
  /**
   * Contra o que cada célula é comparada.
   *
   * `normalizar` troca a régua do mapa inteiro pela régua DO ANO. Num histórico
   * em que o gasto cresceu dez vezes, a régua única não mostra sazonalidade
   * nenhuma: 2012 a 2024 ficam todos abaixo de 10% do máximo — o mapa vira "os
   * anos recentes são mais caros", que já se sabia, e a pergunta de dezembro
   * contra julho fica sem resposta.
   *
   * O preço é que a MESMA cor passa a significar dinheiro diferente em linhas
   * diferentes, e isso não pode ficar implícito: o rodapé diz, e o tooltip
   * continua mostrando o valor absoluto.
   */
  const maximoGlobal = Math.max(...celulas.map((c) => c.total), 1)
  const maximoDoAno = new Map()
  for (const c of celulas) {
    maximoDoAno.set(c.ano, Math.max(maximoDoAno.get(c.ano) || 0, c.total))
  }
  const regua = (ano) =>
    (normalizar ? maximoDoAno.get(ano) || maximoGlobal : maximoGlobal) || 1
  const tom = (v, ano) => RAMPA[Math.min(RAMPA.length - 1,
                                         Math.floor((v / regua(ano)) ** 0.5 * RAMPA.length))]

  return (
    <div className="viz-wrap">
      <div className="viz-heat" style={{ '--linhas': anos.length }}>
        <div className="viz-heat-linha viz-heat-cabecalho">
          <span />
          {MES_CURTO.map((m) => <span key={m} className="viz-heat-mes">{m}</span>)}
        </div>
        {linhas.map((ano) => (
          <div className="viz-heat-linha" key={ano}>
            <span className="viz-heat-ano">{ano}</span>
            {MES_CURTO.map((_, i) => {
              const v = mapa.get(`${ano}-${i + 1}`)
              return (
                <span key={i} className={`viz-heat-cel ${v == null ? 'vazia' : ''}`}
                      style={{ background: v == null ? undefined : tom(v, ano),
                               height: altura }}
                      onMouseEnter={(e) => v != null && setTip({
                        x: e.nativeEvent.offsetX + 12, y: e.nativeEvent.offsetY - 8,
                        titulo: `${MES_CURTO[i]}/${ano}`,
                        linhas: [{ rotulo: 'Gasto', valor: brlExato(v) }],
                      })}
                      onMouseLeave={() => setTip(null)}
                      title={v == null ? 'sem lançamento' : brlExato(v)} />
              )
            })}
          </div>
        ))}
      </div>
      <Tooltip dados={tip} />
      <p className="muted small">
        {normalizar
          ? <>Cada ano tem a <strong>própria régua</strong>: o mês mais escuro é
             o mais caro <em>daquele ano</em>, então a mesma cor em linhas
             diferentes não é o mesmo dinheiro. Passe o mouse para o valor.</>
          : <>Régua única para o mapa inteiro: a mesma cor é o mesmo dinheiro em
             qualquer linha.</>}
        {' '}Célula vazia = mês sem lançamento nenhum, que é diferente de mês com
        gasto zero.
      </p>
    </div>
  )
}

/**
 * Legenda. Com `aoClicar`, cada item vira botão e some do gráfico.
 *
 * A cor do item NÃO sai da posição na lista: ela vem junto com o item. É o que
 * garante que esconder "Casa" não repinte todas as outras — cor que troca de
 * dono a cada clique obriga a reler a legenda a cada clique.
 */
export function Legenda({ itens, aoClicar, ocultas = [] }) {
  if (!aoClicar) {
    return (
      <div className="viz-legenda">
        {itens.map((i) => (
          <span key={i.rotulo}>
            <span className="viz-dot" style={{ background: i.cor }} />
            {i.rotulo}
          </span>
        ))}
      </div>
    )
  }
  return (
    <div className="viz-legenda">
      {itens.map((i) => {
        const oculta = ocultas.includes(i.rotulo)
        return (
          <button key={i.rotulo} type="button"
                  className={`viz-legenda-item ${oculta ? 'oculta' : ''}`}
                  aria-pressed={!oculta}
                  onClick={() => aoClicar(i.rotulo)}>
            <span className="viz-dot"
                  style={{ background: oculta ? 'transparent' : i.cor,
                           boxShadow: `inset 0 0 0 2px ${i.cor}` }} />
            {i.rotulo}
          </button>
        )
      })}
    </div>
  )
}

/** A tabela que acompanha cada gráfico.
 *
 *  Não é acessório: três tons da paleta ficam abaixo de 3:1 contra o branco, e
 *  a regra é rótulo visível OU tabela. Além disso é o caminho para copiar
 *  número para a planilha, que é o que se acaba querendo fazer. */
export function TabelaDados({ colunas, linhas }) {
  return (
    <div className="scroll">
      <table className="grid compact sticky">
        <thead>
          <tr>{colunas.map((c) => <th key={c} className={c.numerica ? 'right' : ''}>{c.rotulo || c}</th>)}</tr>
        </thead>
        <tbody>
          {linhas.map((linha, i) => (
            <tr key={i}>
              {linha.map((celula, j) => (
                <td key={j} className={typeof celula === 'number' ? 'right money' : ''}>
                  {typeof celula === 'number' ? brlExato(celula) : celula}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
