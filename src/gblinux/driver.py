"""Instala o driver do token ePass2003, com os mesmos passos manuais de DRIVER.md."""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

import requests

_URL = "https://site.solutinet.com.br/drivers/Token/ePass2003/Linux/ePass2003_Castle_20141128.tar.gz"
# O pacote traz um script que roda como root, então só é aceito este arquivo exato.
_SHA256 = "00fbb5756b1b01222fe6825ad7ca43da0b662d1f7e2d06584ea7c3c7be490a23"
_PACOTES = ("pcscd", "libpcsclite1", "libccid", "pcsc-tools", "opensc", "libnss3-tools")
_PASTA_DRIVER = "/usr/lib/ePass2003-Linux-x64"
_BIBLIOTECA = "/usr/lib/libcastle.so"
_VERSAO_BIBLIOTECA = "libcastle.so.1.0.0"


class ErroDriver(Exception):
    """Falha na instalação, com uma mensagem pronta para o usuário."""


def _executar(comando: list[str]) -> None:
    try:
        subprocess.run(comando, check=True)
    except (OSError, subprocess.CalledProcessError) as erro:
        raise ErroDriver(f"Falha ao executar `{' '.join(comando)}`: {erro}") from erro


def _baixar(destino: Path) -> None:
    print(f"Baixando {_URL}")
    try:
        resposta = requests.get(_URL, timeout=120)
        resposta.raise_for_status()
    except requests.RequestException as erro:
        raise ErroDriver(f"Não foi possível baixar o driver: {erro}") from erro
    if hashlib.sha256(resposta.content).hexdigest() != _SHA256:
        raise ErroDriver("O arquivo baixado do driver não é o esperado. Nada foi instalado.")
    destino.write_bytes(resposta.content)


def instalar_driver() -> None:
    if platform.machine() != "x86_64" or not shutil.which("apt-get"):
        raise ErroDriver("Este comando só funciona em Ubuntu e derivados de 64 bits (x86_64).")
    root = os.geteuid() == 0
    if not root and not shutil.which("sudo"):
        raise ErroDriver("É preciso ter o sudo, ou rodar este comando como root.")
    sudo = [] if root else ["sudo"]

    # Com uma umask restritiva, os arquivos em /usr/lib ficariam ilegíveis para o usuário comum.
    os.umask(0o022)
    with tempfile.TemporaryDirectory(prefix="gblinux-driver-") as temporaria:
        pasta = Path(temporaria)
        arquivo = pasta / "driver.tar.gz"
        # O download vem antes de tudo: se falhar, o sistema fica como estava.
        _baixar(arquivo)
        _executar(["tar", "-xzf", str(arquivo), "-C", temporaria, "--no-same-owner"])

        for comando in (
            ["apt-get", "update"],
            ["apt-get", "install", "-y", *_PACOTES],
            ["rm", "-rf", _PASTA_DRIVER],
            ["cp", "-rf", str(pasta / "ePass2003-Castle-20141128"), _PASTA_DRIVER],
            ["cp", f"{_PASTA_DRIVER}/x86_64/redist/{_VERSAO_BIBLIOTECA}", "/usr/lib/"],
            ["ln", "-sf", f"/usr/lib/{_VERSAO_BIBLIOTECA}", _BIBLIOTECA],
            ["sh", f"{_PASTA_DRIVER}/x86_64/config/config.sh"],
            ["systemctl", "enable", "--now", "pcscd.socket"],
            ["systemctl", "restart", "pcscd"],
        ):
            _executar([*sudo, *comando])
