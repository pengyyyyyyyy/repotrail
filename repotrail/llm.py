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

    def validated(self) -> "Settings":
        from dataclasses import replace

        base = self.base_url.strip().rstrip("/")
        for suffix in ("/chat/completions", "/models"):
            if base.endswith(suffix):
                base = base[:-len(suffix)]
                break
        parts = urlsplit(base)
        local_http = parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1", "::1"}
        if (not parts.hostname or (parts.scheme != "https" and not local_http)
                or parts.username or parts.password or parts.query or parts.fragment):
            raise ValueError("请填写 HTTPS API 根地址，本地服务可使用 HTTP")
        if any(char in self.key for char in "\r\n"):
            raise ValueError("API Key 中包含换行，请重新填写")
        if any(value is not None and (not math.isfinite(value) or value < 0)
               for value in (self.input_price, self.output_price)):
            raise ValueError("Token 单价需要为非负有限数值")
        return replace(self, base_url=base, key=self.key.strip(), model=self.model.strip())

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
        return settings.validated()


class ModelClient:
    """A bounded Chat Completions client with actual usage accounting."""

    def __init__(self, settings: Settings):
        if not settings.key:
            raise ValueError("尚未配置 REPOTRAIL_API_KEY，可先体验离线示例")
        self.settings = settings
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.usage_complete = True
        self.last_finish_reason = None
        self.started = time.monotonic()

    def complete(self, messages: list[dict], tools: list[dict] | None = None,
                 max_tokens: int = 2800, json_mode: bool = False) -> dict:
        remaining = 180 - (time.monotonic() - self.started)
        if self.calls >= 6 or remaining < 1:
            raise ValueError("已达到本次生成的模型调用或时间上限")
        if len(json.dumps(messages, ensure_ascii=False)) > 140_000:
            raise ValueError("本次模型上下文已达到大小上限")
        body = {"model": self.settings.model, "messages": messages,
                "temperature": 0, "max_tokens": max_tokens}
        if urlsplit(self.settings.base_url).hostname == "api.deepseek.com":
            body["reasoning_effort"] = "low"
        if json_mode:
            body["response_format"] = {"type": "json_object"}
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
            if (json_mode and exc.response.status_code in {400, 422}
                    and any(name in exc.response.text for name in ("response_format", "json_object"))):
                return self.complete(messages, tools=tools, max_tokens=max_tokens, json_mode=False)
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
            message = payload["choices"][0]["message"]
            self.last_finish_reason = payload["choices"][0].get("finish_reason")
            if not isinstance(message, dict):
                raise ValueError("Invalid message")
            return message
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
            "input_price": settings.input_price, "output_price": settings.output_price,
            "elapsed_seconds": round(time.monotonic() - self.started, 2),
            "finish_reason": self.last_finish_reason,
        }

    def stream(self, messages: list[dict], max_tokens: int = 6000):
        """Yield text deltas while accounting for the provider's final usage event."""
        if self.calls >= 6 or len(json.dumps(messages, ensure_ascii=False)) > 140_000:
            raise ValueError("本次模型调用已达到上下文或请求上限")
        self.calls += 1
        received_usage = False
        prior_prompt_tokens, prior_completion_tokens = self.prompt_tokens, self.completion_tokens
        self.last_finish_reason = None
        body = {"model": self.settings.model, "messages": messages, "temperature": 0,
                "max_tokens": max_tokens, "stream": True, "stream_options": {"include_usage": True}}
        if urlsplit(self.settings.base_url).hostname == "api.deepseek.com":
            body["reasoning_effort"] = "low"
        try:
            with httpx.stream("POST", self.settings.base_url + "/chat/completions", json=body,
                              headers={"Authorization": f"Bearer {self.settings.key}"},
                              timeout=httpx.Timeout(60, connect=15), follow_redirects=False) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if time.monotonic() - self.started > 180:
                        raise ValueError("本次回答达到时间上限，请缩小问题范围后重试")
                    if not line.startswith("data:"):
                        continue
                    value = line[5:].strip()
                    if value == "[DONE]":
                        break
                    payload = json.loads(value)
                    usage = payload.get("usage")
                    if usage and "prompt_tokens" in usage and "completion_tokens" in usage:
                        self.prompt_tokens = prior_prompt_tokens + int(usage["prompt_tokens"])
                        self.completion_tokens = prior_completion_tokens + int(usage["completion_tokens"])
                        received_usage = True
                    choices = payload.get("choices") or []
                    if choices and choices[0].get("finish_reason"):
                        self.last_finish_reason = choices[0]["finish_reason"]
                    content = choices[0].get("delta", {}).get("content") if choices else None
                    if isinstance(content, str) and content:
                        yield content
        except httpx.HTTPStatusError as exc:
            raise ValueError(f"模型流式接口返回 HTTP {exc.response.status_code}") from None
        except httpx.HTTPError:
            raise ValueError("模型流式连接失败或超时，请重试") from None
        except (TypeError, KeyError, IndexError, json.JSONDecodeError):
            raise ValueError("模型流式响应格式无法识别") from None
        finally:
            self.usage_complete = self.usage_complete and received_usage


def fetch_models(settings: Settings) -> list[str]:
    try:
        response = httpx.get(
            settings.base_url + "/models",
            headers={"Authorization": f"Bearer {settings.key}"},
            timeout=20, follow_redirects=False,
        )
        response.raise_for_status()
        data = response.json().get("data")
        if not isinstance(data, list):
            raise ValueError("模型列表格式无法识别，可以手动填写模型 ID")
        return sorted({item["id"] for item in data if isinstance(item, dict)
                       and isinstance(item.get("id"), str) and len(item["id"]) <= 300})[:500]
    except httpx.HTTPStatusError as exc:
        raise ValueError(f"获取模型失败，HTTP {exc.response.status_code}，可检查配置或手动填写模型 ID") from None
    except httpx.HTTPError:
        raise ValueError("获取模型超时或连接失败，请检查服务地址") from None
    except (TypeError, AttributeError):
        raise ValueError("模型列表格式无法识别，可以手动填写模型 ID") from None


def test_connection(settings: Settings) -> dict:
    client = ModelClient(settings)
    tool = {"type": "function", "function": {
        "name": "connection_check", "description": "Confirm the API connection",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}}
    message = client.complete([
        {"role": "user", "content": "Call the connection_check tool once with an empty object."}
    ], tools=[tool], max_tokens=128)
    calls = message.get("tool_calls") or []
    if not any(call.get("function", {}).get("name") == "connection_check" for call in calls):
        raise ValueError("接口已响应，模型未返回工具调用，请选择支持工具调用的模型")
    return {"status": "ok", "message": "连接成功，工具调用可用", "metrics": client.metrics()}


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

