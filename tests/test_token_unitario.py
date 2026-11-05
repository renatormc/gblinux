"""Comportamentos que o SoftHSM não reproduz: bloqueio de senha e falta de mecanismo."""

import PyKCS11
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from PyKCS11.LowLevel import (
    CKR_DEVICE_REMOVED,
    CKR_FUNCTION_FAILED,
    CKR_MECHANISM_INVALID,
    CKR_PIN_INCORRECT,
    CKR_PIN_LEN_RANGE,
    CKR_PIN_LOCKED,
    CKR_TOKEN_NOT_PRESENT,
)

from gblinux import erros
from gblinux.token_pkcs11 import Token, traduzir


@pytest.mark.parametrize(
    ("codigo", "mensagem"),
    [
        (CKR_PIN_INCORRECT, erros.SENHA_INCORRETA),
        (CKR_PIN_LEN_RANGE, erros.SENHA_INCORRETA),
        (CKR_PIN_LOCKED, erros.SENHA_BLOQUEADA),
        (CKR_TOKEN_NOT_PRESENT, erros.TOKEN_NAO_ENCONTRADO),
        (CKR_DEVICE_REMOVED, erros.TOKEN_NAO_ENCONTRADO),
        (CKR_FUNCTION_FAILED, erros.ERRO_DRIVER),
    ],
)
def test_traducao_dos_codigos(codigo, mensagem):
    assert traduzir(PyKCS11.PyKCS11Error(codigo)).mensagem == mensagem


def test_fallback_para_ckm_rsa_pkcs_com_digestinfo():
    chave = rsa.generate_private_key(65537, 2048)
    mecanismos = []

    class Sessao:
        def sign(self, _chave, dados, mecanismo):
            mecanismos.append(mecanismo)
            if mecanismo is not PyKCS11.MechanismRSAPKCS1:
                raise PyKCS11.PyKCS11Error(CKR_MECHANISM_INVALID)
            # Assinatura "crua" de CKM_RSA_PKCS: o DigestInfo já vem montado.
            n = chave.private_numbers().public_numbers.n
            em = b"\x00\x01" + b"\xff" * (256 - 3 - len(dados)) + b"\x00" + bytes(dados)
            d = chave.private_numbers().d
            return pow(int.from_bytes(em, "big"), d, n).to_bytes(256, "big")

    token = Token("/inexistente")
    token._sessao = Sessao()
    assinatura = token._assinar_sha256(object(), b"atributos")
    assert len(mecanismos) == 2 and mecanismos[1] is PyKCS11.MechanismRSAPKCS1
    chave.public_key().verify(assinatura, b"atributos", padding.PKCS1v15(), hashes.SHA256())


def test_login_nao_repete():
    class Sessao:
        chamadas = 0

        def login(self, _senha, _tipo):
            Sessao.chamadas += 1

    token = Token("/inexistente")
    token._sessao = Sessao()
    token.login("12345678")
    with pytest.raises(RuntimeError):
        token.login("12345678")
    assert Sessao.chamadas == 1
    token._sessao = None
