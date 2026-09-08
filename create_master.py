"""Explicit, local-only bootstrap. Never called at server startup or via HTTP."""

from getpass import getpass
from pathlib import Path
import sqlite3

from auth_store import AuthStore, auth_database_path


def main():
    store = AuthStore(auth_database_path(Path(__file__).resolve().parent))
    store.initialize()
    print(f"Banco central: {store.path}")
    name = input("Nome do Master: ")
    login = input("Login do Master: ")
    password = getpass("Senha (minimo 12 caracteres): ")
    if password != getpass("Confirme a senha: "):
        raise ValueError("As senhas nao conferem.")
    user_id = store.create_first_master(name, login, password)
    print(f"Master criado. ID: {user_id}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, sqlite3.IntegrityError) as error:
        raise SystemExit(str(error))
