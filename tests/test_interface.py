"""Diálogos e janela com a plataforma offscreen do Qt."""

from __future__ import annotations

import base64
import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from conftest import CPF_1, SENHA
from pdfs import gerar_pdf
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLineEdit

from gblinux.atalho import instalar_atalho, remover_atalho
from gblinux.config import PORTA_DIRETA, PORTA_PAGINA, Config
from gblinux.interface import (
    DialogoCertificado,
    DialogoSenha,
    JanelaPrincipal,
    PerguntasQt,
    token_conectado,
)
from gblinux.servico import ServicoAssinatura, workers_vivos


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def processar_ate(app, condicao, tempo=20.0):
    limite = time.monotonic() + tempo
    while not condicao():
        app.processEvents()
        time.sleep(0.005)
        assert time.monotonic() < limite, "tempo esgotado"


def test_dialogo_senha(app):
    dialogo = DialogoSenha("FULANO:******24725", "https://sistema.exemplo", 2, )
    assert dialogo.campo.echoMode() == QLineEdit.EchoMode.Password
    ok = dialogo.botoes.button(QDialogButtonBox.StandardButton.Ok)
    dialogo.show()
    ok.click()  # senha vazia mantém aberta
    assert dialogo.isVisible()
    dialogo.campo.setText("segredo")
    ok.click()
    assert not dialogo.isVisible()
    assert dialogo.valor() == "segredo"


def test_perguntas_ficam_a_frente_e_devolvem_valor(app):
    perguntas = PerguntasQt()
    resposta = {}
    thread = threading.Thread(
        target=lambda: resposta.setdefault("v", perguntas.pedir_senha("T", "", 1, 30)), daemon=True
    )
    thread.start()
    processar_ate(app, lambda: perguntas._aberto is not None)
    dialogo = perguntas._aberto
    assert isinstance(dialogo, DialogoSenha)
    assert dialogo.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    dialogo.campo.setText("abc12345")
    dialogo.botoes.button(QDialogButtonBox.StandardButton.Ok).click()
    processar_ate(app, lambda: not thread.is_alive())
    assert resposta["v"] == "abc12345"


def test_fechar_dialogo_cancela(app):
    perguntas = PerguntasQt()
    resposta = {}
    thread = threading.Thread(
        target=lambda: resposta.setdefault("v", perguntas.escolher_certificado(["A:1", "B:2"], 30)),
        daemon=True,
    )
    thread.start()
    processar_ate(app, lambda: perguntas._aberto is not None)
    dialogo = perguntas._aberto
    assert isinstance(dialogo, DialogoCertificado)
    assert dialogo.windowTitle() == "Informe o CPF do token inserido"
    assert dialogo.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    dialogo.close()
    processar_ate(app, lambda: not thread.is_alive())
    assert resposta["v"] is None


def test_tempo_limite_fecha_dialogo(app):
    perguntas = PerguntasQt()
    resultado = {}

    def pedir():
        try:
            perguntas.pedir_senha("T", "", 1, 0.2)
        except TimeoutError:
            resultado["tempo"] = True

    thread = threading.Thread(target=pedir, daemon=True)
    thread.start()
    processar_ate(app, lambda: not thread.is_alive() and perguntas._aberto is None)
    assert resultado["tempo"]


def test_lote_com_janela_real_pede_senha_uma_vez(app, token):
    ativo = token("um")
    perguntas = PerguntasQt()
    servico = ServicoAssinatura(Config(biblioteca_pkcs11=ativo["biblioteca"]), perguntas)
    resultado = {}
    documentos = [gerar_pdf(), gerar_pdf(2), gerar_pdf(3)]
    thread = threading.Thread(
        target=lambda: resultado.setdefault("v", servico.assinar(CPF_1, "", documentos)), daemon=True
    )
    thread.start()
    processar_ate(app, lambda: perguntas._aberto is not None, 60)
    perguntas._aberto.campo.setText(SENHA)
    perguntas._aberto.botoes.button(QDialogButtonBox.StandardButton.Ok).click()
    aberturas = []
    limite = time.monotonic() + 60
    while thread.is_alive():
        app.processEvents()
        if perguntas._aberto is not None:
            aberturas.append(1)
        assert time.monotonic() < limite
        time.sleep(0.005)
    assert not aberturas
    assert [d[: len(o)] == o for d, o in zip(resultado["v"], documentos)] == [True] * 3
    servico.encerrar()


def test_janela_mostra_estado(app):
    encerrou = []
    janela = JanelaPrincipal(lambda: encerrou.append(1))
    assert janela.rotulo_estado.text() == "Pronto para assinar"
    janela.ocupado_mudou.emit(True)
    processar_ate(app, lambda: janela.rotulo_estado.text() == "Assinando…")
    janela.resultado_novo.emit("1 documento assinado")
    processar_ate(app, lambda: "1 documento assinado" in janela.rotulo_resultado.text())
    janela.ocupado_mudou.emit(False)
    processar_ate(app, lambda: janela.rotulo_estado.text() == "Pronto para assinar")
    janela.close()
    processar_ate(app, lambda: encerrou)


