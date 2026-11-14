from pathlib import Path

import pytest

from repotrail.index import Limits, build_index


def repository(tmp_path, files):
    for file, content in files.items():
        path = tmp_path / file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return tmp_path


def test_cross_file_import_and_main_guard(tmp_path):
    root = repository(tmp_path, {
        "main.py": "from service import run\ndef main():\n    return run()\nif __name__ == '__main__':\n    main()\n",
        "service.py": "def run():\n    return 7\n",
    })
    index = build_index(root)
    assert index.flows[0].entry == "main.py::main"
    assert any(edge.source == "main.py::main" and edge.target == "service.py::run"
               for edge in index.edges)


def test_relative_import_and_method(tmp_path):
    root = repository(tmp_path, {
        "src/pkg/__init__.py": "",
        "src/pkg/app.py": "from .helper import clean\nclass App:\n    def run(self):\n        return self.finish()\n    def finish(self):\n        return clean()\n",
        "src/pkg/helper.py": "def clean():\n    return 'ok'\n",
    })
    index = build_index(root)
    pairs = {(edge.source, edge.target) for edge in index.edges}
    assert ("src/pkg/app.py::App.run", "src/pkg/app.py::App.finish") in pairs
    assert ("src/pkg/app.py::App.finish", "src/pkg/helper.py::clean") in pairs


def test_module_alias(tmp_path):
    root = repository(tmp_path, {
        "main.py": "import service as svc\ndef main():\n    return svc.run()\n",
        "service.py": "def run():\n    return 7\n",
    })
    assert build_index(root).edges[0].target == "service.py::run"


def test_arguments_and_assignments_shadow_imports(tmp_path):
    root = repository(tmp_path, {
        "main.py": "from service import run\ndef first(run):\n    return run()\ndef second():\n    run = object()\n    return run()\n",
        "service.py": "def run():\n    return 7\n",
    })
    assert not build_index(root).edges


def test_default_arguments_are_not_function_body_edges(tmp_path):
    root = repository(tmp_path, {"main.py": "def build():\n    return 1\ndef main(value=build()):\n    return value\n"})
    assert not build_index(root).edges


def test_overload_uses_final_implementation(tmp_path):
    root = repository(tmp_path, {"main.py": "from typing import overload\n@overload\ndef run(x: int): ...\ndef run(x):\n    return x\ndef main():\n    return run(1)\n"})
    index = build_index(root)
    assert index.symbols["main.py::run"].start == 4
    assert index.edges[0].target == "main.py::run"


def test_snapshot_changes_when_code_or_readme_changes(tmp_path):
    root = repository(tmp_path, {"main.py": "def main():\n    return 1\n", "README.md": "one"})
    first = build_index(root)
    (root / "main.py").write_text("def main():\n    return 2\n")
    second = build_index(root)
    assert first.digest != second.digest
    assert "return 1" in first.files["main.py"]
    (root / "README.md").write_text("two")
    assert second.digest != build_index(root).digest


def test_does_not_execute_source_and_skips_syntax_errors(tmp_path):
    root = repository(tmp_path, {"main.py": "raise RuntimeError('must never run')\ndef main():\n    return 1\n", "broken.py": "def !!"})
    index = build_index(root)
    assert "main.py::main" in index.symbols
    assert len(index.warnings) == 1


def test_limits_fail_explicitly(tmp_path):
    root = repository(tmp_path, {"main.py": "def main():\n    return 1\n"})
    with pytest.raises(ValueError, match="文件数"):
        build_index(root, Limits(max_files=0))
    with pytest.raises(ValueError, match="单文件"):
        build_index(root, Limits(max_file_bytes=3))
    with pytest.raises(ValueError, match="总大小"):
        build_index(root, Limits(max_total_bytes=3))


def test_no_files_and_missing_directory(tmp_path):
    with pytest.raises(ValueError, match="没有可解析"):
        build_index(tmp_path)
    with pytest.raises(ValueError, match="不存在"):
        build_index(tmp_path / "missing")


def test_package_script_precedes_example_routes(tmp_path):
    root = repository(tmp_path, {
        "pyproject.toml": '[project.scripts]\nhello = "pkg.cli:main"\n',
        "src/pkg/cli.py": "def main():\n    return 1\n",
        "examples/app.py": "@app.get('/')\ndef index():\n    return 2\n",
    })
    assert build_index(root).flows[0].entry == "src/pkg/cli.py::main"


def test_symlink_source_is_skipped(tmp_path):
    root = repository(tmp_path, {"main.py": "def main():\n    return 1\n"})
    try:
        (root / "linked.py").symlink_to(root / "main.py")
    except OSError:
        pytest.skip("Windows symlink privileges unavailable")
    assert "linked.py" not in build_index(root).files

