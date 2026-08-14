"""Curriculum ingestion: extract -> chunk -> embed -> store.

A script, deliberately, not an Airflow DAG. Airflow earns its complexity with
multiple sources, recurring schedules and cross-task retry orchestration; a
one-time ingestion of a curated corpus has none of those. See DESIGN.md.

Usage
-----
    python -m scripts.ingest                  # ingest ./content, skip unchanged
    python -m scripts.ingest --force          # re-ingest everything
    python -m scripts.ingest --path other/    # ingest a different directory
    python -m scripts.ingest --dry-run        # parse + chunk, no DB writes

Accepted inputs
---------------
* .txt / .md with a front-matter block:

      ---
      subject: Science
      grade: 8
      chapter: Force and Pressure
      ---
      <chapter text>

* .pdf  - text is extracted with pypdf. Metadata comes from a sidecar
  <name>.meta.json, or is inferred from the directory name (e.g.
  "science_grade10") and the filename. Drop real NCERT chapter PDFs into a
  content subdirectory and this path handles them unchanged.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Allow `python scripts/ingest.py` as well as `python -m scripts.ingest`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import ContentChunk, Subject  # noqa: E402
from app.services.chunking import chunk_text, normalize  # noqa: E402
from app.services.embeddings import get_embedder  # noqa: E402
from app.utils import slugify  # noqa: E402

CONTENT_ROOT = Path(__file__).resolve().parent.parent / "content"

FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
DIR_NAME_RE = re.compile(r"^(?P<subject>[a-z_]+?)_grade(?P<grade>\d+)$")


@dataclass
class Document:
    subject: str
    grade: int
    chapter: str
    text: str
    path: Path

    @property
    def topic(self) -> str:
        return slugify(self.chapter)

    @property
    def source_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def _parse_front_matter(raw: str) -> tuple[dict[str, str], str]:
    match = FRONT_MATTER_RE.match(raw)
    if not match:
        return {}, raw
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip().lower()] = value.strip()
    return meta, raw[match.end() :]


def _meta_from_directory(path: Path) -> dict[str, str]:
    match = DIR_NAME_RE.match(path.parent.name)
    if not match:
        return {}
    return {
        "subject": match.group("subject").replace("_", " ").title(),
        "grade": match.group("grade"),
    }


def _chapter_from_filename(path: Path) -> str:
    stem = re.sub(r"^\d+[-_]", "", path.stem)
    return stem.replace("-", " ").replace("_", " ").strip().title()


def load_text_document(path: Path) -> Document:
    raw = path.read_text(encoding="utf-8")
    meta, body = _parse_front_matter(raw)
    fallback = _meta_from_directory(path)
    subject = meta.get("subject") or fallback.get("subject")
    grade = meta.get("grade") or fallback.get("grade")
    chapter = meta.get("chapter") or _chapter_from_filename(path)
    if not subject or not grade:
        raise ValueError(
            f"{path}: missing subject/grade. Add a front-matter block or place the "
            "file in a directory named like 'science_grade8'."
        )
    return Document(
        subject=subject.strip(),
        grade=int(grade),
        chapter=chapter.strip(),
        text=normalize(body).strip(),
        path=path,
    )


def load_pdf_document(path: Path) -> Document:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency is in requirements
        raise RuntimeError("pypdf is required to ingest PDF files") from exc

    sidecar = path.with_suffix(".meta.json")
    meta = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else {}
    fallback = _meta_from_directory(path)
    subject = meta.get("subject") or fallback.get("subject")
    grade = meta.get("grade") or fallback.get("grade")
    chapter = meta.get("chapter") or _chapter_from_filename(path)
    if not subject or not grade:
        raise ValueError(
            f"{path}: missing subject/grade. Add {sidecar.name} or place the PDF in a "
            "directory named like 'science_grade8'."
        )

    reader = PdfReader(str(path))
    pages = [page.extract_text() or "" for page in reader.pages]
    text = normalize("\n\n".join(pages)).strip()
    if not text:
        raise ValueError(
            f"{path}: no extractable text (likely a scanned PDF; OCR is out of scope)."
        )
    return Document(
        subject=str(subject).strip(),
        grade=int(grade),
        chapter=str(chapter).strip(),
        text=text,
        path=path,
    )


def discover(root: Path) -> list[Document]:
    documents: list[Document] = []
    for path in sorted(root.rglob("*")):
        if path.is_dir() or path.name.startswith("."):
            continue
        if path.suffixes[-2:] == [".meta", ".json"]:
            continue
        if path.suffix.lower() in {".txt", ".md"}:
            documents.append(load_text_document(path))
        elif path.suffix.lower() == ".pdf":
            documents.append(load_pdf_document(path))
    return documents


async def _get_or_create_subject(session, name: str, grade: int) -> Subject:
    subject = (
        await session.execute(
            select(Subject).where(Subject.name == name, Subject.grade == grade)
        )
    ).scalar_one_or_none()
    if subject is None:
        subject = Subject(name=name, grade=grade)
        session.add(subject)
        await session.flush()
    return subject


async def ingest(root: Path, *, force: bool, dry_run: bool) -> int:
    documents = discover(root)
    if not documents:
        print(f"No ingestible files found under {root}")
        return 1

    embedder = get_embedder()
    print(
        f"Found {len(documents)} document(s) under {root}\n"
        f"Embedding provider: {settings.embedding_provider} "
        f"(dim={settings.embedding_dim})\n"
    )

    if dry_run:
        total = 0
        for document in documents:
            chunks = chunk_text(document.text)
            total += len(chunks)
            print(
                f"  {document.subject} G{document.grade} | {document.chapter}"
                f"  ->  {len(chunks)} chunk(s), topic={document.topic}"
            )
        print(f"\nDry run: {total} chunk(s) would be stored. No database writes.")
        return 0

    await init_db()

    stored = skipped = 0
    async with SessionLocal() as session:
        for document in documents:
            subject = await _get_or_create_subject(
                session, document.subject, document.grade
            )

            existing = (
                await session.execute(
                    select(ContentChunk.source_hash).where(
                        ContentChunk.subject_id == subject.id,
                        ContentChunk.topic == document.topic,
                    )
                )
            ).scalars().all()

            if existing and not force and set(existing) == {document.source_hash}:
                print(
                    f"  = unchanged: {document.subject} G{document.grade} | "
                    f"{document.chapter} ({len(existing)} chunks)"
                )
                skipped += 1
                continue

            if existing:
                await session.execute(
                    delete(ContentChunk).where(
                        ContentChunk.subject_id == subject.id,
                        ContentChunk.topic == document.topic,
                    )
                )

            chunks = chunk_text(document.text)
            if not chunks:
                print(f"  ! no chunks produced for {document.path}, skipping")
                continue

            vectors = await embedder.embed(chunks)
            for index, (content, vector) in enumerate(zip(chunks, vectors, strict=True)):
                session.add(
                    ContentChunk(
                        subject_id=subject.id,
                        chapter=document.chapter,
                        topic=document.topic,
                        content=content,
                        embedding=vector,
                        chunk_index=index,
                        source_hash=document.source_hash,
                    )
                )
            await session.commit()
            stored += len(chunks)
            print(
                f"  + ingested: {document.subject} G{document.grade} | "
                f"{document.chapter} -> {len(chunks)} chunk(s) [topic={document.topic}]"
            )

    print(f"\nDone. {stored} chunk(s) stored, {skipped} document(s) unchanged.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest curriculum content.")
    parser.add_argument("--path", type=Path, default=CONTENT_ROOT)
    parser.add_argument(
        "--force", action="store_true", help="re-ingest even if content is unchanged"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="parse and chunk without touching the DB"
    )
    args = parser.parse_args()
    return asyncio.run(ingest(args.path, force=args.force, dry_run=args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
