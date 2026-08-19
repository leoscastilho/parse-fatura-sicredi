"""Análise do histórico completo.

O que estes testes protegem, em ordem de importância:

  1. **O que conta como gasto.** Receita, carregamento (Poupança/Resgate) e
     artefato ficam fora da conta. Errar aqui não dá erro nenhum — dá um número
     plausível e errado, que é o pior tipo de bug num painel.
  2. **A identidade do mês zerado**: receita − gasto − poupança + resgate ≈ 0.
     É a única checagem capaz de apontar lançamento FALTANDO.
  3. **O leitor tolerante.** Cabeçalho com menos colunas que as linhas, `Feb-12`
     ao lado de `02/15/2019`, `-R$ 0.27` com o sinal antes do símbolo. São 14
     anos de planilha; o formato mudou pelo caminho e o leitor tem que aguentar.
  4. **A régua da anomalia é a própria categoria.** R$ 800 é rotina em Casa e
     gritante em Cachorro.
"""

from __future__ import annotations

import pytest

from core.analytics import (
    ARTEFATO, CARREGAMENTO, GASTO, RECEITA, AnalyticsConfig, AnalyticsError,
    analisar, anomalias, categoria_por_periodo, custo_fixo_mensal,
    meses_faltando, meses_que_nao_fecham, pares_que_se_anulam, parse_periodo,
    parse_valor, por_categoria, possivel_dupla_contagem, read_ledger,
    recorrentes, recortar, saude, serie_mensal,
)

CONFIG = """
papeis:
  receita: [Renda Fixa]
  carregamento: [Poupança, Resgate Poupança]
  artefato: [Restante]
nao_detalhado: [Cartão de crédito]
sinonimos:
  hobby: Hobby
  Outro: Outros
cartao:
  marcador: "[Cartão]"
recorrentes:
  minimo_ocorrencias: 3
  meses_recentes: 2
anomalias:
  desvios: 3.0
  minimo_meses: 4
  minimo_valor: 50.0
"""


@pytest.fixture
def cfg():
    return AnalyticsConfig.from_text(CONFIG)


def ledger(linhas: list[tuple], cabecalho: str = "Data,Categoria,Descrição,Valor (R$),Pago") -> str:
    corpo = "\n".join(",".join(f'"{c}"' if "," in str(c) else str(c) for c in linha)
                      for linha in linhas)
    return f"{cabecalho}\n{corpo}\n"


# ---------------------------------------------------------------------------
# parse_valor — o formato do Sheets
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bruto,esperado", [
    ("R$ 1,234.56", 1234.56),
    ("R$ 70.00", 70.0),
    ("-R$ 0.27", -0.27),          # o sinal vem ANTES do símbolo
    ("R$ 55,327.76", 55327.76),
    ("1.234,56", 1234.56),        # formato brasileiro
    ("270.50", 270.50),
    ("1234", 1234.0),
    ("(R$ 10.00)", -10.0),        # parênteses como negativo
    ("", None),
    ("   ", None),
    ("abc", None),
])
def test_parse_valor(bruto, esperado):
    assert parse_valor(bruto) == esperado


def test_valor_ilegivel_nao_derruba_a_analise(cfg):
    """Numa planilha de 14 anos há célula quebrada. Avisa e segue."""
    texto = ledger([
        ("Feb-12", "Casa", "Luz", "R$ 70.00", "x"),
        ("Feb-12", "Casa", "Água", "sei lá", "x"),
    ])
    lancamentos, avisos = read_ledger(texto, cfg)
    assert len(lancamentos) == 1
    assert "1 linha(s) com valor ilegível" in avisos[0]


# ---------------------------------------------------------------------------
# parse_periodo — a data mudou de formato ao longo dos anos
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("data,mes,ano,esperado", [
    ("Feb-12", "", "", (2012, 2)),
    ("02/15/2019", "", "", (2019, 2)),
    ("Aug", "8", "2026", (2026, 8)),
    ("", "3", "2024", (2024, 3)),
    ("qualquer coisa", "7", "2025", (2025, 7)),
    ("", "", "", (None, None)),
])
def test_parse_periodo(data, mes, ano, esperado):
    assert parse_periodo(data, mes, ano) == esperado


