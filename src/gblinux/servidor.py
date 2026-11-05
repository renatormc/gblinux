"""Servidores HTTP das portas 3399 e 4499."""

from __future__ import annotations

import base64
import errno
import json
import logging
import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from . import erros, parametros
from .config import PORTA_DIRETA, PORTA_PAGINA, Config
from .download import FalhaDownload, baixar
from .erros import ErroAssinatura
from .servico import ServicoAssinatura

log = logging.getLogger("assinador.http")

_TIPO_JSON = "application/json, charset=UTF-8"  # malformado, mantido por compatibilidade com a página


class PortaOcupada(Exception):
    def __init__(self, porta: int) -> None:
        super().__init__(f"porta {porta} ocupada")
        self.porta = porta


@dataclass
class Contexto:
    config: Config
    servico: ServicoAssinatura
    ao_resultado: Callable[[str], None] = field(default=lambda _: None)


class _Resposta(Exception):
    """Interrompe o tratamento do pedido com uma resposta de erro já definida."""

    def __init__(self, status: int, corpo: str, tipo: str | None = None) -> None:
        super().__init__(corpo)
        self.status, self.corpo, self.tipo = status, corpo, tipo


def _erro_json(status: int, mensagem: str) -> _Resposta:
    texto = json.dumps(mensagem, ensure_ascii=False)
    return _Resposta(status, f'{{"status":{status}, "message": {texto}}}', _TIPO_JSON)


