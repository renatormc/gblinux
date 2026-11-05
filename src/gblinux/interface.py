"""Janela principal e diálogos."""

from __future__ import annotations

import concurrent.futures
import logging
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

log = logging.getLogger("assinador.interface")

_FABRICANTE_TOKEN = "096e"
_DISPOSITIVOS_USB = Path("/sys/bus/usb/devices")


def token_conectado(raiz: Path = _DISPOSITIVOS_USB) -> bool:
    """Lê o sysfs, sem tocar no driver: consultas periódicas a ele podem travar."""
    try:
        for arquivo in raiz.glob("*/idVendor"):
            try:
                if arquivo.read_text().strip() == _FABRICANTE_TOKEN:
                    return True
            except OSError:
                continue
    except OSError:
        pass
    return False


def _sempre_a_frente(janela: QDialog) -> None:
    janela.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    janela.show()
    janela.raise_()
    janela.activateWindow()


class DialogoCertificado(QDialog):
    def __init__(self, opcoes: list[str]) -> None:
        super().__init__()
        self.setWindowTitle("Informe o CPF do token inserido")
        self.lista = QListWidget()
        self.lista.addItems(opcoes)
        self.lista.setCurrentRow(0)
        self.lista.itemDoubleClicked.connect(lambda _: self.accept())
        botao = QPushButton("Assinar Documento")
        botao.setDefault(True)
        botao.clicked.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.addWidget(self.lista)
        layout.addWidget(botao)
        self.resize(460, 240)

    def valor(self) -> str | None:
        item = self.lista.currentItem()
        return item.text() if item else None


class DialogoSenha(QDialog):
    def __init__(self, titular: str, origem: str, quantidade: int) -> None:
        super().__init__()
        self.setWindowTitle("Senha do certificado")
        documentos = "1 documento" if quantidade == 1 else f"{quantidade} documentos"
        pedinte = f"A página {origem}" if origem else "Uma página"
        aviso = QLabel(f"{pedinte} pediu a assinatura de {documentos} com o certificado {titular}.")
        aviso.setWordWrap(True)
        aviso.setTextFormat(Qt.TextFormat.PlainText)
        self.campo = QLineEdit()
        self.campo.setEchoMode(QLineEdit.EchoMode.Password)
        self.botoes = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.botoes.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        self.botoes.accepted.connect(self._confirmar)
        self.botoes.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(aviso)
        layout.addWidget(QLabel("Informe a senha do certificado:"))
        layout.addWidget(self.campo)
        layout.addWidget(self.botoes)
        self.resize(460, 170)

    def _confirmar(self) -> None:
        # Senha vazia mantém a janela aberta.
        if self.campo.text():
            self.accept()
        else:
            self.campo.setFocus()

    def valor(self) -> str:
        return self.campo.text()


class PerguntasQt(QObject):
    """Mostra os diálogos na thread da interface para a thread que atende o pedido."""

    _abrir_certificado = Signal(object, object)
    _abrir_senha = Signal(object, object)
    _cancelar = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._aberto: QDialog | None = None
        self._abrir_certificado.connect(self._mostrar_certificado)
        self._abrir_senha.connect(self._mostrar_senha)
        self._cancelar.connect(self._rejeitar_aberto)

    # Chamados pela thread do pedido.

    def escolher_certificado(self, opcoes: list[str], tempo_s: float) -> str | None:
        futuro: concurrent.futures.Future = concurrent.futures.Future()
        self._abrir_certificado.emit(opcoes, futuro)
        return self._aguardar(futuro, tempo_s)

    def pedir_senha(self, titular: str, origem: str, quantidade: int, tempo_s: float) -> str | None:
        futuro: concurrent.futures.Future = concurrent.futures.Future()
        self._abrir_senha.emit((titular, origem, quantidade), futuro)
        return self._aguardar(futuro, tempo_s)

    def cancelar_perguntas(self) -> None:
        self._cancelar.emit()

    def _aguardar(self, futuro: concurrent.futures.Future, tempo_s: float) -> str | None:
        try:
            return futuro.result(tempo_s)
        except concurrent.futures.TimeoutError:
            self.cancelar_perguntas()
            raise TimeoutError from None

    # Executados na thread da interface.

    @Slot(object, object)
    def _mostrar_certificado(self, opcoes, futuro) -> None:
        self._mostrar(DialogoCertificado(opcoes), futuro)

    @Slot(object, object)
    def _mostrar_senha(self, argumentos, futuro) -> None:
        self._mostrar(DialogoSenha(*argumentos), futuro)

    def _mostrar(self, dialogo: QDialog, futuro: concurrent.futures.Future) -> None:
        def concluir(codigo: int) -> None:
            aceito = codigo == QDialog.DialogCode.Accepted
            if not futuro.done():
                futuro.set_result(dialogo.valor() if aceito else None)
            if self._aberto is dialogo:
                self._aberto = None
            dialogo.deleteLater()

        dialogo.finished.connect(concluir)
        self._aberto = dialogo
        _sempre_a_frente(dialogo)

    @Slot()
    def _rejeitar_aberto(self) -> None:
        if self._aberto is not None:
            self._aberto.reject()


