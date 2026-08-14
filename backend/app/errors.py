"""Domain exceptions.

`ContentNotAvailableError` is the load-bearing one: it is what makes the
"no ingested content" path fail loudly instead of silently falling back to
ungrounded generation (spec Section 8, negative test).
"""


class EduGenError(Exception):
    """Base class for domain errors."""


class ContentNotAvailableError(EduGenError):
    """Not enough retrieved curriculum content to ground a quiz."""

    def __init__(self, topic: str, found: int, required: int) -> None:
        self.topic = topic
        self.found = found
        self.required = required
        super().__init__(
            f"No grounded quiz can be generated for topic '{topic}': found "
            f"{found} indexed content chunk(s), need at least {required}. "
            "Refusing to generate from the model's general knowledge."
        )


class UngroundedGenerationError(EduGenError):
    """The model returned questions that do not trace to the retrieved chunks."""


class LLMUnavailableError(EduGenError):
    """The configured LLM provider is missing credentials or unreachable."""
