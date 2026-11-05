"""Configuração lida do ambiente."""

from __future__ import annotations

import os
from dataclasses import dataclass

PORTA_PAGINA = 3399
PORTA_DIRETA = 4499


def _int(nome: str, padrao: int) -> int:
    valor = os.environ.get(nome, "").strip()
    return int(valor) if valor else padrao


@dataclass(frozen=True)
class Config:
    biblioteca_pkcs11: str = "/usr/lib/libcastle.so"
    arquivo_cas: str | None = None
    limite_bytes: int = 100 * 1024 * 1024
    tempo_limite_s: float = 600.0
    tempo_conectar_s: float = 30.0
    tempo_ler_s: float = 120.0
    # Tempo que o driver recebe para terminar depois de um SIGTERM.
    tempo_encerrar_s: float = 15.0


def carregar() -> Config:
    return Config(
        biblioteca_pkcs11=os.environ.get("ASSINADOR_PKCS11_LIB") or Config.biblioteca_pkcs11,
        arquivo_cas=os.environ.get("ASSINADOR_CA_BUNDLE") or None,
        limite_bytes=_int("ASSINADOR_LIMITE_BYTES", Config.limite_bytes),
        tempo_limite_s=_int("ASSINADOR_TEMPO_LIMITE_S", int(Config.tempo_limite_s)),
    )