def test_colunas_numericas_vencem_a_coluna_data(cfg):
    """`Data` diz Aug e `Ano` diz 2018, mas o lançamento é de 2026.

    O `all.csv` real tem exatamente essa linha. As colunas numéricas são
    geradas por fórmula e não têm a variação de formato da coluna Data — mas
    quando as duas discordam, quem manda são elas, e o teste fixa isso.
    """
    texto = ledger([("Aug", "Casa", "Luz", "R$ 500.00", "x", "8", "2026", "TRUE")])
    lancamentos, _ = read_ledger(texto, cfg)
    assert lancamentos[0].periodo == "2026-08"


def test_le_linhas_mais_largas_que_o_cabecalho(cfg):
    """O `all.csv` declara 5 colunas e traz 8. Mês e Ano estão nas extras."""
    texto = ledger([
        ("Feb-12", "Casa", "Luz", "R$ 70.00", "x", "2", "2012", "FALSE"),
        ("Mar-12", "Casa", "Água", "R$ 30.00", "x", "3", "2012", "FALSE"),
    ])
    lancamentos, _ = read_ledger(texto, cfg)
    assert [l.periodo for l in lancamentos] == ["2012-02", "2012-03"]


def test_acha_o_cabecalho_abaixo_de_linhas_de_titulo(cfg):
    """A planilha exportada traz título e linha em branco antes do cabeçalho."""
    texto = ("a,,,,\n,Minhas contas,,,\n"
             "Data,Categoria,Descrição,Valor (R$),Pago\n"
             "Feb-12,Casa,Luz,R$ 70.00,x\n")
    lancamentos, _ = read_ledger(texto, cfg)
    assert len(lancamentos) == 1 and lancamentos[0].categoria == "Casa"


# ---------------------------------------------------------------------------
# Papéis — o que entra na conta de gasto
# ---------------------------------------------------------------------------

def test_receita_e_carregamento_ficam_fora_do_gasto(cfg):
    """O teste central. Somar tudo dá um número plausível e errado."""
    texto = ledger([
        ("Jan-24", "Casa", "Luz", "R$ 100.00", "x", "1", "2024", "F"),
        ("Jan-24", "Renda Fixa", "Salário", "R$ 5000.00", "x", "1", "2024", "F"),
        ("Jan-24", "Poupança", "Para o mês seguinte", "R$ 900.00", "x", "1", "2024", "F"),
        ("Jan-24", "Resgate Poupança", "Do mês anterior", "R$ 200.00", "x", "1", "2024", "F"),
        ("Jan-24", "Restante", "Restante do mês", "R$ 4200.00", "", "1", "2024", "F"),
    ])
    lancamentos, _ = read_ledger(texto, cfg)
    mensal = serie_mensal(lancamentos)[0]

    assert mensal["gasto"] == 100.00, "só a luz é gasto"
    assert mensal["receita"] == 5000.00
    assert mensal["carregamento"] == 1100.00
    papeis = {l.categoria: l.papel for l in lancamentos}
    assert papeis["Restante"] == ARTEFATO
    assert papeis["Poupança"] == CARREGAMENTO


def test_categoria_desconhecida_e_gasto(cfg):
    """O caso comum. Classificar um gasto novo como gasto nunca erra."""
    texto = ledger([("Jan-24", "Categoria Inventada", "X", "R$ 10.00", "x", "1", "2024", "F")])
    lancamentos, _ = read_ledger(texto, cfg)
    assert lancamentos[0].papel == GASTO


def test_sinonimos_unificam_a_categoria(cfg):
    """`hobby` e `Hobby` são uma fatia só no gráfico, não duas."""
    texto = ledger([
        ("Jan-24", "hobby", "Jogo", "R$ 50.00", "x", "1", "2024", "F"),
        ("Jan-24", "Hobby", "Outro jogo", "R$ 30.00", "x", "1", "2024", "F"),
    ])
    lancamentos, _ = read_ledger(texto, cfg)
    categorias = por_categoria(lancamentos)
    assert len(categorias) == 1
    assert categorias[0] == {"categoria": "Hobby", "total": 80.0, "lancamentos": 2,
                             "media": 40.0, "share": 1.0, "no_cartao": 0.0}


