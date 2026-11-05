#!/usr/bin/env bash
# Roda dentro do contêiner de construir-deb.sh. /fonte é somente leitura e /saida recebe o pacote.
set -euo pipefail
umask 022

DRIVER_URL="https://site.solutinet.com.br/drivers/Token/ePass2003/Linux/ePass2003_Castle_20141128.tar.gz"
DRIVER_SHA256="00fbb5756b1b01222fe6825ad7ca43da0b662d1f7e2d06584ea7c3c7be490a23"
DESTINO=/opt/gblinux
PACOTE=/tmp/pacote

apt-get update -qq
apt-get install -y -qq --no-install-recommends curl ca-certificates >/dev/null

# Só o que entra no programa: spec.md e outros arquivos locais ficam de fora.
mkdir -p /tmp/projeto
cp -r /fonte/pyproject.toml /fonte/uv.lock /fonte/README.md /fonte/LICENSE \
    /fonte/.python-version /fonte/src /tmp/projeto/
cd /tmp/projeto
versao="$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml | head -1)"

# O Python e as dependências vão juntos: o apt não tem as versões exigidas.
# A construção precisa ser feita no caminho final, que fica gravado nos executáveis.
UV_PYTHON_INSTALL_DIR="$DESTINO/python" \
UV_PROJECT_ENVIRONMENT="$DESTINO/venv" \
UV_PYTHON_PREFERENCE=only-managed \
    uv sync --frozen --no-dev --no-editable --compile-bytecode

mkdir -p "$PACOTE$DESTINO" "$PACOTE/usr/bin" "$PACOTE/usr/lib/udev/rules.d" \
    "$PACOTE/usr/share/doc/gblinux" "$PACOTE/DEBIAN"
cp -a "$DESTINO/." "$PACOTE$DESTINO/"
ln -s "$DESTINO/venv/bin/gblinux" "$PACOTE/usr/bin/gblinux"
ln -s "$DESTINO/venv/bin/testar-assinatura" "$PACOTE/usr/bin/testar-assinatura"
cp LICENSE "$PACOTE/usr/share/doc/gblinux/copyright"

# Atalho e ícone: reaproveita o que o próprio programa gera.
XDG_DATA_HOME="$PACOTE/usr/share" "$DESTINO/venv/bin/python" \
    -c "from gblinux.atalho import instalar_atalho; instalar_atalho()"
rm -f "$PACOTE/usr/share/applications/mimeinfo.cache"

# Driver, nos mesmos caminhos das instruções manuais.
mkdir -p /tmp/driver
curl -fsSL -o /tmp/driver/driver.tar.gz "$DRIVER_URL"
echo "$DRIVER_SHA256  /tmp/driver/driver.tar.gz" | sha256sum -c --quiet
tar -xzf /tmp/driver/driver.tar.gz -C /tmp/driver --no-same-owner
origem=/tmp/driver/ePass2003-Castle-20141128/x86_64
mkdir -p "$PACOTE/usr/lib/ePass2003-Linux-x64"
cp -r "$origem" "$PACOTE/usr/lib/ePass2003-Linux-x64/x86_64"
rm -rf "$PACOTE/usr/lib/ePass2003-Linux-x64/x86_64/sample"
install -m 755 "$origem/redist/libcastle.so.1.0.0" "$PACOTE/usr/lib/libcastle.so.1.0.0"
ln -s libcastle.so.1.0.0 "$PACOTE/usr/lib/libcastle.so"
cp /fonte/empacotamento/60-gblinux-epass2003.rules "$PACOTE/usr/lib/udev/rules.d/"

install -m 755 /fonte/empacotamento/debian/postinst /fonte/empacotamento/debian/postrm "$PACOTE/DEBIAN/"
sed -e "s/@VERSAO@/$versao/" -e "s/@TAMANHO@/$(du -sk --exclude=DEBIAN "$PACOTE" | cut -f1)/" \
    /fonte/empacotamento/debian/control.in >"$PACOTE/DEBIAN/control"

arquivo="/saida/gblinux_${versao}_amd64.deb"
dpkg-deb --root-owner-group -Zxz --build "$PACOTE" "$arquivo"
chown "$DONO" "$arquivo"
ls -lh "$arquivo"
