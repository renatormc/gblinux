#!/usr/bin/env bash
# Gera dist/gblinux_<versão>_amd64.deb. Só precisa do Docker na máquina.
set -euo pipefail

raiz="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$raiz/dist"

docker run --rm \
    -v "$raiz:/fonte:ro" \
    -v "$raiz/dist:/saida" \
    -e DONO="$(id -u):$(id -g)" \
    ghcr.io/astral-sh/uv:0.13.7-debian \
    bash /fonte/empacotamento/montar-deb.sh
