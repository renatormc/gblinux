# Assinador de PDF com token A3

Programa para Linux que assina documentos PDF com certificado digital ICP-Brasil A3 em token USB.
Ele fica aberto no computador e atende os pedidos de assinatura feitos pela página web do sistema.

Estas instruções são para Ubuntu 24.04 ou mais novo e derivados, como Linux Mint e Pop!_OS.

## Instalação pelo pacote .deb (recomendada)

O pacote instala tudo de uma vez: driver do token, programa e entrada no menu de aplicativos. Não é
preciso ter o Python. Baixe o arquivo `.deb` da
[última versão](https://github.com/renatormc/gblinux/releases/latest) e, na pasta onde ele está, rode:

```bash
sudo apt install ./gblinux_*_amd64.deb
```

Depois, retire o token, conecte de novo e abra o **Assinador de PDF** pelo menu. Veja
[Como usar](#como-usar).

Para atualizar, repita o comando com o arquivo novo. Para desinstalar, use
`sudo apt remove gblinux`.

Se você já instalou o assinador pelo pipx, antes rode `gblinux --remover-atalho` e
`pipx uninstall gblinux`.

## Instalação manual

Siga estes passos somente se não for usar o pacote `.deb`. É preciso ter o Python 3.11 ou mais novo;
para conferir a versão, use `python3 --version`.

## 1. Instale o driver do token

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

## 2. Instale o pipx

```bash
sudo apt update
sudo apt install pipx libxcb-cursor0
pipx ensurepath
```

Depois disso, **feche o terminal e abra outro**.

## 3. Instale o assinador

```bash
pipx install gblinux
```

## 4. Crie a entrada no menu de aplicativos

```bash
gblinux --instalar-atalho
```

O **Assinador de PDF** vai aparecer no menu de aplicativos. Se não aparecer logo, encerre a sessão e
entre de novo.

## Como usar

1. Conecte o token.
2. Abra o **Assinador de PDF** pelo menu.
3. Deixe a janela aberta e peça a assinatura na página web.
4. Se a janela de senha aparecer, digite a senha do token.

Para encerrar, clique em **Fechar**.

**Cuidado com a senha:** cada senha errada gasta uma tentativa, e o token é bloqueado quando as
tentativas acabam.

## Como testar

Com o assinador aberto, rode em um terminal:

```bash
testar-assinatura arquivo.pdf
```

O arquivo assinado é salvo na mesma pasta, como `arquivo-assinado.pdf`.

## Problemas comuns

| O que acontece | O que fazer |
|---|---|
| `comando não encontrado` depois de instalar | Feche o terminal e abra outro |
| `Token não encontrado` | Confira se o token está conectado e se o driver foi instalado |
| A janela de senha não aparece, ou `Assinador não respondeu` | Feche o assinador, retire o token, conecte de novo e abra o assinador |
| `O assinador já está aberto` | Procure a janela que já está aberta |
| Aviso de porta ocupada | Feche o assinador antigo ou o outro programa que usa as portas 3399 e 4499 |

## Atualizar a instalação manual

```bash
pipx upgrade gblinux
```

## Desinstalar a instalação manual

```bash
gblinux --remover-atalho
pipx uninstall gblinux
```

## Licença

Este programa é software livre, distribuído sob a licença GPL-3.0 ou mais recente. Veja o arquivo
`LICENSE`.
