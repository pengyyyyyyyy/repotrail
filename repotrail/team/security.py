from __future__ import annotations

import hashlib
import os
import re
import secrets
import time
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet
from sqlalchemy import select

from .database import Secret, User


PASSWORDS = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(24))


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def create_user(session, username: str, name: str, password: str, admin=False) -> User:
    username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9_.@-]{3,80}", username):
        raise ValueError("用户名需要 3–80 位字母、数字或 _.@- 字符")
    if not 12 <= len(password) <= 256:
        raise ValueError("密码长度需要为 12–256 位")
    if session.scalar(select(User.id).where(User.username == username)):
        raise ValueError("用户名已存在")
    user = User(username=username, name=name.strip()[:100] or username,
                password_hash=PASSWORDS.hash(password), admin=admin)
    session.add(user)
    session.flush()
    return user


def verify_password(encoded: str, password: str) -> bool:
    try:
        return PASSWORDS.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


class RequestSizeLimit:
    def __init__(self, app, maximum=12 * 1024 * 1024):
        self.app = app
        self.maximum = maximum

    async def __call__(self, scope, receive, send):
        from fastapi import HTTPException
        from starlette.formparsers import MultiPartException
        from starlette.responses import JSONResponse
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await JSONResponse({"detail": "请求长度无效"}, 400)(scope, receive, send)
        if length > self.maximum:
            return await JSONResponse({"detail": "请求体超过 12 MB 上限"}, 413)(scope, receive, send)
        received = 0
        exceeded = False

        async def limited_receive():
            nonlocal received, exceeded
            message = await receive()
            received += len(message.get("body", b""))
            if received > self.maximum:
                exceeded = True
                if b"multipart/form-data" in headers.get(b"content-type", b""):
                    raise MultiPartException("请求体超过 12 MB 上限")
                raise HTTPException(413, "请求体超过 12 MB 上限")
            return message

        async def limited_send(message):
            if exceeded and message["type"] == "http.response.start":
                message = {**message, "status": 413}
            await send(message)

        await self.app(scope, limited_receive, limited_send)


class Vault:
    """Encrypted database secrets, with a persistent deployment-owned master key."""

    storage_name = "encrypted_database"
    require_persistence = True

    def __init__(self, database, directory: Path):
        self.database = database
        key = os.environ.get("REPOTRAIL_SECRET_KEY", "").encode()
        if not key:
            path = directory / "team.key"
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                # Another process may have just created the shared deployment key.
                for _ in range(20):
                    key = path.read_bytes()
                    if len(key) == 44:
                        break
                    time.sleep(0.05)
            else:
                key = Fernet.generate_key()
                with os.fdopen(descriptor, "wb") as output:
                    output.write(key)
        self.cipher = Fernet(key)

    def get_password(self, service, key):
        with self.database.session() as session:
            row = session.get(Secret, service + ":" + key)
            if row:
                return self.cipher.decrypt(row.ciphertext.encode()).decode()
        # Migrate an existing desktop provider credential when available.
        if service == "repotrail":
            import keyring
            try:
                value = keyring.get_password(service, key)
            except Exception:
                value = None
            if value:
                self.set_password(service, key, value)
                return value
        return None

    def set_password(self, service, key, value):
        with self.database.session.begin() as session:
            session.merge(Secret(id=service + ":" + key,
                                 ciphertext=self.cipher.encrypt(value.encode()).decode()))

    def delete_password(self, service, key):
        with self.database.session.begin() as session:
            row = session.get(Secret, service + ":" + key)
            if row:
                session.delete(row)
