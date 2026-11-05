"""PDFs com estruturas variadas, assinados com chave em memória, sem token."""

from __future__ import annotations

import shutil
import subprocess
from io import BytesIO

import pytest
from asn1crypto import x509 as asn1_x509
from conftest import emitir
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import Encoding
from pdfs import gerar_pdf
from pyhanko.pdf_utils import generic
from pyhanko.pdf_utils.generic import pdf_name
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.pdf_utils.writer import copy_into_new_writer

from gblinux import erros, pdf

NOME = "FULANO SILVA:12345678909"


@pytest.fixture(scope="module")
def chave_cert():
    emitido = emitir(NOME)
    return emitido.chave, asn1_x509.Certificate.load(emitido.certificado.public_bytes(Encoding.DER))


def assinar(dados, chave_cert, nome=NOME, reserva=8192):
    chave, cert = chave_cert
    pdf.validar(dados)
    preparado = pdf.preparar(dados, nome, reserva)
    der = pdf.montar_cms(
        preparado.digest, cert, [], lambda d: chave.sign(d, padding.PKCS1v15(), hashes.SHA256())
    )
    return preparado.embutir(der)


def conferir(dados, tmp_path, quantidade=1):
    if shutil.which("pdfsig"):
        arquivo = tmp_path / "s.pdf"
        arquivo.write_bytes(dados)
        saida = subprocess.run(["pdfsig", str(arquivo)], capture_output=True, text=True).stdout
        assert saida.count("Signature is Valid") == quantidade, saida


def _editar(dados: bytes, funcao) -> bytes:
    escritor = IncrementalPdfFileWriter(BytesIO(dados), strict=False)
    funcao(escritor)
    saida = BytesIO()
    escritor.write(saida)
    return saida.getvalue()


def test_xref_stream_e_object_stream(chave_cert, tmp_path):
    leitor = PdfFileReader(BytesIO(gerar_pdf(2)), strict=False)
    escritor = copy_into_new_writer(leitor)
    escritor.stream_xrefs = True
    fluxo = BytesIO()
    escritor.write(fluxo)
    original = fluxo.getvalue()
    assert b"/XRef" in original
    assinado = assinar(original, chave_cert)
    assert assinado.startswith(original)
    conferir(assinado, tmp_path)
    assert len(PdfFileReader(BytesIO(assinado), strict=False).root["/AcroForm"]["/Fields"]) == 1


def test_formulario_e_anotacoes_existentes(chave_cert, tmp_path):
    def preparar(escritor):
        raiz = escritor.root
        outro = escritor.add_object(
            generic.DictionaryObject(
                {
                    pdf_name("/FT"): pdf_name("/Tx"),
                    pdf_name("/T"): generic.TextStringObject("texto"),
                }
            )
        )
        lista = escritor.add_object(generic.ArrayObject([outro]))
        raiz[pdf_name("/AcroForm")] = generic.DictionaryObject({pdf_name("/Fields"): lista})
        escritor.update_root()
        pagina = raiz["/Pages"]["/Kids"][0]
        pagina[pdf_name("/Annots")] = lista  # lista indireta, compartilhada com /Fields
        escritor.update_container(pagina)

    original = _editar(gerar_pdf(2), preparar)
    assinado = assinar(original, chave_cert)
    conferir(assinado, tmp_path)
    raiz = PdfFileReader(BytesIO(assinado), strict=False).root
    assert len(raiz["/AcroForm"]["/Fields"]) == 2
    assert len(raiz["/Pages"]["/Kids"][0]["/Annots"]) == 2
    assert len(raiz["/Pages"]["/Kids"][1]["/Annots"]) == 1
    assert int(raiz["/AcroForm"]["/SigFlags"]) == 3


def test_fields_repetido_conta_uma_vez(chave_cert):
    assinado = assinar(gerar_pdf(2), chave_cert)

    def repetir(escritor):
        form = escritor.root["/AcroForm"]
        campos = form["/Fields"]
        campos.append(campos.raw_get(0))
        escritor.update_container(campos if campos.container_ref else form)

    repetido = _editar(assinado, repetir)
    raiz = PdfFileReader(BytesIO(repetido), strict=False).root
    assert len(raiz["/AcroForm"]["/Fields"]) == 2
    assert pdf._campos_assinados(raiz) == [f"{NOME} - 0"]


def test_quatro_assinaturas_muda_coluna(chave_cert, tmp_path):
    dados = gerar_pdf()
    for _ in range(4):
        dados = assinar(dados, chave_cert)
    conferir(dados, tmp_path, 4)
    quarta = PdfFileReader(BytesIO(dados), strict=False).root["/AcroForm"]["/Fields"][3]
    assert quarta["/T"] == f"{NOME} - 3"
    assert float(quarta["/Rect"][0]) == 35


def test_docmdp_sem_alteracao_recusado():
    def certificar(escritor):
        parametros = generic.DictionaryObject(
            {pdf_name("/P"): generic.NumberObject(1), pdf_name("/Type"): pdf_name("/TransformParams")}
        )
        referencia = generic.DictionaryObject(
            {pdf_name("/TransformMethod"): pdf_name("/DocMDP"), pdf_name("/TransformParams"): parametros}
        )
        assinatura = escritor.add_object(
            generic.DictionaryObject({pdf_name("/Reference"): generic.ArrayObject([referencia])})
        )
        escritor.root[pdf_name("/Perms")] = generic.DictionaryObject({pdf_name("/DocMDP"): assinatura})
        escritor.update_root()

    with pytest.raises(erros.ErroAssinatura) as erro:
        pdf.validar(_editar(gerar_pdf(), certificar))
    assert erro.value.mensagem == erros.PDF_INVALIDO


def test_contents_cresce_com_a_cadeia(chave_cert, tmp_path):
    chave, cert = chave_cert
    # Uma cadeia pequena fica nos 8192 bytes padrão.
    assert pdf.reserva_contents(cert, [], 256) == 8192

    # Uma cadeia grande usa o tamanho exato, sem cortar nada e sem sobra.
    cadeia = [cert] * 12
    reserva = pdf.reserva_contents(cert, cadeia, 256)
    assert reserva > 8192
    preparado = pdf.preparar(gerar_pdf(), NOME, reserva)
    der = pdf.montar_cms(
        preparado.digest, cert, cadeia, lambda d: chave.sign(d, padding.PKCS1v15(), hashes.SHA256())
    )
    assert len(der) == reserva
    assinado = preparado.embutir(der)
    conferir(assinado, tmp_path)
    assinatura = PdfFileReader(BytesIO(assinado), strict=False).root["/AcroForm"]["/Fields"][0]["/V"]
    assert bytes(assinatura.raw_get("/Contents")) == der


def test_nome_com_acentos(chave_cert, tmp_path):
    nome = "JOÃO DA CONCEIÇÃO:12345678909"
    assinado = assinar(gerar_pdf(), chave_cert, nome=nome)
    conferir(assinado, tmp_path)
    campo = PdfFileReader(BytesIO(assinado), strict=False).root["/AcroForm"]["/Fields"][0]
    assert campo["/T"] == f"{nome} - 0"


def test_selo_tem_tamanho_da_caixa_e_texto_na_esquerda():
    selo = pdf.desenhar_selo(NOME, 35, 86.4)
    assert selo.size == (70, 173)
    # Colunas de pixels à esquerda têm tinta; a parte final da largura está vazia.
    assert selo.crop((0, 0, 36, 173)).getbbox() is not None
    assert selo.crop((40, 0, 70, 173)).getbbox() is None
