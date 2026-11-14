from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx


@dataclass(frozen=True)
class Settings:
    key: str = field(repr=False)
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    input_price: float | None = None
    output_price: float | None = None
    currency: str = "CNY"

    @classmethod
    def from_env(cls) -> "Settings":
        def price(name: str) -> float | None:
            value = os.environ.get(name, "").strip()
            if not value:
                return None
            number = float(value)
            if not math.isfinite(number) or number < 0:
                raise ValueError(f"{name} 需要为非负数")
            return number

        settings = cls(
            key=os.environ.get("REPOTRAIL_API_KEY", ""),
            base_url=os.environ.get("REPOTRAIL_BASE_URL", cls.base_url).rstrip("/"),
            model=os.environ.get("REPOTRAIL_MODEL", cls.model),
            input_price=price("REPOTRAIL_INPUT_PRICE"),
            output_price=price("REPOTRAIL_OUTPUT_PRICE"),
            currency=os.environ.get("REPOTRAIL_CURRENCY", "CNY"),
        )
        parts = urlsplit(settings.base_url)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            raise ValueError("模型服务地址需要是无内嵌凭证的 HTTPS 地址")
        return settings


class ModelClient:
    """A bounded, non-streaming Chat Completions client with actual usage accounting."""

    def __init__(self, settings: Settings):
        if not settings.key:
            raise ValueError("尚未配置 REPOTRAIL_API_KEY，可先体验离线示例")
        self.settings = settings
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.usage_complete = True
        self.started = time.monotonic()

    def complete(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        remaining = 180 - (time.monotonic() - self.started)
        if self.calls >= 6 or remaining < 1:
            raise ValueError("已达到本次生成的模型调用或时间上限")
        if len(json.dumps(messages, ensure_ascii=False)) > 140_000:
            raise ValueError("本次模型上下文已达到大小上限")
        body = {"model": self.settings.model, "messages": messages,
                "temperature": 0, "max_tokens": 2800}
        if tools:
            body["tools"] = tools
        self.calls += 1
        try:
            response = httpx.post(
                self.settings.base_url + "/chat/completions", json=body,
                headers={"Authorization": f"Bearer {self.settings.key}"},
                timeout=httpx.Timeout(min(60, remaining), connect=min(15, remaining)), follow_redirects=False,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ValueError(f"模型服务返回 HTTP {exc.response.status_code}，请检查接口与模型配置") from None
        except httpx.HTTPError:
            raise ValueError("模型请求失败或超时，请检查服务地址与网络") from None
        try:
            payload = response.json()
            usage = payload.get("usage") or {}
            if "prompt_tokens" not in usage or "completion_tokens" not in usage:
                self.usage_complete = False
            self.prompt_tokens += int(usage.get("prompt_tokens", 0))
            self.completion_tokens += int(usage.get("completion_tokens", 0))
            return payload["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise ValueError("模型响应格式不符合 Chat Completions 接口") from None

    def metrics(self) -> dict:
        settings = self.settings
        cost = None
        if self.usage_complete and settings.input_price is not None and settings.output_price is not None:
            cost = (self.prompt_tokens * settings.input_price
                    + self.completion_tokens * settings.output_price) / 1_000_000
        return {
            "model": settings.model, "calls": self.calls,
            "prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens,
            "usage_complete": self.usage_complete,
            "estimated_cost": cost, "currency": settings.currency,
            "elapsed_seconds": round(time.monotonic() - self.started, 2),
        }


def parse_object(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
    try:
        result = json.loads(text)
    except (ValueError, TypeError):
        raise ValueError("模型未返回有效 JSON，无法生成可校验导览") from None
    if not isinstance(result, dict):
        raise ValueError("模型需要返回 JSON 对象")
    return result

