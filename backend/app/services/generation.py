"""Curriculum-grounded question generation (spec Section 4.1).

Three layers enforce grounding, in order:

1. Retrieval scopes chunks to the requested (subject, topic) in SQL, so the
   prompt physically cannot contain another chapter's text.
2. The prompt instructs the model to use only the numbered context and to cite
   the chunk ids it used.
3. `validate_generated_questions` re-checks every citation against the ids
   that were actually retrieved. A question citing anything else is treated as
   evidence the model drifted to general knowledge and raises
   `UngroundedGenerationError` -- it is never silently accepted.

Layer 3 is the one under test; layers 1 and 2 are best-effort.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.errors import UngroundedGenerationError
from app.services.llm import LLMClient, get_llm_client
from app.services.proficiency import DIFFICULTIES
from app.services.retrieval import RetrievedChunk

logger = logging.getLogger(__name__)

OPTION_LETTERS = ("A", "B", "C", "D")

PROMPT_TEMPLATE = """You are an NCERT curriculum question setter for Indian school students.

You will be given numbered excerpts from the NCERT textbook chapter
"{chapter}" ({subject}, Grade {grade}). These excerpts are your ONLY permitted
source of facts.

HARD RULES
1. Use ONLY information stated in the excerpts below. Do NOT use any outside or
   general knowledge, even if you are confident it is correct.
2. If the excerpts do not contain enough material for a question, produce fewer
   questions. Never invent content to reach the requested count.
3. Every question MUST list, in "source_chunk_ids", the id(s) of the excerpt(s)
   that contain the answer. Only ids from the list below are valid.
4. The answer must be verifiable by reading the cited excerpt alone.
5. Exactly one of the four options is correct; the other three must be clearly
   wrong to a student who has read the excerpt, but plausible in style.
6. Write at the reading level of a Grade {grade} student.

DIFFICULTY DEFINITIONS
- easy:   direct recall of a fact stated verbatim in an excerpt.
- medium: requires combining two statements, or applying a stated definition.
- hard:   requires reasoning about a stated process, cause/effect, or a
          worked relationship described in the excerpts.

REQUESTED QUESTIONS
Produce exactly this mix, in this order: {difficulty_list}
(total {n_questions} questions)

EXCERPTS
{context}

