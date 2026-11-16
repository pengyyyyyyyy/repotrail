from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .engine import deterministic_demo, generate
from .export import codetour, markdown, write_json
from .index import build_index
from .llm import ModelClient
from .repository import acquire
from .provider import ProviderStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="repotrail")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Open the local reading workspace")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--team", action="store_true", help="Run the authenticated team workspace")
    serve.add_argument("--host", default="127.0.0.1")
    commands.add_parser("worker", help="Run the durable team job worker")
    commands.add_parser("migrate", help="Initialize or migrate the team database")
    admin = commands.add_parser("admin", help="Create an administrator or reset an account password")
    admin.add_argument("action", choices=["create", "reset-password"])
    admin.add_argument("username")
    admin.add_argument("--name", default="")
    example = commands.add_parser("seed", help="Create the public onboarding example project")
    example.add_argument("--username", required=True, help="Existing administrator username")
    checkout = Path(__file__).resolve().parents[1]
    example.add_argument("--source", default=str(checkout if (checkout / "pyproject.toml").exists() else Path(__file__).parent))
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
            if args.team:
                from .team.server import create_team_app
                app = create_team_app(start_worker=os.environ.get("REPOTRAIL_EMBEDDED_WORKER", "1") == "1")
            else:
                from .server import create_app
                if args.host != "127.0.0.1":
                    raise ValueError("共享部署请使用 serve --team")
                app = create_app()
            uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
            return 0
        if args.command in {"admin", "worker", "migrate", "seed"}:
            from .team.database import Database
            directory = Path(os.environ.get("REPOTRAIL_DATA_DIR", Path.cwd() / ".repotrail"))
            database = Database(directory)
            database.migrate()
            if args.command == "migrate":
                print("Database schema version 1 is ready")
                return 0
            if args.command == "admin":
                from getpass import getpass
                from sqlalchemy import delete, select
                from .team.database import LoginSession, User
                from .team.security import PASSWORDS, create_user
                password = getpass("Password (12–256 characters): ")
                if not 12 <= len(password) <= 256:
                    raise ValueError("密码长度需要为 12–256 位")
                if password != getpass("Confirm password: "):
                    raise ValueError("两次输入的密码不同")
                with database.session.begin() as session:
                    if args.action == "create":
                        create_user(session, args.username, args.name, password, admin=True)
                    else:
                        user = session.scalar(select(User).where(User.username == args.username.strip().lower()))
                        if not user:
                            raise ValueError("账号不存在")
                        user.password_hash, user.active = PASSWORDS.hash(password), True
                        session.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
                print("Account updated")
                return 0
            if args.command == "seed":
                from .team.example import seed
                print("Example project " + seed(database, args.username, Path(args.source)))
                return 0
            from .team.worker import Runtime, Worker
            worker = Worker(Runtime(database, directory))
            import signal
            signal.signal(signal.SIGTERM, lambda signum, frame: worker.stop())
            try:
                worker.run()
            except KeyboardInterrupt:
                worker.stop()
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
            result = generate(index, flow, ModelClient(ProviderStore(Path.cwd() / ".repotrail").current()), baseline=args.baseline)
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

