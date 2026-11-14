from __future__ import annotations

import argparse
import json
from pathlib import Path

from .engine import deterministic_demo, generate
from .export import codetour, markdown, write_json
from .index import build_index
from .llm import ModelClient, Settings
from .repository import acquire


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="repotrail")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Open the local reading workspace")
    serve.add_argument("--port", type=int, default=8765)
    inspect = commands.add_parser("inspect", help="Index a repository without model calls")
    inspect.add_argument("source")
    trail = commands.add_parser("generate", help="Generate and verify a reading trail")
    trail.add_argument("source")
    trail.add_argument("--flow", help="Exact candidate flow ID from inspect")
    trail.add_argument("--output", default=".repotrail/output")
    trail.add_argument("--baseline", action="store_true", help="One-call baseline without repair")
    demo = commands.add_parser("demo", help="Generate an explicitly labeled offline example")
    demo.add_argument("--output", default=".repotrail/demo")
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            import uvicorn
            from .server import create_app
            uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="warning")
            return 0
        if args.command == "demo":
            index = build_index(Path(__file__).parent / "sample")
            result = deterministic_demo(index, index.flows[0])
        else:
            root = acquire(args.source, Path.cwd() / ".repotrail")
            index = build_index(root)
            if args.command == "inspect":
                print(json.dumps(index.overview(), ensure_ascii=False, indent=2))
                return 0
            flow = next((item for item in index.flows if item.id == args.flow), None) if args.flow else index.flows[0]
            if flow is None:
                raise ValueError("指定的 flow 不在候选列表中，请先运行 inspect")
            result = generate(index, flow, ModelClient(Settings.from_env()), baseline=args.baseline)
        output = Path(args.output)
        write_json(output / "trail.json", result)
        write_json(output / "reading.tour", codetour(result))
        (output / "trail.md").write_text(markdown(result), encoding="utf-8")
        print(f"Saved {output.resolve()}")
        print(json.dumps(result["metrics"], ensure_ascii=False))
        return 0
    except (ValueError, OSError) as exc:
        parser.exit(1, f"RepoTrail: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())

