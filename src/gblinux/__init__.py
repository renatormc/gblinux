"""Assinador de PDF com token A3 para Linux."""


def main() -> int:
    # Importação tardia: o processo filho usa este pacote e não deve carregar o Qt.
    from .aplicacao import main as principal

    return principal()
