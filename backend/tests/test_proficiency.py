"""Spec 4.2: the adaptive logic is deterministic and explainable.

A scripted answer sequence must produce exactly these scores and exactly this
next-quiz difficulty selection. Values are hand-derived from the formula in
`app/services/proficiency.py`, not copied from a previous run.
"""

from __future__ import annotations

import pytest

from app.services.proficiency import (
    ALPHA,
    INITIAL_SCORE,
    ProficiencyState,
    difficulty_mix,
    difficulty_sequence,
    replay,
    score_to_band,
    update_score,
)

# (difficulty, is_correct) -> expected score after that answer, expected band.
#
#  start 0.5000
#  1. medium correct  : 0.5000 + 0.30*(0.80 - 0.5000) = 0.5900
#  2. medium correct  : 0.5900 + 0.30*(0.80 - 0.5900) = 0.6530
#  3. hard   correct  : 0.6530 + 0.30*(1.00 - 0.6530) = 0.7571
#  4. hard   incorrect: 0.7571 + 0.30*(0.40 - 0.7571) = 0.6500
#  5. medium incorrect: 0.6500 + 0.30*(0.20 - 0.6500) = 0.5150
#  6. easy   incorrect: 0.5150 + 0.30*(0.00 - 0.5150) = 0.3605
SCRIPTED_SEQUENCE: list[tuple[str, bool, float, str]] = [
    ("medium", True, 0.5900, "medium"),
    ("medium", True, 0.6530, "medium"),
    ("hard", True, 0.7571, "hard"),
    ("hard", False, 0.6500, "medium"),
    ("medium", False, 0.5150, "medium"),
    ("easy", False, 0.3605, "easy"),
]


def test_constants_are_the_documented_ones():
    assert ALPHA == 0.30
    assert INITIAL_SCORE == 0.50


def test_scripted_sequence_produces_exact_scores_and_bands():
    state = ProficiencyState()
    assert state.score == INITIAL_SCORE
    assert state.answers_count == 0

    for step, (difficulty, is_correct, expected_score, expected_band) in enumerate(
        SCRIPTED_SEQUENCE, start=1
    ):
        state = state.apply(difficulty, is_correct)
        assert state.score == expected_score, f"score diverged at step {step}"
        assert state.band == expected_band, f"band diverged at step {step}"
        assert state.answers_count == step


def test_replay_matches_step_by_step_application():
    answers = [(d, c) for d, c, _, _ in SCRIPTED_SEQUENCE]
    assert replay(answers).score == SCRIPTED_SEQUENCE[-1][2]


def test_next_quiz_difficulty_after_scripted_sequence():
    """The whole point: the sequence above must land on an *easy* next quiz."""
    final_score = replay([(d, c) for d, c, _, _ in SCRIPTED_SEQUENCE]).score
    assert final_score == 0.3605
    assert score_to_band(final_score) == "easy"
    assert difficulty_mix(final_score, 5) == {"easy": 3, "medium": 2, "hard": 0}
    assert difficulty_sequence(final_score, 5) == [
        "easy",
        "easy",
        "easy",
        "medium",
        "medium",
    ]


def test_update_is_a_pure_function():
    """Same inputs, same output, and the caller's value is never mutated."""
    assert update_score(0.5, "medium", True) == update_score(0.5, "medium", True)
    assert update_score(0.5, "medium", True) == 0.59


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0.0, "easy"),
        (0.3999, "easy"),
        (0.40, "medium"),  # boundary is inclusive-low
        (0.6999, "medium"),
        (0.70, "hard"),
        (1.0, "hard"),
    ],
)
def test_band_boundaries(score: float, expected: str):
    assert score_to_band(score) == expected


def test_score_is_clamped_to_unit_interval():
    perfect = replay([("hard", True)] * 50)
    assert perfect.score <= 1.0
    hopeless = replay([("easy", False)] * 50)
    assert hopeless.score >= 0.0


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 6, 7, 10, 20])
@pytest.mark.parametrize("score", [0.0, 0.2, 0.39, 0.4, 0.55, 0.69, 0.7, 0.9, 1.0])
def test_mix_always_allocates_exactly_n_questions(score: float, n: int):
    mix = difficulty_mix(score, n)
    assert sum(mix.values()) == n
    assert all(count >= 0 for count in mix.values())


@pytest.mark.parametrize(
    ("score", "n", "expected"),
    [
        (0.20, 5, {"easy": 3, "medium": 2, "hard": 0}),
        (0.50, 5, {"easy": 1, "medium": 3, "hard": 1}),
        (0.85, 5, {"easy": 0, "medium": 2, "hard": 3}),
        # Largest-remainder allocation, ties broken easy -> medium -> hard.
        (0.50, 4, {"easy": 1, "medium": 2, "hard": 1}),
        (0.85, 3, {"easy": 0, "medium": 1, "hard": 2}),
    ],
)
def test_mix_is_the_documented_distribution(score: float, n: int, expected: dict):
    assert difficulty_mix(score, n) == expected


def test_mix_is_deterministic_across_repeated_calls():
    first = [difficulty_mix(0.5, 4) for _ in range(20)]
    assert all(m == first[0] for m in first)


def test_a_stronger_student_never_gets_an_easier_mix():
    """Monotonicity: more hard questions as the score rises, never fewer."""
    hard_counts = [difficulty_mix(s / 100, 5)["hard"] for s in range(0, 101)]
    assert hard_counts == sorted(hard_counts)


def test_unknown_difficulty_is_rejected():
    with pytest.raises(ValueError):
        update_score(0.5, "impossible", True)
