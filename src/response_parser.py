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
    error_type: Optional[str] = None  # "invalid_json" or "no_move" when move is None


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


# Pattern to find "move": "X" or "move": 'X' in JSON (prefer last occurrence = LLM's final answer)
_JSON_MOVE_PATTERN = re.compile(
    r'"move"\s*:\s*"([^"]+)"',
    re.IGNORECASE,
)


def _extract_json(text: str) -> Optional[dict]:
    """Extract JSON from text, handling markdown code blocks."""
    s = text.strip()
    # Try last code block first (LLMs often put final JSON at the end)
    code_blocks = list(re.finditer(r"```(?:json)?\s*([\s\S]*?)\s*```", s))
    if code_blocks:
        for match in reversed(code_blocks):
            block = match.group(1).strip()
            try:
                data = json.loads(block)
                if isinstance(data, dict) and "move" in data:
                    return data
            except json.JSONDecodeError:
                continue
    # Try parsing the whole string
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # Try to find a JSON object at the end (after last newline or after last })
    for tail in (s, s[s.rfind("}"):] if "}" in s else ""):
        if not tail:
            continue
        try:
            data = json.loads(tail)
            if isinstance(data, dict) and "move" in data:
                return data
        except json.JSONDecodeError:
            continue
    return None


def _extract_move_from_json_literal(response: str) -> Optional[str]:
    """Find the last \"move\": \"...\" in the response (LLM's intended move)."""
    matches = list(_JSON_MOVE_PATTERN.finditer(response))
    if not matches:
        return None
    move = matches[-1].group(1).strip()
    if move.upper().startswith("0-0"):
        move = move.replace("0", "O")
    return move if move else None


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
        ParsedResponse with move (or None if not found), explanation, and
        error_type ("invalid_json" or "no_move") when move is None.
    """
    if not response or not response.strip():
        return ParsedResponse(move=None, explanation="", error_type="no_move")

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
        if move:
            return ParsedResponse(move=move, explanation=str(explanation or "").strip())
        return ParsedResponse(
            move=None,
            explanation=str(explanation or "").strip(),
            error_type="no_move",
        )

    # Fallback: prefer last "move": "X" in text (avoids taking a move from prose)
    move = _extract_move_from_json_literal(response)
    if move:
        return ParsedResponse(move=move, explanation="")
    # Then try last PGN-like token in text
    move = _extract_move_regex(response)
    if move:
        return ParsedResponse(move=move, explanation="")
    return ParsedResponse(move=None, explanation="", error_type="invalid_json")


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
