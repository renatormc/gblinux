"""Instalação do driver, sem tocar no sistema: os comandos externos são só registrados."""

from __future__ import annotations

import hashlib
import subprocess
import sys

import pytest
import requests

from gblinux import aplicacao, driver


class _Resposta:
    content = b"pacote"

    def raise_for_status(self):
        pass


@pytest.fixture
def comandos(monkeypatch):
    registro = []
    monkeypatch.setattr(driver.subprocess, "run", lambda c, check: registro.append(c))
    monkeypatch.setattr(driver.requests, "get", lambda url, timeout: _Resposta())
    monkeypatch.setattr(driver, "_SHA256", hashlib.sha256(_Resposta.content).hexdigest())
    monkeypatch.setattr(driver.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(driver.shutil, "which", lambda programa: f"/usr/bin/{programa}")
    monkeypatch.setattr(driver.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(driver.os, "umask", lambda valor: 0)
    return registro


def test_segue_o_procedimento_com_sudo(comandos):
    driver.instalar_driver()
    assert comandos[0][0] == "tar"
    privilegiados = comandos[1:]
    assert all(c[0] == "sudo" for c in privilegiados)
    assert [c[1] for c in privilegiados] == [
        "apt-get", "apt-get", "rm", "cp", "cp", "ln", "sh", "systemctl", "systemctl",
    ]  # fmt: skip
    assert ["sudo", "ln", "-sf", "/usr/lib/libcastle.so.1.0.0", "/usr/lib/libcastle.so"] in comandos


def test_root_nao_usa_sudo(comandos, monkeypatch):
    monkeypatch.setattr(driver.os, "geteuid", lambda: 0)
    driver.instalar_driver()
    assert not any(c[0] == "sudo" for c in comandos)
    assert ["apt-get", "update"] in comandos


def test_arquivo_diferente_nao_altera_o_sistema(comandos, monkeypatch):
    monkeypatch.setattr(driver, "_SHA256", "0" * 64)
    with pytest.raises(driver.ErroDriver, match="não é o esperado"):
        driver.instalar_driver()
    assert comandos == []


def test_falha_no_download_nao_altera_o_sistema(comandos, monkeypatch):
    def falhar(url, timeout):
        raise requests.ConnectionError("sem rede")

    monkeypatch.setattr(driver.requests, "get", falhar)
    with pytest.raises(driver.ErroDriver, match="sem rede"):
        driver.instalar_driver()
    assert comandos == []


def test_comando_que_falha_interrompe(comandos, monkeypatch):
    def executar(comando, check):
        comandos.append(comando)
        if "rm" in comando:
            raise subprocess.CalledProcessError(1, comando)

    monkeypatch.setattr(driver.subprocess, "run", executar)
    with pytest.raises(driver.ErroDriver, match="rm -rf"):
        driver.instalar_driver()
    assert comandos[-1][1] == "rm"


@pytest.mark.parametrize(
    ("maquina", "programas"),
    [("aarch64", {"apt-get", "sudo"}), ("x86_64", {"sudo"}), ("x86_64", {"apt-get"})],
)
def test_sistema_sem_suporte(comandos, monkeypatch, maquina, programas):
    monkeypatch.setattr(driver.platform, "machine", lambda: maquina)
    monkeypatch.setattr(driver.shutil, "which", lambda p: f"/usr/bin/{p}" if p in programas else None)
    with pytest.raises(driver.ErroDriver):
        driver.instalar_driver()
    assert comandos == []


def test_linha_de_comando(monkeypatch, capsys):
    chamadas = []
    monkeypatch.setattr(aplicacao, "instalar_driver", lambda: chamadas.append(True))
    monkeypatch.setattr(sys, "argv", ["gblinux", "install-drive"])
    assert aplicacao.main() == 0
    assert chamadas == [True]
    assert "conecte de novo" in capsys.readouterr().out


def test_linha_de_comando_informa_erro(monkeypatch, capsys):
    def falhar():
        raise driver.ErroDriver("deu errado")

    monkeypatch.setattr(aplicacao, "instalar_driver", falhar)
    monkeypatch.setattr(sys, "argv", ["gblinux", "install-drive"])
    assert aplicacao.main() == 1
    assert "deu errado" in capsys.readouterr().err
