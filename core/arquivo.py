"""O CONTAINER do arquivo — antes de qualquer banco entrar na conversa.

Duas perguntas que não são do Sicredi nem do BTG, e sim do FORMATO em que o
arquivo chegou:

  * **Está protegido por senha?** O BTG manda a fatura como `.xlsx` cifrado, e
    isso não é uma assinatura de banco: é um invólucro. Reconhecê-lo aqui, e
    não no perfil, é o que permite a tela dizer "este arquivo pede senha" antes
    de saber de quem ele é — que é a ordem em que a pessoa vive o problema.

  * **Que texto dá para ler dele?** A detecção de banco procura assinatura no
    texto do arquivo, e um `.xlsx` é um ZIP: os primeiros 8 KB são cabeçalho de
    compactação, sem uma letra legível. Enquanto o Sicredi era o único a ler
    planilha isso não aparecia — a extensão já decidia sozinha. Com o BTG são
    dois, e sem abrir o ZIP a escolha entre eles viraria um empate insolúvel.

Nada aqui guarda a senha. Ela entra como argumento, é usada para decifrar em
memória e sai de escopo com a chamada; não vai para o SQLite, não vai para o
log, e nenhum endpoint a devolve.
"""

from __future__ import annotations

import io
import re
import zipfile
from html import unescape

# `D0CF11E0A1B11AE1` é o cabeçalho do OLE2/CDFV2 — o mesmo container do `.xls`
# antigo do Sicredi. Ele NÃO distingue cifrado de não-cifrado: serve só para
# não pagar o custo do msoffcrypto em CSV e em ZIP, que nunca são cifrados
# deste jeito.
MAGICA_OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
MAGICA_ZIP = b"PK\x03\x04"

# As partes do `.xlsx` que carregam texto de verdade. `sharedStrings.xml` tem
# quase tudo (o Excel guarda cada string uma vez só e as células apontam para
# ela); a primeira planilha entra por causa do arquivo que grava tudo inline.
PARTES_COM_TEXTO = ("xl/sharedStrings.xml", "xl/worksheets/sheet1.xml")

TAGS = re.compile(r"<[^>]*>")


class PrecisaDeSenha(RuntimeError):
    """O arquivo está cifrado e nenhuma senha foi informada."""


class SenhaIncorreta(RuntimeError):
    """A senha informada não abre o arquivo."""


def esta_protegido(blob: bytes) -> bool:
    """Este arquivo está cifrado por senha?

    Pergunta estrutural, respondida sem senha nenhuma — é o que permite o
    pré-voo dizer "digite a senha deste arquivo" em vez de estourar um erro de
    leitura que não explica nada.

    O `.xls` do site do Sicredi mora no MESMO container OLE2 de um `.xlsx`
    cifrado, então a mágica de 8 bytes não basta: quem responde é o
    msoffcrypto, olhando se existem os fluxos `EncryptionInfo` e
    `EncryptedPackage` lá dentro.

    A guarda da mágica é de CUSTO, não de correção — tirá-la não muda nenhuma
    resposta (um CSV faz o msoffcrypto levantar, e o `except` devolve False),
    só faz todo upload de CSV construir um `OfficeFile` para descobrir isso.
    Não há teste para ela de propósito: seria um teste de desempenho disfarçado.
    """
    if not blob.startswith(MAGICA_OLE2):
        return False
    try:
        import msoffcrypto

        return bool(msoffcrypto.OfficeFile(io.BytesIO(blob)).is_encrypted())
    except Exception:
        # Qualquer coisa que o msoffcrypto não entenda é, para os nossos fins,
        # um arquivo não protegido: quem vai reclamar é o leitor do banco, com
        # uma mensagem sobre o CONTEÚDO, que é a que ajuda.
        return False


def abrir_protegido(blob: bytes, senha: str) -> bytes:
    """Decifra em memória e devolve o `.xlsx` de dentro.

    Levanta `PrecisaDeSenha` quando não veio senha e `SenhaIncorreta` quando a
    que veio não abre. São dois estados diferentes para a tela: um pede o campo,
    o outro diz que o que foi digitado não confere.
    """
    if not senha:
        raise PrecisaDeSenha("arquivo protegido por senha")

    import msoffcrypto

    saida = io.BytesIO()
    documento = msoffcrypto.OfficeFile(io.BytesIO(blob))
    try:
        documento.load_key(password=senha)
        documento.decrypt(saida)
    except Exception as exc:  # InvalidKeyError, DecryptionError, ValueError…
        raise SenhaIncorreta("senha não confere") from exc
    return saida.getvalue()


def amostra_de_texto(blob: bytes, limite: int = 8192) -> str:
    """O texto legível do começo do arquivo, seja ele qual for.

    Para CSV e para o `.xls` antigo é o próprio começo do arquivo. Para o
    `.xlsx`, que é um ZIP, são as partes que guardam texto — sem isso a
    detecção enxergaria bytes comprimidos e nenhuma assinatura casaria.

    Não decifra nada: um arquivo protegido tem de passar por `abrir_protegido`
    antes, e é de propósito que essa ordem seja explícita em quem chama.
    """
    if blob.startswith(MAGICA_ZIP):
        return _texto_do_zip(blob, limite)
    return blob[:limite].decode("utf-8", errors="replace")


def _texto_do_zip(blob: bytes, limite: int) -> str:
    pedacos: list[str] = []
    restante = limite
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as arquivo:
            nomes = set(arquivo.namelist())
            for parte in PARTES_COM_TEXTO:
                if parte not in nomes or restante <= 0:
                    continue
                with arquivo.open(parte) as fluxo:
                    # Lê com folga porque a maior parte do XML são tags, que
                    # somem no `TAGS.sub` logo abaixo: ler só `restante` bytes
                    # brutos devolveria quase nada de texto.
                    bruto = fluxo.read(restante * 8).decode("utf-8", errors="replace")
                # `unescape` porque o acento pode não estar escrito como acento:
                # o Excel grava "Cartão" em UTF-8, mas o openpyxl grava
                # "Cart&#227;o". Sem desfazer a entidade, a assinatura de um
                # banco casaria ou não conforme o PROGRAMA que gerou a planilha
                # — e o mesmo extrato seria reconhecido num caminho e não no
                # outro, sem nada na tela explicando a diferença.
                texto = TAGS.sub(" ", unescape(bruto))[:restante]
                pedacos.append(texto)
                restante -= len(texto)
    except (zipfile.BadZipFile, OSError):
        return blob[:limite].decode("utf-8", errors="replace")
    return " ".join(pedacos)
