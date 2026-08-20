/**
 * Ler períodos de viagem de um CSV — `start_date,end_date,trip_name`.
 *
 * Por que existe: digitar viagem por viagem é aceitável para a fatura do mês,
 * que tem uma ou nenhuma. Não é para o histórico inteiro, onde estão as vinte
 * viagens dos últimos cinco anos — e o histórico inteiro é justamente quando dá
 * para lembrar de todas elas. Quem tem essa lista já a tem em algum lugar: uma
 * aba da planilha, o Google Calendar exportado, um bloco de notas.
 *
 * A LEITURA ACONTECE NO NAVEGADOR, e não numa rota nova. Na tela de upload
 * ainda não existe transação — o backend não teria onde guardar nada — e o
 * arquivo já está na mão do cliente. O que sai daqui é a mesma lista que o
 * editor manual produz (`{inicio, fim, rotulo}` em ISO), então o backend
 * continua com um formato só de entrada e valida tudo de novo do lado dele.
 *
 * TOLERÂNCIA COM LIMITE. Aceita `,` `;` e tab, com ou sem cabeçalho, com as
 * datas em ISO ou em `dd/mm/aaaa`, com o nome entre aspas ou solto. Não aceita
 * `mm/dd/aaaa`: `03/05/2026` seria março ou maio, e um palpite errado marcaria
 * uma viagem de dois meses de distância sem avisar. Aqui a barra é sempre
 * brasileira, e quem exporta em formato americano usa ISO.
 *
 * NADA É DESCARTADO EM SILÊNCIO: as linhas que não deram certo voltam em
 * `erros`, com o número da linha, para a tela mostrar.
 */

// Uma linha de CSV respeitando aspas: `2026-07-01,2026-07-10,"Gramado, RS"`
// vira três células. Sem isto o nome com vírgula viraria duas colunas e a
// segunda metade sumiria.
function celulas(linha, separador) {
  const saida = []
  let atual = ''
  let entreAspas = false

  for (let i = 0; i < linha.length; i += 1) {
    const c = linha[i]
    if (entreAspas) {
      if (c === '"' && linha[i + 1] === '"') { atual += '"'; i += 1 }
      else if (c === '"') entreAspas = false
      else atual += c
    } else if (c === '"') {
      entreAspas = true
    } else if (c === separador) {
      saida.push(atual)
      atual = ''
    } else {
      atual += c
    }
  }
  saida.push(atual)
  // Sem aparar: as células viram o nome da viagem coladas de volta com o
  // separador, e aparar aqui transformaria "Gramado, RS" em "Gramado,RS".
  // Quem precisa de célula limpa (as datas) já apara por conta própria.
  return saida
}

// O separador é o que aparece mais na primeira linha com conteúdo. Contar em
// vez de fixar `,` é o que faz um export de Excel em português (`;`) funcionar
// sem o usuário precisar saber que existe diferença.
function separadorDe(linhas) {
  const amostra = linhas.find((l) => l.trim()) || ''
  const candidatos = [',', ';', '\t']
  let melhor = ','
  let maior = 0
  for (const c of candidatos) {
    const quantos = amostra.split(c).length - 1
    if (quantos > maior) { melhor = c; maior = quantos }
  }
  return melhor
}

const bissexto = (ano) => (ano % 4 === 0 && ano % 100 !== 0) || ano % 400 === 0
const DIAS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

function montar(ano, mes, dia) {
  if (mes < 1 || mes > 12 || dia < 1) return ''
  const limite = mes === 2 && bissexto(ano) ? 29 : DIAS[mes - 1]
  if (dia > limite) return ''
  const dd = String(dia).padStart(2, '0')
  const mm = String(mes).padStart(2, '0')
  return `${ano}-${mm}-${dd}`
}

/**
 * Uma data de célula em `AAAA-MM-DD`, ou '' se ilegível.
 *
 * A validação é de calendário, não de forma: `31/02/2026` casa com o padrão e
 * não existe. Deixá-la passar criaria um período que o backend recusaria
 * depois, com a mensagem falando de outra coisa.
 */
export function lerData(bruto) {
  const texto = String(bruto || '').trim()
  if (!texto) return ''

  const iso = texto.match(/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$/)
  if (iso) return montar(+iso[1], +iso[2], +iso[3])

  const br = texto.match(/^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2}|\d{4})$/)
  if (br) {
    // Ano de dois dígitos é sempre deste século. Um extrato de 1998 não existe
    // neste portal, e "26" virar 1926 seria pior do que recusar.
    const ano = br[3].length === 2 ? 2000 + +br[3] : +br[3]
    return montar(ano, +br[2], +br[1])
  }
  return ''
}

