import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from repotrail.llm import Settings
from repotrail.provider import ProviderStore
from repotrail.server import create_app


class MemoryVault:
    def __init__(self):
        self.values = {}

    def set_password(self, service, account, value):
        self.values[service, account] = value

    def get_password(self, service, account):
        return self.values.get((service, account))

    def delete_password(self, service, account):
        self.values.pop((service, account), None)


@pytest.fixture
def provider(tmp_path, monkeypatch):
    monkeypatch.delenv("REPOTRAIL_API_KEY", raising=False)
    monkeypatch.delenv("REPOTRAIL_BASE_URL", raising=False)
    store = ProviderStore(tmp_path, MemoryVault())
    with TestClient(create_app(tmp_path, provider_store=store)) as client:
        yield client, store


def configuration(**overrides):
    return {"name": "Test provider", "base_url": "https://example.test/v1",
            "api_key": "fixture-secret-key", "model": "fixture-model", **overrides}


def test_settings_save_reload_and_never_return_secret(provider):
    client, store = provider
    response = client.post("/api/provider", json=configuration())
    assert response.status_code == 200
    assert response.json()["configured"]
    assert response.json()["storage"] == "keyring"
    assert "fixture-secret-key" not in response.text
    assert "fixture-secret-key" not in store.path.read_text(encoding="utf-8")
    assert "fixture-secret-key" not in client.get("/api/provider").text
    assert client.get("/api/health").json()["model_ready"]
    restarted = ProviderStore(store.path.parent, store.vault)
    assert restarted.current().key == "fixture-secret-key"
    assert restarted.current().model == "fixture-model"


def test_empty_key_preserves_same_endpoint_and_rotation_cleans_old_credential(provider):
    client, store = provider
    client.post("/api/provider", json=configuration())
    response = client.post("/api/provider", json=configuration(api_key="", model="new-model"))
    assert response.status_code == 200
    assert store.current().key == "fixture-secret-key"
    assert store.current().model == "new-model"
    assert len(store.vault.values) == 1


def test_new_endpoint_requires_its_own_key(provider):
    client, store = provider
    client.post("/api/provider", json=configuration())
    response = client.post("/api/provider", json=configuration(api_key="", base_url="https://other.test/v1"))
    assert response.status_code == 400
    assert store.current().base_url == "https://example.test/v1"


def test_copied_credential_id_does_not_unlock_another_endpoint(provider):
    client, store = provider
    client.post("/api/provider", json=configuration())
    metadata = json.loads(store.path.read_text(encoding="utf-8"))
    metadata["base_url"] = "https://other.test/v1"
    store.path.write_text(json.dumps(metadata), encoding="utf-8")
    assert ProviderStore(store.path.parent, store.vault).current().key == ""


def test_vault_unavailable_uses_current_service_session(tmp_path, monkeypatch):
    vault = MemoryVault()
    monkeypatch.setattr(vault, "set_password", lambda *args: (_ for _ in ()).throw(RuntimeError("unavailable")))
    store = ProviderStore(tmp_path, vault)
    result = store.configure("local", "https://example.test/v1", "session-key", "model")
    assert result["storage"] == "session"
    assert store.current().key == "session-key"
    assert "session-key" not in store.path.read_text(encoding="utf-8")


def test_file_save_failure_keeps_active_config_and_cleans_new_secret(provider, monkeypatch):
    client, store = provider
    client.post("/api/provider", json=configuration())
    def fail_write(*args):
        raise OSError("write failure")
    monkeypatch.setattr("repotrail.provider.write_json", fail_write)
    response = client.post("/api/provider", json=configuration(api_key="replacement-key"))
    assert response.status_code == 400
    assert store.current().key == "fixture-secret-key"
    assert list(store.vault.values.values()) == ["fixture-secret-key"]


