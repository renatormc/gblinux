"""Processo filho: um por pedido, único lugar em que a biblioteca PKCS#11 é carregada.

Protocolo: JSON, uma mensagem por linha, em stdin e stdout.

    pai -> filho  {"cpf": "...", "documentos": ["<b64>", ...], "biblioteca": "..."}
    filho -> pai  {"tipo": "escolher", "opcoes": ["NOME:CPF", ...]}
    pai -> filho  {"escolha": "NOME:CPF"} ou {"escolha": null}
    filho -> pai  {"tipo": "senha", "titular": "NOME:******78901"}
    pai -> filho  {"senha": "..."} ou {"senha": null}
    filho -> pai  {"tipo": "resultado", "documentos": ["<b64>", ...]}
    filho -> pai  {"tipo": "erro", "mensagem": "..."}

A senha só é enviada depois do pedido do filho, quando PDF e certificado já foram conferidos.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import signal
import sys
import traceback
from collections.abc import Callable

from . import erros, pdf
from .erros import ErroAssinatura
from .parametros import cpf_valido
from .token_pkcs11 import CertificadoToken, Token, agora_utc

_PR_SET_PDEATHSIG = 1


def candidatos(certificados: list[CertificadoToken]) -> list[CertificadoToken]:
    """Certificados pessoais dentro da validade. Ainda falta conferir a chave, após o login."""
    agora = agora_utc()
    return [c for c in certificados if c.cpf and c.modulo is not None and c.valido_em(agora)]


def selecionar(
    cpf: str,
    disponiveis: list[CertificadoToken],
    escolher: Callable[[list[str]], str | None],
) -> tuple[str, list[CertificadoToken]]:
    """Filtra os certificados pelo CPF pedido, ou pergunta ao usuário, e devolve o CPF escolhido."""
    if cpf_valido(cpf):
        selecionados = [c for c in disponiveis if cpf in c.cn]
        if not selecionados:
            raise ErroAssinatura(erros.chave_nao_encontrada(cpf))
        return cpf, selecionados

    if not disponiveis:
        raise ErroAssinatura(erros.NENHUM_CERTIFICADO)

    por_cpf: dict[str, CertificadoToken] = {}
    for certificado in sorted(disponiveis, key=lambda c: c.nao_depois, reverse=True):
        por_cpf.setdefault(certificado.cpf, certificado)
    if len(por_cpf) == 1:
        escolhido = next(iter(por_cpf))
    else:
        rotulos = {c.cn: valor for valor, c in por_cpf.items()}
        resposta = escolher(list(rotulos))
        if resposta not in rotulos:
            raise ErroAssinatura(erros.CANCELADO)
        escolhido = rotulos[resposta]
    return escolhido, [c for c in disponiveis if c.cpf == escolhido]


def montar_cadeia(
    assinante: CertificadoToken, certificados: list[CertificadoToken]
) -> list[CertificadoToken]:
    """Segue os emissores pelo DN exato, pois o token pode trazer mais de uma geração de ACs."""
    cadeia: list[CertificadoToken] = []
    atual = assinante
    vistos = {assinante.der}
    while atual.certificado.issuer != atual.certificado.subject:
        emissores = [
            c
            for c in certificados
            if c.der not in vistos and c.certificado.subject == atual.certificado.issuer
        ]
        if not emissores:
            break
        identificador = atual.certificado.authority_key_identifier
        emissor = next(
            (c for c in emissores if identificador and c.certificado.key_identifier == identificador),
            emissores[0],
        )
        cadeia.append(emissor)
        vistos.add(emissor.der)
        atual = emissor
    return cadeia


def assinar_documentos(
    documentos: list[bytes],
    cpf: str,
    biblioteca: str,
    escolher: Callable[[list[str]], str | None],
    pedir_senha: Callable[[str], str | None],
) -> list[bytes]:
    # O PDF é conferido primeiro: um documento ruim não pode custar uma tentativa de senha.
    for documento in documentos:
        pdf.validar(documento)

    with Token(biblioteca) as token:
        certificados = token.listar_certificados()
        cpf_alvo, selecionados = selecionar(cpf, candidatos(certificados), escolher)

        senha = pedir_senha(pdf.mascarar(selecionados[0].cn))
        if senha is None:
            raise ErroAssinatura(erros.CANCELADO)
        if token.senha_impossivel(senha):
            raise ErroAssinatura(erros.SENHA_INCORRETA)
        token.login(senha)
        del senha

        com_chave = []
        for certificado in selecionados:
            chave = token.localizar_chave(certificado)
            if chave is not None:
                com_chave.append((certificado, chave))
        if not com_chave:
            raise ErroAssinatura(erros.chave_nao_encontrada(cpf_alvo))
        assinante, chave = max(com_chave, key=lambda par: par[0].nao_depois)

        cadeia = montar_cadeia(assinante, certificados)
        reserva = pdf.reserva_contents(
            assinante.certificado,
            [c.certificado for c in cadeia],
            # Arredonda para cima: um módulo pode ter tamanho não múltiplo de 8 bits.
            (assinante.certificado.public_key.bit_size + 7) // 8,
        )
        assinados = []
        for documento in documentos:
            preparado = pdf.preparar(documento, assinante.cn, reserva)
            der = pdf.montar_cms(
                preparado.digest,
                assinante.certificado,
                [c.certificado for c in cadeia],
                lambda dados: token.assinar(chave, assinante, dados),
            )
            assinados.append(preparado.embutir(der))
        return assinados


def _encerrar_por_sinal(_sinal, _quadro) -> None:
    # O Python só executa isto quando a chamada ao driver retorna; assim a operação em
    # andamento termina, e o `finally` ainda faz logout, fecha a sessão e finaliza.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    raise SystemExit(143)


def main() -> None:
    # O driver pode escrever em stdout. O protocolo fica em um descritor próprio.
    saida = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    signal.signal(signal.SIGTERM, _encerrar_por_sinal)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        ctypes.CDLL(None).prctl(_PR_SET_PDEATHSIG, signal.SIGTERM)
    except (OSError, AttributeError):
        pass  # sem isso, o fim do stdin ainda encerra o processo

    entrada = sys.stdin

    def enviar(mensagem: dict) -> None:
        saida.write(json.dumps(mensagem) + "\n")
        saida.flush()

    def receber() -> dict:
        linha = entrada.readline()
        if not linha:
            raise SystemExit(0)
        return json.loads(linha)

    def escolher(opcoes: list[str]) -> str | None:
        enviar({"tipo": "escolher", "opcoes": opcoes})
        return receber().get("escolha")

    def pedir_senha(titular: str) -> str | None:
        enviar({"tipo": "senha", "titular": titular})
        return receber().get("senha")

    codigo = 0
    try:
        pedido = receber()
        documentos = [base64.b64decode(d) for d in pedido["documentos"]]
        assinados = assinar_documentos(
            documentos, pedido.get("cpf", ""), pedido["biblioteca"], escolher, pedir_senha
        )
        enviar(
            {"tipo": "resultado", "documentos": [base64.b64encode(d).decode() for d in assinados]}
        )
    except ErroAssinatura as erro:
        enviar({"tipo": "erro", "mensagem": erro.mensagem})
    except SystemExit as saida_pedida:
        codigo = saida_pedida.code if isinstance(saida_pedida.code, int) else 0
    except Exception as erro:
        # Só o tipo vai para o log: a mensagem poderia conter dados do pedido.
        print(f"Erro no worker: {type(erro).__name__}", file=sys.stderr)
        if os.environ.get("ASSINADOR_DEPURAR_WORKER"):
            traceback.print_exc()
        enviar({"tipo": "erro", "mensagem": erros.ERRO_DRIVER})
    # Evita que destrutores da biblioteca segurem o processo depois do C_Finalize.
    saida.flush()
    sys.stderr.flush()
    os._exit(codigo)


if __name__ == "__main__":
    main()