/**
 * O CSV inteiro -> `{ ranges, erros }`.
 *
 * O cabeçalho é detectado pelo CONTEÚDO, não pelo nome: se as duas primeiras
 * células da primeira linha não são datas, ela é cabeçalho. Assim funciona com
 * `start_date,end_date,trip_name`, com `Ida;Volta;Viagem` e sem cabeçalho
 * nenhum, sem precisar de uma lista de nomes aceitos que sempre esquece um.
 */
export function lerPeriodosCsv(texto) {
  const linhas = String(texto || '').replace(/^﻿/, '').split(/\r\n|\r|\n/)
  const separador = separadorDe(linhas)
  const ranges = []
  const erros = []
  let primeira = true
  let cabecalho = null

  linhas.forEach((linha, indice) => {
    if (!linha.trim()) return
    const numero = indice + 1
    const partes = celulas(linha, separador)
    const inicio = lerData(partes[0])
    const fim = lerData(partes[1])

    if (primeira) {
      primeira = false
      if (!inicio || !fim) {
        // Guardada, não descartada: se no fim não sobrar período nenhum, esta
        // linha vira o aviso. Um arquivo de uma linha só, com a data errada,
        // seria engolido como cabeçalho e sumiria sem explicação.
        cabecalho = { numero, texto: linha.trim() }
        return
      }
    }

    if (partes.length < 2) {
      erros.push(`linha ${numero}: esperava início e fim, veio só "${linha.trim()}"`)
      return
    }
    if (!inicio || !fim) {
      const qual = (!inicio ? partes[0] : partes[1]).trim()
      erros.push(`linha ${numero}: data ilegível "${qual}" — use 2026-07-15 ou 15/07/2026`)
      return
    }
    if (fim < inicio) {
      erros.push(`linha ${numero}: a volta (${partes[1]}) é antes da ida (${partes[0]})`)
      return
    }
    // As sobras viram nome junto com o separador de volta: quem escreveu
    // `2026-07-01,2026-07-10,Gramado, RS` sem aspas quis "Gramado, RS", e
    // ficar só com "Gramado" perderia metade sem dizer nada.
    const rotulo = partes.slice(2).join(separador).trim()
    ranges.push({ inicio, fim, rotulo })
  })

  if (!ranges.length && cabecalho) {
    erros.push(
      `linha ${cabecalho.numero}: "${cabecalho.texto}" foi tratada como `
      + 'cabeçalho e não sobrou período nenhum — se ela era um período, as '
      + 'datas precisam vir como 2026-07-15 ou 15/07/2026')
  }
  return { ranges, erros }
}

/**
 * O texto de um `File`, via FileReader.
 *
 * `File.text()` seria uma linha e não existe no jsdom onde os testes rodam —
 * ou seja, o caminho testado não seria o caminho executado. O FileReader existe
 * nos dois, e é a única razão de esta função ter três linhas em vez de uma.
 */
export function textoDoArquivo(arquivo) {
  return new Promise((resolve, reject) => {
    const leitor = new FileReader()
    leitor.onload = () => resolve(String(leitor.result || ''))
    leitor.onerror = () => reject(leitor.error || new Error('falha na leitura'))
    leitor.readAsText(arquivo, 'utf-8')
  })
}

/**
 * Junta os períodos lidos aos que já estão na tela, sem repetir.
 *
 * Importar o mesmo arquivo duas vezes é o acidente óbvio desta funcionalidade —
 * o input não some depois do uso — e a segunda importação não pode dobrar a
 * lista. A identidade é a JANELA (ida e volta), não o nome: o mesmo período com
 * o nome escrito diferente continua sendo a mesma viagem, e dois períodos
 * iguais marcariam as mesmas compras duas vezes.
 */
export function juntarPeriodos(atuais, novos) {
  const vistos = new Set(atuais.map((r) => `${r.inicio}|${r.fim}`))
  const adicionados = []
  for (const r of novos) {
    const chave = `${r.inicio}|${r.fim}`
    if (vistos.has(chave)) continue
    vistos.add(chave)
    adicionados.push(r)
  }
  return { lista: [...atuais, ...adicionados], adicionados: adicionados.length,
           repetidos: novos.length - adicionados.length }
}
