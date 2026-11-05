"""Atalho no menu de aplicativos, instalado na pasta do usuário, sem root."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

_ICONE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<path fill="#f5f5f5" stroke="#444" stroke-width="3" d="M14 5h26l12 12v42H14z"/>
<path fill="none" stroke="#444" stroke-width="3" d="M40 5v12h12"/>
<path fill="none" stroke="#1565c0" stroke-width="4" stroke-linecap="round"
 d="M20 46c5-14 8-14 9-6s4 7 7 1 4-5 8 0"/>
</svg>
"""


def _pasta_dados() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")


def _atualizar_menu(pasta: Path) -> None:
    """Alguns ambientes só mostram a mudança depois de atualizar o índice do menu."""
    programa = shutil.which("update-desktop-database")
    if programa:
        subprocess.run([programa, str(pasta)], capture_output=True, check=False)


def instalar_atalho() -> Path:
    dados = _pasta_dados()
    icone = dados / "icons/hicolor/scalable/apps/gblinux.svg"
    icone.parent.mkdir(parents=True, exist_ok=True)
    icone.write_text(_ICONE, encoding="utf-8")

    atalho = dados / "applications/gblinux.desktop"
    atalho.parent.mkdir(parents=True, exist_ok=True)
    comando = shlex.join([sys.executable, "-m", "gblinux"])
    atalho.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Assinador de PDF\n"
        "Comment=Assina PDFs com o token A3\n"
        f"Exec={comando}\n"
        "Icon=gblinux\n"
        "Terminal=false\n"
        "Categories=Office;\n",
        encoding="utf-8",
    )
    _atualizar_menu(atalho.parent)
    return atalho


def remover_atalho() -> None:
    dados = _pasta_dados()
    (dados / "applications/gblinux.desktop").unlink(missing_ok=True)
    (dados / "icons/hicolor/scalable/apps/gblinux.svg").unlink(missing_ok=True)
    _atualizar_menu(dados / "applications")
