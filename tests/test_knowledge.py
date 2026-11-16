import io
import subprocess

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy import select

from repotrail.team.database import Chunk, Database, Document, Project
from repotrail.team.knowledge import parse_document, retrieve, split_document


def test_small_markdown_has_one_chunk_and_long_lines_are_bounded():
    pieces = split_document("guide.md", "# Guide\nOne\nTwo\nThree")
    assert len(pieces) == 1
    assert pieces[0]["location"] == {"file": "guide.md", "page": 1, "start": 1, "end": 4}
    assert max(len(piece["body"]) for piece in split_document("long.md", "x" * 9000)) <= 1800


def test_text_pdf_is_parsed_with_page_boundaries():
    writer = PdfWriter()
    page = writer.add_blank_page(400, 500)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 30 400 Td (Project onboarding) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    assert "Project onboarding" in parse_document("guide.pdf", output.getvalue())
    with pytest.raises(ValueError):
        parse_document("secret.env", b"content")
    with pytest.raises(ValueError):
        parse_document("invalid.md", b"\xff\xfe")


def test_hybrid_retrieval_is_project_and_version_scoped(tmp_path):
    database = Database(tmp_path)
    database.migrate()
    with database.session.begin() as session:
        project = Project(name="One", source="/source")
        other = Project(name="Other", source="/other")
        session.add_all([project, other])
        session.flush()
        current = Document(project_id=project.id, name="current.md", body="semantic match", digest="a" * 64)
        old = Document(project_id=project.id, name="old.md", body="obsolete", digest="b" * 64, active=False)
        private = Document(project_id=other.id, name="private.md", body="other team", digest="c" * 64)
        session.add_all([current, old, private])
        session.flush()
        for document in [current, old, private]:
            session.add(Chunk(project_id=document.project_id, document_id=document.id, title=document.name,
                              body=document.body, location={"file": document.name}, embedding=[1.0, 0.0], embedding_model="test"))
    class Embeddings:
        def embed(self, texts, query=False):
            return "test", [[1.0, 0.0]]
    result = retrieve(database, Embeddings(), project.id, "missing", [current.id], "unrelated query")
    assert len(result) == 1
    assert result[0]["document_id"] == current.id
    with database.session.begin() as session:
        session.delete(session.get(Document, current.id))
    assert retrieve(database, Embeddings(), project.id, "missing", [current.id], "query") == []


def test_repository_markdown_discovery_respects_gitignore(tmp_path):
    from repotrail.index import _files
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / ".gitignore").write_text("ignored.md\n")
    (tmp_path / "ignored.md").write_text("private local notes")
    (tmp_path / "README.md").write_text("public project documentation")
    assert [path.name for path in _files(tmp_path, ".md")] == ["README.md"]


def test_source_aliases_are_verified_and_resolved():
    from repotrail.team.worker import normalize_citations
    source = {"id": "a" * 32}
    assert normalize_citations("Fact [S1]", [source]) == "Fact [" + "a" * 32 + "]"
    with pytest.raises(ValueError):
        normalize_citations("Fact [S2]", [source])
    with pytest.raises(ValueError):
        normalize_citations("Fact [" + "b" * 32 + "]", [source])
