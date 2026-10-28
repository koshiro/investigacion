"""Autenticación de un solo usuario: argon2 + TOTP (sin reutilizar códigos) + sesión de 12 h guardada con hash."""

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field

import psycopg
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

SESION_HORAS = 12
MAX_FALLOS = 5
BLOQUEO_S = 900
_ph = PasswordHasher()
# Hash de un valor al azar: verificar contra él cuando el usuario no existe iguala el tiempo de respuesta.
_FALSO = _ph.hash(secrets.token_hex(16))


def hashear(password: str) -> str:
    return _ph.hash(password)


def password_ok(hash_guardado: str | None, password: str) -> bool:
    try:
        return _ph.verify(hash_guardado or _FALSO, password) and hash_guardado is not None
    except VerificationError, InvalidHashError:
        return False


def paso_totp(secreto: str, codigo: str, ultimo_paso: int, ahora: float | None = None) -> int | None:
    """Devuelve el paso (ventana de 30 s) si el código es válido (±1 ventana) y no se usó antes; si no, None."""
    codigo = codigo.strip().replace(" ", "")
    if not (codigo.isdigit() and len(codigo) == 6):
        return None
    actual = int((ahora if ahora is not None else time.time()) // 30)
    totp = pyotp.TOTP(secreto)
    for paso in (actual, actual - 1, actual + 1):
        if paso > ultimo_paso and hmac.compare_digest(totp.at(paso * 30), codigo):
            return paso
    return None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def crear_sesion(conn: psycopg.Connection, user_id) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO web_session (created_by, user_id, token_hash, expira_at) "
        "VALUES ('service:web', %s, %s, now() + make_interval(hours => %s))",
        (user_id, hash_token(token), SESION_HORAS),
    )
    conn.execute("DELETE FROM web_session WHERE expira_at < now()")
    return token


def usuario_de_sesion(conn: psycopg.Connection, token: str | None):
    if not token:
        return None
    fila = conn.execute(
        "SELECT u.id, u.nombre FROM web_session s JOIN app_user u ON u.id = s.user_id "
        "WHERE s.token_hash = %s AND s.expira_at > now()",
        (hash_token(token),),
    ).fetchone()
    return fila


def cerrar_sesion(conn: psycopg.Connection, token: str | None) -> None:
    if token:
        conn.execute("DELETE FROM web_session WHERE token_hash = %s", (hash_token(token),))


def token_csrf(clave: str, token_sesion: str) -> str:
    return hmac.new(clave.encode(), hash_token(token_sesion).encode(), hashlib.sha256).hexdigest()[:40]


@dataclass
class Limitador:
    """Bloquea tras MAX_FALLOS intentos fallidos seguidos (por clave: usuario o IP) durante BLOQUEO_S."""

    fallos: dict[str, tuple[int, float]] = field(default_factory=dict)
    reloj: callable = time.monotonic  # type: ignore[type-arg]

    def bloqueado(self, clave: str) -> bool:
        n, hasta = self.fallos.get(clave, (0, 0.0))
        return n >= MAX_FALLOS and self.reloj() < hasta

    def fallo(self, clave: str) -> None:
        n, hasta = self.fallos.get(clave, (0, 0.0))
        if n >= MAX_FALLOS and self.reloj() >= hasta:
            n = 0
        self.fallos[clave] = (n + 1, self.reloj() + BLOQUEO_S)

    def exito(self, clave: str) -> None:
        self.fallos.pop(clave, None)
