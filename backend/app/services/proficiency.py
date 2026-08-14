"""Deterministic, explainable proficiency scoring and adaptive difficulty.

Spec Section 4.2. Everything in this module is a pure function of its inputs:
no database, no clock, no randomness. That is what makes the scripted-sequence
test in `tests/test_proficiency.py` able to assert exact values.

THE FORMULA, in one sentence
---------------------------
Per-topic proficiency is an exponentially-weighted moving average in [0, 1]
that starts at 0.50 and, after every answer, moves 30% of the way toward a
target set by the question's difficulty and correctness:

    score <- round(score + ALPHA * (target - score), 4)

where ALPHA = 0.30 and `target` comes from the credit table below.

Why difficulty-weighted targets: a correct answer on an easy question is weak
evidence of mastery (target 0.60, so it can never pull a strong student up
past 0.60), while a correct answer on a hard question is strong evidence
(target 1.00). Symmetrically, missing an easy question is strong negative
evidence (target 0.00) and missing a hard one is mild (target 0.40). The EWMA
gives recency weighting for free -- the most recent answer always contributes
30% of the new score -- without needing to store answer history.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

# --- Constants (all of the tunable behaviour lives here) --------------------

ALPHA = 0.30
INITIAL_SCORE = 0.50
SCORE_PRECISION = 4

DIFFICULTIES: tuple[str, ...] = ("easy", "medium", "hard")

#: target = ANSWER_CREDIT[difficulty][is_correct]
ANSWER_CREDIT: dict[str, dict[bool, float]] = {
    "easy": {True: 0.60, False: 0.00},
    "medium": {True: 0.80, False: 0.20},
    "hard": {True: 1.00, False: 0.40},
}

#: Band thresholds. score < 0.40 -> easy, < 0.70 -> medium, else hard.
BAND_THRESHOLDS: tuple[tuple[float, str], ...] = ((0.40, "easy"), (0.70, "medium"))

#: The difficulty *mix* each band asks for, as integer weights.
#: A quiz of N questions allocates N across these weights (see `difficulty_mix`).
BAND_WEIGHTS: dict[str, dict[str, int]] = {
    "easy": {"easy": 3, "medium": 2, "hard": 0},
    "medium": {"easy": 1, "medium": 3, "hard": 1},
    "hard": {"easy": 0, "medium": 2, "hard": 3},
}


def _round(value: float) -> float:
    """Half-up rounding to SCORE_PRECISION.

    Python's built-in `round` is banker's rounding, which would make the
    scripted-sequence test's expected values depend on floating-point parity.
    """
    quant = Decimal(1).scaleb(-SCORE_PRECISION)
    return float(Decimal(repr(value)).quantize(quant, rounding=ROUND_HALF_UP))


def update_score(current_score: float, difficulty: str, is_correct: bool) -> float:
    """Apply one answer to a proficiency score. Pure function.

    >>> update_score(0.50, "medium", True)
    0.59
    """
    if difficulty not in ANSWER_CREDIT:
        raise ValueError(f"unknown difficulty: {difficulty!r}")
    target = ANSWER_CREDIT[difficulty][bool(is_correct)]
    updated = current_score + ALPHA * (target - current_score)
    return _round(min(1.0, max(0.0, updated)))


def score_to_band(score: float) -> str:
    """Map a proficiency score to the difficulty band that drives the next quiz."""
    for threshold, band in BAND_THRESHOLDS:
        if score < threshold:
            return band
    return "hard"


def difficulty_mix(score: float, n_questions: int) -> dict[str, int]:
    """Deterministic difficulty distribution for the next quiz.

    Allocates `n_questions` across the band's integer weights using the
    largest-remainder method, with ties broken in the fixed order
    easy -> medium -> hard. Deterministic for any (score, n_questions).
    """
    if n_questions <= 0:
        raise ValueError("n_questions must be positive")

    weights = BAND_WEIGHTS[score_to_band(score)]
    total_weight = sum(weights.values())

    exact = {d: n_questions * weights[d] / total_weight for d in DIFFICULTIES}
    allocation = {d: int(exact[d]) for d in DIFFICULTIES}

    remaining = n_questions - sum(allocation.values())
    # Largest remainder first; ties broken by DIFFICULTIES order (index as the
    # tie-break key keeps this stable and independent of dict iteration order).
    order = sorted(
        DIFFICULTIES,
        key=lambda d: (-(exact[d] - allocation[d]), DIFFICULTIES.index(d)),
    )
    for d in order[:remaining]:
        allocation[d] += 1

    return allocation


def difficulty_sequence(score: float, n_questions: int) -> list[str]:
    """The mix flattened into a fixed easy -> medium -> hard question order."""
    mix = difficulty_mix(score, n_questions)
    return [d for d in DIFFICULTIES for _ in range(mix[d])]


@dataclass(frozen=True)
class ProficiencyState:
    score: float = INITIAL_SCORE
    answers_count: int = 0

    def apply(self, difficulty: str, is_correct: bool) -> ProficiencyState:
        return ProficiencyState(
            score=update_score(self.score, difficulty, is_correct),
            answers_count=self.answers_count + 1,
        )

    @property
    def band(self) -> str:
        return score_to_band(self.score)


def replay(answers: list[tuple[str, bool]], start: float = INITIAL_SCORE) -> ProficiencyState:
    """Replay a scripted (difficulty, is_correct) sequence. Used by tests and
    by `GET /users/{id}/proficiency?explain=true` to show the working."""
    state = ProficiencyState(score=start, answers_count=0)
    for difficulty, is_correct in answers:
        state = state.apply(difficulty, is_correct)
    return state


def explain(score: float, n_questions: int) -> dict:
    """Human-readable trace of the adaptive decision, for the UI and for
    tracing the logic live in an interview."""
    band = score_to_band(score)
    return {
        "score": score,
        "band": band,
        "rule": (
            f"score < {BAND_THRESHOLDS[0][0]} -> easy; "
            f"< {BAND_THRESHOLDS[1][0]} -> medium; else hard"
        ),
        "mix": difficulty_mix(score, n_questions),
        "alpha": ALPHA,
    }