def test_token_conectado_le_sysfs(tmp_path):
    assert not token_conectado(tmp_path)
    (tmp_path / "1-1").mkdir()
    (tmp_path / "1-1/idVendor").write_text("1d6b\n")
    assert not token_conectado(tmp_path)
    (tmp_path / "1-2").mkdir()
    (tmp_path / "1-2/idVendor").write_text("096e\n")
    assert token_conectado(tmp_path)


def test_atalho(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    atalho = instalar_atalho()
    texto = atalho.read_text()
    assert atalho == tmp_path / "applications/gblinux.desktop"
    assert "Exec=" in texto and "-m gblinux" in texto
    icone = tmp_path / "icons/hicolor/scalable/apps/gblinux.svg"
    assert icone.exists()
    remover_atalho()
    assert not atalho.exists() and not icone.exists()
    remover_atalho()  # remover de novo não é erro


# --------------------------------------------------- programa completo, nas portas reais


def _porta_aberta(porta: int, host: str = "127.0.0.1") -> bool:
    familia = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(familia) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, porta)) == 0


def _ambiente_app(tmp_path, biblioteca="/nao/existe.so"):
    ambiente = dict(os.environ)
    ambiente.update(
        QT_QPA_PLATFORM="offscreen",
        XDG_RUNTIME_DIR=str(tmp_path),
        ASSINADOR_PKCS11_LIB=biblioteca,
    )
    return ambiente


# Mostra o aviso sem bloquear: uma caixa modal em offscreen não teria quem a fechasse.
_SEM_CAIXA = (
    "import sys; from PySide6.QtWidgets import QMessageBox; "
    "QMessageBox.critical = staticmethod(lambda *a: None); "
    "import gblinux; sys.exit(gblinux.main())"
)


@pytest.fixture
def programa(tmp_path, token):
    if _porta_aberta(PORTA_PAGINA) or _porta_aberta(PORTA_DIRETA):
        pytest.skip("Portas 3399 ou 4499 já estão em uso nesta máquina")
    ativo = token("um")
    saida = open(tmp_path / "saida.log", "w+")
    processo = subprocess.Popen(
        [sys.executable, "-m", "gblinux"],
        env=_ambiente_app(tmp_path, ativo["biblioteca"]),
        stdout=saida,
        stderr=subprocess.STDOUT,
    )
    limite = time.monotonic() + 30
    while not _porta_aberta(PORTA_PAGINA):
        assert processo.poll() is None, Path(saida.name).read_text()
        assert time.monotonic() < limite
        time.sleep(0.1)
    yield processo, saida
    if processo.poll() is None:
        processo.terminate()
        processo.wait(30)
    saida.close()


def _b64(texto: str) -> str:
    return base64.b64encode(texto.encode()).decode()


def test_programa_completo(programa, tmp_path):
    processo, saida = programa
    segredo = "SenhaErradaXYZ"
    conexao = http.client.HTTPConnection("127.0.0.1", PORTA_PAGINA, timeout=60)
    documento = base64.b64encode(gerar_pdf()).decode()
    conexao.request(
        "POST",
        f"/assinarPdf?nocache=1&cpf={_b64(CPF_1)}&senha={_b64(SENHA)}",
        body=json.dumps({"documento": documento}),
    )
    resposta = conexao.getresponse()
    assert resposta.status == 200
    conexao.close()

    conexao = http.client.HTTPConnection("127.0.0.1", PORTA_PAGINA, timeout=60)
    conexao.request(
        "POST",
        f"/assinarPdf?cpf={_b64(CPF_1)}&senha={_b64(segredo)}",
        body=json.dumps({"documento": documento}),
    )
    resposta = conexao.getresponse()
    assert resposta.status == 400
    assert json.loads(resposta.read())["message"] == "Senha incorreta para o token"
    conexao.close()

    processo.send_signal(signal.SIGTERM)
    assert processo.wait(40) == 0
    assert not _porta_aberta(PORTA_PAGINA) and not _porta_aberta(PORTA_DIRETA)
    assert not workers_vivos()

    saida.seek(0)
    registros = saida.read()
    assert "/assinarPdf" in registros
    for proibido in (segredo, _b64(segredo), SENHA, CPF_1, _b64(CPF_1), "nocache"):
        assert proibido not in registros


def test_segunda_instancia_avisa_e_encerra(programa, tmp_path):
    segunda = subprocess.run(
        [sys.executable, "-c", _SEM_CAIXA],
        env=_ambiente_app(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert segunda.returncode == 1
    assert "já está aberto" in segunda.stderr


def test_porta_ocupada_avisa_e_encerra(tmp_path):
    if _porta_aberta(PORTA_DIRETA) or _porta_aberta(PORTA_PAGINA):
        pytest.skip("Portas 3399 ou 4499 já estão em uso nesta máquina")
    with socket.socket() as ocupante:
        ocupante.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        ocupante.bind(("127.0.0.1", PORTA_DIRETA))
        ocupante.listen()
        processo = subprocess.run(
            [sys.executable, "-c", _SEM_CAIXA],
            env=_ambiente_app(tmp_path),
            capture_output=True,
            text=True,
            timeout=30,
        )
    assert processo.returncode == 1
    assert f"porta {PORTA_DIRETA}" in processo.stderr
    assert not _porta_aberta(PORTA_PAGINA)