def test_models_are_discovered_from_custom_path(provider, monkeypatch):
    client, _ = provider
    def get(url, **kwargs):
        assert url == "https://example.test/custom/v2/models"
        assert kwargs["headers"]["Authorization"] == "Bearer fixture-secret-key"
        return httpx.Response(200, request=httpx.Request("GET", url), json={"data": [
            {"id": "z-model"}, {"id": "a-model"}, {"id": "a-model"}, {"other": "invalid"}]})
    monkeypatch.setattr(httpx, "get", get)
    response = client.post("/api/provider/models", json=configuration(base_url="https://example.test/custom/v2", model=""))
    assert response.json()["models"] == ["a-model", "z-model"]


def test_connection_checks_real_tool_call_contract(provider, monkeypatch):
    client, _ = provider
    def post(url, **kwargs):
        assert kwargs["json"]["max_tokens"] == 128
        assert kwargs["json"]["tools"][0]["function"]["name"] == "connection_check"
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "choices": [{"message": {"role": "assistant", "tool_calls": [{
                "function": {"name": "connection_check", "arguments": "{}"}}]}}]})
    monkeypatch.setattr(httpx, "post", post)
    response = client.post("/api/provider/test", json=configuration())
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_text_only_model_response_is_not_reported_as_tool_success(provider, monkeypatch):
    client, _ = provider
    monkeypatch.setattr(httpx, "post", lambda url, **kwargs: httpx.Response(200,
        request=httpx.Request("POST", url), json={"choices": [{"message": {"content": "OK"}}]}))
    assert client.post("/api/provider/test", json=configuration()).status_code == 400


def test_saved_configuration_is_used_by_generation(provider, monkeypatch):
    client, _ = provider
    client.post("/api/provider", json=configuration())
    captured = []
    def generate(index, flow, model_client):
        captured.append(model_client.settings)
        return {"test": True}
    monkeypatch.setattr("repotrail.server.generate", generate)
    root = Path(__file__).parents[1] / "repotrail" / "sample"
    repository = client.post("/api/analyze", json={"source": str(root)}).json()
    job = client.post("/api/generate", json={"repository_id": repository["repository_id"],
                                           "flow_id": repository["flows"][0]["id"]}).json()
    for _ in range(50):
        if client.get("/api/jobs/" + job["job_id"]).json()["status"] == "complete":
            break
        time.sleep(.01)
    assert captured[0].model == "fixture-model"
    assert captured[0].key == "fixture-secret-key"


@pytest.mark.parametrize("base", ["https://example.test/custom/chat/completions/",
                                  "https://example.test/custom/models"])
def test_full_endpoint_is_normalized(base):
    assert Settings(key="key", base_url=base).validated().base_url == "https://example.test/custom"


def test_local_http_and_invalid_remote_http():
    assert Settings(key="key", base_url="http://127.0.0.1:8001/v1").validated().base_url.startswith("http:")
    with pytest.raises(ValueError):
        Settings(key="key", base_url="http://remote.test/v1").validated()


def test_invalid_environment_can_be_reconfigured(provider, monkeypatch):
    client, _ = provider
    monkeypatch.setenv("REPOTRAIL_BASE_URL", "invalid")
    assert client.post("/api/provider", json=configuration()).status_code == 200


def test_removed_copy_is_absent_from_frontend():
    static = Path(__file__).parents[1] / "repotrail" / "static"
    text = (static / "index.html").read_text(encoding="utf-8") + (static / "app.js").read_text(encoding="utf-8")
    for removed in ["证据与待核实部分", "说明语义需要人工核对", "静态连接可匹配", "连接待核实", "解释语义待人工核对"]:
        assert removed not in text


def test_invalid_input_response_omits_submitted_secret(provider):
    client, _ = provider
    oversized_key = "private-key-" + "x" * 9000
    response = client.post("/api/provider", json=configuration(api_key=oversized_key))
    assert response.status_code == 422
    assert "private-key-" not in response.text
    assert "input" not in response.json()["detail"][0]
