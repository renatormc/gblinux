"""Decodificação da query da porta 3399 e validação do CPF."""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from urllib.parse import unquote, unquote_plus

_ESPACOS = re.compile(r"\s+")
_PADRAO_BASE64 = re.compile(r"^[A-Za-z0-9+/=\r\n]+$")


@dataclass(frozen=True)
class ParametrosPagina:
    cpf: str = ""
    senha: str = ""
    file_download: str = ""


def _base64_tolerante(valor: str) -> bytes:
    limpo = _ESPACOS.sub("", valor).rstrip("=")
    try:
        return base64.b64decode(limpo + "=" * (-len(limpo) % 4))
    except (binascii.Error, ValueError):
        return b""


def decodificar_query(query: str) -> ParametrosPagina:
    """`query` é a parte crua da URL, depois do `?`; pode ser vazia."""
    pares: dict[str, str] = {}
    # unquote, e não unquote_plus: o `+` faz parte do alfabeto Base64.
    for par in unquote(query).split("&"):
        nome, _, valor = par.partition("=")
        pares.setdefault(nome, valor)

    def texto(nome: str) -> str:
        return _base64_tolerante(pares.get(nome, "")).decode("utf-8", "replace")

    # A senha não passa por URL-decode. Bytes que não são UTF-8 são preservados com
    # surrogateescape: o worker recusa sem gastar uma tentativa, em vez de enviar outra senha.
    senha = _base64_tolerante(pares.get("senha", "")).decode("utf-8", "surrogateescape")
    return ParametrosPagina(
        cpf=unquote_plus(texto("cpf")),
        senha=senha,
        file_download=unquote_plus(texto("fileDownload")),
    )


def decodificar_documento(valor: str) -> bytes:
    """Decodifica um PDF em Base64, removendo espaços em branco."""
    return _base64_tolerante(valor)


def parece_base64(valor: str) -> bool:
    return bool(_PADRAO_BASE64.match(valor))


def cpf_valido(cpf: str) -> bool:
    if len(cpf) != 11 or not (cpf.isascii() and cpf.isdigit()) or len(set(cpf)) == 1:
        return False
    digitos = [int(c) for c in cpf]
    for tamanho in (9, 10):
        soma = sum(d * p for d, p in zip(digitos[:tamanho], range(tamanho + 1, 1, -1)))
        resto = 11 - soma % 11
        if (0 if resto >= 10 else resto) != digitos[tamanho]:
            return False
    return True
