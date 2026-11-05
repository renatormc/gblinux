"""Tempo limite e encerramento do processo filho, com um filho que simula o driver travado."""

from __future__ import annotations

import subprocess
import sys
import threading
import time

import pytest

from gblinux import erros
from gblinux.config import Config
from gblinux.erros import ErroAssinatura
from gblinux.servico import ServicoAssinatura, workers_vivos


class SemPerguntas:
    def escolher_certificado(self, opcoes, tempo_s):
        return None

    def pedir_senha(self, titular, origem, quantidade, tempo_s):
        return None

    def cancelar_perguntas(self):
        pass


def _substituir_filho(monkeypatch, programa: str, sinais: list):
    """Faz o serviço abrir `programa` no lugar do worker e registra os sinais enviados."""
    original = subprocess.Popen

    class Popen(original):
        def __init__(self, _args, **opcoes):
            super().__init__([sys.executable, "-c", programa], **opcoes)

        def send_signal(self, sinal):
            sinais.append(sinal)
            super().send_signal(sinal)

        def kill(self):
            raise AssertionError("SIGKILL é proibido")

    monkeypatch.setattr("gblinux.servico.subprocess.Popen", Popen)


TRAVADO_ENCERRA_NO_SIGTERM = "import time\nwhile True: time.sleep(1)\n"


def test_tempo_limite_usa_sigterm_e_responde(monkeypatch):
    sinais = []
    _substituir_filho(monkeypatch, TRAVADO_ENCERRA_NO_SIGTERM, sinais)
    servico = ServicoAssinatura(Config(tempo_limite_s=0.5), SemPerguntas())
    inicio = time.monotonic()
    with pytest.raises(ErroAssinatura) as erro:
        servico.assinar("", "", [b"x"])
    assert erro.value.mensagem == erros.SEM_RESPOSTA
    assert time.monotonic() - inicio < 10
    assert len(sinais) == 1
    assert sinais[0] == 15


def test_filho_que_morre_vira_erro_de_driver(monkeypatch):
    _substituir_filho(monkeypatch, "raise SystemExit(3)", [])
    servico = ServicoAssinatura(Config(), SemPerguntas())
    with pytest.raises(ErroAssinatura) as erro:
        servico.assinar("", "", [b"x"])
    assert erro.value.mensagem == erros.ERRO_DRIVER


def test_encerrar_com_pedido_em_andamento_termina_o_filho(monkeypatch):
    sinais = []
    _substituir_filho(monkeypatch, TRAVADO_ENCERRA_NO_SIGTERM, sinais)
    servico = ServicoAssinatura(Config(), SemPerguntas())
    resultado = {}

    def pedir():
        try:
            servico.assinar("", "", [b"x"])
        except ErroAssinatura as erro:
            resultado["mensagem"] = erro.mensagem

    thread = threading.Thread(target=pedir, daemon=True)
    thread.start()
    while servico._filho is None:
        time.sleep(0.01)
    assert servico.ocupado
    servico.encerrar()
    thread.join(10)
    assert resultado["mensagem"] == erros.CANCELADO
    assert sinais == [15]
    assert servico._filho.poll() is not None


def test_filho_preso_bloqueia_novo_acesso_ao_token(monkeypatch):
    # Ignora SIGTERM como um driver travado dentro de uma chamada USB.
    programa = "import signal,time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(4)\n"
    _substituir_filho(monkeypatch, programa, [])
    servico = ServicoAssinatura(Config(tempo_limite_s=0.3, tempo_encerrar_s=0.2), SemPerguntas())
    with pytest.raises(ErroAssinatura):
        servico.assinar("", "", [b"x"])
    preso = servico._filho
    assert preso.poll() is None
    with pytest.raises(ErroAssinatura) as erro:
        servico.assinar("", "", [b"x"])
    assert erro.value.mensagem == erros.SEM_RESPOSTA
    assert servico._filho is preso  # nenhum processo novo foi aberto
    preso.wait(10)


def _worker_falso(segundos: float) -> subprocess.Popen:
    """Processo que aparece em /proc como um worker de uma execução anterior."""
    return subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({segundos})", "-m", "gblinux.worker"]
    )


def test_workers_vivos_le_a_linha_de_comando(tmp_path):
    def processo(pid, *argumentos):
        (tmp_path / pid).mkdir()
        (tmp_path / pid / "cmdline").write_bytes(b"\0".join(a.encode() for a in argumentos) + b"\0")

    processo("10", "python", "-m", "gblinux.worker")
    processo("11", "python", "-m", "gblinux")
    processo("12", "bash", "-c", "pgrep -f '-m gblinux.worker'")
    (tmp_path / "self").mkdir()
    assert workers_vivos(tmp_path) == [10]


def test_worker_antigo_preso_recusa_sem_abrir_outro(monkeypatch):
    antigo = _worker_falso(30)
    try:
        abertos = []
        monkeypatch.setattr("gblinux.servico.subprocess.Popen", lambda *a, **k: abertos.append(a))
        servico = ServicoAssinatura(Config(tempo_encerrar_s=0.5), SemPerguntas())
        inicio = time.monotonic()
        with pytest.raises(ErroAssinatura) as erro:
            servico.assinar("", "", [b"x"])
        assert erro.value.mensagem == erros.SEM_RESPOSTA
        assert time.monotonic() - inicio < 5
        assert not abertos
    finally:
        antigo.terminate()
        antigo.wait(10)


def test_worker_antigo_que_termina_libera_o_pedido(monkeypatch):
    antigo = _worker_falso(0.6)
    threading.Thread(target=antigo.wait, daemon=True).start()  # recolhe o processo ao terminar
    _substituir_filho(monkeypatch, "raise SystemExit(3)", [])
    servico = ServicoAssinatura(Config(tempo_encerrar_s=10), SemPerguntas())
    with pytest.raises(ErroAssinatura) as erro:
        servico.assinar("", "", [b"x"])
    # O filho chegou a abrir: o erro é o dele, e não a recusa por token ocupado.
    assert erro.value.mensagem == erros.ERRO_DRIVER