def test_marca_o_que_passou_no_cartao(cfg):
    texto = ledger([
        ("Jan-24", "Alimentação", "[Cartão] Supermercado {Em 3/Jan}", "R$ 200.00", "x", "1", "2024", "F"),
        ("Jan-24", "Alimentação", "Feira", "R$ 80.00", "x", "1", "2024", "F"),
    ])
    lancamentos, _ = read_ledger(texto, cfg)
    assert [l.cartao for l in lancamentos] == [True, False]
    assert serie_mensal(lancamentos)[0]["no_cartao"] == 200.0


def test_fatura_agregada_conta_como_gasto_mas_fica_marcada(cfg):
    """A fatura numa linha só É gasto — o dinheiro saiu.

    O que ela não é é DETALHADA: ninguém sabe quanto foi mercado ou transporte.
    Tratá-la como artefato apagaria gasto real; ignorar a distinção faria a tela
    de categorias mentir por omissão.
    """
    texto = ledger([("Jan-24", "Cartão de crédito", "Fatural do mês atual",
                     "R$ 3000.00", "x", "1", "2024", "F")])
    lancamentos, _ = read_ledger(texto, cfg)
    assert lancamentos[0].papel == GASTO
    assert lancamentos[0].nao_detalhado is True
    assert serie_mensal(lancamentos)[0]["nao_detalhado"] == 3000.0


# ---------------------------------------------------------------------------
# A identidade do mês zerado
# ---------------------------------------------------------------------------

def test_mes_que_fecha_nao_aparece(cfg):
    texto = ledger([
        ("Jan-24", "Renda Fixa", "Salário", "R$ 5000.00", "x", "1", "2024", "F"),
        ("Jan-24", "Casa", "Aluguel", "R$ 3000.00", "x", "1", "2024", "F"),
        ("Jan-24", "Poupança", "Para fevereiro", "R$ 2500.00", "x", "1", "2024", "F"),
        ("Jan-24", "Resgate Poupança", "De dezembro", "R$ 500.00", "x", "1", "2024", "F"),
    ])
    lancamentos, _ = read_ledger(texto, cfg)
    assert meses_que_nao_fecham(lancamentos) == []


def test_lancamento_faltando_aparece_no_saldo(cfg):
    """Some R$ 300 de gasto e a identidade acusa — é o ponto dela."""
    texto = ledger([
        ("Jan-24", "Renda Fixa", "Salário", "R$ 5000.00", "x", "1", "2024", "F"),
        ("Jan-24", "Casa", "Aluguel", "R$ 2700.00", "x", "1", "2024", "F"),
        ("Jan-24", "Poupança", "Para fevereiro", "R$ 2500.00", "x", "1", "2024", "F"),
        ("Jan-24", "Resgate Poupança", "De dezembro", "R$ 500.00", "x", "1", "2024", "F"),
    ])
    lancamentos, _ = read_ledger(texto, cfg)
    fora = meses_que_nao_fecham(lancamentos)
    assert len(fora) == 1 and fora[0]["saldo"] == 300.0


def test_mes_so_de_gastos_nao_e_cobrado_pela_identidade(cfg):
    """Sem receita nem carregamento a conta não se aplica — e acusar seria ruído."""
    texto = ledger([("Jan-24", "Casa", "Luz", "R$ 100.00", "x", "1", "2024", "F")])
    lancamentos, _ = read_ledger(texto, cfg)
    assert meses_que_nao_fecham(lancamentos) == []


def test_pares_que_se_anulam_viram_um_achado_so():
    """Jan sobra 45 e fev falta 45: é um lançamento no mês errado, não R$ 90 sumidos."""
    pares = pares_que_se_anulam([
        {"periodo": "2024-01", "saldo": -45.0},
        {"periodo": "2024-02", "saldo": 45.0},
        {"periodo": "2024-06", "saldo": -168.17},
    ])
    assert pares == [{"a": "2024-01", "b": "2024-02", "valor": 45.0}]


