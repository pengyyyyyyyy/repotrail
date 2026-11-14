from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def markdown(trail: dict) -> str:
    repo = trail["repository"]
    lines = [f"# {trail['title']}", "", trail["summary"], "",
             f"生成方式 `{trail['mode']}`", "",
             f"源码快照 `{repo['digest']}`", "",
             f"Git 版本 `{repo.get('revision') or 'local snapshot'}`", "",
             "源码定位与静态关系经过程序检查。说明文字与运行时行为需要人工核对。", ""]
    for number, step in enumerate(trail["steps"], 1):
        lines.extend([f"## {number}. {step['title']}", "", step["explanation"], "",
                      f"`{step['file']}` · L{step['start']}–L{step['end']}", "",
                      "````python", step["source"], "````", ""])
        if step["connection"]:
            lines.extend([f"连接状态 `{step['connection']['status']}`", ""])
    if trail["gaps"]:
        lines.extend(["## 需要核实的部分", ""])
        lines.extend(f"- {gap}" for gap in trail["gaps"])
    return "\n".join(lines) + "\n"


def codetour(trail: dict) -> dict:
    result = {"title": trail["title"], "description": trail["summary"], "steps": []}
    revision = trail["repository"].get("revision")
    if revision and not trail["repository"].get("dirty"):
        result["ref"] = revision
    if trail["repository"].get("dirty"):
        result["description"] += "\n\n快照包含本地更改。请在相同工作区核对源码与行号。"
    for step in trail["steps"]:
        status = "入口" if not step["connection"] else step["connection"]["status"]
        result["steps"].append({"file": step["file"], "line": step["start"],
                                "title": step["title"],
                                "description": step["explanation"] + f"\n\n静态连接状态 `{status}`。说明语义需要人工核对。"})
    if trail["gaps"] and result["steps"]:
        result["steps"][-1]["description"] += "\n\n需要核实的部分\n\n" + "\n".join(
            f"- {gap}" for gap in trail["gaps"])
    return result


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()

