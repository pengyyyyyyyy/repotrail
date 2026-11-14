import httpx
import pytest

from repotrail.llm import ModelClient, Settings, parse_object


def test_json_fences_and_invalid_structures():
    assert parse_object('```json\n{"ok": true}\n```') == {"ok": True}
    for content in ["[]", "invalid", "null"]:
        with pytest.raises(ValueError):
            parse_object(content)


def test_missing_key_and_invalid_provider(monkeypatch):
    with pytest.raises(ValueError, match="REPOTRAIL_API_KEY"):
        ModelClient(Settings(key=""))
    monkeypatch.setenv("REPOTRAIL_BASE_URL", "https://name:secret@example.com/v1")
    with pytest.raises(ValueError, match="HTTPS"):
        Settings.from_env()


def test_usage_and_configured_prices(monkeypatch):
    def post(url, **kwargs):
        assert url == "https://example.com/v1/chat/completions"
        assert kwargs["headers"]["Authorization"] == "Bearer test-key"
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "choices": [{"message": {"content": "{}"}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 500}})
    monkeypatch.setattr(httpx, "post", post)
    client = ModelClient(Settings("test-key", "https://example.com/v1", "test", 2, 8))
    client.complete([{"role": "user", "content": "test"}])
    assert client.metrics()["estimated_cost"] == 0.006
    assert client.metrics()["calls"] == 1


def test_provider_missing_usage_does_not_claim_zero_cost(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda url, **kwargs: httpx.Response(200,
        request=httpx.Request("POST", url), json={"choices": [{"message": {"content": "{}"}}]}))
    client = ModelClient(Settings("test-key", input_price=2, output_price=8))
    client.complete([])
    assert not client.metrics()["usage_complete"]
    assert client.metrics()["estimated_cost"] is None


def test_provider_error_is_sanitized(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda url, **kwargs: httpx.Response(401,
        request=httpx.Request("POST", url), text="secret-provider-body"))
    client = ModelClient(Settings("test-key"))
    with pytest.raises(ValueError) as error:
        client.complete([])
    assert "401" in str(error.value)
    assert "secret-provider-body" not in str(error.value)
    assert "test-key" not in repr(client.settings)


def test_call_limit():
    client = ModelClient(Settings("test-key"))
    client.calls = 6
    with pytest.raises(ValueError, match="上限"):
        client.complete([])