# ---------------------------------------------------------------------------
# Séries
# ---------------------------------------------------------------------------

def test_mes_sem_lancamento_nao_vira_zero(cfg):
    """Zerar faria a linha despencar e inventar um mês de austeridade.

    O histórico real tem 23 meses assim (ago/2015 a set/2016). O gráfico precisa
    poder desenhar buraco, e para isso o mês tem que estar AUSENTE da série.
    """
    texto = ledger([
        ("Jan-24", "Casa", "Luz", "R$ 100.00", "x", "1", "2024", "F"),
        ("Mar-24", "Casa", "Luz", "R$ 120.00", "x", "3", "2024", "F"),
    ])
    lancamentos, _ = read_ledger(texto, cfg)
    assert [m["periodo"] for m in serie_mensal(lancamentos)] == ["2024-01", "2024-03"]
    assert meses_faltando(lancamentos) == ["2024-02"]


def test_categoria_por_periodo_dobra_a_cauda_em_outras(cfg):
    """A paleta tem 8 posições validadas; uma 9ª série exigiria inventar um tom."""
    linhas = [("Jan-24", f"Cat{i}", "x", f"R$ {100 - i}.00", "x", "1", "2024", "F")
              for i in range(10)]
    lancamentos, _ = read_ledger(ledger(linhas), cfg)
    empilhado = categoria_por_periodo(lancamentos, top=7)
    assert len(empilhado["categorias"]) == 8
    assert empilhado["categorias"][-1] == "Outras"
    # Nada se perde ao dobrar: a soma da pilha é o total do mês.
    assert round(sum(empilhado["periodos"][0]["valores"]), 2) == round(
        sum(l.valor for l in lancamentos), 2)


# ---------------------------------------------------------------------------
# Recorrentes
# ---------------------------------------------------------------------------

def test_recorrente_precisa_de_meses_diferentes(cfg):
    """Cinco compras na mesma semana não são assinatura.

    Sem esta regra, uma ida a mais ao mercado no mesmo mês viraria "custo fixo"
    e inflaria justamente o número que responde "quanto do mês já está
    comprometido antes de eu decidir nada".
    """
    mesmo_mes = ledger([("Jan-24", "Alimentação", "Mercado", "R$ 100.00", "x", "1", "2024", "F")
                        for _ in range(6)])
    lancamentos, _ = read_ledger(mesmo_mes, cfg)
    assert recorrentes(lancamentos, cfg) == []

    varios = ledger([("x", "Casa", "Internet", "R$ 99.90", "x", str(m), "2024", "F")
                     for m in range(1, 5)])
    lancamentos, _ = read_ledger(varios, cfg)
    achados = recorrentes(lancamentos, cfg)
    assert len(achados) == 1
    assert achados[0]["meses"] == 4 and achados[0]["mediana"] == 99.90


def test_recorrente_mede_a_variacao_de_preco(cfg):
    """A luz saiu de R$ 70 para R$ 500 em 14 anos — isso é a informação."""
    linhas = [("x", "Casa", "Luz", f"R$ {v}.00", "x", str(m), "2024", "F")
              for m, v in zip(range(1, 7), [100, 100, 100, 200, 200, 200])]
    lancamentos, _ = read_ledger(ledger(linhas), cfg)
    achado = recorrentes(lancamentos, cfg)[0]
    assert achado["variacao"] == 1.0, "dobrou"


def test_custo_fixo_soma_so_os_ativos():
    ativos = [{"mediana": 100.0, "ativo": True}, {"mediana": 50.0, "ativo": True},
              {"mediana": 999.0, "ativo": False}]
    assert custo_fixo_mensal(ativos) == 150.0


def test_fatura_nao_entra_no_custo_fixo(cfg):
    """Aparece todo mês, mas é balde de gasto variável — não compromisso."""
    linhas = [("x", "Cartão de crédito", "Fatural do mês atual",
               f"R$ {v}.00", "x", str(m), "2024", "F")
              for m, v in zip(range(1, 7), [3000, 8000, 4000, 9000, 5000, 12000])]
    lancamentos, _ = read_ledger(ledger(linhas), cfg)
    assert recorrentes(lancamentos, cfg) == []


