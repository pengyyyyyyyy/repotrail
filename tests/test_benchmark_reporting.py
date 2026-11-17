import json
from types import SimpleNamespace

import pytest

from benchmarks import run


def test_failed_baseline_is_reported_and_system_generation_still_runs(tmp_path, monkeypatch):
    manifest = {"review_status": "pending", "repositories": [{
        "name": "fixture", "url": "https://example.org/fixture", "revision": "abc",
        "entry": "app.py::main", "title": "Fixture", "questions": [{"symbol": "app.py::main"}]}]}
    (tmp_path / "cases.json").write_text(json.dumps(manifest), "utf-8")
    monkeypatch.setattr(run, "__file__", str(tmp_path / "run.py"))
    monkeypatch.setattr(run, "build_index", lambda path: SimpleNamespace(revision="abc", digest="digest"))
    monkeypatch.setattr(run, "verify_case", lambda case, index: [])
    settings = SimpleNamespace(model="fixture-model", base_url="https://example.org/v1")
    monkeypatch.setattr(run, "ProviderStore", lambda path: SimpleNamespace(current=lambda: settings))
    monkeypatch.setattr(run, "ModelClient", lambda settings: SimpleNamespace(metrics=lambda: {"calls": 1}))
    attempted = []

    def generate(index, flow, client, baseline=False):
        attempted.append(baseline)
        if baseline:
            raise ValueError("Fixture output reached the token limit")
        return {"steps": [{"symbol_id": "app.py::main"}], "validation": {}, "metrics": {"calls": 1}}

    monkeypatch.setattr(run, "generate", generate)
    output = tmp_path / "results"
    with pytest.raises(SystemExit) as error:
        run.main(["compare", "--output", str(output)])
    assert error.value.code == 1
    assert attempted == [True, False]
    report = json.loads((output / "compare.json").read_text("utf-8"))
    comparison = report["results"][0]["comparison"]
    assert comparison["baseline"]["status"] == "failed"
    assert comparison["baseline"]["mechanical_symbol_coverage"] is None
    assert comparison["system"]["status"] == "complete"
    assert comparison["system"]["mechanical_symbol_coverage"] == 1
    assert report["failed_generations"] == 1
