"""Pede uma assinatura ao programa aberto, fazendo as mesmas chamadas HTTP da página web."""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests

from .config import PORTA_PAGINA

_ORIGEM = "http://localhost"
_TEMPO_LIMITE_S = 610  # um pouco acima dos 600 s do assinador, para receber o erro dele


def _b64(texto: str) -> str:
    return base64.b64encode(texto.encode()).decode()


def _argumentos(argv: list[str] | None) -> argparse.Namespace:
    analisador = argparse.ArgumentParser(
        prog="testar-assinatura",
        description="Assina um PDF pelo assinador aberto, como se o pedido viesse do navegador.",
    )
    analisador.add_argument("pdf", help="caminho de um PDF local, ou URL http(s) a ser baixada")
    analisador.add_argument("--cpf", default="", help="CPF sem pontuação (padrão: escolher no token)")
    analisador.add_argument(
        "--enviar-senha",
        action="store_true",
        help="digita a senha aqui e a envia no pedido (padrão: digitar na janela do assinador)",
    )
    analisador.add_argument("--saida", type=Path, help="arquivo de saída (padrão: <nome>-assinado.pdf)")
    analisador.add_argument("--porta", type=int, default=PORTA_PAGINA, help=argparse.SUPPRESS)
    return analisador.parse_args(argv)


def _mensagem_de_erro(corpo: str) -> str:
    try:
        return json.loads(corpo)["message"]
    except (ValueError, KeyError, TypeError):
        return corpo


def _pedir(args: argparse.Namespace, senha: str, sessao: requests.Session) -> requests.Response:
    base = f"http://localhost:{args.porta}/assinarPdf"
    query = [f"nocache={int(time.time() * 1000)}"]
    if args.cpf:
        query.append(f"cpf={_b64(args.cpf)}")
    if senha:
        query.append(f"senha={_b64(senha)}")
    cabecalhos = {"Origin": _ORIGEM}

    if args.pdf.lower().startswith(("http://", "https://")):
        query.append(f"fileDownload={_b64(args.pdf)}")
        print(f"GET /assinarPdf com fileDownload={args.pdf}")
        return sessao.get(f"{base}?{'&'.join(query)}", headers=cabecalhos, timeout=_TEMPO_LIMITE_S)

    conteudo = Path(args.pdf).read_bytes()
    # JSON em outra origem faz o navegador enviar antes uma consulta OPTIONS.
    previa = sessao.options(
        base,
        headers={
            **cabecalhos,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
        timeout=10,
    )
    print(f"OPTIONS /assinarPdf -> {previa.status_code}")
    print(f"POST /assinarPdf com documento de {len(conteudo)} bytes")
    return sessao.post(
        f"{base}?{'&'.join(query)}",
        data=json.dumps({"documento": base64.b64encode(conteudo).decode()}),
        headers={**cabecalhos, "Content-Type": "application/json"},
        timeout=_TEMPO_LIMITE_S,
    )


def _caminho_saida(args: argparse.Namespace) -> Path:
    if args.saida:
        return args.saida
    if args.pdf.lower().startswith(("http://", "https://")):
        nome = Path(urlsplit(args.pdf).path).stem or "documento"
        return Path(f"{nome}-assinado.pdf")
    origem = Path(args.pdf)
    return origem.with_name(f"{origem.stem}-assinado.pdf")


def main(argv: list[str] | None = None) -> int:
    args = _argumentos(argv)
    eh_url = args.pdf.lower().startswith(("http://", "https://"))
    if not eh_url and not Path(args.pdf).is_file():
        print(f"Arquivo não encontrado: {args.pdf}", file=sys.stderr)
        return 2

    senha = getpass.getpass("Senha do token (uma tentativa): ") if args.enviar_senha else ""
    if not senha:
        print("Se o assinador pedir, digite a senha na janela dele.")

    sessao = requests.Session()
    sessao.trust_env = False  # o pedido é local e não deve passar por proxy
    try:
        resposta = _pedir(args, senha, sessao)
    except requests.ConnectionError:
        print(
            f"Não foi possível conectar à porta {args.porta}. Abra o programa com: "
            "uv run gblinux",
            file=sys.stderr,
        )
        return 1
    except requests.Timeout:
        print("O assinador não respondeu no tempo limite.", file=sys.stderr)
        return 1

    if resposta.status_code != 200:
        mensagem = _mensagem_de_erro(resposta.content.decode("utf-8", "replace"))
        print(f"Erro {resposta.status_code}: {mensagem}", file=sys.stderr)
        return 1

    assinado = base64.b64decode(resposta.content)
    saida = _caminho_saida(args)
    saida.write_bytes(assinado)
    print(f"Assinado: {saida} ({len(assinado)} bytes)")
    if not eh_url and not assinado.startswith(Path(args.pdf).read_bytes()):
        print("Erro: o PDF original não foi preservado como prefixo do assinado.", file=sys.stderr)
        return 1

    if shutil.which("pdfsig"):
        verificacao = subprocess.run(
            ["pdfsig", str(saida)], capture_output=True, text=True, check=False
        )
        print(verificacao.stdout.rstrip())
        if "Signature is Valid" not in verificacao.stdout:
            print("Erro: o pdfsig não confirmou a assinatura.", file=sys.stderr)
            return 1
    else:
        print("pdfsig não encontrado; a assinatura não foi conferida.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
