# Desenvolvimento do Assinador de PDF

Serviço local com janela que assina PDFs com certificado ICP-Brasil A3 em token, nas portas 3399 e
4499.

## Uso

```bash
uv sync
uv run gblinux                    # abre a janela e as portas
uv run gblinux --instalar-atalho  # cria o atalho no menu de aplicativos, sem root
```

| Variável | Padrão | Função |
|---|---|---|
| `ASSINADOR_PKCS11_LIB` | `/usr/lib/libcastle.so` | Biblioteca PKCS#11 do token |
| `ASSINADOR_CA_BUNDLE` | CAs do `certifi` | Arquivo de CAs para baixar os PDFs por HTTPS |
| `ASSINADOR_LIMITE_BYTES` | 104857600 | Tamanho máximo do corpo e do download |
| `ASSINADOR_TEMPO_LIMITE_S` | 600 | Tempo total de um pedido, incluindo a digitação da senha |

## Testar uma assinatura

Com o programa aberto, em outro terminal, faça as mesmas chamadas HTTP da página web:

```bash
uv run testar-assinatura arquivo.pdf                  # senha na janela do assinador
uv run testar-assinatura arquivo.pdf --cpf 12345678909
uv run testar-assinatura arquivo.pdf --enviar-senha   # digita a senha aqui e a envia no pedido
uv run testar-assinatura https://servidor/arquivo.pdf # modo fileDownload
```

O resultado é gravado em `arquivo-assinado.pdf` (ou no caminho de `--saida`) e conferido com o `pdfsig`.

## Estrutura

| Módulo (`src/gblinux/`) | Responsabilidade |
|---|---|
| `aplicacao.py`, `interface.py` | Inicialização, instância única, janela e diálogos |
| `servidor.py`, `parametros.py`, `download.py` | Contrato HTTP das duas portas |
| `servico.py` | Fila de pedidos e controle do processo filho (somente SIGTERM) |
| `worker.py` | Processo filho de cada pedido: seleção do certificado e assinatura |
| `token_pkcs11.py` | Acesso ao token; só o filho carrega a biblioteca |
| `pdf.py` | Revisões incrementais, selo e CMS |

## Testes

```bash
sudo apt install softhsm2   # ou ASSINADOR_TESTE_SOFTHSM=/caminho/libsofthsm2.so
uv run pytest
```

Os testes usam somente SoftHSM, nunca o token real. A contagem de `C_Login` usa o `pkcs11-spy` do
pacote `opensc`. Sem SoftHSM, os testes que dependem dele são ignorados. Os testes do programa
completo usam as portas 3399 e 4499, e são ignorados se elas estiverem ocupadas.

## Primeiro teste com o token real

A inicialização do driver, o casamento da chave e o mecanismo de assinatura só podem ser confirmados com o token. **Cada senha
errada consome uma tentativa.**

1. Reinsira o token e abra o programa.
2. Peça uma assinatura sem enviar a senha, por exemplo com `curl -X POST http://localhost:3399/assinarPdf -d '{"documento":"<PDF em Base64>"}'`.
3. Se a janela de senha aparecer, a inicialização do driver e a listagem funcionaram.
4. Digite a senha com cuidado. Uma assinatura válida confirma o casamento da chave e o mecanismo.
