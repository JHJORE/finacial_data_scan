"""Shared helpers for the screener pipeline."""

import random


def extract_token_usage(response) -> tuple[int, int]:
    """Extract (input_tokens, output_tokens) from a Gemini response.

    Input  = prompt_token_count + tool_use_prompt_token_count
    Output = candidates_token_count + thoughts_token_count
    """
    usage = getattr(response, "usage_metadata", None)
    if not usage:
        return 0, 0

    input_tokens = (
        (getattr(usage, "prompt_token_count", 0) or 0)
        + (getattr(usage, "tool_use_prompt_token_count", 0) or 0)
    )
    output_tokens = (
        (getattr(usage, "candidates_token_count", 0) or 0)
        + (getattr(usage, "thoughts_token_count", 0) or 0)
    )
    return input_tokens, output_tokens


def is_retryable(error_str: str) -> bool:
    """Check whether a Gemini API error is transient and worth retrying.

    Includes 400/INVALID_ARGUMENT because Gemini returns this intermittently
    for url_context calls that succeed on retry (e.g. same SEC URL works for
    Apple but fails transiently for Accenture).
    """
    return any(k in error_str for k in (
        "429", "RESOURCE_EXHAUSTED",
        "400", "INVALID_ARGUMENT",
        "500", "503", "UNAVAILABLE",
    ))


def backoff(attempt: int) -> float:
    """Exponential backoff with jitter: ~10s, ~20s, ~40s, ~80s."""
    base = 2 ** (attempt + 1) * 5
    return base + random.uniform(0, base * 0.3)
