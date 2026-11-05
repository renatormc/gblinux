# Instalação manual do driver do token

O comando `gblinux install-drive` faz estes passos sozinho. Siga este procedimento somente se não
puder usar o comando.

```bash
sudo apt update
sudo apt install -y pcscd libpcsclite1 libccid pcsc-tools opensc libnss3-tools


cd ~/Downloads

# 1. Baixar e extrair o pacote oficial do ePass 2003
curl -O "https://site.solutinet.com.br/drivers/Token/ePass2003/Linux/ePass2003_Castle_20141128.tar.gz"
tar -zxvf ePass2003_Castle_20141128.tar.gz

# 2. Copiar para /usr/lib no formato esperado pelo Assinador Serpro
sudo rm -rf /usr/lib/ePass2003-Linux-x64
sudo cp -rf ~/Downloads/ePass2003-Castle-20141128 /usr/lib/ePass2003-Linux-x64

# 3. Criar atalhos da biblioteca PKCS#11 (libcastle.so.1.0.0) em /usr/lib
sudo cp /usr/lib/ePass2003-Linux-x64/x86_64/redist/libcastle.so.1.0.0 /usr/lib/
sudo ln -sf /usr/lib/libcastle.so.1.0.0 /usr/lib/libcastle.so

# 4. Configurar regras USB (udev) e reiniciar o daemon pcscd
sudo sh /usr/lib/ePass2003-Linux-x64/x86_64/config/config.sh
sudo systemctl enable --now pcscd.socket
sudo systemctl restart pcscd
```

Depois disso, retire o token e conecte de novo.
