"""Download dos PDFs: somente http e https, com tempo limite e tamanho máximo."""

from __future__ import annotations

from urllib.parse import urlsplit

import requests

from .config import Config


class FalhaDownload(Exception):
    pass


def baixar(url: str, config: Config) -> bytes:
    if urlsplit(url).scheme.lower() not in ("http", "https"):
        raise FalhaDownload("somente URLs http e https são aceitas")
    dados = bytearray()
    try:
        # Em redirecionamentos, o requests só aceita http e https, e recusa file:// ou ftp://.
        with requests.get(
            url,
            stream=True,
            timeout=(config.tempo_conectar_s, config.tempo_ler_s),
            verify=config.arquivo_cas or True,
        ) as resposta:
            resposta.raise_for_status()
            for bloco in resposta.iter_content(1024 * 1024):
                dados += bloco
                if len(dados) > config.limite_bytes:
                    raise FalhaDownload("o arquivo excede o tamanho máximo permitido")
    except requests.RequestException as erro:
        raise FalhaDownload(str(erro)) from erro
    return bytes(dados)