# ---------------------------------------------------------------------------
# Anomalias
# ---------------------------------------------------------------------------

def test_anomalia_e_medida_contra_a_propria_categoria(cfg):
    """R$ 800 é rotina em Casa e gritante em Cachorro."""
    linhas = []
    for m in range(1, 8):
        linhas.append(("x", "Casa", "Aluguel", "R$ 800.00", "x", str(m), "2024", "F"))
        linhas.append(("x", "Cachorro", "Ração", "R$ 80.00", "x", str(m), "2024", "F"))
    linhas.append(("x", "Cachorro", "Cirurgia", "R$ 800.00", "x", "8", "2024", "F"))
    linhas.append(("x", "Casa", "Aluguel", "R$ 800.00", "x", "8", "2024", "F"))

    lancamentos, _ = read_ledger(ledger(linhas), cfg)
    achados = anomalias(lancamentos, cfg)
    assert [a["categoria"] for a in achados] == ["Cachorro"]
    assert achados[0]["periodo"] == "2024-08"


def test_categoria_estavel_nao_gera_anomalia(cfg):
    linhas = [("x", "Casa", "Luz", "R$ 100.00", "x", str(m), "2024", "F")
              for m in range(1, 9)]
    lancamentos, _ = read_ledger(ledger(linhas), cfg)
    assert anomalias(lancamentos, cfg) == []


# ---------------------------------------------------------------------------
# Dupla contagem — detectada, não presumida
# ---------------------------------------------------------------------------

def test_fatura_agregada_sozinha_nao_e_dupla_contagem(cfg):
    texto = ledger([("Jan-24", "Cartão de crédito", "Fatural do mês atual",
                     "R$ 3000.00", "x", "1", "2024", "F")])
    lancamentos, _ = read_ledger(texto, cfg)
    assert possivel_dupla_contagem(lancamentos) == []


def test_itens_e_fatura_no_mesmo_mes_sao_denunciados(cfg):
    """Colar um extrato num mês que já tem a linha agregada dobra o total."""
    texto = ledger([
        ("Jan-24", "Cartão de crédito", "Fatural do mês atual", "R$ 3000.00", "x", "1", "2024", "F"),
        ("Jan-24", "Alimentação", "[Cartão] Mercado {Em 3/Jan}", "R$ 300.00", "x", "1", "2024", "F"),
    ])
    lancamentos, _ = read_ledger(texto, cfg)
    achado = possivel_dupla_contagem(lancamentos)
    assert achado == [{"periodo": "2024-01", "itens": 300.0, "fatura": 3000.0}]


# ---------------------------------------------------------------------------
# Ponta a ponta
# ---------------------------------------------------------------------------

def test_analisar_devolve_o_painel_inteiro(cfg):
    linhas = []
    for m in range(1, 13):
        linhas += [
            ("x", "Renda Fixa", "Salário", "R$ 5000.00", "x", str(m), "2025", "F"),
            ("x", "Casa", "Aluguel", "R$ 2000.00", "x", str(m), "2025", "F"),
            ("x", "Alimentação", "Mercado da semana", "R$ 600.00", "x", str(m), "2025", "F"),
        ]
    resultado = analisar(ledger(linhas), cfg)

    assert resultado["resumo"]["meses_com_dado"] == 12
    assert resultado["resumo"]["total_gasto"] == 31200.0
    assert resultado["resumo"]["total_receita"] == 60000.0
    assert resultado["resumo"]["media_mensal"] == 2600.0
    assert resultado["resumo"]["custo_fixo_mensal"] == 2600.0
    assert len(resultado["serie_mensal"]) == 12
    assert resultado["saude"]["meses_faltando"] == []


def test_arquivo_vazio_e_recusado(cfg):
    with pytest.raises(AnalyticsError):
        read_ledger("", cfg)


