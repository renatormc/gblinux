"""PDFs mínimos para os testes, sem dependência de arquivos externos."""

from __future__ import annotations


def gerar_pdf(paginas: int = 1) -> bytes:
    objetos: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count %d /Kids [%s] >>"
        % (paginas, b" ".join(b"%d 0 R" % (3 + 2 * i) for i in range(paginas))),
    ]
    for i in range(paginas):
        objetos.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents %d 0 R >>" % (4 + 2 * i)
        )
        fluxo = b"BT /F1 12 Tf 100 700 Td (Pagina %d) Tj ET" % (i + 1)
        objetos.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(fluxo), fluxo))
    saida = bytearray(b"%PDF-1.7\n")
    posicoes = []
    for numero, objeto in enumerate(objetos, 1):
        posicoes.append(len(saida))
        saida += b"%d 0 obj\n%s\nendobj\n" % (numero, objeto)
    inicio_xref = len(saida)
    saida += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objetos) + 1)
    for posicao in posicoes:
        saida += b"%010d 00000 n \n" % posicao
    saida += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objetos) + 1,
        inicio_xref,
    )
    return bytes(saida)
