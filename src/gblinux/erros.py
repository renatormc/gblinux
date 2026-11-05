"""Mensagens de erro de assinatura. A página depende destes textos; não os corrija."""

from __future__ import annotations

CANCELADO = "ASSINATURA DE DOCUMENTO CANCELADO PELO USUÁRIO"
TOKEN_NAO_ENCONTRADO = "Token não encontrado"
SENHA_INCORRETA = "Senha incorreta para o token"
PDF_INVALIDO = (
    "Documento não pode ser aberto e editado. Verifique se o documento esta protegido de "
    "edição ou contem senha e se os dados do TOKEN foram informados corretamente!"
)
NENHUM_CERTIFICADO = "Nenhum certificado valido. Procure o gestor da sua instituição!"
FALHA_LISTAR = (
    "Houve um erro ao listar os dados do Token, verifique se o token ta inserido "
    "corretamente e o dados informados"
)
ERRO_DRIVER = "Erro desconhecido, Verifique se foi instalado corretamente os drivers do token"
SENHA_BLOQUEADA = "Token bloqueado por excesso de tentativas de senha."
SEM_RESPOSTA = "Assinador não respondeu. Reinsira o token e tente novamente."
GOVBR_INDISPONIVEL = "Assinatura via Gov.br não está disponível nesta versão"
SEM_DOCUMENTO = "Nenhum arquivo para fazer a assinatura!"


def chave_nao_encontrada(cpf: str) -> str:
    return (
        f"Chave privada do token não encontrada para o CPF [{cpf}]. Verifique se o token está "
        "inserido, se a chave privada está correta e se o certicado está valido!"
    )


class ErroAssinatura(Exception):
    """Erro que vira a resposta JSON `{"status": 400, "message": ...}`."""

    def __init__(self, mensagem: str) -> None:
        super().__init__(mensagem)
        self.mensagem = mensagem
