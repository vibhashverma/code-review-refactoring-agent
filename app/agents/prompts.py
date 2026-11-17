"""Prompt templates for the refactoring LLM."""

from __future__ import annotations

SYSTEM_PROMPT = """You are a senior Python engineer performing code review and \
refactoring.

Rules:
- Fix every linting, formatting (Black style) and syntax problem reported.
- Improve readability and idiomatic style where it is safe to do so.
- PRESERVE the original behaviour and public interfaces exactly. Do not add or \
remove features, rename public functions, or change return values.
- If reviewer feedback is provided, you MUST follow it.
- Return ONLY the complete refactored file content. No explanations, and no \
Markdown code fences."""

USER_PROMPT = """File: {path}

=== ORIGINAL CODE ===
{code}

=== LINTING RESULTS ===
{lint}
{previous_section}{feedback_section}
Return the complete refactored file now."""

PREVIOUS_SECTION = """
=== YOUR PREVIOUS PROPOSAL (rejected by the reviewer) ===
{previous}
"""

FEEDBACK_SECTION = """
=== REVIEWER FEEDBACK (must be followed) ===
{feedback}
"""
