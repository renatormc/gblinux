"""Ponto de entrada do programa com janela."""

from __future__ import annotations

import argparse
import logging
import signal
import sys

from PySide6.QtCore import QDir, QLockFile, QStandardPaths, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from . import config as configuracao
from . import erros
from .atalho import instalar_atalho, remover_atalho
from .interface import JanelaPrincipal, PerguntasQt
from .servico import ServicoAssinatura, workers_vivos
from .servidor import Contexto, PortaOcupada, Servidores


def _avisar(mensagem: str) -> None:
    print(mensagem, file=sys.stderr)
    QMessageBox.critical(None, "Assinador de PDF", mensagem)


def main() -> int:
    argumentos = argparse.ArgumentParser(description="Assinador de PDF com token A3")
    argumentos.add_argument(
        "--instalar-atalho", action="store_true", help="cria o atalho no menu de aplicativos e sai"
    )
    argumentos.add_argument(
        "--remover-atalho", action="store_true", help="remove o atalho do menu de aplicativos e sai"
    )
    opcoes = argumentos.parse_args()
    if opcoes.instalar_atalho:
        print(f"Atalho criado em {instalar_atalho()}")
        return 0
    if opcoes.remover_atalho:
        remover_atalho()
        print("Atalho removido")
        return 0

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    aplicacao = QApplication(sys.argv[:1])
    aplicacao.setApplicationName("gblinux")
    aplicacao.setQuitOnLastWindowClosed(False)

    pasta = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.RuntimeLocation)
    trava = QLockFile(QDir(pasta or QDir.tempPath()).filePath("gblinux.lock"))
    trava.setStaleLockTime(0)
    if not trava.tryLock(0):
        _avisar("O assinador já está aberto.")
        return 1

    config = configuracao.carregar()
    perguntas = PerguntasQt()
    janela: JanelaPrincipal | None = None

    def ao_mudar_ocupado(ocupado: bool) -> None:
        if janela is not None:
            janela.ocupado_mudou.emit(ocupado)

    def ao_resultado(mensagem: str) -> None:
        if janela is not None:
            janela.resultado_novo.emit(mensagem)

    servico = ServicoAssinatura(config, perguntas, ao_mudar_ocupado)
    servidores = Servidores(Contexto(config, servico, ao_resultado))
    try:
        servidores.iniciar()
    except PortaOcupada as erro:
        _avisar(
            f"A porta {erro.porta} está ocupada: o assinador já está aberto ou outro "
            "programa usa a porta."
        )
        return 1

    def encerrar() -> None:
        servidores.parar()
        servico.encerrar()

    janela = JanelaPrincipal(encerrar)
    janela.show()
    if workers_vivos():  # sobra de uma execução anterior, preso no driver
        janela.resultado_novo.emit(erros.SEM_RESPOSTA)

    # O Python só trata sinais quando recupera o controle do laço de eventos do Qt.
    def tratar_sinal(_sinal, _quadro) -> None:
        QTimer.singleShot(0, janela.encerrar_sem_perguntar)

    signal.signal(signal.SIGTERM, tratar_sinal)
    signal.signal(signal.SIGINT, tratar_sinal)
    relogio = QTimer()
    relogio.timeout.connect(lambda: None)
    relogio.start(200)

    return aplicacao.exec()