class _Tratador(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Assinador"
    sys_version = ""
    timeout = 120  # leitura de um cliente parado; não limita a duração da assinatura

    # ------------------------------------------------------------- infraestrutura

    def log_request(self, code="-", size="-") -> None:
        # Só o caminho: a query carrega CPF e senha.
        log.info("%s %s -> %s", self.command, self._caminho(), code)

    def log_error(self, format, *args) -> None:  # noqa: A002
        # A mensagem padrão inclui a linha do pedido, com a query.
        log.warning("Pedido HTTP rejeitado")

    def log_message(self, format, *args) -> None:  # noqa: A002
        pass

    def _caminho(self) -> str:
        return urlsplit(getattr(self, "path", "") or "").path

    def _query(self) -> str:
        return urlsplit(self.path).query

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def _responder(self, status: int, corpo: str = "", tipo: str | None = None) -> None:
        dados = corpo.encode("utf-8", "replace")
        self.close_connection = True
        self.send_response(status)
        if tipo:
            self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(dados)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(dados)

    def _ler_corpo(self) -> bytes:
        try:
            tamanho = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise _Resposta(400, "Content-Length inválido") from None
        if tamanho < 0:
            raise _Resposta(400, "Content-Length inválido")
        if tamanho > self.server.contexto.config.limite_bytes:
            # Descarta o corpo sem guardá-lo: fechar a conexão com dados pendentes faria o
            # navegador ver um erro de rede em vez desta resposta.
            while tamanho > 0:
                bloco = self.rfile.read(min(tamanho, 1024 * 1024))
                if not bloco:
                    break
                tamanho -= len(bloco)
            raise _Resposta(413, "O corpo excede o tamanho máximo permitido")
        return self.rfile.read(tamanho)

    def do_OPTIONS(self) -> None:
        self.close_connection = True
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Connection", "close")
        self.end_headers()

    def do_GET(self) -> None:
        self._despachar("GET")

    def do_POST(self) -> None:
        self._despachar("POST")

    def _despachar(self, metodo: str) -> None:
        rota = self.rotas.get((metodo, self._caminho()))
        if rota is None:
            self._responder(404)
            return
        contexto = self.server.contexto
        try:
            rota(self)
        except _Resposta as resposta:
            self._registrar_resultado(resposta.corpo, resposta.tipo)
            self._responder(resposta.status, resposta.corpo, resposta.tipo)
        except ErroAssinatura as erro:
            contexto.ao_resultado(erro.mensagem)
            resposta = _erro_json(400, erro.mensagem)
            self._responder(resposta.status, resposta.corpo, resposta.tipo)
        except (BrokenPipeError, ConnectionError, TimeoutError):
            log.info("Conexão encerrada pelo cliente")
        except Exception as erro:
            log.error("Erro interno: %s", type(erro).__name__)
            contexto.ao_resultado(erros.ERRO_DRIVER)
            resposta = _erro_json(400, erros.ERRO_DRIVER)
            self._responder(resposta.status, resposta.corpo, resposta.tipo)

    def _registrar_resultado(self, corpo: str, tipo: str | None) -> None:
        mensagem = corpo
        if tipo == _TIPO_JSON:
            try:
                mensagem = json.loads(corpo).get("message", corpo)
            except ValueError:
                pass
        self.server.contexto.ao_resultado(mensagem)

    def _assinar(self, cpf: str, senha: str, documentos: list[bytes]) -> list[bytes]:
        contexto = self.server.contexto
        origem = self.headers.get("Origin", "")
        assinados = contexto.servico.assinar(cpf, senha, documentos, origem)
        quantidade = len(assinados)
        contexto.ao_resultado(
            "1 documento assinado" if quantidade == 1 else f"{quantidade} documentos assinados"
        )
        return assinados

    def _sucesso_um(self, assinado: bytes) -> None:
        self._responder(200, base64.b64encode(assinado).decode(), _TIPO_JSON)

    def _sucesso_varios(self, assinados: list[bytes]) -> None:
        corpo = json.dumps([base64.b64encode(d).decode() for d in assinados], separators=(",", ":"))
        self._responder(200, corpo, _TIPO_JSON)

    rotas: dict = {}


class _TratadorPagina(_Tratador):
    """Porta 3399: parâmetros em Base64 na query e PDF por URL ou no corpo."""

    def _baixar(self, url: str, modelo: str) -> bytes:
        try:
            return baixar(url, self.server.contexto.config)
        except FalhaDownload as erro:
            raise _Resposta(400, f"{modelo} -> {erro}") from erro

    def _assinar_pdf(self) -> None:
        valores = parametros.decodificar_query(self._query())
        if valores.file_download:
            documento = self._baixar(valores.file_download, "Não foi possivel baixar o arquivo")
        else:
            documento = self._documento_do_corpo()
        self._sucesso_um(self._assinar(valores.cpf, valores.senha, [documento])[0])

    def _documento_do_corpo(self) -> bytes:
        try:
            conteudo = json.loads(self._ler_corpo() or b"{}")
        except ValueError:
            raise _Resposta(400, erros.SEM_DOCUMENTO) from None
        valor = conteudo.get("documento") if isinstance(conteudo, dict) else None
        if not isinstance(valor, str) or not valor.strip():
            raise _Resposta(400, erros.SEM_DOCUMENTO)
        return parametros.decodificar_documento(valor)

    def _assinar_pdfs(self) -> None:
        valores = parametros.decodificar_query(self._query())
        corpo = self._ler_corpo()
        try:
            conteudo = json.loads(corpo) if corpo.strip() else {}
            if not isinstance(conteudo, dict):
                raise ValueError("o corpo precisa ser um objeto")
            itens = conteudo.get("documentos") or []
            if not isinstance(itens, list):
                raise ValueError("documentos precisa ser uma lista")
        except ValueError as erro:
            raise _Resposta(400, f"Erro ao processar JSON: {erro}") from erro
        if conteudo.get("assinarComGovBR") is True:
            raise _erro_json(500, erros.GOVBR_INDISPONIVEL)

        documentos = []
        for item in itens:
            if item == "":
                continue
            documentos.append(self._processar_item(item))
        if not documentos:
            raise _Resposta(400, erros.SEM_DOCUMENTO)
        self._sucesso_varios(self._assinar(valores.cpf, valores.senha, documentos))

    def _processar_item(self, item) -> bytes:
        modelo = "Erro ao processar documento"
        if not isinstance(item, str):
            raise _Resposta(400, f"{modelo} -> o item precisa ser um texto")
        if item.lower().startswith(("http://", "https://")):
            return self._baixar(item, modelo)
        if parametros.parece_base64(item):
            return parametros.decodificar_documento(item)
        raise _Resposta(400, f"{modelo} -> o item não é uma URL http(s) nem Base64")

    def _lote_disponivel(self) -> None:
        self._responder(200, '{"ativo": true}', _TIPO_JSON)

    def _govbr_disponivel(self) -> None:
        self._responder(200, '{"disponivel": false}', _TIPO_JSON)

    rotas = {
        ("GET", "/assinarPdf"): _assinar_pdf,
        ("POST", "/assinarPdf"): _assinar_pdf,
        ("POST", "/assinarPdfs"): _assinar_pdfs,
        ("GET", "/assinadorLoteDisponivel"): _lote_disponivel,
        ("GET", "/assinadorGovBrDisponivel"): _govbr_disponivel,
    }


class _TratadorDireto(_Tratador):
    """Porta 4499: CPF e senha em headers de texto puro, e PDF sempre no corpo."""

    def _credencial(self, nome: str) -> str:
        # O módulo http decodifica headers como latin-1; os bytes originais são recuperados.
        valor = self.headers.get(nome, "")
        return valor.encode("latin-1", "replace").decode("utf-8", "surrogateescape")

    def _corpo_json(self) -> dict:
        corpo = self._ler_corpo()
        if not corpo.strip():
            return {}
        try:
            conteudo = json.loads(corpo)
        except ValueError as erro:
            raise _erro_json(400, f"Erro ao processar JSON: {erro}") from erro
        if not isinstance(conteudo, dict):
            raise _erro_json(400, "Erro ao processar JSON: o corpo precisa ser um objeto")
        return conteudo

    def _assinar_pdf(self) -> None:
        valor = self._corpo_json().get("documento")
        if not isinstance(valor, str) or not valor.strip():
            raise _erro_json(400, erros.SEM_DOCUMENTO)
        documento = parametros.decodificar_documento(valor)
        assinados = self._assinar(self._credencial("cpf"), self._credencial("senha"), [documento])
        self._sucesso_um(assinados[0])

    def _assinar_pdfs(self) -> None:
        itens = self._corpo_json().get("documentos") or []
        if not isinstance(itens, list):
            raise _erro_json(400, "Erro ao processar JSON: documentos precisa ser uma lista")
        documentos = []
        for item in itens:
            if item == "":
                continue
            if not isinstance(item, str) or not parametros.parece_base64(item):
                raise _erro_json(400, "Erro ao processar documento -> o item não é Base64")
            documentos.append(parametros.decodificar_documento(item))
        if not documentos:
            raise _erro_json(400, erros.SEM_DOCUMENTO)
        self._sucesso_varios(
            self._assinar(self._credencial("cpf"), self._credencial("senha"), documentos)
        )

    rotas = {
        ("POST", "/assinarPdf"): _assinar_pdf,
        ("POST", "/assinarPdfs"): _assinar_pdfs,
    }


class _Servidor(ThreadingHTTPServer):
    daemon_threads = True
    # Em Linux, SO_REUSEADDR não permite dois processos escutando na mesma porta.
    allow_reuse_address = True

    def __init__(self, endereco, tratador, familia, contexto: Contexto) -> None:
        self.address_family = familia
        self.contexto = contexto
        super().__init__(endereco, tratador)

    def server_bind(self) -> None:
        # O bind padrão faz resolução reversa de nome, que aqui é desnecessária.
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(self.server_address)
        self.server_address = self.socket.getsockname()
        self.server_name = "localhost"
        self.server_port = self.server_address[1]


class Servidores:
    """Escuta somente em 127.0.0.1 e ::1, nunca em todas as interfaces."""

    def __init__(
        self,
        contexto: Contexto,
        porta_pagina: int = PORTA_PAGINA,
        porta_direta: int = PORTA_DIRETA,
    ) -> None:
        self._contexto = contexto
        self._portas = ((porta_pagina, _TratadorPagina), (porta_direta, _TratadorDireto))
        self._servidores: list[_Servidor] = []
        self._threads: list[threading.Thread] = []

    def iniciar(self) -> None:
        """Abre todas as portas. Levanta `PortaOcupada` sem deixar nenhuma aberta."""
        try:
            for porta, tratador in self._portas:
                self._servidores.append(
                    _Servidor(("127.0.0.1", porta), tratador, socket.AF_INET, self._contexto)
                )
                try:
                    self._servidores.append(
                        _Servidor(("::1", porta), tratador, socket.AF_INET6, self._contexto)
                    )
                except OSError as erro:
                    # Máquina sem IPv6 não é erro; porta ocupada, sim.
                    if erro.errno == errno.EADDRINUSE:
                        raise
                    log.info("IPv6 local indisponível na porta %s", porta)
        except OSError as erro:
            self.parar()
            if erro.errno == errno.EADDRINUSE:
                raise PortaOcupada(porta) from erro
            raise
        for servidor in self._servidores:
            thread = threading.Thread(target=servidor.serve_forever, daemon=True)
            thread.start()
            self._threads.append(thread)

    def parar(self) -> None:
        for servidor in self._servidores:
            if self._threads:
                servidor.shutdown()
            servidor.server_close()
        self._servidores.clear()
        self._threads.clear()
