# Assinador de PDF com token A3

Programa para Linux que assina documentos PDF com certificado digital ICP-Brasil A3 em token USB.
Ele fica aberto no computador e atende os pedidos de assinatura feitos pela página web do sistema.

Estas instruções são para Ubuntu 24.04 ou mais novo e derivados, como Linux Mint e Pop!_OS.

## Instalação

Copie e cole este comando no terminal, sem `sudo`. Ele instala o pipx, o assinador, o driver do token e
a entrada no menu de aplicativos, e pede a senha do `sudo` quando precisar.

```bash
curl -fsSL https://raw.githubusercontent.com/renatormc/gblinux/master/install.sh | bash
```

Depois, retire o token, conecte de novo e abra o **Assinador de PDF** pelo menu. Veja
[Como usar](#como-usar).

Se o terminal disser que o `curl` não foi encontrado, instale-o com `sudo apt install curl`.

## Instalação por etapas

Siga estes passos somente se não quiser usar o comando acima. É preciso ter o Python 3.11 ou mais novo;
para conferir a versão, use `python3 --version`.

## 1. Instale o pipx

```bash
sudo apt update
sudo apt install pipx libxcb-cursor0
pipx ensurepath
```

Depois disso, **feche o terminal e abra outro**.

## 2. Instale o assinador

```bash
pipx install gblinux
```

## 3. Instale o driver do token

```bash
gblinux install-drive
```

O comando pede a senha do `sudo`. Depois disso, retire o token e conecte de novo.

Se preferir instalar o driver à mão, veja o
[procedimento manual](https://github.com/renatormc/gblinux/blob/master/DRIVER.md).

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

## Atualizar

```bash
pipx upgrade gblinux
```

## Desinstalar

```bash
gblinux --remover-atalho
pipx uninstall gblinux
```

## Licença

Este programa é software livre, distribuído sob a licença GPL-3.0 ou mais recente. Veja o arquivo
`LICENSE`.
