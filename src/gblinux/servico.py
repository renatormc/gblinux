"""Fila de assinaturas e controle do processo filho."""

from __future__ import annotations

import base64
import concurrent.futures
import json
import logging
import queue
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Protocol

from . import erros
from .config import Config
from .erros import ErroAssinatura

log = logging.getLogger("assinador.servico")


def workers_vivos(raiz: Path = Path("/proc")) -> list[int]:
    """PIDs de processos do token ainda vivos, inclusive de uma execução anterior do programa."""
    pids = []
    for pasta in raiz.iterdir():
        if not pasta.name.isdigit():
            continue
        try:
            argumentos = (pasta / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        for anterior, atual in zip(argumentos, argumentos[1:]):
            if anterior == b"-m" and atual == b"gblinux.worker":
                pids.append(int(pasta.name))
                break
    return pids


class Perguntas(Protocol):
    """Diálogos com o usuário. `TimeoutError` indica que o tempo limite acabou."""

    def escolher_certificado(self, opcoes: list[str], tempo_s: float) -> str | None: ...

    def pedir_senha(self, titular: str, origem: str, quantidade: int, tempo_s: float) -> str | None: ...

    def cancelar_perguntas(self) -> None: ...


@dataclass(frozen=True)
class Pedido:
    cpf: str
    senha: str
    documentos: list[bytes]
    origem: str
    prazo: float


class ServicoAssinatura:
    """Atende um pedido de cada vez, na ordem de chegada, cada um em um processo filho."""

    def __init__(
        self,
        config: Config,
        perguntas: Perguntas,
        ao_mudar_ocupado: Callable[[bool], None] = lambda _: None,
    ) -> None:
        self._config = config
        self._perguntas = perguntas
        self._ao_mudar_ocupado = ao_mudar_ocupado
        self._executor = concurrent.futures.ThreadPoolExecutor(1, "assinatura")
        self._trava = threading.Lock()
        self._pendentes = 0
        self._filho: subprocess.Popen | None = None
        self._encerrando = False

    @property
    def ocupado(self) -> bool:
        with self._trava:
            return self._pendentes > 0

    def assinar(self, cpf: str, senha: str, documentos: list[bytes], origem: str = "") -> list[bytes]:
        """Bloqueia até o fim do pedido e levanta `ErroAssinatura` em caso de falha."""
        pedido = Pedido(cpf, senha, documentos, origem, time.monotonic() + self._config.tempo_limite_s)
        self._alterar_pendentes(+1)
        try:
            return self._executor.submit(self._executar, pedido).result()
        except (concurrent.futures.CancelledError, RuntimeError) as erro:
            raise ErroAssinatura(erros.CANCELADO) from erro
        finally:
            self._alterar_pendentes(-1)

    def encerrar(self) -> None:
        """Cancela o que está na fila e termina o filho, dando tempo ao driver."""
        self._encerrando = True
        self._perguntas.cancelar_perguntas()
        self._executor.shutdown(wait=False, cancel_futures=True)
        with self._trava:
            filho = self._filho
        if filho is not None:
            self._terminar(filho)

    def _alterar_pendentes(self, variacao: int) -> None:
        with self._trava:
            antes = self._pendentes > 0
            self._pendentes += variacao
            depois = self._pendentes > 0
        if antes != depois:
            self._ao_mudar_ocupado(depois)

    def _terminar(self, filho: subprocess.Popen) -> bool:
        """SIGTERM, nunca SIGKILL: matar o processo à força pode travar o token."""
        if filho.poll() is None:
            filho.send_signal(signal.SIGTERM)
        try:
            filho.wait(self._config.tempo_encerrar_s)
        except subprocess.TimeoutExpired:
            log.warning("Processo do token (pid %s) não terminou após SIGTERM", filho.pid)
            return False
        return True

    def _executar(self, pedido: Pedido) -> list[bytes]:
        if self._encerrando:
            raise ErroAssinatura(erros.CANCELADO)
        with self._trava:
            preso = self._filho
        # Um filho anterior preso no driver ainda segura o token; abrir outro só pioraria.
        if preso is not None and preso.poll() is None:
            restante = pedido.prazo - time.monotonic()
            try:
                preso.wait(max(0.0, min(restante, self._config.tempo_encerrar_s)))
            except subprocess.TimeoutExpired:
                raise ErroAssinatura(erros.SEM_RESPOSTA) from None
        if time.monotonic() >= pedido.prazo:
            raise ErroAssinatura(erros.SEM_RESPOSTA)

        self._exigir_token_livre(pedido)

        filho = subprocess.Popen(
            [sys.executable, "-m", "gblinux.worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
        )
        with self._trava:
            self._filho = filho
        try:
            resultado = self._conversar(filho, pedido)
        except TimeoutError:
            self._perguntas.cancelar_perguntas()
            self._terminar(filho)
            raise ErroAssinatura(erros.SEM_RESPOSTA) from None
        except BaseException:
            self._terminar(filho)
            raise
        # Espera o filho fazer logout e C_Finalize antes de liberar a fila.
        try:
            filho.stdin.close()
            filho.wait(max(self._config.tempo_encerrar_s, pedido.prazo - time.monotonic()))
        except (OSError, subprocess.TimeoutExpired):
            self._terminar(filho)
        return resultado

    def _exigir_token_livre(self, pedido: Pedido) -> None:
        """Recusa logo o pedido se um processo antigo seguir preso no driver.

        A fila atende um pedido por vez, então qualquer processo vivo aqui é de uma execução
        anterior. Um novo ficaria parado no semáforo do driver até o tempo limite. O processo
        antigo recebe alguns segundos, pois pode estar apenas terminando de encerrar a sessão.
        """
        limite = time.monotonic() + self._config.tempo_encerrar_s
        while pids := workers_vivos():
            if time.monotonic() >= min(limite, pedido.prazo):
                log.warning("Processo antigo do token ainda preso (pid %s)", pids)
                raise ErroAssinatura(erros.SEM_RESPOSTA)
            time.sleep(0.2)

    def _conversar(self, filho: subprocess.Popen, pedido: Pedido) -> list[bytes]:
        mensagens: queue.Queue[dict | None] = queue.Queue()
        threading.Thread(target=_ler_mensagens, args=(filho.stdout, mensagens), daemon=True).start()
        inicial = {
            "cpf": pedido.cpf,
            "biblioteca": self._config.biblioteca_pkcs11,
            "documentos": [base64.b64encode(d).decode() for d in pedido.documentos],
        }
        # Em outra thread: um filho travado não pode segurar esta com o pipe cheio.
        threading.Thread(target=_enviar, args=(filho.stdin, inicial), daemon=True).start()

        def restante() -> float:
            valor = pedido.prazo - time.monotonic()
            if valor <= 0:
                raise TimeoutError
            return valor

        while True:
            try:
                mensagem = mensagens.get(timeout=restante())
            except queue.Empty:
                raise TimeoutError from None
            if mensagem is None:
                if self._encerrando:
                    raise ErroAssinatura(erros.CANCELADO)
                log.warning("Processo do token terminou sem resposta (código %s)", filho.wait())
                raise ErroAssinatura(erros.ERRO_DRIVER)
            tipo = mensagem.get("tipo")
            if tipo == "resultado":
                return [base64.b64decode(d) for d in mensagem["documentos"]]
            if tipo == "erro":
                raise ErroAssinatura(mensagem["mensagem"])
            if tipo == "escolher":
                escolha = self._perguntas.escolher_certificado(mensagem["opcoes"], restante())
                _enviar(filho.stdin, {"escolha": escolha})
            elif tipo == "senha":
                senha = pedido.senha or self._perguntas.pedir_senha(
                    mensagem["titular"], pedido.origem, len(pedido.documentos), restante()
                )
                _enviar(filho.stdin, {"senha": senha or None})


def _ler_mensagens(fluxo: IO[str], destino: queue.Queue) -> None:
    try:
        for linha in fluxo:
            destino.put(json.loads(linha))
    except (OSError, ValueError):
        pass
    destino.put(None)


def _enviar(fluxo: IO[str], mensagem: dict) -> None:
    try:
        fluxo.write(json.dumps(mensagem) + "\n")
        fluxo.flush()
    except (OSError, ValueError):
        pass  # o filho terminou; o leitor percebe e trata