Return JSON matching the required schema. No prose outside the JSON.
"""


@dataclass
class GeneratedQuestion:
    question_text: str
    options: dict[str, str]
    correct_answer: str
    difficulty: str
    explanation: str
    source_chunk_ids: list[int]


@dataclass
class GenerationResult:
    questions: list[GeneratedQuestion]
    provider: str
    cached: bool = False
    dropped: list[str] = field(default_factory=list)


def build_prompt(
    chunks: list[RetrievedChunk],
    *,
    subject: str,
    grade: int,
    chapter: str,
    difficulties: list[str],
) -> str:
    context = "\n\n".join(
        f"[excerpt id={c.id}]\n{c.content}" for c in chunks
    )
    return PROMPT_TEMPLATE.format(
        chapter=chapter,
        subject=subject,
        grade=grade,
        difficulty_list=", ".join(difficulties),
        n_questions=len(difficulties),
        context=context,
    )


def validate_generated_questions(
    raw: dict,
    *,
    allowed_chunk_ids: set[int],
    expected_difficulties: list[str] | None = None,
) -> tuple[list[GeneratedQuestion], list[str]]:
    """Validate model output against the retrieved context.

    Returns (valid questions, reasons for dropped questions).
    Raises `UngroundedGenerationError` if a question cites a chunk that was not
    retrieved, or if nothing survives validation.
    """
    items = raw.get("questions")
    if not isinstance(items, list) or not items:
        raise UngroundedGenerationError("Model returned no questions.")

    valid: list[GeneratedQuestion] = []
    dropped: list[str] = []
    seen_texts: set[str] = set()

    for index, item in enumerate(items):
        label = f"question[{index}]"
        if not isinstance(item, dict):
            dropped.append(f"{label}: not an object")
            continue

        # --- grounding: hard failure, checked first -------------------------
        raw_ids = item.get("source_chunk_ids")
        if not isinstance(raw_ids, list) or not raw_ids:
            dropped.append(f"{label}: no source_chunk_ids")
            continue
        try:
            cited = [int(i) for i in raw_ids]
        except (TypeError, ValueError):
            dropped.append(f"{label}: non-integer source_chunk_ids")
            continue

        unknown = sorted(set(cited) - allowed_chunk_ids)
        if unknown:
            raise UngroundedGenerationError(
                f"{label} cites chunk id(s) {unknown} that were not retrieved for "
                f"this topic (allowed: {sorted(allowed_chunk_ids)}). Treating the "
                "generation as ungrounded and refusing it."
            )

        # --- structure: droppable -------------------------------------------
        text = str(item.get("question_text", "")).strip()
        if len(text) < 10:
            dropped.append(f"{label}: question_text too short")
            continue
        key = " ".join(text.lower().split())
        if key in seen_texts:
            dropped.append(f"{label}: duplicate question_text")
            continue

        options = item.get("options")
        if not isinstance(options, dict) or set(options) != set(OPTION_LETTERS):
            dropped.append(f"{label}: options must have keys A-D")
            continue
        option_values = [str(options[letter]).strip() for letter in OPTION_LETTERS]
        if any(not v for v in option_values):
            dropped.append(f"{label}: empty option")
            continue
        if len({v.lower() for v in option_values}) != len(OPTION_LETTERS):
            dropped.append(f"{label}: duplicate options")
            continue

        correct = str(item.get("correct_answer", "")).strip().upper()
        if correct not in OPTION_LETTERS:
            dropped.append(f"{label}: correct_answer not one of A-D")
            continue

        difficulty = str(item.get("difficulty", "")).strip().lower()
        if difficulty not in DIFFICULTIES:
            dropped.append(f"{label}: unknown difficulty {difficulty!r}")
            continue

        seen_texts.add(key)
        valid.append(
            GeneratedQuestion(
                question_text=text,
                options=dict(zip(OPTION_LETTERS, option_values, strict=True)),
                correct_answer=correct,
                difficulty=difficulty,
                explanation=str(item.get("explanation", "")).strip(),
                source_chunk_ids=sorted(set(cited)),
            )
        )

    if not valid:
        raise UngroundedGenerationError(
            "No generated question survived validation: " + "; ".join(dropped)
        )

    if expected_difficulties:
        valid = _align_to_mix(valid, expected_difficulties)

    return valid, dropped


def _align_to_mix(
    questions: list[GeneratedQuestion], expected: list[str]
) -> list[GeneratedQuestion]:
    """Order the surviving questions to follow the requested difficulty mix.

    The mix is the adaptive contract; if the model over-produced one band we
    keep the questions but present them in easy->medium->hard order so the
    quiz still reads as the intended progression.
    """
    by_difficulty: dict[str, list[GeneratedQuestion]] = {d: [] for d in DIFFICULTIES}
    for question in questions:
        by_difficulty[question.difficulty].append(question)

    ordered: list[GeneratedQuestion] = []
    leftovers: list[GeneratedQuestion] = []
    for difficulty in expected:
        bucket = by_difficulty[difficulty]
        if bucket:
            ordered.append(bucket.pop(0))
    for difficulty in DIFFICULTIES:
        leftovers.extend(by_difficulty[difficulty])
    return ordered + leftovers[: max(0, len(questions) - len(ordered))]


async def generate_questions(
    chunks: list[RetrievedChunk],
    *,
    subject: str,
    grade: int,
    chapter: str,
    difficulties: list[str],
    client: LLMClient | None = None,
) -> GenerationResult:
    """Generate and validate a grounded question set for one topic."""
    if not chunks:
        # Callers are expected to have raised ContentNotAvailableError already;
        # this is a belt-and-braces guard so no code path can prompt with an
        # empty context.
        raise UngroundedGenerationError(
            "generate_questions called with no retrieved chunks."
        )

    client = client or get_llm_client()
    prompt = build_prompt(
        chunks, subject=subject, grade=grade, chapter=chapter, difficulties=difficulties
    )
    context = {
        "chunks": [{"id": c.id, "content": c.content} for c in chunks],
        "difficulties": difficulties,
        "subject": subject,
        "grade": grade,
        "chapter": chapter,
    }

    raw = await client.generate_questions(prompt, context=context)
    questions, dropped = validate_generated_questions(
        raw,
        allowed_chunk_ids={c.id for c in chunks},
        expected_difficulties=difficulties,
    )
    if dropped:
        logger.warning("Dropped %d generated question(s): %s", len(dropped), dropped)

    return GenerationResult(questions=questions, provider=client.name, dropped=dropped)
