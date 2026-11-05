#!/usr/bin/env bash
# Instala o .deb em contêineres limpos e confere o programa. Não usa token real.
# Uso: empacotamento/testar-deb.sh [imagem...]   (padrão: ubuntu:24.04 ubuntu:26.04)
set -euo pipefail

raiz="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
versao="$(sed -n 's/^version = "\(.*\)"$/\1/p' "$raiz/pyproject.toml" | head -1)"
[ "$#" -gt 0 ] || set -- ubuntu:24.04 ubuntu:26.04

for imagem in "$@"; do
    echo "=== $imagem"
    docker run --rm -v "$raiz/dist:/dist:ro" -e VERSAO="$versao" "$imagem" bash -euc '
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq

        # Simula quem já seguiu as instruções manuais: o pacote precisa substituir esses arquivos.
        mkdir -p /usr/lib/ePass2003-Linux-x64/x86_64/redist
        echo antigo >/usr/lib/libcastle.so.1.0.0
        ln -s /usr/lib/libcastle.so.1.0.0 /usr/lib/libcastle.so

        apt-get install -y -qq --no-install-recommends "/dist/gblinux_${VERSAO}_amd64.deb" >/dev/null
        echo "ok: instalado"

        # Antes de instalar o xvfb, que traz bibliotecas gráficas e esconderia uma dependência faltando.
        ausentes="$(find /opt/gblinux/venv/lib/python*/site-packages/PySide6 \
                /opt/gblinux/venv/lib/python*/site-packages/shiboken6 \
                \( -name "libqxcb.so" -o -name "libqwayland*.so" -o -name "QtWidgets*.so" \
                   -o -name "QtGui*.so" -o -name "QtCore*.so" -o -name "Shiboken*.so" \) \
                -exec env LD_LIBRARY_PATH="$(echo /opt/gblinux/venv/lib/python*/site-packages/PySide6/Qt/lib)" ldd {} + \
                | grep "not found" | sort -u || true)"
        if [ -n "$ausentes" ]; then
            echo "FALHA: bibliotecas ausentes:"; echo "$ausentes"; exit 1
        fi
        echo "ok: bibliotecas do Qt presentes"

        [ "$(readlink -f /usr/lib/libcastle.so)" = /usr/lib/libcastle.so.1.0.0 ]
        [ "$(wc -c </usr/lib/libcastle.so.1.0.0)" -gt 1000000 ]
        [ -f /usr/lib/ePass2003-Linux-x64/x86_64/redist/libcastle.so.1.0.0 ]
        [ -f /usr/lib/udev/rules.d/60-gblinux-epass2003.rules ]
        [ -f /usr/share/icons/hicolor/scalable/apps/gblinux.svg ]
        echo "ok: driver, regra udev e ícone no lugar"

        apt-get install -y -qq --no-install-recommends xvfb desktop-file-utils xauth curl poppler-utils >/dev/null
        desktop-file-validate /usr/share/applications/gblinux.desktop
        grep "^Exec=" /usr/share/applications/gblinux.desktop

        useradd -m pessoa
        su pessoa -c "
            set -eu
            cd ~
            /opt/gblinux/venv/bin/python -c \"
import PyKCS11
b = PyKCS11.PyKCS11Lib(); b.load(\\\"/usr/lib/libcastle.so\\\")
print(\\\"ok: driver carregado; tokens:\\\", len(b.getSlotList(tokenPresent=True)))
\"
            exec_atalho=\$(sed -n \"s/^Exec=//p\" /usr/share/applications/gblinux.desktop)
            QT_QPA_PLATFORM=xcb xvfb-run -a \$exec_atalho >janela.log 2>&1 &
            for i in \$(seq 1 30); do
                curl -s -o /dev/null http://localhost:3399/ && curl -s -o /dev/null http://localhost:4499/ && break
                sleep 1
            done
            curl -s -o /dev/null http://localhost:3399/ || { echo FALHA: porta 3399 fechada; cat janela.log; exit 1; }
            curl -s -o /dev/null http://localhost:4499/ || { echo FALHA: porta 4499 fechada; cat janela.log; exit 1; }
            echo ok: janela aberta pelo comando do atalho, portas 3399 e 4499 respondendo

            /opt/gblinux/venv/bin/python -c \"from PIL import Image; Image.new(\\\"RGB\\\", (50, 50), \\\"white\\\").save(\\\"a.pdf\\\")\"
            testar-assinatura a.pdf >assinatura.log 2>&1 || true
            cat assinatura.log
            grep -q \"Token não encontrado\" assinatura.log || { echo FALHA: resposta inesperada sem token; cat janela.log; exit 1; }
            echo ok: pedido passou pelo processo filho e pelo driver, sem token conectado
            pkill -TERM -f \"^/opt/gblinux/venv/bin/python -m gblinux\"
        "

        apt-get purge -y -qq gblinux >/dev/null
        for c in /opt/gblinux /usr/bin/gblinux /usr/lib/libcastle.so /usr/lib/libcastle.so.1.0.0 \
                 /usr/lib/udev/rules.d/60-gblinux-epass2003.rules /usr/share/applications/gblinux.desktop; do
            if [ -e "$c" ]; then echo "FALHA: sobrou $c"; exit 1; fi
        done
        echo "ok: remoção limpa"
    '
done
echo "Todos os testes passaram."
