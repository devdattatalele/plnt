"""Auth — username/password + session cookies.

Backing store: ~/.plnt/auth.toml. Schema:

    [users.<username>]
    password_hash = "scrypt$<n>$<r>$<p>$<salt_hex>$<hash_hex>"

Sessions: in-memory dict, 24-hour TTL. Restart wipes them — users log in again.

scrypt rather than argon2-cffi keeps the dep surface clean (stdlib only). For
a local-first single-user box this is fine.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from plnt.config import paths

SESSION_TTL_SECONDS = 60 * 60 * 24  # 24h

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, hash_hex = encoded.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    try:
        dk = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p), dklen=len(hash_hex) // 2,
        )
    except (ValueError, MemoryError):
        return False
    return hmac.compare_digest(dk, bytes.fromhex(hash_hex))


# ---------------------------------------------------------------- store


class AuthStore:
    """File-backed map of username -> password_hash."""

    def __init__(self, path: Path | None = None):
        self.path = path or (paths().home / "auth.toml")
        self._lock = threading.Lock()

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        with open(self.path, "rb") as f:
            data = tomllib.load(f)
        return data.get("users", {})

    def _write(self, users: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# plnt auth store — do not edit by hand", ""]
        for username, fields in users.items():
            lines.append(f"[users.{username}]")
            for k, v in fields.items():
                lines.append(f'{k} = "{v}"')
            lines.append("")
        # Restrict to owner-readable (0600).
        tmp = self.path.with_suffix(".toml.tmp")
        tmp.write_text("\n".join(lines), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(self.path)

    def list_users(self) -> list[str]:
        with self._lock:
            return sorted(self._load().keys())

    def set_password(self, username: str, password: str) -> None:
        if not username or not username.replace("_", "").replace("-", "").isalnum():
            raise ValueError(f"invalid username {username!r}")
        if len(password) < 1:
            raise ValueError("empty password")
        with self._lock:
            users = self._load()
            users[username] = {"password_hash": hash_password(password)}
            self._write(users)

    def verify(self, username: str, password: str) -> bool:
        with self._lock:
            users = self._load()
            entry = users.get(username)
        if not entry:
            return False
        return verify_password(password, entry.get("password_hash", ""))

    def has_any_user(self) -> bool:
        with self._lock:
            return bool(self._load())

    def bootstrap_if_empty(self) -> tuple[str, str] | None:
        """If no users exist, create admin with a random password.

        Returns (username, plaintext_password) so the caller can print it once.
        """
        with self._lock:
            users = self._load()
            if users:
                return None
            password = secrets.token_urlsafe(9)
            users["admin"] = {"password_hash": hash_password(password)}
            self._write(users)
            return ("admin", password)


# ---------------------------------------------------------------- sessions


@dataclass
class Session:
    token: str
    username: str
    expires_at: float


class Sessions:
    """In-memory session store. Single process, single host."""

    def __init__(self) -> None:
        self._items: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, username: str) -> Session:
        token = secrets.token_urlsafe(32)
        sess = Session(token=token, username=username, expires_at=time.time() + SESSION_TTL_SECONDS)
        with self._lock:
            self._items[token] = sess
        return sess

    def get(self, token: str) -> Session | None:
        if not token:
            return None
        with self._lock:
            sess = self._items.get(token)
            if sess is None:
                return None
            if sess.expires_at < time.time():
                self._items.pop(token, None)
                return None
            return sess

    def destroy(self, token: str) -> None:
        with self._lock:
            self._items.pop(token, None)
