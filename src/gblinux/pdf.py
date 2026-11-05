"""Assinatura visível e estrutura do PDF.

O arquivo recebe duas atualizações incrementais, o que preserva os bytes originais:
a primeira cria o widget e a segunda preenche a assinatura.
"""

from __future__ import annotations

import math
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from io import BytesIO

from asn1crypto import cms, x509
from PIL import Image, ImageDraw, ImageFont
from pyhanko.pdf_utils import generic
from pyhanko.pdf_utils.generic import pdf_name
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign.signers.pdf_byterange import PdfByteRangeDigest

from .erros import PDF_INVALIDO, ErroAssinatura

TAMANHO_CONTENTS = 8192
_FLAG_IMPRIMIR = 4
_FLAG_IMPRIMIR_BLOQUEADO = 132
_FONTES = (
    "/usr/share/fonts/truetype/msttcorefonts/Arial.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/liberation-sans/LiberationSans-Regular.ttf",
    "/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",
)


# ---------------------------------------------------------------- leitura


def _obter(dicionario: generic.DictionaryObject, chave: str, padrao=None):
    """Como `dict.get`, mas resolve referências indiretas; o `get` do pyHanko não resolve."""
    return dicionario[chave] if chave in dicionario else padrao


def _campos_assinados(raiz: generic.DictionaryObject) -> list[str]:
    """Nomes dos campos já assinados, na ordem do PDF e sem repetição.

    Alguns geradores repetem a mesma referência em /Fields uma vez por página, por isso a repetição é
    descartada; cada assinatura precisa contar uma única vez no cálculo da posição.
    """
    form = _obter(raiz, "/AcroForm")
    if form is None:
        return []
    nomes: list[str] = []
    visitados: set[int | tuple[int, int]] = set()

    def visitar(campos, prefixo: str, tipo_herdado) -> None:
        for bruto in campos:
            chave = (
                (bruto.idnum, bruto.generation)
                if isinstance(bruto, generic.IndirectObject)
                else id(bruto)
            )
            if chave in visitados:
                continue
            visitados.add(chave)
            campo = bruto.get_object()
            if not isinstance(campo, generic.DictionaryObject):
                continue
            parcial = str(_obter(campo, "/T", ""))
            nome = ".".join(p for p in (prefixo, parcial) if p)
            tipo = _obter(campo, "/FT", tipo_herdado)
            if tipo == "/Sig" and "/V" in campo and nome not in nomes:
                nomes.append(nome)
            filhos = _obter(campo, "/Kids")
            if filhos is not None:
                visitar(filhos, nome, tipo)

    visitar(_obter(form, "/Fields", generic.ArrayObject()), "", None)
    return nomes


def _referencias_paginas(raiz: generic.DictionaryObject) -> list[generic.IndirectObject]:
    paginas: list[generic.IndirectObject] = []
    visitados: set[tuple[int, int]] = set()

    def visitar(referencia) -> None:
        chave = (referencia.idnum, referencia.generation)
        if chave in visitados:
            return
        visitados.add(chave)
        no = referencia.get_object()
        if "/Kids" in no:
            for filho in _elementos(no["/Kids"]):
                visitar(filho)
        else:
            paginas.append(referencia)

    visitar(raiz.raw_get("/Pages"))
    return paginas


def _elementos(lista: generic.ArrayObject) -> Iterator:
    """Elementos da lista sem resolver as referências indiretas."""
    return (lista.raw_get(i) for i in range(len(lista)))


def _exige_edicao_permitida(raiz: generic.DictionaryObject) -> None:
    """Uma certificação DocMDP com /P 1 proíbe qualquer alteração no documento."""
    permissoes = _obter(raiz, "/Perms")
    if permissoes is None or "/DocMDP" not in permissoes:
        return
    for referencia in _obter(permissoes["/DocMDP"], "/Reference", generic.ArrayObject()):
        parametros = _obter(referencia.get_object(), "/TransformParams")
        if parametros is not None and int(_obter(parametros, "/P", 2)) == 1:
            raise ValueError("documento certificado sem permissão de alteração")