def test_arquivo_sem_as_colunas_necessarias(cfg):
    with pytest.raises(AnalyticsError, match="não achei as colunas"):
        read_ledger("Coluna A,Coluna B\n1,2\n", cfg)


# ---------------------------------------------------------------------------
# Pela API
# ---------------------------------------------------------------------------

def test_endpoint_analisa_csv(client):
    csv_bytes = ledger([
        ("Jan-24", "Casa", "Luz", "R$ 100.00", "x", "1", "2024", "F"),
        ("Feb-24", "Casa", "Luz", "R$ 120.00", "x", "2", "2024", "F"),
    ]).encode("utf-8")
    resposta = client.post("/analytics", files={"file": ("all.csv", csv_bytes, "text/csv")})
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["arquivo"] == "all.csv"
    assert corpo["resumo"]["total_gasto"] == 220.0


def test_endpoint_recusa_nao_csv(client):
    resposta = client.post("/analytics",
                           files={"file": ("x.xls", b"\x00\x01", "application/vnd.ms-excel")})
    assert resposta.status_code == 415


def test_endpoint_explica_csv_ilegivel(client):
    resposta = client.post("/analytics",
                           files={"file": ("x.csv", b"a,b\n1,2\n", "text/csv")})
    assert resposta.status_code == 422
    assert "não achei as colunas" in resposta.json()["detail"]


def test_analise_nao_deixa_estado(client):
    """É leitura, não revisão: nada de transaction_id, nada gravado."""
    csv_bytes = ledger([("Jan-24", "Casa", "Luz", "R$ 100.00", "x", "1", "2024", "F")]).encode()
    corpo = client.post("/analytics",
                        files={"file": ("all.csv", csv_bytes, "text/csv")}).json()
    assert "transaction_id" not in corpo


# ---------------------------------------------------------------------------
# O CSV como o Google Sheets exporta
# ---------------------------------------------------------------------------

SHEETS_BRUTO = '''a,,,,,,,,
,Minhas contas,,,,,,,
,Data,Categoria,Item,Valor,Pago,Mês,Ano,Filtro
,Aug,Restante,Restante do mês de Agosto de 2026,"R$ 55,327.76",,8,2018,TRUE
,Feb-12,Renda Fixa,Salário AGT,R$ 689.43,x,2,2012,FALSE
,Feb-12,Casa,Conta de luz,R$ 70.00,x,2,2012,FALSE
,Mar-12,Saúde,Academia,R$ 25.00,x,3,2012,FALSE
,Mar-12,Ajuste,Estorno,-R$ 0.27,x,3,2012,FALSE
'''


def test_le_o_csv_como_o_sheets_exporta(cfg):
    """Coluna em branco à esquerda, título solto e uma linha `a,,,,` no topo.

    É o arquivo que sai do "baixar como CSV" com a formatação da planilha junto.
    Pedir para o usuário limpar antes seria transformar um detalhe do Sheets em
    trabalho manual recorrente.
    """
    lancamentos, _ = read_ledger(SHEETS_BRUTO, cfg)

    assert len(lancamentos) == 5
    por_desc = {l.descricao: l for l in lancamentos}
    assert por_desc["Conta de luz"].periodo == "2012-02"
    assert por_desc["Conta de luz"].papel == GASTO
    assert por_desc["Salário AGT"].papel == RECEITA
    # `Item` é o nome da coluna de descrição neste formato.
    assert por_desc["Academia"].categoria == "Saúde"
    # Negativo com o sinal antes do R$.
    assert por_desc["Estorno"].valor == -0.27


def test_a_coluna_extra_da_esquerda_nao_vira_categoria(cfg):
    """O erro que isto previne: ler tudo deslocado em uma coluna e acabar com
    `Data` na categoria e `Categoria` na descrição, sem nenhum erro visível."""
    lancamentos, _ = read_ledger(SHEETS_BRUTO, cfg)
    assert {l.categoria for l in lancamentos} == {
        "Restante", "Renda Fixa", "Casa", "Saúde", "Ajuste"}


