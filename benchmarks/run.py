"""Run from the checkout with python -m benchmarks.run verify|compare."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import textwrap
from dataclasses import asdict
from pathlib import Path

from repotrail.engine import PROMPT_VERSION, generate
from repotrail.export import write_json
from repotrail.index import build_index
from repotrail.llm import ModelClient
from repotrail.models import Flow
from repotrail.provider import ProviderStore


def verify_case(case, index):
    if index.revision != case["revision"]:
        raise ValueError(f"{case['name']} 版本不匹配，需要检出 {case['revision']}")
    checked = []
    for question in case["questions"]:
        symbol = index.symbols.get(question["symbol"])
        if not symbol:
            raise ValueError(f"无法定位参考符号 {question['symbol']}")
        source = "\n".join(index.files[symbol.file].splitlines()[symbol.start - 1:symbol.end])
        canonical = ast.unparse(ast.parse(textwrap.dedent(source)))
        if question["source_fragment"] not in canonical:
            raise ValueError(f"参考源码已变化 {question['id']}")
        checked.append({"id": question["id"], "symbol": symbol.id,
                        "start": symbol.start, "end": symbol.end,
                        "source_url": f"{case['url']}/blob/{index.revision}/{symbol.file}#L{symbol.start}"})
    return checked


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["verify", "compare"])
    parser.add_argument("--checkouts", type=Path, default=Path("benchmarks/checkouts"))
    parser.add_argument("--output", type=Path, default=Path("benchmarks/results"))
    parser.add_argument("--resume", action="store_true", help="Reuse matching completed generation files")
    args = parser.parse_args(argv)
    manifest = json.loads(Path(__file__).with_name("cases.json").read_text(encoding="utf-8"))
    settings = ProviderStore(Path.cwd() / ".repotrail").current() if args.mode == "compare" else None
    provider_fingerprint = hashlib.sha256(settings.base_url.encode()).hexdigest() if settings else None
    if settings:
        ModelClient(settings)  # Fail before processing when credentials are absent.
    results = []
    for case in manifest["repositories"]:
        index = build_index(args.checkouts / case["name"])
        checks = verify_case(case, index)
        item = {"repository": case["name"], "revision": index.revision,
                "digest": index.digest, "reference_source_checks": checks,
                "human_review_status": "pending"}
        if args.mode == "compare":
            flow = Flow(case["entry"], case["title"], case["entry"], "评测集锁定的阅读起点")
            comparisons = {}
            for baseline in [True, False]:
                label = "baseline" if baseline else "system"
                output = args.output / case["name"] / f"{label}.json"
                client = ModelClient(settings)
                trail = None
                if args.resume and output.exists():
                    saved = json.loads(output.read_text("utf-8"))
                    if (saved.get("repository", {}).get("digest") == index.digest
                            and saved.get("repository", {}).get("revision") == index.revision
                            and saved.get("metrics", {}).get("model") == settings.model
                            and saved.get("benchmark_provider_fingerprint") == provider_fingerprint
                            and saved.get("prompt_version") == PROMPT_VERSION
                            and saved.get("flow") == asdict(flow)
                            and saved.get("mode") == ("baseline" if baseline else "model")):
                        trail = saved
                resumed = trail is not None
                try:
                    if trail is None:
                        trail = generate(index, flow, client, baseline=baseline)
                        trail["benchmark_provider_fingerprint"] = provider_fingerprint
                        write_json(output, trail)
                except ValueError as exc:
                    comparisons[label] = {"status": "failed", "error": str(exc), "metrics": client.metrics(),
                                          "mechanical_symbol_coverage": None, "human_semantic_score": None}
                    continue
                included = {step["symbol_id"] for step in trail["steps"]}
                comparisons[label] = {
                    "status": "complete", "resumed": resumed,
                    "mechanical_symbol_coverage": sum(q["symbol"] in included for q in case["questions"]) / len(case["questions"]),
                    "validation": trail["validation"], "metrics": trail["metrics"],
                    "human_semantic_score": None,
                }
            item["comparison"] = comparisons
        results.append(item)
        write_json(args.output / f"{args.mode}.json", {"mode": args.mode, "results": results,
                                                     "status": "in_progress"})
    report = {"mode": args.mode, "model_evaluation_completed": args.mode == "compare",
              "reference_review_status": manifest["review_status"], "results": results}
    failed = sum(attempt["status"] == "failed" for result in results
                 for attempt in result.get("comparison", {}).values())
    report["failed_generations"] = failed
    write_json(args.output / f"{args.mode}.json", report)
    print(json.dumps({"mode": args.mode, "repositories": len(results),
                      "reference_source_checks": sum(len(r["reference_source_checks"]) for r in results),
                      "model_evaluation_completed": report["model_evaluation_completed"],
                      "failed_generations": failed}, indent=2))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

