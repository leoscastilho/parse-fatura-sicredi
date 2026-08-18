"""Edição do YAML: o teste que impede a perda silenciosa de comentários.

O `categories.yml` é editado por inserção e remoção de LINHA justamente para
preservar os `# ?` e a ordem escolhida a mão. Um refactor que trocasse isso por
`yaml.dump` passaria em qualquer teste de "o valor foi gravado?" e destruiria o
arquivo. Por isso aqui a contagem de comentários é asserção de primeira classe.
"""

from __future__ import annotations

import pytest
import yaml

from core import Ruleset
from core.yaml_edit import (
    YamlEditError, add_category, add_keyword, add_to_list, list_entries,
    list_rules, move_entry, remove_entry, rule_add, rule_move, rule_remove,
    rule_update, set_comment,
)

SAMPLE = """# cabeçalho explicativo
# segunda linha do cabeçalho

configuracao:
  categoria_padrao: ""
  categorias:
    - Alimentação
    - Casa
    - Lazer

excluir:
  - PAGAMENTO DEBITO

# ---- regras ordenadas ----
regras:
  # estornos primeiro
  - padrao: 'CREDITO ANUIDADE'
    categoria: Ajuste
  - padrao: '^IOF'
    categoria: Imposto          # imposto, não tarifa

# ---- palavras-chave ----
palavras:

  Alimentação:
    - SUPERMERCADO
    - OGGI                     # ? gelateria — histórico oscila

  Casa:
    - MOVEIS

marketplaces:
  - AMAZON

desconhecidos:
  - FULANO DE TAL
"""


def comentarios(texto: str) -> int:
    return sum(1 for linha in texto.splitlines() if "#" in linha)


# ---------------------------------------------------------------------------
# Palavras-chave
# ---------------------------------------------------------------------------

def test_add_keyword_preserva_comentarios():
    novo = add_keyword(SAMPLE, "Casa", "SOFA")
    assert comentarios(novo) == comentarios(SAMPLE)
    assert "SOFA" in yaml.safe_load(novo)["palavras"]["Casa"]
    assert len(novo.splitlines()) == len(SAMPLE.splitlines()) + 1


def test_add_keyword_cria_categoria_inexistente():
    novo = add_keyword(SAMPLE, "Saúde", "DROGARIA")
    assert yaml.safe_load(novo)["palavras"]["Saúde"] == ["DROGARIA"]
    assert comentarios(novo) == comentarios(SAMPLE)


def test_remove_entry_tira_so_a_linha_certa():
    novo = remove_entry(SAMPLE, "palavras", "Alimentação", "OGGI")
    dados = yaml.safe_load(novo)
    assert dados["palavras"]["Alimentação"] == ["SUPERMERCADO"]
    assert dados["palavras"]["Casa"] == ["MOVEIS"]
    # Some 1 comentário: o `# ?` que estava na própria linha do OGGI.
    assert comentarios(novo) == comentarios(SAMPLE) - 1


def test_remove_entry_respeita_a_categoria():
    """`MOVEIS` existe em Casa; pedir para removê-lo de Alimentação tem que falhar."""
    with pytest.raises(YamlEditError, match="não encontrado"):
        remove_entry(SAMPLE, "palavras", "Alimentação", "MOVEIS")


def test_move_entry_troca_de_categoria():
    novo = move_entry(SAMPLE, "Alimentação", "Lazer", "OGGI")
    dados = yaml.safe_load(novo)
    assert "OGGI" not in dados["palavras"]["Alimentação"]
    assert "OGGI" in dados["palavras"]["Lazer"]


def test_add_category_mantem_ordem_alfabetica():
    novo = add_category(SAMPLE, "Cachorro")
    categorias = yaml.safe_load(novo)["configuracao"]["categorias"]
    assert categorias == ["Alimentação", "Cachorro", "Casa", "Lazer"]


def test_add_to_list_em_bloco_plano():
    novo = add_to_list(SAMPLE, "desconhecidos", "CICLANO", ["# comentário"])
    assert yaml.safe_load(novo)["desconhecidos"] == ["FULANO DE TAL", "CICLANO"]


def test_list_entries_encontra_os_chutes():
    entradas = list_entries(SAMPLE)
    chutes = [e for e in entradas if e["flagged"]]
    assert len(chutes) == 1
    assert chutes[0]["value"] == "OGGI"
    assert chutes[0]["categoria"] == "Alimentação"


