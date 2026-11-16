from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import threading
import time
from collections import Counter
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path

import httpx
from pypdf import PdfReader
from sqlalchemy import or_, select

from ..export import write_json
from .database import Chunk, Document


MAX_UPLOAD = 10 * 1024 * 1024
DEFAULT_EMBEDDING = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
embedding_usage = ContextVar("embedding_usage", default=None)


def parse_document(name: str, data: bytes) -> str:
    if len(data) > MAX_UPLOAD:
        raise ValueError("单份文档最大为 10 MB")
    suffix = Path(name).suffix.lower()
    if suffix == ".md":
        try:
            body = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValueError("Markdown 文件需要使用 UTF-8 编码") from None
    elif suffix == ".pdf":
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted or len(reader.pages) > 100:
                raise ValueError("PDF 需要解除加密，且最多为 100 页")
            body = "\f".join(page.extract_text() or "" for page in reader.pages)
        except ValueError:
            raise
        except Exception:
            raise ValueError("无法解析 PDF，请上传可提取文本的 PDF") from None
    else:
        raise ValueError("文档格式需要为 Markdown 或文本型 PDF")
    if not body.strip():
        raise ValueError("未提取到文本，请上传 Markdown 或文本型 PDF")
    if len(body) > 500_000:
        raise ValueError("单份文档提取后的文本最多为 50 万字符")
    return body


def split_document(name: str, body: str) -> list[dict]:
    chunks = []
    headings = []
    for page, text in enumerate(body.split("\f"), 1):
        lines = text.splitlines()
        first = 0
        while first < len(lines):
            end = first
            length = 0
            while end < len(lines) and length < 1800:
                length += len(lines[end]) + 1
                match = re.match(r"^(#{1,6})\s+(.+)", lines[end])
                if match:
                    depth = len(match[1])
                    headings = headings[:depth - 1] + [match[2]]
                end += 1
            content = "\n".join(lines[first:end])
            # Very long single lines are split with a character overlap as well.
            for offset in range(0, len(content), 1600):
                fragment = content[offset:offset + 1800]
                if fragment.strip():
                    chunks.append({"title": " / ".join([name, *headings]), "body": fragment,
                                   "location": {"file": name, "page": page,
                                                "start": first + 1, "end": end}})
            if end == len(lines):
                break
            first = max(first + 1, end - 2)
    return chunks


class Embeddings:
    def __init__(self, directory: Path, vault):
        self.directory = directory
        self.vault = vault
        self.lock = threading.Lock()

    def settings(self):
        path = self.directory / "embedding.json"
        saved = json.loads(path.read_text("utf-8")) if path.exists() else {}
        return {"mode": os.environ.get("REPOTRAIL_EMBEDDINGS") or saved.get("mode", "local"),
                "model": os.environ.get("REPOTRAIL_EMBEDDING_MODEL") or saved.get("model", DEFAULT_EMBEDDING),
                "base_url": os.environ.get("REPOTRAIL_EMBEDDING_URL") or saved.get("base_url", "")}

    def public(self):
        return {**self.settings(), "has_key": bool(os.environ.get("REPOTRAIL_EMBEDDING_KEY")
                                                   or self.vault.get_password("embedding", "api"))}

    def configure(self, mode: str, model: str, base_url: str, key: str):
        from ..llm import Settings
        if mode not in {"local", "api", "off"}:
            raise ValueError("请选择本地嵌入、兼容 API 或关键词检索模式")
        if mode == "api":
            base_url = Settings(key=key, base_url=base_url, model=model).validated().base_url
            previous = self.settings()
            if not key and previous["base_url"] != base_url:
                raise ValueError("更换嵌入服务地址时请重新填写密钥")
        if not model.strip() and mode != "off":
            raise ValueError("请填写嵌入模型名称")
        if key:
            self.vault.set_password("embedding", "api", key)
        write_json(self.directory / "embedding.json", {
            "mode": mode, "model": model.strip(), "base_url": base_url})
        return self.public()

    @lru_cache(maxsize=1)
    def local_model(self, name):
        try:
            from fastembed import TextEmbedding
        except ImportError:
            raise ValueError("请安装本地嵌入依赖 pip install 'repotrail[embeddings]'，或配置嵌入 API") from None
        try:
            return TextEmbedding(model_name=name, cache_dir=str(self.directory / "embeddings"), threads=2)
        except Exception:
            raise ValueError("本地嵌入模型加载失败，请检查模型名称、缓存及下载网络") from None

    def embed(self, texts: list[str], query=False) -> tuple[str, list[list[float]]]:
        config = self.settings()
        mode, model = config["mode"], config["model"]
        if mode == "off":
            return "", []
        tag = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        if mode == "local":
            with self.lock:
                engine = self.local_model(model)
                method = engine.query_embed if query else engine.passage_embed
                return tag, [vector.tolist() for vector in method(texts)]
        key = os.environ.get("REPOTRAIL_EMBEDDING_KEY") or self.vault.get_password("embedding", "api")
        if not key:
            raise ValueError("请配置嵌入 API 密钥")
        vectors = []
        try:
            for first in range(0, len(texts), 32):
                started = time.monotonic()
                response = httpx.post(config["base_url"].rstrip("/") + "/embeddings",
                                      headers={"Authorization": f"Bearer {key}"},
                                      json={"model": model, "input": texts[first:first + 32]},
                                      timeout=60, follow_redirects=False)
                response.raise_for_status()
                payload = response.json()
                usage = payload.get("usage") or {}
                observer = embedding_usage.get()
                if observer:
                    observer({"model": model, "kind": "embedding", "calls": 1,
                              "prompt_tokens": usage.get("prompt_tokens", 0), "completion_tokens": 0,
                              "usage_complete": "prompt_tokens" in usage, "estimated_cost": None,
                              "currency": os.environ.get("REPOTRAIL_CURRENCY", "CNY"),
                              "elapsed_seconds": round(time.monotonic() - started, 2)})
                values = sorted(payload["data"], key=lambda item: item["index"])
                vectors.extend(item["embedding"] for item in values)
            if len(vectors) != len(texts) or not vectors or not all(
                    len(value) == len(vectors[0]) and all(math.isfinite(float(n)) for n in value)
                    for value in vectors):
                raise ValueError("嵌入向量数量或格式无效")
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            raise ValueError("嵌入 API 请求失败或返回格式无效，请检查配置") from None
        return tag, vectors


