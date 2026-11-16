import hashlib
from pathlib import Path

from sqlalchemy import select

from .database import Document, Job, Membership, Project, User
from .knowledge import parse_document


def seed(database, username: str, source: Path) -> str:
    """Create an explicitly fictional company knowledge space around RepoTrail's real source."""
    with database.session.begin() as session:
        admin = session.scalar(select(User).where(User.username == username.lower(), User.admin.is_(True)))
        if not admin:
            raise ValueError("请先创建指定管理员")
        existing = session.scalar(select(Project).where(Project.name == "RepoTrail · 示例培训"))
        if existing:
            return existing.id
        project = Project(name="RepoTrail · 示例培训", source=str(source.resolve()),
                          description="RepoTrail 真实源码与模拟企业知识库，用于复现研发入职培训流程。")
        session.add(project)
        session.flush()
        session.add(Membership(project_id=project.id, user_id=admin.id))
        session.add(Job(project_id=project.id, user_id=admin.id, kind="index"))
        for path in sorted((Path(__file__).parents[1] / "sample" / "knowledge").glob("*.md")):
            data = path.read_bytes()
            row = Document(project_id=project.id, name=path.name, body=parse_document(path.name, data),
                           digest=hashlib.sha256(data).hexdigest())
            session.add(row)
            session.flush()
            session.add(Job(project_id=project.id, user_id=admin.id, kind="document", payload={"document_id": row.id}))
        return project.id
