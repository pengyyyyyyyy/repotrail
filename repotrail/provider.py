"""Active API configuration with OS credential storage and environment fallback."""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from dataclasses import replace
from pathlib import Path

import keyring

from .export import write_json
from .llm import Settings


class ProviderStore:
    def __init__(self, directory: Path, vault=keyring):
        self.path = directory / "provider.json"
        self.vault = vault
        self.lock = threading.RLock()
        self.active: Settings | None = None
        self.name = "兼容 API"
        self.storage = "environment"

    @staticmethod
    def credential_prefix(base_url: str) -> str:
        return hashlib.sha256(base_url.encode()).hexdigest() + ":"

    def metadata(self) -> dict:
        try:
            if self.path.stat().st_size > 64_000:
                return {}
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def current(self) -> Settings:
        with self.lock:
            if self.active is not None:
                return self.active
            try:
                environment = Settings.from_env()
            except ValueError:
                environment = Settings(key="")
            data = self.metadata()
            if not data:
                return environment
            try:
                settings = replace(environment, key="", base_url=data["base_url"],
                                   model=data["model"]).validated()
            except (ValueError, KeyError, TypeError, AttributeError):
                return environment
            credential = data.get("credential_id", "")
            if isinstance(credential, str) and credential.startswith(self.credential_prefix(settings.base_url)):
                try:
                    settings = replace(settings, key=self.vault.get_password("repotrail", credential) or "")
                except Exception:
                    pass
            if not settings.key and settings.base_url == environment.base_url:
                settings = replace(settings, key=environment.key)
            self.name = str(data.get("name", "兼容 API"))[:80]
            self.storage = "keyring" if settings.key and credential else "session"
            self.active = settings
            return settings

    def public(self) -> dict:
        with self.lock:
            settings = self.current()
            return {"name": self.name, "base_url": settings.base_url,
                    "model": settings.model, "has_key": bool(settings.key),
                    "configured": bool(settings.key and settings.model), "storage": self.storage}

    def resolve(self, base_url: str, key: str, model: str, require_model=True) -> Settings:
        with self.lock:
            previous = self.current()
            settings = replace(previous, key=key, base_url=base_url, model=model).validated()
            if not settings.key and settings.base_url == previous.base_url:
                settings = replace(settings, key=previous.key)
            if not settings.key:
                raise ValueError("请填写 API Key")
            if require_model and not settings.model:
                raise ValueError("请选择或填写模型 ID")
            return settings

    def save(self, name: str, settings: Settings) -> dict:
        with self.lock:
            credential = self.credential_prefix(settings.base_url) + uuid.uuid4().hex
            storage = "keyring"
            try:
                self.vault.set_password("repotrail", credential, settings.key)
            except Exception:
                credential = ""
                storage = "session"
            old = self.metadata()
            try:
                write_json(self.path, {"name": name.strip() or "兼容 API",
                                      "base_url": settings.base_url, "model": settings.model,
                                      "credential_id": credential})
            except OSError:
                if credential:
                    self._remove_credential(credential)
                raise ValueError("配置保存失败，请检查本地目录权限") from None
            self.active = settings
            self.name = name.strip() or "兼容 API"
            self.storage = storage
            old_credential = old.get("credential_id")
            if (isinstance(old_credential, str) and old_credential
                    and isinstance(old.get("base_url"), str)
                    and old_credential.startswith(self.credential_prefix(old["base_url"]))):
                self._remove_credential(old_credential)
            return self.public()

    def configure(self, name: str, base_url: str, key: str, model: str) -> dict:
        with self.lock:
            settings = self.resolve(base_url, key, model)
            return self.save(name, settings)

    def _remove_credential(self, credential: str) -> None:
        try:
            self.vault.delete_password("repotrail", credential)
        except Exception:
            pass