class JanelaPrincipal(QMainWindow):
    ocupado_mudou = Signal(bool)
    resultado_novo = Signal(str)
    _encerrado = Signal()

    def __init__(self, ao_encerrar) -> None:
        """`ao_encerrar` fecha portas e processos; pode demorar, e roda fora da interface."""
        super().__init__()
        self._ao_encerrar = ao_encerrar
        self._ocupado = False
        self._encerrando = False
        self.setWindowTitle("Assinador de PDF")

        self.rotulo_estado = QLabel("Pronto para assinar")
        self.rotulo_token = QLabel()
        self.rotulo_resultado = QLabel("Nenhum pedido atendido")
        self.rotulo_resultado.setWordWrap(True)
        self.rotulo_resultado.setTextFormat(Qt.TextFormat.PlainText)
        self.botao_fechar = QPushButton("Fechar")
        self.botao_fechar.clicked.connect(self.close)

        central = QWidget()
        layout = QVBoxLayout(central)
        for widget in (
            self.rotulo_estado,
            self.rotulo_token,
            self.rotulo_resultado,
            self.botao_fechar,
        ):
            layout.addWidget(widget)
        self.setCentralWidget(central)
        self.resize(380, 170)

        self.ocupado_mudou.connect(self._aplicar_ocupado)
        self.resultado_novo.connect(self._aplicar_resultado)
        self._encerrado.connect(self._terminar_aplicacao)

        self._atualizar_token()
        self._relogio = QTimer(self)
        self._relogio.timeout.connect(self._atualizar_token)
        self._relogio.start(2000)

    @Slot(bool)
    def _aplicar_ocupado(self, ocupado: bool) -> None:
        self._ocupado = ocupado
        if not self._encerrando:
            self.rotulo_estado.setText("Assinando…" if ocupado else "Pronto para assinar")

    @Slot(str)
    def _aplicar_resultado(self, mensagem: str) -> None:
        self.rotulo_resultado.setText(f"{datetime.now():%H:%M:%S} — {mensagem}")

    def _atualizar_token(self) -> None:
        conectado = token_conectado()
        self.rotulo_token.setText("Token conectado" if conectado else "Token não conectado")

    def encerrar_sem_perguntar(self) -> None:
        self._ocupado = False
        self.close()

    def closeEvent(self, evento) -> None:  # noqa: N802
        if self._encerrando:
            evento.ignore()
            return
        if self._ocupado:
            resposta = QMessageBox.question(
                self,
                "Assinatura em andamento",
                "Há uma assinatura em andamento. Deseja cancelar e fechar o assinador?",
            )
            if resposta != QMessageBox.StandardButton.Yes:
                evento.ignore()
                return
        evento.ignore()  # a janela fica aberta até os processos terminarem
        self._encerrando = True
        self.rotulo_estado.setText("Encerrando…")
        self.botao_fechar.setEnabled(False)
        threading.Thread(target=self._encerrar_em_segundo_plano, daemon=True).start()

    def _encerrar_em_segundo_plano(self) -> None:
        try:
            self._ao_encerrar()
        except Exception as erro:
            log.error("Erro ao encerrar: %s", type(erro).__name__)
        self._encerrado.emit()

    @Slot()
    def _terminar_aplicacao(self) -> None:
        # exit, e não quit: o quit do Qt 6 pede o fechamento das janelas, e o closeEvent acima
        # recusaria, cancelando o encerramento.
        QApplication.exit(0)