def test_restante_nao_entra_no_gasto_mesmo_vindo_do_sheets(cfg):
    """R$ 55 mil de saldo no topo do arquivo não podem virar despesa."""
    lancamentos, _ = read_ledger(SHEETS_BRUTO, cfg)
    total = sum(m["gasto"] for m in serie_mensal(lancamentos))
    assert total == round(70.00 + 25.00 - 0.27, 2)


def test_custo_fixo_usa_o_preco_de_hoje(cfg):
    """A luz saiu de R$ 100 para R$ 500; o comprometido é R$ 500, não a mediana.

    Somar a mediana da série inteira responderia "quanto isso custava em média
    nos últimos anos", que não é a pergunta — a pergunta é quanto do mês que
    vem já está gasto.
    """
    linhas = [("x", "Casa", "Luz", f"R$ {v}.00", "x", str(m), "2025", "F")
              for m, v in zip(range(1, 13), [100] * 6 + [500] * 6)]
    lancamentos, _ = read_ledger(ledger(linhas), cfg)
    achado = recorrentes(lancamentos, cfg)[0]

    assert achado["mediana"] == 300.0, "mediana da série inteira"
    assert achado["mediana_recente"] == 300.0, "12 meses: ainda pega tudo"
    assert custo_fixo_mensal([achado]) == 300.0

    # Com histórico mais longo, a diferença aparece.
    longas = [("x", "Casa", "Luz", "R$ 100.00", "x", str(m), "2023", "F")
              for m in range(1, 13)] + linhas
    lancamentos, _ = read_ledger(ledger(longas), cfg)
    achado = recorrentes(lancamentos, cfg)[0]
    assert achado["mediana"] == 100.0, "24 meses: a maioria é o preço antigo"
    assert achado["mediana_recente"] == 300.0, "os 12 últimos já são o preço novo"
    assert custo_fixo_mensal([achado]) == 300.0


# ---------------------------------------------------------------------------
# Recorte por período — o filtro global da aba
# ---------------------------------------------------------------------------

def _tres_anos(cfg):
    linhas = []
    for ano in (2024, 2025, 2026):
        for m in range(1, 13):
            linhas += [
                ("x", "Renda Fixa", "Salário", "R$ 5000.00", "x", str(m), str(ano), "F"),
                ("x", "Casa", "Aluguel", f"R$ {1000 * (ano - 2023)}.00", "x", str(m), str(ano), "F"),
            ]
    return read_ledger(ledger(linhas), cfg)[0]


def test_recorte_respeita_as_duas_pontas(cfg):
    lancamentos = _tres_anos(cfg)
    dentro = recortar(lancamentos, "2025-01", "2025-12")
    assert {l.periodo[:4] for l in dentro} == {"2025"}
    assert len(dentro) == 24


def test_recorte_sem_limites_devolve_tudo(cfg):
    lancamentos = _tres_anos(cfg)
    assert recortar(lancamentos, None, None) is lancamentos


def test_lancamento_sem_data_nunca_entra_no_recorte(cfg):
    """Uma compra sem data não pertence a período nenhum.

    Deixá-la passar faria "últimos 12 meses" incluir um terreno de 2021 — e o
    total do recorte deixaria de bater com a soma dos meses mostrados.
    """
    texto = ledger([
        ("", "Construção", "Terreno", "R$ 35000.00", "x", "", "", "F"),
        ("Jan-25", "Casa", "Luz", "R$ 100.00", "x", "1", "2025", "F"),
    ])
    lancamentos, _ = read_ledger(texto, cfg)
    assert len(lancamentos) == 2
    assert len(recortar(lancamentos, "2025-01", "2025-12")) == 1