def tokens(value: str) -> list[str]:
    words = re.findall(r"[a-zA-Z0-9_]+|[\u3400-\u9fff]+", value.lower())
    result = []
    for word in words:
        result.append(word)
        result.extend(part for part in word.split("_") if part != word)
        if re.match(r"[\u3400-\u9fff]", word):
            result.extend(word[index:index + 2] for index in range(len(word) - 1))
    return result


def retrieve(database, embeddings, project_id: str, snapshot_id: str,
             document_ids: list[str], query: str, limit=10, scope="all") -> list[dict]:
    with database.session() as session:
        statement = select(Chunk).where(
            Chunk.project_id == project_id,
            or_(Chunk.snapshot_id == snapshot_id, Chunk.document_id.in_(document_ids)))
        if scope == "code":
            statement = statement.where(Chunk.document_id.is_(None))
        elif scope == "documents":
            statement = statement.where(Chunk.document_id.is_not(None))
        rows = session.scalars(statement).all()
    if not rows:
        return []
    unique = {}
    for row in rows:
        unique.setdefault(hashlib.sha256(row.body.encode()).digest(), row)
    rows = list(unique.values())
    terms = set(tokens(query))
    corpus = [Counter(tokens(row.title + "\n" + row.body)) for row in rows]
    average = sum(sum(counts.values()) for counts in corpus) / len(corpus)
    frequency = Counter(term for counts in corpus for term in terms if term in counts)
    lexical = []
    for index, counts in enumerate(corpus):
        score = 0.0
        length = sum(counts.values())
        for term in terms:
            count = counts[term]
            if count:
                weight = math.log(1 + (len(rows) - frequency[term] + 0.5) / (frequency[term] + 0.5))
                score += weight * count * 2.2 / (count + 1.2 * (0.25 + 0.75 * length / max(average, 1)))
        if score:
            lexical.append((index, score))
    rankings = [sorted(lexical, key=lambda item: -item[1])]
    tag, vectors = embeddings.embed([query], query=True)
    if vectors:
        vector = vectors[0]
        norm = math.sqrt(sum(number * number for number in vector))
        semantic = []
        for index, row in enumerate(rows):
            value = row.embedding
            if value is None or row.embedding_model != tag or len(value) != len(vector):
                continue
            denominator = norm * math.sqrt(sum(number * number for number in value))
            score = sum(a * b for a, b in zip(vector, value)) / denominator if denominator else 0
            if score > 0:
                semantic.append((index, score))
        rankings.append(sorted(semantic, key=lambda item: -item[1]))
    fused = Counter()
    for ranking in rankings:
        for rank, (index, score) in enumerate(ranking[:80], 1):
            fused[index] += 1 / (60 + rank)
    return [{"id": rows[index].id, "title": rows[index].title, "body": rows[index].body,
             "location": rows[index].location, "document_id": rows[index].document_id,
             "snapshot_id": rows[index].snapshot_id}
            for index, score in fused.most_common(limit)]


def prepare_chunks(embeddings, pieces):
    if not pieces:
        return []
    tag, vectors = embeddings.embed([piece["title"] + "\n" + piece["body"] for piece in pieces])
    return [{**piece, "embedding": vectors[index] if vectors else None, "embedding_model": tag}
            for index, piece in enumerate(pieces)]


def store_chunks(session, project_id, pieces, document_id=None, snapshot_id=None):
    for piece in pieces:
        session.add(Chunk(project_id=project_id, document_id=document_id, snapshot_id=snapshot_id,
                          **piece))


def active_documents(session, project_id):
    return list(session.scalars(select(Document.id).join(Chunk, Chunk.document_id == Document.id).where(
        Document.project_id == project_id, Document.active.is_(True)).distinct()))
