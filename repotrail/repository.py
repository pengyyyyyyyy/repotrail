from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
from pathlib import Path


GITHUB_URL = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")


def acquire(source: str, cache_dir: Path) -> Path:
    """Clone public GitHub HTTPS repositories, or use an existing local directory."""
    if len(source) > 1000:
        raise ValueError("仓库地址过长")
    if not source.startswith(("https://", "http://", "git@", "ssh://")):
        path = Path(source).expanduser().resolve()
        if not path.is_dir():
            raise ValueError("本地仓库目录不存在")
        return path
    match = GITHUB_URL.fullmatch(source)
    if not match or any(part in {".", ".."} for part in match.groups()):
        raise ValueError("请输入公开仓库的 https://github.com/owner/repo 地址")
    owner, name = match.groups()
    url = f"https://github.com/{owner}/{name}.git"
    key = hashlib.sha256(url.lower().encode()).hexdigest()[:20]
    destination = cache_dir / "repositories" / key / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_dir():
        return destination
    # Each operation has its own temporary checkout. An atomic rename publishes it.
    temporary = Path(tempfile.mkdtemp(prefix="clone-", dir=destination.parent))
    environment = os.environ.copy()
    environment.update(GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1")
    try:
        subprocess.run(
            ["git", "-c", f"core.hooksPath={os.devnull}", "-c", "core.fsmonitor=false",
             "-c", "credential.helper=", "-c", "protocol.file.allow=never",
             "clone", "--depth", "1", "--single-branch", "--", url, str(temporary)],
            check=True, capture_output=True, timeout=120, env=environment,
        )
        try:
            temporary.rename(destination)
        except OSError:
            if not destination.is_dir():
                raise
    except subprocess.TimeoutExpired as exc:
        raise ValueError("克隆超时，请先在本地克隆后输入目录") from exc
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("无法克隆公开仓库，请检查地址、Git 安装及网络") from exc
    finally:
        if (temporary.exists() and not temporary.is_symlink()
                and temporary.resolve().is_relative_to(destination.parent.resolve())
                and temporary.name.startswith("clone-")):
            import shutil
            shutil.rmtree(temporary, ignore_errors=True)
    return destination


def acquire_team(source: str, cache_dir: Path, token: str = "") -> Path:
    """Make a fresh HTTPS checkout, or read an administrator-selected local directory."""
    import shlex
    import sys
    from urllib.parse import urlsplit

    if not source.startswith(("https://", "http://", "ssh://", "git@")):
        return acquire(source, cache_dir)
    parts = urlsplit(source)
    if (len(source) > 1000 or parts.scheme != "https" or not parts.hostname
            or parts.username or parts.password or parts.query or parts.fragment
            or not parts.path.strip("/") or any(char.isspace() for char in source)):
        raise ValueError("请输入 HTTPS Git 仓库地址，访问令牌请单独配置")
    if any(character in token for character in "\r\n"):
        raise ValueError("访问令牌包含换行，请重新填写")
    parent = cache_dir / "team-repositories"
    parent.mkdir(parents=True, exist_ok=True)
    destination = Path(tempfile.mkdtemp(prefix="snapshot-", dir=parent))
    environment = os.environ.copy()
    environment.update(GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1",
                       GIT_CONFIG_GLOBAL=os.devnull, REPOTRAIL_GIT_TOKEN=token)
    executable = shlex.quote(sys.executable.replace("\\", "/"))
    helper = f"!{executable} -m repotrail.git_credentials"
    try:
        subprocess.run([
            "git", "-c", f"core.hooksPath={os.devnull}", "-c", "core.fsmonitor=false",
            "-c", "credential.helper=", "-c", f"credential.helper={helper}",
            "-c", "protocol.file.allow=never", "-c", "http.followRedirects=false",
            "clone", "--depth", "1", "--single-branch", "--", source, str(destination),
        ], check=True, capture_output=True, timeout=120, env=environment)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        import shutil
        if destination.resolve().is_relative_to(parent.resolve()) and destination.name.startswith("snapshot-"):
            shutil.rmtree(destination, ignore_errors=True)
        raise ValueError("仓库获取失败，请检查地址、访问令牌、Git 安装及网络") from None
    return destination

