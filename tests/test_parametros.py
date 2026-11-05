import base64
from urllib.parse import quote

from gblinux.parametros import cpf_valido, decodificar_query, parece_base64


def b64(texto: str) -> str:
    return base64.b64encode(texto.encode()).decode()


def test_query_vazia():
    p = decodificar_query("")
    assert (p.cpf, p.senha, p.file_download) == ("", "", "")


def test_decodifica_e_ignora_desconhecidos():
    q = f"nocache=1&cpf={b64('12345678909')}&senha={b64('abc')}&fileDownload={b64('http://x/a.pdf')}"
    p = decodificar_query(q)
    assert p.cpf == "12345678909"
    assert p.senha == "abc"
    assert p.file_download == "http://x/a.pdf"


def test_padding_ausente_e_percent_encoded():
    sem_padding = b64("abcd1").rstrip("=")
    assert decodificar_query(f"senha={sem_padding}").senha == "abcd1"
    assert decodificar_query(f"senha={quote(b64('abcd1'), safe='')}").senha == "abcd1"


def test_mais_do_alfabeto_base64_permanece():
    valor = base64.b64encode(b"\xfb\xef\xbe").decode()
    assert "+" in valor
    assert decodificar_query(f"senha={valor}").senha.encode("utf-8", "surrogateescape") == b"\xfb\xef\xbe"


def test_senha_nao_sofre_url_decode_mas_cpf_e_url_sim():
    p = decodificar_query(f"senha={b64('a+b%20c')}&fileDownload={b64('http://x/a+b%20c.pdf')}")
    assert p.senha == "a+b%20c"
    assert p.file_download == "http://x/a b c.pdf"


def test_cpf():
    assert cpf_valido("12345678909")
    assert cpf_valido("52998224725")
    assert not cpf_valido("12345678900")
    assert not cpf_valido("11111111111")
    assert not cpf_valido("123.456.789-09")
    assert not cpf_valido("")


def test_regex_base64():
    assert parece_base64("QUJD\r\nREVG==")
    assert not parece_base64("file:///etc/hostname")
    assert not parece_base64("http://x")
