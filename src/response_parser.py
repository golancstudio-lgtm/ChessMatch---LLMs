"""
Response parser: extract move and explanation from LLM reply.

Expects JSON format: {"move": "e4", "explanation": "..."}
Falls back to regex parsing if JSON is invalid (for backward compatibility).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class ParsedResponse:
    """Parsed LLM response with move and explanation."""

    move: Optional[str]
    explanation: str


# PGN move patterns (fallback when JSON fails)
_CASTLING_PATTERN = re.compile(
    r"\b(O-O-O|0-0-0|O-O|0-0)(?=[^\w]|$)",
    re.IGNORECASE,
)
_PIECE_PATTERN = re.compile(
    r"\b([KQRBN][a-h]?[1-8]?x?[a-h][1-8][+#]?)(?=[^\w]|$)",
    re.IGNORECASE,
)
_PAWN_PATTERN = re.compile(
    r"\b([a-h](?:x[a-h])?[1-8](=[QRBN])?[+#]?)(?=[^\w]|$)",
    re.IGNORECASE,
)
_PGN_PATTERNS = [_CASTLING_PATTERN, _PIECE_PATTERN, _PAWN_PATTERN]


def _extract_json(text: str) -> Optional[dict]:
    """Extract JSON from text, handling markdown code blocks."""
    s = text.strip()
    # Remove markdown code blocks: ```json ... ``` or ``` ... ```
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", s)
    if match:
        s = match.group(1).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None


def _extract_move_regex(text: str) -> Optional[str]:
    """Fallback: extract PGN move using regex (last match by position)."""
    if not text or not text.strip():
        return None
    s = re.sub(r"^```\w*\s*", "", text.strip())
    s = re.sub(r"\s*```\s*$", "", s)
    s = s.strip()

    last_match = None
    last_end = -1
    for pattern in _PGN_PATTERNS:
        for m in pattern.finditer(s):
            if m.end() > last_end:
                last_end = m.end()
                last_match = m

    if last_match:
        move = last_match.group(1).strip()
        if move.upper().startswith("0-0"):
            move = move.replace("0", "O")
        return move
    return None


def parse_llm_response(response: str) -> ParsedResponse:
    """
    Parse LLM response. Prefers JSON format; falls back to regex for move.

    Args:
        response: Raw text response from the LLM.

    Returns:
        ParsedResponse with move (or None if not found) and explanation.
    """
    if not response or not response.strip():
        return ParsedResponse(move=None, explanation="")

    data = _extract_json(response)
    if data and isinstance(data, dict):
        move = data.get("move")
        explanation = data.get("explanation", "")
        if isinstance(move, str) and move.strip():
            move = move.strip()
            if move.upper().startswith("0-0"):
                move = move.replace("0", "O")
            return ParsedResponse(move=move, explanation=str(explanation or "").strip())
        # JSON has move field but empty/invalid - try to get from explanation
        move = _extract_move_regex(str(explanation)) or _extract_move_regex(response)
        return ParsedResponse(move=move, explanation=str(explanation or "").strip())

    # Fallback: regex
    move = _extract_move_regex(response)
    return ParsedResponse(move=move, explanation="")


def extract_pgn_move(response: str) -> Optional[str]:
    """
    Extract the PGN move from the LLM's response.

    Uses parse_llm_response for JSON parsing; returns move only.
    """
    parsed = parse_llm_response(response)
    return parsed.move


def format_for_display(response: str) -> str:
    """
    Format LLM response for console display. Parses JSON and shows
    explanation + move in readable format; does not print raw JSON.
    """
    parsed = parse_llm_response(response)
    if parsed.explanation and parsed.move:
        return f"{parsed.explanation}\n\n{parsed.move}"
    if parsed.explanation:
        return parsed.explanation
    if parsed.move:
        return parsed.move
    # Fallback: show raw response (e.g. malformed JSON)
    return response.strip() or "(empty)"
