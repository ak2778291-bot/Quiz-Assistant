"""Paragraph-aware chunking for curriculum text.

Curriculum prose is already authored in short topical paragraphs, so the
chunker packs whole paragraphs up to a word budget rather than cutting at a
fixed character count mid-sentence -- a chunk that ends mid-definition
produces a question the student cannot answer from the cited source.
"""

from __future__ import annotations

import re

TARGET_WORDS = 180
MAX_WORDS = 260
OVERLAP_WORDS = 40

_WS = re.compile(r"[ \t]+")


def normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_WS.sub(" ", line).strip() for line in text.split("\n")]
    return "\n".join(lines)


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", normalize(text)) if p.strip()]


def _split_long_paragraph(paragraph: str) -> list[str]:
    """Fall back to sentence packing for a paragraph over MAX_WORDS."""
    sentences = re.split(r"(?<=[.!?])\s+", paragraph)
    out: list[str] = []
    current: list[str] = []
    count = 0
    for sentence in sentences:
        words = len(sentence.split())
        if current and count + words > TARGET_WORDS:
            out.append(" ".join(current))
            current, count = [], 0
        current.append(sentence)
        count += words
    if current:
        out.append(" ".join(current))
    return out


def chunk_text(text: str) -> list[str]:
    """Pack paragraphs into ~TARGET_WORDS chunks with a small word overlap."""
    units: list[str] = []
    for paragraph in _paragraphs(text):
        if len(paragraph.split()) > MAX_WORDS:
            units.extend(_split_long_paragraph(paragraph))
        else:
            units.append(paragraph)

    chunks: list[str] = []
    current: list[str] = []
    count = 0
    for unit in units:
        words = len(unit.split())
        if current and count + words > TARGET_WORDS:
            chunks.append("\n\n".join(current))
            tail = " ".join(chunks[-1].split()[-OVERLAP_WORDS:])
            current, count = [tail], OVERLAP_WORDS
        current.append(unit)
        count += words
    if current:
        text_chunk = "\n\n".join(current)
        # Don't emit a trailing chunk that is only the overlap tail.
        if len(current) > 1 or not chunks:
            chunks.append(text_chunk)

    return [c for c in chunks if c.strip()]
