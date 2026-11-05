"""Fluxo completo de assinatura, com SoftHSM e HTTP de verdade."""

from __future__ import annotations

import base64
import http.client
import http.server
import json
import logging
import re
import shutil
import socket
import subprocess
import threading
from io import BytesIO

import pytest
from pdfs import gerar_pdf
from pyhanko.pdf_utils.reader import PdfFileReader

from gblinux.config import Config
from gblinux import erros
from gblinux.servico import ServicoAssinatura
from gblinux.servidor import Contexto, PortaOcupada, Servidores

from conftest import CPF_1, CPF_2, NOME_1, NOME_2, SENHA

pdfsig_disponivel = shutil.which("pdfsig") is not None


def b64(dados: bytes | str) -> str:
    if isinstance(dados, str):
        dados = dados.encode()
    return base64.b64encode(dados).decode()


def porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class PerguntasFalsas:
    def __init__(self, senha=SENHA, escolha=None):
        self.senha = senha
        self.escolha = escolha
        self.pedidos_senha = 0
        self.opcoes = None

    def escolher_certificado(self, opcoes, tempo_s):
        self.opcoes = opcoes
        return self.escolha

    def pedir_senha(self, titular, origem, quantidade, tempo_s):
        self.pedidos_senha += 1
        return self.senha

    def cancelar_perguntas(self):
        pass


class Ambiente:
    def __init__(self, biblioteca, perguntas):
        self.perguntas = perguntas
        config = Config(biblioteca_pkcs11=biblioteca, limite_bytes=5_000_000)
        self.servico = ServicoAssinatura(config, perguntas)
        for _ in range(20):  # a porta livre em IPv4 pode estar em uso em IPv6
            self.pagina, self.direta = porta_livre(), porta_livre()
            self.servidores = Servidores(Contexto(config, self.servico), self.pagina, self.direta)
            try:
                self.servidores.iniciar()
                break
            except PortaOcupada:
                continue

    def pedir(self, metodo, caminho, corpo=None, porta=None, headers=None, host="127.0.0.1"):
        conexao = http.client.HTTPConnection(host, porta or self.pagina, timeout=120)
        conteudo = json.dumps(corpo) if isinstance(corpo, (dict, list)) else corpo
        conexao.request(metodo, caminho, body=conteudo, headers=headers or {})
        resposta = conexao.getresponse()
        dados = resposta.read().decode()
        conexao.close()
        return resposta, dados

    def assinar_url(self, cpf=CPF_1, senha=SENHA, extra=""):
        return f"/assinarPdf?nocache=1&cpf={b64(cpf)}&senha={b64(senha)}{extra}"

    def fechar(self):
        self.servidores.parar()
        self.servico.encerrar()


@pytest.fixture
def ambiente(token):
    criados = []

    def criar(nome="um", perguntas=None, espiar=False):
        ativo = token(nome, espiar)
        amb = Ambiente(ativo["biblioteca"], perguntas or PerguntasFalsas())
        amb.log_spy = ativo["log_spy"]
        criados.append(amb)
        return amb

    yield criar
    for amb in criados:
        amb.fechar()


def verificar_pdfsig(dados: bytes, tmp_path) -> str:
    arquivo = tmp_path / "saida.pdf"
    arquivo.write_bytes(dados)
    saida = subprocess.run(
        ["pdfsig", str(arquivo)], capture_output=True, text=True, check=False
    ).stdout
    return saida


def extrair_cms(dados: bytes) -> bytes:
    casamentos = list(re.finditer(rb"/Contents\s*<([0-9a-fA-F]+)>", dados))
    return bytes.fromhex(casamentos[-1].group(1).decode())


