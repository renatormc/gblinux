from gblinux.pdf import calcular_posicao, mascarar


def test_primeira_e_segunda_posicao():
    nome = "X" * 13 + ":12345678909"
    assert len(nome) == 25
    nome = "X" * 12 + ":12345678909"
    p = calcular_posicao(nome, [])
    assert (p.x1, p.y1, p.x2) == (10, 65, 45)
    assert round(p.y2, 4) == 151.4
    assert p.nome_campo == f"{nome} - 0"

    segunda = calcular_posicao(nome, ["Y" * 28])
    assert (segunda.x1, segunda.x2) == (10, 45)
    assert round(segunda.y1, 4) == 165.8
    assert round(segunda.y2, 4) == 252.2
    assert segunda.nome_campo == f"{nome} - 1"


def test_quarta_assinatura_muda_de_coluna():
    p = calcular_posicao("A:12345678909", ["a" * 10, "b" * 10, "c" * 10])
    assert (p.x1, p.x2, p.y1) == (35, 70, 65)


def test_mascara():
    assert mascarar("FULANO DE TAL:52998224725") == "FULANO DE TAL:******24725"
