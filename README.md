# RepoTrail

A self-hosted onboarding workspace for engineering teams, grounded in Python source and project knowledge.

[简体中文](README.zh-CN.md) · [Architecture](docs/architecture.md) · [Research](docs/research.md) · [Evaluation](benchmarks/README.md)

Open a GitHub repository or local checkout, choose an entry point, and follow a reading trail alongside the source. RepoTrail includes a Python symbol index, model-assisted explanations, and Markdown, JSON and CodeTour exports.

Team mode adds project-scoped access, Markdown/PDF knowledge ingestion, hybrid retrieval, reviewed onboarding courses, private follow-up conversations, persistent learning progress and administrator acceptance of learning summaries.

## Team workspace

```sh
git clone https://github.com/pengyyyyyyyy/repotrail.git
cd repotrail
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev,embeddings]'
.venv/bin/python -m repotrail.cli admin create admin
.venv/bin/python -m repotrail.cli seed --username admin --source .
.venv/bin/python -m repotrail.cli serve --team --port 8766
```

On Windows use `.venv\Scripts\python.exe`. Open [the team workspace](http://127.0.0.1:8766). The local quick start uses SQLite and an embedded worker. The supplied Docker Compose deployment uses PostgreSQL, pgvector and a separate durable worker. [Deployment and operations](docs/self-hosting.md)

![RepoTrail reading workspace](docs/assets/workspace.png)

## Single-user reading workspace

Python 3.11 or newer and Git are required. From this checkout, create an environment.

~~~sh
python -m venv .venv
~~~

On macOS or Linux, run the environment's interpreter.

~~~sh
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m repotrail.cli serve
~~~

On Windows PowerShell, use the following commands.

~~~powershell
& .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
& .\.venv\Scripts\python.exe -m repotrail.cli serve
~~~

Open [the local workspace](http://127.0.0.1:8765).

## Connect a model

1. Click **API 接入** in the upper-right corner.
2. Choose a provider template or enter a custom connection name and API base URL.
3. Enter your API key, fetch the model list, or enter a model ID directly.
4. Test the connection, then save and use it.

The API should support Chat Completions and function calling. The base URL can include a custom path. Model discovery uses `/models` and generation uses `/chat/completions`. Saving settings applies immediately to the web workspace, CLI and benchmark runner.

Connection metadata is stored in the local, Git-ignored .repotrail directory. Credentials use the operating system's credential store. When that store is unavailable, the web service uses the key for its current session.

![API connection settings](docs/assets/api-settings.png)

Existing REPOTRAIL_API_KEY, REPOTRAIL_BASE_URL and REPOTRAIL_MODEL environment variables also work. Optional prices per million tokens are documented in .env.example.

## Command line

~~~sh
python -m repotrail.cli inspect /path/to/repository
python -m repotrail.cli generate /path/to/repository --output .repotrail/output
python -m repotrail.cli demo
python -m pytest -q
python -m benchmarks.run verify
~~~

Run these commands with the virtual environment's Python. The inspect command accepts a local directory or public GitHub HTTPS URL. Use generate --flow with an exact candidate ID to select an entry point.

## Scope

The default analysis limit is 300 Python files, 200 KB per file and 4 MB total source. Each analysis creates an immutable source snapshot with a SHA-256 digest. Remote checkouts are cached at their downloaded version.

The index covers import aliases, relative imports, direct calls and same-class method candidates. The generation workflow uses bounded source-reading tools and a repair pass. [Implementation details](docs/architecture.md)

## Foundations

Provider configuration and model discovery are informed by OpenMinis. CodeTour informs the export format, PocketFlow informs staged generation, and Aider and Repomix inform context selection. [References and attribution](THIRD_PARTY.md)

## License

MIT. Frontend craft guidance from unslop by Damir Sokolovsky (@heydamir), CC BY 4.0, is credited in THIRD_PARTY.md.