def test_list_entries_separa_os_blocos():
    blocos = {e["block"] for e in list_entries(SAMPLE)}
    assert blocos == {"palavras", "marketplaces", "desconhecidos", "excluir"}


# ---------------------------------------------------------------------------
# Regras ordenadas (regex)
# ---------------------------------------------------------------------------

def test_list_rules_le_padrao_categoria_e_comentario():
    regras = list_rules(SAMPLE)
    assert [r["padrao"] for r in regras] == ["CREDITO ANUIDADE", "^IOF"]
    assert regras[1]["categoria"] == "Imposto"
    assert regras[1]["comment"] == "imposto, não tarifa"


def test_rule_move_inverte_a_ordem_e_mantem_comentarios():
    novo = rule_move(SAMPLE, 1, -1)
    assert [r["padrao"] for r in list_rules(novo)] == ["^IOF", "CREDITO ANUIDADE"]
    assert comentarios(novo) == comentarios(SAMPLE)
    # E o resto do arquivo não foi tocado.
    assert yaml.safe_load(novo)["palavras"] == yaml.safe_load(SAMPLE)["palavras"]


def test_rule_add_com_barra_invertida_sobrevive_ao_roundtrip():
    """Aspas duplas em YAML interpretariam `\\s` — por isso a escrita usa simples."""
    padrao = r"^IFOOD\s*\*"
    novo = rule_add(SAMPLE, padrao, "Alimentação")
    assert list_rules(novo)[-1]["padrao"] == padrao
    assert Ruleset.from_text(novo).ordered_rules[-1][0].pattern == padrao


def test_rule_add_recusa_regex_invalido():
    import re
    with pytest.raises(re.error):
        rule_add(SAMPLE, "MERCADO(", "Casa")


def test_rule_update_troca_padrao_e_categoria():
    novo = rule_update(SAMPLE, 0, "CREDITO ANUIDADE|ESTORNO", "Ajuste")
    assert list_rules(novo)[0]["padrao"] == "CREDITO ANUIDADE|ESTORNO"
    assert len(list_rules(novo)) == 2


def test_rule_remove_nao_engole_o_rodape():
    """O comentário `# ---- palavras-chave ----` vem depois da última regra."""
    novo = rule_remove(SAMPLE, 1)
    assert "# ---- palavras-chave ----" in novo
    assert len(list_rules(novo)) == 1
    assert yaml.safe_load(novo)["palavras"]["Casa"] == ["MOVEIS"]


def test_rule_index_invalido_falha():
    with pytest.raises(YamlEditError, match="não existe"):
        rule_remove(SAMPLE, 99)


def test_ordem_das_regras_decide_o_resultado():
    """A primeira que casa vence — é isso que dá sentido ao botão de reordenar."""
    texto = (
        "configuracao: {categoria_padrao: ''}\n"
        "regras:\n"
        "  - padrao: 'MERCADO'\n"
        "    categoria: Casa\n"
        "  - padrao: 'MERCADO LIVRE'\n"
        "    categoria: Lazer\n"
        "palavras: {}\n"
    )
    assert Ruleset.from_text(texto).classify("MERCADO LIVRE").categoria == "Casa"
    invertido = rule_move(texto, 1, -1)
    assert Ruleset.from_text(invertido).classify("MERCADO LIVRE").categoria == "Lazer"


def test_set_comment_limpa_o_chute_sem_mudar_o_valor():
    novo = set_comment(SAMPLE, "palavras", "Alimentação", "OGGI", "")
    dados = yaml.safe_load(novo)
    assert dados["palavras"]["Alimentação"] == ["SUPERMERCADO", "OGGI"]
    assert not [e for e in list_entries(novo) if e["flagged"]]
    assert len(novo.splitlines()) == len(SAMPLE.splitlines())


def test_set_comment_pode_escrever_uma_nota():
    novo = set_comment(SAMPLE, "palavras", "Alimentação", "OGGI", "confirmado 2026")
    entrada = next(e for e in list_entries(novo) if e["value"] == "OGGI")
    assert entrada["comment"] == "confirmado 2026"
    assert entrada["flagged"] is False


def test_set_comment_em_entrada_inexistente_falha():
    with pytest.raises(YamlEditError, match="não encontrado"):
        set_comment(SAMPLE, "palavras", "Casa", "NAO EXISTE", "")
