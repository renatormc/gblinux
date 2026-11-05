"""Acesso ao token por PKCS#11. Só o processo filho (worker) deve importar este módulo."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import PyKCS11
from asn1crypto import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_der_public_key
from PyKCS11.LowLevel import (
    CKA_CLASS,
    CKA_ID,
    CKA_MODULUS,
    CKA_VALUE,
    CKC_X_509,
    CKA_CERTIFICATE_TYPE,
    CKF_SERIAL_SESSION,
    CKM_SHA256_RSA_PKCS,
    CKO_CERTIFICATE,
    CKO_PRIVATE_KEY,
    CKR_DEVICE_ERROR,
    CKR_DEVICE_REMOVED,
    CKR_MECHANISM_INVALID,
    CKR_PIN_INCORRECT,
    CKR_PIN_INVALID,
    CKR_PIN_LEN_RANGE,
    CKR_PIN_LOCKED,
    CKR_TOKEN_NOT_PRESENT,
    CKU_USER,
)

from .erros import (
    ERRO_DRIVER,
    FALHA_LISTAR,
    SENHA_BLOQUEADA,
    SENHA_INCORRETA,
    TOKEN_NAO_ENCONTRADO,
    ErroAssinatura,
)

_CN_ICP = re.compile(r"^(.+):(\d{11})$")
# Prefixo DER do DigestInfo de SHA-256 (RFC 8017, nota 1 da seção 9.2).
_DIGEST_INFO_SHA256 = bytes.fromhex("3031300d060960864801650304020105000420")


@dataclass(frozen=True)
class CertificadoToken:
    der: bytes
    id: bytes
    certificado: x509.Certificate

    @property
    def cn(self) -> str:
        try:
            return str(self.certificado.subject.native.get("common_name", ""))
        except ValueError:
            return ""

    @property
    def cpf(self) -> str | None:
        casamento = _CN_ICP.match(self.cn)
        return casamento.group(2) if casamento else None

    @property
    def nao_depois(self) -> datetime:
        return self.certificado.not_valid_after

    def valido_em(self, momento: datetime) -> bool:
        return self.certificado.not_valid_before <= momento <= self.certificado.not_valid_after

    @property
    def modulo(self) -> int | None:
        chave = self.certificado.public_key
        return chave.native["public_key"]["modulus"] if chave.algorithm == "rsa" else None


def traduzir(erro: PyKCS11.PyKCS11Error, padrao: str = ERRO_DRIVER) -> ErroAssinatura:
    codigo = erro.value
    if codigo in (CKR_PIN_INCORRECT, CKR_PIN_LEN_RANGE, CKR_PIN_INVALID):
        return ErroAssinatura(SENHA_INCORRETA)
    if codigo == CKR_PIN_LOCKED:
        return ErroAssinatura(SENHA_BLOQUEADA)
    if codigo in (CKR_TOKEN_NOT_PRESENT, CKR_DEVICE_REMOVED, CKR_DEVICE_ERROR):
        return ErroAssinatura(TOKEN_NAO_ENCONTRADO)
    return ErroAssinatura(padrao)


def _para_bytes(valor) -> bytes:
    return bytes(valor or b"")


class Token:
    """Uma sessão com o token, do `C_Initialize` ao `C_Finalize`. Use com `with`."""

    def __init__(self, biblioteca: str) -> None:
        self._biblioteca = biblioteca
        self._lib: PyKCS11.PyKCS11Lib | None = None
        self._sessao = None
        self._slot = None
        self._logado = False

    def __enter__(self) -> "Token":
        if not os.path.exists(self._biblioteca):
            raise ErroAssinatura(TOKEN_NAO_ENCONTRADO)
        try:
            self._lib = PyKCS11.PyKCS11Lib()
            self._lib.load(self._biblioteca)
            slots = self._lib.getSlotList(tokenPresent=True)
            if not slots:
                raise ErroAssinatura(TOKEN_NAO_ENCONTRADO)
            self._slot = slots[0]
            self._sessao = self._lib.openSession(self._slot, CKF_SERIAL_SESSION)
        except PyKCS11.PyKCS11Error as erro:
            self.encerrar()
            raise traduzir(erro) from erro
        except ErroAssinatura:
            self.encerrar()
            raise
        return self

    def __exit__(self, *_excecao) -> None:
        self.encerrar()

    def encerrar(self) -> None:
        """C_Logout, C_CloseSession e C_Finalize, mesmo depois de um erro."""
        sessao, self._sessao = self._sessao, None
        lib, self._lib = self._lib, None
        if sessao is not None:
            if self._logado:
                self._logado = False
                try:
                    sessao.logout()
                except PyKCS11.PyKCS11Error:
                    pass
            try:
                sessao.closeSession()
            except PyKCS11.PyKCS11Error:
                pass
        if lib is not None:
            try:
                lib.unload()  # chama C_Finalize
            except PyKCS11.PyKCS11Error:
                pass

    def listar_certificados(self) -> list[CertificadoToken]:
        try:
            objetos = self._sessao.findObjects(
                [(CKA_CLASS, CKO_CERTIFICATE), (CKA_CERTIFICATE_TYPE, CKC_X_509)]
            )
            brutos = [
                self._sessao.getAttributeValue(o, [CKA_VALUE, CKA_ID], allAsBinary=True)
                for o in objetos
            ]
        except PyKCS11.PyKCS11Error as erro:
            raise traduzir(erro, FALHA_LISTAR) from erro
        certificados = []
        for valor, identificador in brutos:
            der = _para_bytes(valor)
            try:
                certificado = x509.Certificate.load(der)
                # A análise é preguiçosa; só exige os campos que serão usados.
                certificado.not_valid_after, certificado.not_valid_before, certificado.subject.native
            except ValueError:
                continue
            certificados.append(CertificadoToken(der, _para_bytes(identificador), certificado))
        return certificados

    def senha_impossivel(self, senha: str) -> bool:
        """Indica a senha que com certeza falharia, para não gastar uma tentativa."""
        try:
            tamanho = len(senha.encode("utf-8"))
        except UnicodeEncodeError:
            return True
        if tamanho == 0:
            return True
        try:
            info = self._lib.getTokenInfo(self._slot)
        except PyKCS11.PyKCS11Error:
            return False
        minimo, maximo = info.ulMinPinLen, info.ulMaxPinLen
        if 0 < minimo <= maximo and not minimo <= tamanho <= maximo:
            return True
        return False

    def login(self, senha: str) -> None:
        """Envia a senha uma única vez. Quem chama não deve repetir em caso de falha."""
        if self._logado:
            raise RuntimeError("login repetido")
        try:
            self._sessao.login(senha, CKU_USER)
        except PyKCS11.PyKCS11Error as erro:
            raise traduzir(erro) from erro
        self._logado = True

    def localizar_chave(self, certificado: CertificadoToken):
        """Chave privada do certificado: pelo CKA_ID e, se não houver, pelo módulo RSA."""
        modulo = certificado.modulo
        if modulo is None:
            return None
        try:
            por_id = self._sessao.findObjects(
                [(CKA_CLASS, CKO_PRIVATE_KEY), (CKA_ID, tuple(certificado.id))]
            )
            for chave in por_id:
                if self._modulo_confere(chave, modulo):
                    return chave
            for chave in self._sessao.findObjects([(CKA_CLASS, CKO_PRIVATE_KEY)]):
                if self._modulo_confere(chave, modulo, exigir_leitura=True):
                    return chave
        except PyKCS11.PyKCS11Error as erro:
            raise traduzir(erro) from erro
        return None

    def _modulo_confere(self, chave, modulo: int, exigir_leitura: bool = False) -> bool:
        try:
            valor = self._sessao.getAttributeValue(chave, [CKA_MODULUS], allAsBinary=True)[0]
        except PyKCS11.PyKCS11Error:
            return not exigir_leitura
        if not valor:
            return not exigir_leitura
        return int.from_bytes(_para_bytes(valor), "big") == modulo

    def assinar(self, chave, certificado: CertificadoToken, dados: bytes) -> bytes:
        """RSA PKCS#1 v1.5 com SHA-256, conferida com a chave pública antes de ser usada."""
        try:
            assinatura = self._assinar_sha256(chave, dados)
        except PyKCS11.PyKCS11Error as erro:
            raise traduzir(erro) from erro
        publica = load_der_public_key(certificado.certificado.public_key.dump())
        try:
            publica.verify(assinatura, dados, padding.PKCS1v15(), hashes.SHA256())
        except Exception as erro:
            raise ErroAssinatura(ERRO_DRIVER) from erro
        return assinatura

    def _assinar_sha256(self, chave, dados: bytes) -> bytes:
        try:
            return bytes(self._sessao.sign(chave, dados, PyKCS11.Mechanism(CKM_SHA256_RSA_PKCS, None)))
        except PyKCS11.PyKCS11Error as erro:
            if erro.value != CKR_MECHANISM_INVALID:
                raise
        digest = hashes.Hash(hashes.SHA256())
        digest.update(dados)
        return bytes(
            self._sessao.sign(chave, _DIGEST_INFO_SHA256 + digest.finalize(), PyKCS11.MechanismRSAPKCS1)
        )


def agora_utc() -> datetime:
    return datetime.now(timezone.utc)