def test_todas_as_metricas_seguem_o_recorte(cfg):
    """O ponto do filtro global: nenhuma métrica pode ignorá-lo.

    Média mensal, custo fixo e categorias precisam ser RECALCULADOS sobre o
    período — fatiar depois de agregar daria números que não batem entre si.
    """
    texto = ledger([("x", "Casa", "Aluguel", f"R$ {1000 * (ano - 2023)}.00",
                     "x", str(m), str(ano), "F")
                    for ano in (2024, 2025, 2026) for m in range(1, 13)])

    tudo = analisar(texto, cfg)
    so_2026 = analisar(texto, cfg, inicio="2026-01", fim="2026-12")

    assert tudo["resumo"]["meses_com_dado"] == 36
    assert so_2026["resumo"]["meses_com_dado"] == 12
    assert so_2026["resumo"]["media_mensal"] == 3000.0, "só o aluguel de 2026"
    assert so_2026["resumo"]["custo_fixo_mensal"] == 3000.0
    assert so_2026["por_categoria"][0]["total"] == 36000.0
    assert len(so_2026["serie_mensal"]) == 12
    assert len(so_2026["anual"]) == 1


def test_intervalo_disponivel_ignora_o_recorte(cfg):
    """O seletor de datas precisa dos limites do ARQUIVO, não do recorte —
    senão, depois de filtrar, não haveria como voltar."""
    texto = ledger([("x", "Casa", "Luz", "R$ 100.00", "x", str(m), str(a), "F")
                    for a in (2024, 2026) for m in (1, 6)])
    recortado = analisar(texto, cfg, inicio="2026-01", fim="2026-12")
    assert recortado["intervalo_disponivel"] == {"inicio": "2024-01", "fim": "2026-06"}
    assert recortado["filtro"] == {"inicio": "2026-01", "fim": "2026-12"}


def test_recorte_vazio_explica_o_que_existe(cfg):
    texto = ledger([("x", "Casa", "Luz", "R$ 100.00", "x", "1", "2024", "F")])
    with pytest.raises(AnalyticsError, match="O arquivo cobre 2024-01"):
        analisar(texto, cfg, inicio="2030-01", fim="2030-12")


def test_endpoint_aceita_o_intervalo(client):
    csv_bytes = ledger([("x", "Casa", "Luz", "R$ 100.00", "x", str(m), str(a), "F")
                        for a in (2024, 2026) for m in (1, 6)]).encode()
    resposta = client.post("/analytics",
                           files={"file": ("all.csv", csv_bytes, "text/csv")},
                           data={"inicio": "2026-01", "fim": "2026-12"})
    assert resposta.status_code == 200
    assert resposta.json()["resumo"]["total_gasto"] == 200.0


# ---------------------------------------------------------------------------
# O resíduo do mês diz o que fazer
# ---------------------------------------------------------------------------

def test_residuo_negativo_e_resgate_faltando(cfg):
    """Gastou mais do que entrou: o dinheiro veio de algum lugar não lançado."""
    texto = ledger([
        ("Jun-21", "Renda Fixa", "Salário", "R$ 5000.00", "x", "6", "2021", "F"),
        ("Jun-21", "Construção", "Terreno", "R$ 40000.00", "x", "6", "2021", "F"),
    ])
    lancamentos, _ = read_ledger(texto, cfg)
    fora = meses_que_nao_fecham(lancamentos)
    assert fora[0]["tipo"] == "falta_resgate"
    assert fora[0]["saldo"] == -35000.0


def test_residuo_positivo_e_sobra_sem_destino(cfg):
    texto = ledger([
        ("Jan-25", "Renda Fixa", "Salário", "R$ 5000.00", "x", "1", "2025", "F"),
        ("Jan-25", "Casa", "Luz", "R$ 100.00", "x", "1", "2025", "F"),
    ])
    lancamentos, _ = read_ledger(texto, cfg)
    fora = meses_que_nao_fecham(lancamentos)
    assert fora[0]["tipo"] == "sobra_sem_destino"
    assert fora[0]["saldo"] == 4900.0


def test_saude_devolve_todos_os_meses_que_nao_fecham(cfg):
    """Cortar em 12 escondia o resto — e o resto é justamente a lista de
    coisas a arrumar."""
    linhas = [("x", "Renda Fixa", "Salário", "R$ 5000.00", "x", str(m), "2025", "F")
              for m in range(1, 13)]
    lancamentos, _ = read_ledger(ledger(linhas), cfg)
    assert len(saude(lancamentos, [])["meses_que_nao_fecham"]) == 12
