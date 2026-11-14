import json
from pathlib import Path

from repotrail.engine import deterministic_demo, generate, tool_result, validate_trail
from repotrail.export import codetour, markdown, write_json
from repotrail.index import build_index
from repotrail.models import DraftTrail


SAMPLE = Path(__file__).parents[1] / "repotrail" / "sample"


def draft(ids):
    return DraftTrail.model_validate({"title": "test", "summary": "test summary",
        "steps": [{"symbol_id": item, "title": item, "explanation": "Inspect source"} for item in ids],
        "gaps": []})


def test_demo_has_actual_cross_file_evidence():
    index = build_index(SAMPLE)
    result = deterministic_demo(index, index.flows[0])
    assert result["mode"] == "offline_demo"
    assert result["metrics"]["calls"] == 0
    assert [step["symbol_id"] for step in result["steps"]] == [
        "cli.py::main", "service.py::create_message", "formatting.py::normalize_name"]
    assert result["validation"]["static_connections"] == 2
    assert result["validation"]["prose_review"] == "requires_human_review"


def test_fabricated_symbols_are_reported_and_not_located():
    index = build_index(SAMPLE)
    result = validate_trail(index, index.flows[0], draft(["cli.py::main", "fake.py::invented"]))
    assert result["validation"]["located_steps"] == 1
    assert result["validation"]["requested_steps"] == 2
    assert any("未知符号" in item for item in result["gaps"])


def test_missing_connection_is_explicit():
    index = build_index(SAMPLE)
    result = validate_trail(index, index.flows[0], draft(["cli.py::main", "formatting.py::normalize_name"]))
    assert result["steps"][1]["connection"]["status"] == "unresolved"
    assert result["validation"]["unresolved_connections"] == 1


def test_sibling_calls_can_connect_to_an_earlier_step(tmp_path):
    (tmp_path / "main.py").write_text("def main():\n    first()\n    second()\ndef first():\n    return 1\ndef second():\n    return 2\n")
    index = build_index(tmp_path)
    result = validate_trail(index, index.flows[0], draft(["main.py::main", "main.py::first", "main.py::second"]))
    assert result["steps"][2]["connection"]["source"] == "main.py::main"
    assert result["validation"]["static_connections"] == 2


def test_wrong_entry_and_duplicate_steps_are_reported():
    index = build_index(SAMPLE)
    result = validate_trail(index, index.flows[0], draft(["service.py::create_message", "service.py::create_message"]))
    assert len(result["validation_issues"]) == 2
    assert len(result["steps"]) == 1


def test_tools_are_bounded_and_do_not_read_arbitrary_files():
    index = build_index(SAMPLE)
    result = tool_result(index, "read_symbols", {"symbol_ids": ["../secret::x"]})
    assert "error" in result["symbols"][0]
    assert "error" in tool_result(index, "search_code", {"query": "x"})
    assert "error" in tool_result(index, "execute", {"command": "anything"})
    assert tool_result(index, "search_code", {"query": "normalize_name"})["matches"]


class FakeClient:
    def __init__(self, messages):
        self.messages = iter(messages)
        self.calls = 0

    def complete(self, messages, tools=None):
        self.calls += 1
        return next(self.messages)

    def metrics(self):
        return {"calls": self.calls, "model": "test-double"}


def test_repair_replaces_invalid_route_once():
    index = build_index(SAMPLE)
    bad = draft(["cli.py::main", "nonexistent"]).model_dump_json()
    good = draft(["cli.py::main", "service.py::create_message"]).model_dump_json()
    client = FakeClient([{"content": bad}, {"content": good}])
    result = generate(index, index.flows[0], client)
    assert result["repair_attempted"]
    assert not result["validation_issues"]
    assert client.calls == 2


def test_failed_repair_keeps_partial_result():
    index = build_index(SAMPLE)
    bad = draft(["cli.py::main", "nonexistent"]).model_dump_json()
    client = FakeClient([{"content": bad}, {"content": "invalid"}])
    result = generate(index, index.flows[0], client)
    assert result["steps"][0]["symbol_id"] == "cli.py::main"
    assert any("补查" in item for item in result["gaps"])


def test_baseline_is_one_call_without_repair():
    index = build_index(SAMPLE)
    client = FakeClient([{"content": draft(["cli.py::main", "fake"]).model_dump_json()}])
    result = generate(index, index.flows[0], client, baseline=True)
    assert client.calls == 1
    assert not result["repair_attempted"]
    assert result["mode"] == "baseline"


def test_tool_call_workflow():
    index = build_index(SAMPLE)
    tool_call = {"role": "assistant", "content": None, "tool_calls": [{"id": "read1", "type": "function",
        "function": {"name": "read_symbols", "arguments": json.dumps({"symbol_ids": ["cli.py::main"]})}}]}
    client = FakeClient([tool_call, {"content": draft(["cli.py::main"]).model_dump_json()}])
    result = generate(index, index.flows[0], client)
    assert result["trace"][0]["tool"] == "read_symbols"
    assert client.calls == 2


def test_exports_preserve_uncertainty_and_revision(tmp_path):
    index = build_index(SAMPLE)
    result = deterministic_demo(index, index.flows[0])
    result["repository"]["revision"] = "a" * 40
    tour = codetour(result)
    assert tour["ref"] == "a" * 40
    assert tour["steps"][0]["line"] == result["steps"][0]["start"]
    assert "需要核实" in tour["steps"][-1]["description"]
    assert "offline_demo" in markdown(result)
    write_json(tmp_path / "out" / "trail.json", result)
    assert json.loads((tmp_path / "out" / "trail.json").read_text(encoding="utf-8"))["mode"] == "offline_demo"
    result["repository"]["dirty"] = True
    assert "ref" not in codetour(result)
    assert "本地更改" in codetour(result)["description"]

