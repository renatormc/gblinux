#!/usr/bin/env bash
# Instala o Assinador de PDF: pipx, programa, driver do token e entrada no menu de aplicativos.
# Uso: curl -fsSL https://raw.githubusercontent.com/renatormc/gblinux/master/install.sh | bash
set -euo pipefail

# Tudo fica em uma função, chamada na última linha: um download interrompido não executa nada.
principal() {
    if [ "$(id -u)" -eq 0 ]; then
        echo "Rode sem sudo: o programa é instalado para o seu usuário e a senha é pedida quando preciso." >&2
        exit 1
    fi
    if ! command -v apt-get >/dev/null 2>&1 || ! command -v sudo >/dev/null 2>&1; then
        echo "Este instalador só funciona em Ubuntu e derivados, com o sudo disponível." >&2
        exit 1
    fi

    echo "==> Instalando o pipx"
    sudo apt-get update
    sudo apt-get install -y pipx libxcb-cursor0
    pipx ensurepath
    # O ensurepath só vale para os próximos terminais.
    PATH="$(pipx environment --value PIPX_BIN_DIR):$PATH"

    echo "==> Instalando o assinador"
    # --force: rodar o instalador de novo traz a versão mais recente.
    pipx install --force gblinux

    echo "==> Instalando o driver do token"
    gblinux install-drive

    echo "==> Criando a entrada no menu de aplicativos"
    gblinux --instalar-atalho

    echo
    echo "Pronto. Retire o token, conecte de novo e abra o Assinador de PDF pelo menu de aplicativos."
    echo "Para usar os comandos no terminal, feche este terminal e abra outro."
}

# Com curl | bash, a entrada padrão é o próprio script: nenhum comando pode consumi-la.
principal </dev/null
