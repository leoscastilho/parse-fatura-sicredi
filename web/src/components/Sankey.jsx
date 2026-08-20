import { useState } from 'react'
import { SERIES, brlExato, brlCompacto } from './charts'

const INK = '#24291F'
const MUTED = '#5A645A'

/**
 * De onde veio e para onde foi, numa figura só.
 *
 * DUAS DECISÕES DE COR
 * --------------------
 * 1. **As faixas da esquerda são coloridas; as da direita, não.** As origens
 *    são poucas (quatro, ou oito na análise do casal) e cabem na paleta
 *    validada. Os destinos passam de doze, e inventar uma décima terceira cor
 *    quebraria a separação para daltônicos que a paleta garante. Do lado
 *    direito quem carrega a identidade é o RÓTULO, que está sempre escrito, e
 *    a espessura já codifica a magnitude — é a mesma regra das barras
 *    horizontais deste portal.
 * 2. **A cor segue a origem, não a posição.** Passar o mouse numa origem
 *    destaca a faixa dela; nada é repintado.
 *
 * O desenho é SVG puro, como o resto: uma dependência de layout de Sankey
 * custaria mais que o bundle inteiro para empilhar retângulos e desenhar
 * bézieres.
 */
export default function Sankey({ dados, altura = 560 }) {
  const [foco, setFoco] = useState(null)
  if (!dados?.origens?.length || !dados?.destinos?.length) return null

  const { origens, destinos, total } = dados
  const W = 760
  const LARGURA_NO = 12
  // A calha da esquerda é mais larga que a da direita porque os nomes de lá
  // são mais compridos: "Resgate de aplicação · marina" contra "Casa".
  const XL = 232, XM = W / 2 - LARGURA_NO / 2, XR = W - 190
  const T = 10
  // Vão entre nós: o que impede duas faixas vizinhas de virarem uma mancha só.
  const VAO = 6
  const alturaUtil = (lado) => altura - T * 2 - VAO * (lado.length - 1)
  const escalaEsq = alturaUtil(origens) / (total || 1)
  const escalaDir = alturaUtil(destinos) / (total || 1)

  // Uma faixa fina demais some; uma de 0,3px não é honesta nem legível. O piso
  // de 1,5px mantém a linha visível sem mentir sobre a proporção — o valor
  // escrito ao lado é quem diz o número.
  const espessura = (v, escala) => Math.max(1.5, v * escala)

  /**
   * O texto tem que caber na calha — o SVG corta o que passa, sem aviso.
   *
   * `.csv` sai porque é ruído: dois arquivos numa análise do casal são duas
   * pessoas, e o que interessa é o nome, não a extensão.
   */
  const rotulo = (nome) => {
    const limpo = nome.replace(/\.csv\b/gi, '')
    return limpo.length > 32 ? `${limpo.slice(0, 31)}…` : limpo
  }

  const empilhar = (itens, escala) => {
    let y = T
    return itens.map((item) => {
      const h = espessura(item.valor, escala)
      const topo = y
      y += h + VAO
      return { ...item, y: topo, h }
    })
  }

  /**
   * Afasta os RÓTULOS sem mexer nas faixas.
   *
   * Uma categoria de R$ 18 mil num total de R$ 2,9 milhões ganha 3px de faixa —
   * e o rótulo dela tem duas linhas, 22px. Sem isto, as oito categorias de
   * baixo escrevem umas por cima das outras e viram um borrão preto; foi
   * exatamente o que apareceu na primeira renderização.
   *
   * Engrossar a faixa fina para caber o texto seria mentir sobre a proporção,
   * que é a única coisa que um Sankey tem a dizer. Então a faixa fica onde
   * está, o rótulo sobe, e uma linha fina liga um ao outro.
   */
  const ALTURA_ROTULO = 26
  const separarRotulos = (itens) => {
    const alvo = itens.map((i) => i.y + i.h / 2)
    for (let i = 1; i < alvo.length; i += 1) {
      alvo[i] = Math.max(alvo[i], alvo[i - 1] + ALTURA_ROTULO)
    }
    // Volta de baixo para cima: se a pilha estourou o quadro, ela é empurrada
    // para dentro em vez de sair pela borda.
    const limite = altura - ALTURA_ROTULO / 2
    if (alvo[alvo.length - 1] > limite) {
      alvo[alvo.length - 1] = limite
      for (let i = alvo.length - 2; i >= 0; i -= 1) {
        alvo[i] = Math.min(alvo[i], alvo[i + 1] - ALTURA_ROTULO)
      }
    }
    return itens.map((item, i) => ({ ...item, yRotulo: alvo[i] }))
  }

  const esq = separarRotulos(empilhar(origens, escalaEsq))
  const dir = separarRotulos(empilhar(destinos, escalaDir))

  const alturaPool = Math.max(...[esq, dir].map(
    (lado) => lado[lado.length - 1].y + lado[lado.length - 1].h - T))

  // Fita: uma bézier com as duas pontas na horizontal. `C` com os pontos de
  // controle no meio do vão dá a curva em S que se espera de um Sankey.
  const fita = (x1, y1, x2, y2, h) => {
    const meio = (x1 + x2) / 2
    return `M${x1} ${y1} C${meio} ${y1} ${meio} ${y2} ${x2} ${y2}`
      + ` v${h} C${meio} ${y2 + h} ${meio} ${y1 + h} ${x1} ${y1 + h} Z`
  }

  let acumuladoEsq = T
  let acumuladoDir = T

  return (
    <div className="viz-wrap">
      <svg viewBox={`0 0 ${W} ${altura}`} className="viz" role="img"
           aria-label="De onde veio e para onde foi o dinheiro do período">
        {/* Entradas */}
        {esq.map((o, i) => {
          const cor = SERIES[i % SERIES.length]
          const yPool = acumuladoEsq
          acumuladoEsq += o.h
          const apagada = foco !== null && foco !== o.nome
          return (
            <g key={o.nome} onMouseEnter={() => setFoco(o.nome)}
               onMouseLeave={() => setFoco(null)}>
              <path d={fita(XL + LARGURA_NO, o.y, XM, yPool, o.h)}
                    fill={cor} opacity={apagada ? 0.12 : 0.42} />
              <rect x={XL} y={o.y} width={LARGURA_NO} height={o.h} fill={cor} rx="2" />
              {/* Linha-guia quando o rótulo teve que subir para não colidir. */}
              {Math.abs(o.yRotulo - (o.y + o.h / 2)) > 2 && (
                <line x1={XL - 5} y1={o.yRotulo - 4} x2={XL} y2={o.y + o.h / 2}
                      stroke={cor} strokeWidth="1" opacity=".5" />
              )}
              <text x={XL - 8} y={o.yRotulo} textAnchor="end"
                    fontSize="12" fill={INK}>{rotulo(o.nome)}</text>
              <text x={XL - 8} y={o.yRotulo + 13} textAnchor="end"
                    fontSize="11" fill={MUTED}>{brlCompacto(o.valor)}</text>
            </g>
          )
        })}

        {/* O caixa do período. Um nó só no meio porque o dinheiro não tem
            etiqueta: entrou, virou uma coisa só, saiu. */}
        <rect x={XM} y={T} width={LARGURA_NO} height={alturaPool}
              fill={MUTED} rx="2" />

        {/* Saídas */}
        {dir.map((d) => {
          const yPool = acumuladoDir
          acumuladoDir += d.h
          const sobra = d.nome === 'Sobra do período'
          return (
            <g key={d.nome}>
              <path d={fita(XM + LARGURA_NO, yPool, XR, d.y, d.h)}
                    fill={sobra ? '#1baf7a' : SERIES[0]}
                    opacity={foco === null ? 0.3 : 0.12} />
              <rect x={XR} y={d.y} width={LARGURA_NO} height={d.h}
                    fill={sobra ? '#1baf7a' : SERIES[0]} rx="2" />
              {Math.abs(d.yRotulo - (d.y + d.h / 2)) > 2 && (
                <line x1={XR + LARGURA_NO} y1={d.y + d.h / 2}
                      x2={XR + LARGURA_NO + 5} y2={d.yRotulo - 4}
                      stroke={sobra ? '#1baf7a' : SERIES[0]} strokeWidth="1" opacity=".5" />
              )}
              <text x={XR + LARGURA_NO + 8} y={d.yRotulo}
                    fontSize="12" fill={INK}>{rotulo(d.nome)}</text>
              <text x={XR + LARGURA_NO + 8} y={d.yRotulo + 13}
                    fontSize="11" fill={MUTED}>{brlCompacto(d.valor)}</text>
            </g>
          )
        })}
      </svg>

      <p className="muted small">
        Passe o mouse numa origem para seguir a faixa dela. Total do período:{' '}
        <strong>{brlExato(total)}</strong>.
      </p>
    </div>
  )
}