def _abrir_para_edicao(pdf: bytes) -> IncrementalPdfFileWriter:
    escritor = IncrementalPdfFileWriter(BytesIO(pdf), strict=False)
    if escritor.prev.encrypted:
        raise ValueError("documento criptografado")
    return escritor


def validar(pdf: bytes) -> None:
    """Falha antes do pedido de senha, para não gastar uma tentativa à toa."""
    try:
        leitor = PdfFileReader(BytesIO(pdf), strict=False)
        if leitor.encrypted:
            raise ValueError("documento criptografado")
        if not _referencias_paginas(leitor.root):
            raise ValueError("documento sem páginas")
        _exige_edicao_permitida(leitor.root)
        _campos_assinados(leitor.root)
    except Exception as erro:
        raise ErroAssinatura(PDF_INVALIDO) from erro


# ---------------------------------------------------------------- geometria


@dataclass(frozen=True)
class Posicao:
    x1: float
    y1: float
    x2: float
    y2: float
    nome_campo: str


def calcular_posicao(nome: str, anteriores: list[str]) -> Posicao:
    n = 0
    deslocamento = 0.0
    for campo in anteriores:
        n += 1
        deslocamento += len(campo) * 3.6
        if n % 3 == 0:
            deslocamento = 0.0
    x1 = 10 + 25 * (n // 3)
    x2 = x1 + 35
    y1 = 65 + deslocamento
    y2 = y1 + len(nome) * 3.6
    return Posicao(x1, y1, x2, y2, f"{nome} - {n}")


def _numero(valor: float) -> generic.PdfObject:
    arredondado = round(valor, 4)
    if arredondado == int(arredondado):
        return generic.NumberObject(int(arredondado))
    return generic.FloatObject(Decimal(str(arredondado)))


# ---------------------------------------------------------------- aparência


def mascarar(nome: str) -> str:
    """`FULANO:12345678901` vira `FULANO:******78901`."""
    pessoa, _, cpf = nome.rpartition(":")
    return f"{pessoa}:******{cpf[6:]}"


def _fonte(tamanho: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for caminho in _FONTES:
        try:
            return ImageFont.truetype(caminho, tamanho)
        except OSError:
            continue
    return ImageFont.load_default(tamanho)


def desenhar_selo(nome: str, largura: float, altura: float) -> Image.Image:
    """Máscara alfa do selo: 2 px por ponto, com o texto subindo pela margem esquerda."""
    px_largura = math.ceil(largura * 2)
    px_altura = math.ceil(altura * 2)
    # Desenha deitado e gira: o topo da imagem deitada vira a esquerda da caixa.
    deitado = Image.new("L", (px_altura, px_largura), 0)
    desenho = ImageDraw.Draw(deitado)
    fonte = _fonte(12)
    desenho.text((0, 15), "Assinado eletronicamente por:", font=fonte, fill=255, anchor="ls")
    desenho.text((0, 30), mascarar(nome), font=fonte, fill=255, anchor="ls")
    return deitado.transpose(Image.Transpose.ROTATE_90)


def _stream(dicionario: dict, dados: bytes) -> generic.StreamObject:
    entradas = {pdf_name(k): v for k, v in dicionario.items()}
    entradas[pdf_name("/Filter")] = pdf_name("/FlateDecode")
    return generic.StreamObject(entradas, encoded_data=zlib.compress(dados))


def _aparencia(escritor: IncrementalPdfFileWriter, posicao: Posicao, nome: str):
    largura = posicao.x2 - posicao.x1
    altura = posicao.y2 - posicao.y1
    selo = desenhar_selo(nome, largura, altura)
    comuns = {
        "/Type": pdf_name("/XObject"),
        "/Subtype": pdf_name("/Image"),
        "/Width": generic.NumberObject(selo.width),
        "/Height": generic.NumberObject(selo.height),
        "/ColorSpace": pdf_name("/DeviceGray"),
        "/BitsPerComponent": generic.NumberObject(8),
    }
    mascara = escritor.add_object(_stream(comuns, selo.tobytes()))
    imagem = escritor.add_object(
        _stream({**comuns, "/SMask": mascara}, bytes(selo.width * selo.height))
    )
    conteudo = f"q {selo.width / 2:g} 0 0 {selo.height / 2:g} 0 0 cm /Im0 Do Q".encode()
    recursos = generic.DictionaryObject(
        {
            pdf_name("/XObject"): generic.DictionaryObject({pdf_name("/Im0"): imagem}),
        }
    )
    return escritor.add_object(
        _stream(
            {
                "/Type": pdf_name("/XObject"),
                "/Subtype": pdf_name("/Form"),
                "/BBox": generic.ArrayObject(
                    [_numero(0), _numero(0), _numero(largura), _numero(altura)]
                ),
                "/Resources": recursos,
            },
            conteudo,
        )
    )


# ---------------------------------------------------------------- revisões


def _obter_form(escritor: IncrementalPdfFileWriter) -> generic.DictionaryObject:
    """Devolve o /AcroForm já marcado para gravação, criando-o se não existir."""
    raiz = escritor.root
    bruto = raiz.raw_get("/AcroForm") if "/AcroForm" in raiz else None
    if bruto is None:
        form = generic.DictionaryObject({pdf_name("/Fields"): generic.ArrayObject()})
        raiz[pdf_name("/AcroForm")] = escritor.add_object(form)
        escritor.update_root()
        return form
    if isinstance(bruto, generic.IndirectObject):
        escritor.mark_update(bruto)
        return bruto.get_object()
    escritor.update_root()
    return bruto


def _anexar(
    escritor: IncrementalPdfFileWriter,
    dono: generic.DictionaryObject,
    chave: str,
    referencia: generic.IndirectObject,
) -> None:
    """Acrescenta `referencia` à lista `dono[chave]`, direta ou indireta, ou cria a lista."""
    if chave not in dono:
        dono[pdf_name(chave)] = generic.ArrayObject([referencia])
        escritor.update_container(dono)
        return
    bruto = dono.raw_get(chave)
    if isinstance(bruto, generic.IndirectObject):
        lista = bruto.get_object()
        if referencia not in lista:  # uma mesma lista pode ser compartilhada entre páginas
            lista.append(referencia)
            escritor.mark_update(bruto)
    else:
        bruto.append(referencia)
        escritor.update_container(dono)


def _revisao_campo(pdf: bytes, nome: str) -> tuple[bytes, int, Posicao]:
    escritor = _abrir_para_edicao(pdf)
    paginas = _referencias_paginas(escritor.root)
    posicao = calcular_posicao(nome, _campos_assinados(escritor.root))
    widget = generic.DictionaryObject(
        {
            pdf_name("/FT"): pdf_name("/Sig"),
            pdf_name("/Type"): pdf_name("/Annot"),
            pdf_name("/Subtype"): pdf_name("/Widget"),
            pdf_name("/F"): generic.NumberObject(_FLAG_IMPRIMIR),
            pdf_name("/DA"): generic.TextStringObject("/Helv 0 Tf 0 g"),
            pdf_name("/T"): generic.TextStringObject(posicao.nome_campo),
            pdf_name("/P"): paginas[0],
            pdf_name("/Rect"): generic.ArrayObject(
                [
                    _numero(posicao.x1),
                    _numero(posicao.y1),
                    _numero(posicao.x2),
                    _numero(posicao.y2),
                ]
            ),
        }
    )
    referencia = escritor.add_object(widget)
    for pagina in paginas:
        _anexar(escritor, pagina.get_object(), "/Annots", referencia)
    # A referência aparece uma única vez em /Fields, mesmo com o widget em várias páginas.
    form = _obter_form(escritor)
    _anexar(escritor, form, "/Fields", referencia)

    saida = BytesIO()
    escritor.write(saida)
    return saida.getvalue(), referencia.idnum, posicao


def _data_pdf(agora: datetime) -> str:
    deslocamento = agora.strftime("%z") or "+0000"
    return f"D:{agora:%Y%m%d%H%M%S}{deslocamento[:3]}'{deslocamento[3:5]}'"


@dataclass
class DocumentoPreparado:
    """PDF com /Contents reservado, à espera do CMS."""

    digest: bytes
    _geracao: Iterator
    _saida: BytesIO
    posicao: Posicao

    def embutir(self, der: bytes) -> bytes:
        self._geracao.send(der)
        return self._saida.getvalue()


def preparar(pdf: bytes, nome: str, reserva: int, agora: datetime | None = None) -> DocumentoPreparado:
    """Grava as duas revisões, deixando /Contents vazio, e calcula o hash do /ByteRange."""
    agora = agora or datetime.now().astimezone()
    try:
        revisao, id_widget, posicao = _revisao_campo(pdf, nome)
        escritor = _abrir_para_edicao(revisao)
        assinatura = PdfByteRangeDigest(bytes_reserved=reserva * 2)
        assinatura[pdf_name("/Type")] = pdf_name("/Sig")
        assinatura[pdf_name("/Filter")] = pdf_name("/Adobe.PPKLite")
        assinatura[pdf_name("/SubFilter")] = pdf_name("/adbe.pkcs7.detached")
        assinatura[pdf_name("/M")] = generic.TextStringObject(_data_pdf(agora))
        for chave in ("/Reason", "/Location", "/ContactInfo"):
            assinatura[pdf_name(chave)] = generic.TextStringObject("")
        referencia_assinatura = escritor.add_object(assinatura)

        referencia_widget = generic.IndirectObject(id_widget, 0, escritor)
        widget = referencia_widget.get_object()
        widget[pdf_name("/F")] = generic.NumberObject(_FLAG_IMPRIMIR_BLOQUEADO)
        widget[pdf_name("/V")] = referencia_assinatura
        widget[pdf_name("/AP")] = generic.DictionaryObject(
            {pdf_name("/N"): _aparencia(escritor, posicao, nome)}
        )
        escritor.mark_update(referencia_widget)

        form = _obter_form(escritor)
        form[pdf_name("/SigFlags")] = generic.NumberObject(3)

        geracao = assinatura.fill(escritor, "sha256")
        preparado, saida = next(geracao)
    except ErroAssinatura:
        raise
    except Exception as erro:
        raise ErroAssinatura(PDF_INVALIDO) from erro
    return DocumentoPreparado(preparado.document_digest, geracao, saida, posicao)


# ---------------------------------------------------------------- CMS


def reserva_contents(
    assinante: x509.Certificate, cadeia: list[x509.Certificate], tamanho_chave_bytes: int
) -> int:
    """Espaço para o CMS: 8192 bytes, ou mais se a cadeia real exigir.

    A assinatura RSA tem sempre o tamanho da chave e o hash tem 32 bytes. Por isso, montar o CMS
    com valores vazios desses tamanhos dá o tamanho exato do CMS final.
    """
    modelo = montar_cms(bytes(32), assinante, cadeia, lambda _: bytes(tamanho_chave_bytes))
    return max(TAMANHO_CONTENTS, len(modelo))


def atributos_assinados(digest: bytes) -> cms.CMSAttributes:
    return cms.CMSAttributes(
        [
            cms.CMSAttribute({"type": "content_type", "values": ["data"]}),
            cms.CMSAttribute({"type": "message_digest", "values": [digest]}),
        ]
    )


def montar_cms(
    digest: bytes,
    assinante: x509.Certificate,
    cadeia: list[x509.Certificate],
    assinar: Callable[[bytes], bytes],
) -> bytes:
    atributos = atributos_assinados(digest)
    assinatura = assinar(atributos.dump())
    algoritmo_hash = cms.DigestAlgorithm({"algorithm": "sha256"})
    info = cms.SignerInfo(
        {
            "version": "v1",
            "sid": cms.SignerIdentifier(
                {
                    "issuer_and_serial_number": cms.IssuerAndSerialNumber(
                        {"issuer": assinante.issuer, "serial_number": assinante.serial_number}
                    )
                }
            ),
            "digest_algorithm": algoritmo_hash,
            "signed_attrs": atributos,
            "signature_algorithm": cms.SignedDigestAlgorithm({"algorithm": "rsassa_pkcs1v15"}),
            "signature": assinatura,
        }
    )
    dados = cms.SignedData(
        {
            "version": "v1",
            "digest_algorithms": [algoritmo_hash],
            "encap_content_info": {"content_type": "data"},
            "certificates": [cms.CertificateChoices({"certificate": c}) for c in [assinante, *cadeia]],
            "signer_infos": [info],
        }
    )
    return cms.ContentInfo({"content_type": "signed_data", "content": dados}).dump()

