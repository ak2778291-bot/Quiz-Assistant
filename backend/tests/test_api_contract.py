"""API surface checks that need no database.

Catches contract regressions (a leaked answer key, a dropped route) at import
time rather than at runtime.
"""

from __future__ import annotations

from app.main import app

EXPECTED_ROUTES = {
    ("POST", "/auth/register"),
    ("POST", "/auth/login"),
    ("GET", "/auth/me"),
    ("GET", "/subjects"),
    ("POST", "/quizzes"),
    ("GET", "/quizzes/{quiz_id}"),
    ("POST", "/quizzes/{quiz_id}/answer"),
    ("GET", "/quizzes/{quiz_id}/results"),
    ("GET", "/users/{user_id}/proficiency"),
    ("GET", "/cache/stats"),
    ("GET", "/health"),
}


def _schema() -> dict:
    return app.openapi()


def test_all_documented_routes_exist():
    schema = _schema()
    actual = {
        (method.upper(), path)
        for path, operations in schema["paths"].items()
        for method in operations
    }
    assert actual >= EXPECTED_ROUTES


def test_student_facing_question_never_carries_the_answer_key():
    """A quiz in progress must not hand the client the correct answer."""
    properties = _schema()["components"]["schemas"]["QuestionOut"]["properties"]
    assert "correct_answer" not in properties
    assert "source_chunk_ids" in properties, "grounding must stay visible"


def test_results_answer_key_is_nullable_until_answered():
    properties = _schema()["components"]["schemas"]["ResultQuestion"]["properties"]
    types = {entry.get("type") for entry in properties["correct_answer"]["anyOf"]}
    assert types == {"string", "null"}