@pytest.mark.skipif(not pdfsig_disponivel, reason="pdfsig ausente")
def test_assina_e_valida(ambiente, tmp_path):
    amb = ambiente()
    original = gerar_pdf()
    resposta, corpo = amb.pedir("POST", amb.assinar_url(), {"documento": b64(original)})
    assert resposta.status == 200, corpo
    assert resposta.getheader("Content-Type") == "application/json, charset=UTF-8"
    assert resposta.getheader("Access-Control-Allow-Origin") == "*"
    assert "\n" not in corpo and not corpo.startswith('"')
    assinado = base64.b64decode(corpo)
    assert assinado.startswith(original)

    relatorio = verificar_pdfsig(assinado, tmp_path)
    assert "Signature is Valid" in relatorio, relatorio
    assert "adbe.pkcs7.detached" in relatorio
    assert "SHA-256" in relatorio

    cms = tmp_path / "a.der"
    cms.write_bytes(extrair_cms(assinado))
    impresso = subprocess.run(
        ["openssl", "cms", "-cmsout", "-print", "-inform", "DER", "-in", str(cms)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    atributos = re.findall(r"object: (\S+) \(([\d.]+)\)", impresso.split("signedAttrs")[1].split("signatureAlgorithm")[0])
    assert sorted(o for o, _ in atributos) == ["contentType", "messageDigest"]
    assert re.search(r"unsignedAttrs:\s*<ABSENT>", impresso)
    assert "rsaEncryption" in impresso.split("signatureAlgorithm")[1]


def _leitor(dados: bytes) -> PdfFileReader:
    return PdfFileReader(BytesIO(dados), strict=False)


def test_duas_paginas_e_rect(ambiente, tmp_path):
    amb = ambiente()
    resposta, corpo = amb.pedir("POST", amb.assinar_url(), {"documento": b64(gerar_pdf(2))})
    assert resposta.status == 200, corpo
    assinado = base64.b64decode(corpo)
    leitor = _leitor(assinado)
    campos = leitor.root["/AcroForm"]["/Fields"]
    assert len(campos) == 1
    widget = campos[0]
    assert [float(v) for v in widget["/Rect"]] == [10, 65, 45, 151.4]
    assert widget["/T"] == f"{NOME_1} - 0"
    assert int(widget["/F"]) == 132
    assert int(leitor.root["/AcroForm"]["/SigFlags"]) == 3
    assert widget["/DA"] == "/Helv 0 Tf 0 g"
    # Um único objeto, referenciado em cada página.
    referencia = leitor.root["/AcroForm"]["/Fields"].raw_get(0)
    paginas = leitor.root["/Pages"]["/Kids"]
    assert len(paginas) == 2
    for pagina in paginas:
        assert pagina.get_object()["/Annots"].raw_get(0) == referencia
    assert widget["/P"] == paginas[0].get_object()
    assinatura = widget["/V"]
    assert assinatura["/Filter"] == "/Adobe.PPKLite"
    assert assinatura["/SubFilter"] == "/adbe.pkcs7.detached"
    assert (assinatura["/Reason"], assinatura["/Location"], assinatura["/ContactInfo"]) == ("", "", "")
    assert re.fullmatch(r"D:\d{14}[+-]\d\d'\d\d'", assinatura["/M"])
    assert len(assinatura.raw_get("/Contents")) == 8192
    # Duas atualizações incrementais: revisão do campo e revisão da assinatura.
    assert assinado.count(b"%%EOF") == gerar_pdf(2).count(b"%%EOF") + 2


@pytest.mark.skipif(not pdfsig_disponivel, reason="pdfsig ausente")
def test_assinar_de_novo(ambiente, tmp_path):
    amb = ambiente()
    original = gerar_pdf(2)
    _, primeiro = amb.pedir("POST", amb.assinar_url(), {"documento": b64(original)})
    uma = base64.b64decode(primeiro)
    resposta, segundo = amb.pedir("POST", amb.assinar_url(), {"documento": b64(uma)})
    assert resposta.status == 200, segundo
    duas = base64.b64decode(segundo)
    assert duas.startswith(uma) and duas.startswith(original)
    relatorio = verificar_pdfsig(duas, tmp_path)
    assert relatorio.count("Signature is Valid") == 2, relatorio
    widget = _leitor(duas).root["/AcroForm"]["/Fields"][1]
    assert widget["/T"] == f"{NOME_1} - 1"
    # O campo anterior tem 28 caracteres.
    assert [round(float(v), 4) for v in widget["/Rect"]] == [10, 165.8, 45, 252.2]


def test_filedownload_e_file_recusado(ambiente, tmp_path):
    amb = ambiente()
    original = gerar_pdf()

    class Pdf(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/redir":
                self.send_response(302)
                self.send_header("Location", "/a.pdf")
                self.end_headers()
            elif self.path == "/arquivo-local":
                self.send_response(302)
                self.send_header("Location", "file:///etc/hostname")
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header("Content-Length", str(len(original)))
                self.end_headers()
                self.wfile.write(original)

        def log_message(self, *_):
            pass

    servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Pdf)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{servidor.server_address[1]}"
        for caminho in ("/a.pdf", "/redir"):
            url = amb.assinar_url(extra=f"&fileDownload={b64(base + caminho)}")
            resposta, corpo = amb.pedir("GET", url)
            assert resposta.status == 200, corpo
            assert base64.b64decode(corpo).startswith(original)

        for destino in ("file:///etc/hostname", base + "/arquivo-local"):
            url = amb.assinar_url(extra=f"&fileDownload={b64(destino)}")
            resposta, corpo = amb.pedir("GET", url)
            assert resposta.status == 400
            assert corpo.startswith("Não foi possivel baixar o arquivo -> ")
            assert resposta.getheader("Content-Type") is None
    finally:
        servidor.shutdown()
        servidor.server_close()


def test_mesmo_rotulo_usa_o_valido(ambiente, pki):
    from cryptography.hazmat.primitives.serialization import Encoding

    amb = ambiente("um")
    _, corpo = amb.pedir("POST", amb.assinar_url(), {"documento": b64(gerar_pdf())})
    cms_der = extrair_cms(base64.b64decode(corpo))
    assert pki.pessoal.certificado.public_bytes(Encoding.DER) in cms_der
    assert pki.vencido.certificado.public_bytes(Encoding.DER) not in cms_der


def test_cadeia_escolhe_emissor_exato(ambiente, pki):
    from cryptography.hazmat.primitives.serialization import Encoding

    amb = ambiente("um")
    _, corpo = amb.pedir("POST", amb.assinar_url(), {"documento": b64(gerar_pdf())})
    cms_der = extrair_cms(base64.b64decode(corpo))
    assert pki.ac.certificado.public_bytes(Encoding.DER) in cms_der
    assert pki.raiz.certificado.public_bytes(Encoding.DER) in cms_der
    assert pki.ac_outra.certificado.public_bytes(Encoding.DER) not in cms_der


def test_senha_errada_um_unico_login(ambiente):
    amb = ambiente(espiar=True)
    resposta, corpo = amb.pedir(
        "POST", amb.assinar_url(senha="senhaerrada"), {"documento": b64(gerar_pdf())}
    )
    assert resposta.status == 400
    assert json.loads(corpo) == {"status": 400, "message": erros.SENHA_INCORRETA}
    registro = amb.log_spy.read_text(errors="replace")
    assert len(re.findall(r"C_Login\b", registro)) == 1
    assert "C_CloseSession" in registro
    assert "C_Finalize" in registro


def test_sucesso_faz_logout_close_finalize_em_ordem(ambiente):
    amb = ambiente(espiar=True)
    resposta, _ = amb.pedir("POST", amb.assinar_url(), {"documento": b64(gerar_pdf())})
    assert resposta.status == 200
    registro = amb.log_spy.read_text(errors="replace")
    posicoes = [registro.rindex(nome) for nome in ("C_Login", "C_Logout", "C_CloseSession", "C_Finalize")]
    assert posicoes == sorted(posicoes)
    assert len(re.findall(r"C_Login\b", registro)) == 1


def test_senha_curta_nao_chega_ao_token(ambiente):
    amb = ambiente(espiar=True)
    resposta, corpo = amb.pedir("POST", amb.assinar_url(senha="x"), {"documento": b64(gerar_pdf())})
    assert resposta.status == 400 and json.loads(corpo)["message"] == erros.SENHA_INCORRETA
    assert "C_Login" not in amb.log_spy.read_text(errors="replace")


def test_lote_pede_senha_uma_vez(ambiente):
    perguntas = PerguntasFalsas()
    amb = ambiente(perguntas=perguntas)
    documentos = [b64(gerar_pdf(i + 1)) for i in range(3)]
    resposta, corpo = amb.pedir(
        "POST", f"/assinarPdfs?cpf={b64(CPF_1)}", {"documentos": documentos, "assinarComGovBR": False}
    )
    assert resposta.status == 200, corpo
    assert perguntas.pedidos_senha == 1
    assinados = json.loads(corpo)
    assert len(assinados) == 3
    for i, item in enumerate(assinados):
        assert base64.b64decode(item).startswith(gerar_pdf(i + 1))


def test_lote_tudo_ou_nada(ambiente):
    amb = ambiente()
    documentos = [b64(gerar_pdf()), b64(b"nao e pdf")]
    resposta, corpo = amb.pedir("POST", amb.assinar_url().replace("assinarPdf", "assinarPdfs"), {"documentos": documentos})
    assert resposta.status == 400
    assert json.loads(corpo)["message"] == erros.PDF_INVALIDO


def test_pdf_invalido_nao_pede_senha(ambiente):
    perguntas = PerguntasFalsas()
    amb = ambiente(perguntas=perguntas)
    resposta, corpo = amb.pedir("POST", f"/assinarPdf?cpf={b64(CPF_1)}", {"documento": b64(b"lixo")})
    assert resposta.status == 400
    assert json.loads(corpo)["message"] == erros.PDF_INVALIDO
    assert perguntas.pedidos_senha == 0


def test_pdf_com_senha_recusado(ambiente):
    from pyhanko.pdf_utils.writer import copy_into_new_writer

    escritor = copy_into_new_writer(_leitor(gerar_pdf()))
    escritor.encrypt(owner_pass="dono", user_pass="usuario")
    saida = BytesIO()
    escritor.write(saida)
    amb = ambiente()
    resposta, corpo = amb.pedir("POST", amb.assinar_url(), {"documento": b64(saida.getvalue())})
    assert resposta.status == 400 and json.loads(corpo)["message"] == erros.PDF_INVALIDO


def test_options_e_rotas(ambiente):
    amb = ambiente()
    for porta in (amb.pagina, amb.direta):
        resposta, corpo = amb.pedir("OPTIONS", "/qualquer", porta=porta)
        assert resposta.status == 204 and corpo == ""
        assert resposta.getheader("Access-Control-Allow-Origin") == "*"
        assert resposta.getheader("Access-Control-Allow-Methods") == "POST, GET, OPTIONS"
        assert resposta.getheader("Access-Control-Allow-Headers") == "*"
        resposta, _ = amb.pedir("GET", "/naoexiste", porta=porta)
        assert resposta.status == 404
        assert resposta.getheader("Access-Control-Allow-Origin") == "*"
    resposta, corpo = amb.pedir("GET", "/assinadorLoteDisponivel")
    assert resposta.status == 200 and json.loads(corpo) == {"ativo": True}
    resposta, corpo = amb.pedir("GET", "/assinadorGovBrDisponivel")
    assert resposta.status == 200 and json.loads(corpo) == {"disponivel": False}


def test_somente_enderecos_locais(ambiente):
    amb = ambiente()
    saida = subprocess.run(["ss", "-ltnH"], capture_output=True, text=True, check=True).stdout
    enderecos = [
        coluna
        for linha in saida.splitlines()
        for coluna in linha.split()[3:4]
        if coluna.endswith((f":{amb.pagina}", f":{amb.direta}"))
    ]
    assert len(enderecos) >= 2
    assert all(e.startswith(("127.0.0.1:", "[::1]:")) for e in enderecos), enderecos


def test_ipv6_local(ambiente):
    amb = ambiente()
    try:
        resposta, _ = amb.pedir("GET", "/assinadorLoteDisponivel", host="::1")
    except OSError:
        pytest.skip("IPv6 indisponível")
    assert resposta.status == 200


def test_logs_sem_segredos(ambiente, caplog):
    amb = ambiente()
    caplog.set_level(logging.DEBUG)
    segredo = "SenhaSecreta99"
    url = amb.assinar_url(senha=segredo)
    amb.pedir("POST", url, {"documento": b64(gerar_pdf())})
    amb.pedir("POST", "/assinarPdf", {"documento": b64(gerar_pdf())}, porta=amb.direta, headers={"senha": segredo, "cpf": CPF_1})
    texto = caplog.text
    assert "/assinarPdf" in texto
    for proibido in (segredo, b64(segredo), CPF_1, b64(CPF_1), "nocache"):
        assert proibido not in texto


def test_sem_query_e_sem_documento(ambiente):
    amb = ambiente()
    resposta, corpo = amb.pedir("POST", "/assinarPdf")
    assert resposta.status == 400 and corpo == erros.SEM_DOCUMENTO
    assert resposta.getheader("Content-Type") is None


def test_assinar_pdfs_erros(ambiente):
    amb = ambiente()
    resposta, corpo = amb.pedir("POST", "/assinarPdfs", "{invalido")
    assert resposta.status == 400 and corpo.startswith("Erro ao processar JSON: ")
    resposta, corpo = amb.pedir("POST", "/assinarPdfs", {"documentos": ["", ""]})
    assert resposta.status == 400 and corpo == erros.SEM_DOCUMENTO
    resposta, corpo = amb.pedir("POST", "/assinarPdfs", {"documentos": ["file:///etc/hostname"]})
    assert resposta.status == 400 and corpo.startswith("Erro ao processar documento -> ")
    resposta, corpo = amb.pedir("POST", "/assinarPdfs", {"documentos": ["QUJD"], "assinarComGovBR": True})
    assert resposta.status == 500
    assert json.loads(corpo)["message"] == erros.GOVBR_INDISPONIVEL


def test_porta_4499(ambiente):
    amb = ambiente()
    headers = {"cpf": CPF_1, "senha": SENHA, "Content-Type": "application/json"}
    original = b64(gerar_pdf())
    resposta, corpo = amb.pedir("POST", "/assinarPdf", {"documento": original}, amb.direta, headers)
    assert resposta.status == 200, corpo
    resposta, corpo = amb.pedir("POST", "/assinarPdfs", {"documentos": [original, original]}, amb.direta, headers)
    assert resposta.status == 200 and len(json.loads(corpo)) == 2
    resposta, corpo = amb.pedir("POST", "/assinarPdf", "{ruim", amb.direta, headers)
    assert resposta.status == 400 and json.loads(corpo)["status"] == 400
    resposta, corpo = amb.pedir("POST", "/assinarPdfs", {"documentos": ["http://x/a.pdf"]}, amb.direta, headers)
    assert resposta.status == 400 and json.loads(corpo)["status"] == 400
    resposta, _ = amb.pedir("GET", "/assinarPdf", porta=amb.direta)
    assert resposta.status == 404


def test_corpo_acima_do_limite(ambiente):
    amb = ambiente()
    resposta, _ = amb.pedir("POST", "/assinarPdf", "x" * 5_000_001)
    assert resposta.status == 413


def test_escolha_de_certificado(ambiente):
    perguntas = PerguntasFalsas(escolha=NOME_2)
    amb = ambiente("dois", perguntas)
    resposta, corpo = amb.pedir("POST", f"/assinarPdf?senha={b64(SENHA)}", {"documento": b64(gerar_pdf())})
    assert resposta.status == 200, corpo
    assert sorted(perguntas.opcoes) == sorted([NOME_1, NOME_2])
    widget = _leitor(base64.b64decode(corpo)).root["/AcroForm"]["/Fields"][0]
    assert widget["/T"] == f"{NOME_2} - 0"


def test_cancelar_escolha_e_senha(ambiente):
    amb = ambiente("dois", PerguntasFalsas(escolha=None))
    resposta, corpo = amb.pedir("POST", "/assinarPdf", {"documento": b64(gerar_pdf())})
    assert json.loads(corpo) == {"status": 400, "message": erros.CANCELADO}
    assert resposta.status == 400
    amb2 = ambiente("um", PerguntasFalsas(senha=None))
    _, corpo = amb2.pedir("POST", "/assinarPdf", {"documento": b64(gerar_pdf())})
    assert json.loads(corpo)["message"] == erros.CANCELADO


def test_um_cpf_no_token_nao_abre_escolha(ambiente):
    perguntas = PerguntasFalsas()
    amb = ambiente("um", perguntas)
    resposta, _ = amb.pedir("POST", f"/assinarPdf?cpf={b64('invalido')}", {"documento": b64(gerar_pdf())})
    assert resposta.status == 200
    assert perguntas.opcoes is None


def test_erros_de_certificado_e_chave(ambiente):
    amb = ambiente("sem_chave")
    _, corpo = amb.pedir("POST", amb.assinar_url(), {"documento": b64(gerar_pdf())})
    assert json.loads(corpo)["message"] == erros.chave_nao_encontrada(CPF_1)
    _, corpo = amb.pedir("POST", amb.assinar_url(cpf=CPF_2), {"documento": b64(gerar_pdf())})
    assert json.loads(corpo)["message"] == erros.chave_nao_encontrada(CPF_2)
    amb2 = ambiente("so_acs")
    _, corpo = amb2.pedir("POST", "/assinarPdf", {"documento": b64(gerar_pdf())})
    assert json.loads(corpo)["message"] == erros.NENHUM_CERTIFICADO


def test_biblioteca_ausente(token):
    token()
    amb = Ambiente("/nao/existe/lib.so", PerguntasFalsas())
    try:
        _, corpo = amb.pedir("POST", amb.assinar_url(), {"documento": b64(gerar_pdf())})
    finally:
        amb.fechar()
    assert json.loads(corpo)["message"] == erros.TOKEN_NAO_ENCONTRADO


def test_pedidos_simultaneos_em_fila(ambiente):
    amb = ambiente()
    resultados = []

    def pedir():
        resposta, _ = amb.pedir("POST", amb.assinar_url(), {"documento": b64(gerar_pdf())})
        resultados.append(resposta.status)

    threads = [threading.Thread(target=pedir) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert resultados == [200] * 4


def test_comando_testar_assinatura(ambiente, tmp_path, monkeypatch, capsys):
    from gblinux import testar

    perguntas = PerguntasFalsas()
    amb = ambiente(perguntas=perguntas)
    entrada = tmp_path / "teste.pdf"
    entrada.write_bytes(gerar_pdf(2))
    porta = ["--porta", str(amb.pagina)]

    # Sem senha no pedido: quem pergunta é o assinador.
    assert testar.main([str(entrada), "--cpf", CPF_1, *porta]) == 0
    assert perguntas.pedidos_senha == 1
    saida = tmp_path / "teste-assinado.pdf"
    assert saida.read_bytes().startswith(entrada.read_bytes())
    assert "Signature is Valid" in capsys.readouterr().out

    # Com senha digitada no terminal: vai no pedido, e o assinador não pergunta de novo.
    monkeypatch.setattr(testar.getpass, "getpass", lambda _: SENHA)
    destino = tmp_path / "outro.pdf"
    assert testar.main([str(entrada), "--enviar-senha", "--saida", str(destino), *porta]) == 0
    assert perguntas.pedidos_senha == 1
    assert destino.exists()

    # Senha errada: mostra a mensagem do assinador e não grava nada.
    monkeypatch.setattr(testar.getpass, "getpass", lambda _: "senhaerrada")
    falha = tmp_path / "falha.pdf"
    assert testar.main([str(entrada), "--enviar-senha", "--saida", str(falha), *porta]) == 1
    assert erros.SENHA_INCORRETA in capsys.readouterr().err
    assert not falha.exists()

    assert testar.main([str(tmp_path / "nao-existe.pdf"), *porta]) == 2


def test_comando_sem_programa_aberto(tmp_path, capsys):
    from gblinux import testar

    entrada = tmp_path / "teste.pdf"
    entrada.write_bytes(gerar_pdf())
    assert testar.main([str(entrada), "--porta", str(porta_livre())]) == 1
    assert "uv run gblinux" in capsys.readouterr().err
