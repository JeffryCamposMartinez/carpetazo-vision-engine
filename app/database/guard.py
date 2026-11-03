# ruff: noqa: E501
"""Guardia compartida: las herramientas de laboratorio solo operan en SQLite o en el PostgreSQL privado (127.0.0.1:5498)."""

from sqlalchemy.engine import make_url

LAB_PORT = 5498
LAB_HOSTS = {"127.0.0.1", "localhost", "::1"}


class DatabaseNotAllowed(Exception):
    pass


def assert_lab_database(url: str) -> None:
    u = make_url(url)
    if u.get_backend_name() == "sqlite":
        return
    if u.get_backend_name() == "postgresql" and u.host in LAB_HOSTS and u.port == LAB_PORT:
        return
    raise DatabaseNotAllowed(f"Base de datos no permitida ({u.render_as_string(hide_password=True)}): este comando solo opera en el laboratorio privado (127.0.0.1:{LAB_PORT}) o en SQLite")
