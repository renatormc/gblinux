"""Infraestrutura de testes: token SoftHSM com certificados ICP-Brasil fictícios.

A biblioteca é procurada em `ASSINADOR_TESTE_SOFTHSM` e nos caminhos usuais do pacote `softhsm2`.
Nada aqui toca o token real: o ambiente do teste sempre aponta para o SoftHSM.
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import PyKCS11
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from PyKCS11.LowLevel import (
    CKA_CERTIFICATE_TYPE,
    CKA_CLASS,
    CKA_COEFFICIENT,
    CKA_DECRYPT,
    CKA_EXPONENT_1,
    CKA_EXPONENT_2,
    CKA_ID,
    CKA_ISSUER,
    CKA_KEY_TYPE,
    CKA_LABEL,
    CKA_MODULUS,
    CKA_PRIME_1,
    CKA_PRIME_2,
    CKA_PRIVATE,
    CKA_PRIVATE_EXPONENT,
    CKA_PUBLIC_EXPONENT,
    CKA_SENSITIVE,
    CKA_SERIAL_NUMBER,
    CKA_SIGN,
    CKA_SUBJECT,
    CKA_TOKEN,
    CKA_VALUE,
    CKC_X_509,
    CKF_RW_SESSION,
    CKF_SERIAL_SESSION,
    CKK_RSA,
    CKO_CERTIFICATE,
    CKO_PRIVATE_KEY,
    CKU_SO,
)

SENHA = "12345678"
SENHA_SO = "87654321"
CPF_1 = "12345678909"
CPF_2 = "52998224725"
NOME_1 = f"FULANO SILVA:{CPF_1}"  # 24 caracteres
NOME_2 = f"BELTRANA SOUZA:{CPF_2}"

_CANDIDATAS = (
    os.environ.get("ASSINADOR_TESTE_SOFTHSM", ""),
    "/usr/lib/softhsm/libsofthsm2.so",
    "/usr/lib/x86_64-linux-gnu/softhsm/libsofthsm2.so",
    "/usr/lib64/pkcs11/libsofthsm2.so",
)
SOFTHSM = next((c for c in _CANDIDATAS if c and Path(c).exists()), None)
SPY = next(
    (
        c
        for c in (
            "/usr/lib/x86_64-linux-gnu/pkcs11/pkcs11-spy.so",
            "/usr/lib/x86_64-linux-gnu/pkcs11-spy.so",
            "/usr/lib64/pkcs11/pkcs11-spy.so",
        )
        if Path(c).exists()
    ),
    None,
)


@dataclass(frozen=True)
class Emitido:
    certificado: x509.Certificate
    chave: rsa.RSAPrivateKey


def _nome(cn: str) -> x509.Name:
    return x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "BR"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ICP-Teste"),
            x509.NameAttribute(NameOID.COMMON_NAME, cn),
        ]
    )


def emitir(cn, emissor=None, ca=False, inicio=-30, fim=365) -> Emitido:
    chave = rsa.generate_private_key(65537, 2048)
    agora = dt.datetime.now(dt.timezone.utc)
    construtor = (
        x509.CertificateBuilder()
        .subject_name(_nome(cn))
        .issuer_name(emissor.certificado.subject if emissor else _nome(cn))
        .public_key(chave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(agora + dt.timedelta(days=inicio))
        .not_valid_after(agora + dt.timedelta(days=fim))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(chave.public_key()), critical=False)
    )
    assinante = emissor.chave if emissor else chave
    construtor = construtor.add_extension(
        x509.AuthorityKeyIdentifier.from_issuer_public_key(assinante.public_key()), critical=False
    )
    return Emitido(construtor.sign(assinante, hashes.SHA256()), chave)


@dataclass
class Pki:
    raiz: Emitido
    ac: Emitido
    ac_outra: Emitido
    pessoal: Emitido
    vencido: Emitido
    segundo: Emitido


@pytest.fixture(scope="session")
def pki() -> Pki:
    raiz = emitir("AC Raiz Teste G2", ca=True, inicio=-1000, fim=3650)
    # Duas gerações, como no token real: só a G2 emitiu os certificados pessoais.
    ac = emitir("AC Teste G2", emissor=raiz, ca=True, inicio=-1000, fim=3650)
    ac_outra = emitir("AC Teste", ca=True, inicio=-3000, fim=3650)
    return Pki(
        raiz=raiz,
        ac=ac,
        ac_outra=ac_outra,
        pessoal=emitir(NOME_1, emissor=ac),
        vencido=emitir(NOME_1, emissor=ac, inicio=-800, fim=-30),
        segundo=emitir(NOME_2, emissor=ac),
    )


def _gravar_certificado(sessao, emitido: Emitido, id_: bytes, rotulo: str) -> None:
    cert = emitido.certificado
    sessao.createObject(
        [
            (CKA_CLASS, CKO_CERTIFICATE),
            (CKA_CERTIFICATE_TYPE, CKC_X_509),
            (CKA_TOKEN, True),
            (CKA_PRIVATE, False),
            (CKA_ID, tuple(id_)),
            (CKA_LABEL, rotulo),
            (CKA_SUBJECT, tuple(cert.subject.public_bytes())),
            (CKA_ISSUER, tuple(cert.issuer.public_bytes())),
            (CKA_SERIAL_NUMBER, tuple(cert.serial_number.to_bytes(21, "big").lstrip(b"\0"))),
            (CKA_VALUE, tuple(cert.public_bytes(serialization.Encoding.DER))),
        ]
    )


def _gravar_chave(sessao, chave: rsa.RSAPrivateKey, id_: bytes, rotulo: str) -> None:
    numeros = chave.private_numbers()

    def b(valor: int) -> tuple:
        return tuple(valor.to_bytes((valor.bit_length() + 7) // 8, "big"))

    sessao.createObject(
        [
            (CKA_CLASS, CKO_PRIVATE_KEY),
            (CKA_KEY_TYPE, CKK_RSA),
            (CKA_TOKEN, True),
            (CKA_PRIVATE, True),
            (CKA_SENSITIVE, True),
            (CKA_SIGN, True),
            (CKA_DECRYPT, True),
            (CKA_ID, tuple(id_)),
            (CKA_LABEL, rotulo),
            (CKA_MODULUS, b(numeros.public_numbers.n)),
            (CKA_PUBLIC_EXPONENT, b(numeros.public_numbers.e)),
            (CKA_PRIVATE_EXPONENT, b(numeros.d)),
            (CKA_PRIME_1, b(numeros.p)),
            (CKA_PRIME_2, b(numeros.q)),
            (CKA_EXPONENT_1, b(numeros.dmp1)),
            (CKA_EXPONENT_2, b(numeros.dmq1)),
            (CKA_COEFFICIENT, b(numeros.iqmp)),
        ]
    )


def criar_token(pasta: Path, itens) -> Path:
    """`itens`: (Emitido, id, rótulo, com_chave). Cria o token e devolve o softhsm2.conf."""
    tokens = pasta / "tokens"
    tokens.mkdir(parents=True)
    conf = pasta / "softhsm2.conf"
    conf.write_text(f"directories.tokendir = {tokens}\nobjectstore.backend = file\nlog.level = ERROR\n")
    os.environ["SOFTHSM2_CONF"] = str(conf)
    lib = PyKCS11.PyKCS11Lib()
    lib.load(SOFTHSM)
    slot = lib.getSlotList(tokenPresent=True)[0]
    lib.initToken(slot, SENHA_SO, "TESTE")
    slot = lib.getSlotList(tokenPresent=True)[0]
    sessao = lib.openSession(slot, CKF_SERIAL_SESSION | CKF_RW_SESSION)
    sessao.login(SENHA_SO, CKU_SO)
    sessao.initPin(SENHA)
    sessao.logout()
    sessao.login(SENHA)
    for emitido, id_, rotulo, com_chave in itens:
        _gravar_certificado(sessao, emitido, id_, rotulo)
        if com_chave:
            _gravar_chave(sessao, emitido.chave, id_, rotulo)
    sessao.logout()
    sessao.closeSession()
    lib.unload()
    return conf


@pytest.fixture(scope="session")
def _token_base(pki, tmp_path_factory) -> dict[str, Path]:
    if SOFTHSM is None:
        pytest.skip("SoftHSM não encontrado; instale softhsm2 ou defina ASSINADOR_TESTE_SOFTHSM")
    acs = [
        (pki.raiz, b"\xa0", "AC", False),
        (pki.ac, b"\xa1", "AC", False),
        (pki.ac_outra, b"\xa2", "AC", False),
    ]
    mesmo_rotulo = "Certificado de Fulano"
    base = tmp_path_factory.mktemp("hsm")
    return {
        # O vencido vem primeiro, para que a seleção não acerte só pela ordem.
        "um": criar_token(
            base / "um",
            [
                (pki.vencido, bytes.fromhex("d5364419"), mesmo_rotulo, True),
                (pki.pessoal, bytes.fromhex("b010b227"), mesmo_rotulo, True),
                *acs,
            ],
        ),
        "dois": criar_token(
            base / "dois",
            [
                (pki.pessoal, b"\x01", "Fulano", True),
                (pki.segundo, b"\x02", "Beltrana", True),
                *acs,
            ],
        ),
        "sem_chave": criar_token(base / "sem_chave", [(pki.pessoal, b"\x01", "Fulano", False), *acs]),
        "so_acs": criar_token(base / "so_acs", acs),
    }


@pytest.fixture
def token(_token_base, tmp_path, monkeypatch):
    """Devolve função que ativa uma cópia nova de um token; preserva o contador de senha."""

    def ativar(nome: str = "um", espiar: bool = False) -> dict:
        destino = tmp_path / f"copia_{nome}"
        shutil.copytree(_token_base[nome].parent, destino)
        conf = destino / "softhsm2.conf"
        conf.write_text(
            f"directories.tokendir = {destino / 'tokens'}\nobjectstore.backend = file\nlog.level = ERROR\n"
        )
        monkeypatch.setenv("SOFTHSM2_CONF", str(conf))
        resultado = {"biblioteca": SOFTHSM, "log_spy": None}
        if espiar:
            if SPY is None:
                pytest.skip("pkcs11-spy não encontrado")
            registro = destino / "spy.log"
            monkeypatch.setenv("PKCS11SPY", SOFTHSM)
            monkeypatch.setenv("PKCS11SPY_OUTPUT", str(registro))
            resultado.update(biblioteca=SPY, log_spy=registro)
        return resultado

    return ativar
