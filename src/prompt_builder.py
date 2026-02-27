"""
Prompt builder: constructs system and user prompts for each move.

Builds prompts that include the current board state (FEN), move history,
and instructions for the LLM to respond with a single PGN move.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


def _format_time(seconds: float) -> str:
    """Format seconds as M:SS."""
    if seconds == float("inf") or seconds < 0:
        return "∞"
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m}:{s:02d}"


@dataclass
class MoveRequest:
    """Context for building a move request prompt."""

    fen: str
    move_history: list[str]
    side_to_move: str  # "White" or "Black"
    legal_moves: Optional[list[str]] = None  # All legal moves in PGN; LLM must choose one
    is_retry: bool = False
    error_message: Optional[str] = None
    previous_attempt: Optional[str] = None
    is_parse_error: bool = False  # True if invalid JSON or unparseable move (not illegal move)
    rejected_moves: Optional[list[str]] = None  # All rejected move strings this turn (do not repeat)
    white_remaining: Optional[float] = None  # Seconds, None = no timer
    black_remaining: Optional[float] = None


# --- System prompt (same for all moves, varies by side) ---

SYSTEM_PROMPT_TEMPLATE = """You are playing chess as {side}. Your opponent has just moved (or you are making the first move as White).

Choose openings and moves that fit the position. You may use any opening you think is good—not only the most popular or common ones. Vary your play; do not feel obliged to follow the same opening (e.g. Ruy Lopez) every game.

Rules:
- Reply with a JSON object containing exactly two fields: "move" and "explanation".
- "move": exactly ONE move. You must choose from the "Legal moves" list given in the position—use one of those strings exactly.
- "explanation": a brief explanation of why you chose this move.
- Use standard PGN: K=king, Q=queen, R=rook, B=bishop, N=knight. Pawns have no letter (e4, exd5). Castling: O-O (kingside), O-O-O (queenside).

Example response:
{{"move": "e4", "explanation": "I control the center and open lines for my pieces."}}
"""


def build_system_prompt(side_to_move: str) -> str:
    """
    Build the system prompt that defines the LLM's role and output format.

    Args:
        side_to_move: "White" or "Black"

    Returns:
        System prompt string.
    """
    return SYSTEM_PROMPT_TEMPLATE.format(side=side_to_move)


# --- User prompt ---

TIME_SECTION = """
Time remaining: White {white_time}, Black {black_time}. You ({side}) have {your_time} left.
Consider your remaining time and respond quickly to avoid running out of time.
"""

USER_PROMPT_FIRST_MOVE = """Current position (FEN): {fen}
{time_section}
{legal_moves_section}It is White's turn. Make your first move. Reply with JSON: {{"move": "...", "explanation": "..."}}
"""

USER_PROMPT_TEMPLATE = """Current position (FEN): {fen}
{time_section}
Moves played so far: {move_history}

{legal_moves_section}It is {side}'s turn. Make your move. Reply with JSON: {{"move": "...", "explanation": "..."}}
"""

USER_PROMPT_RETRY_ILLEGAL = """Current position (FEN): {fen}
{time_section}
Moves played so far: {move_history}

{legal_moves_section}It is {side}'s turn. Your previous move "{previous_attempt}" was illegal: {error_message}
{rejected_moves_section}

Please choose a move from the legal moves list above. Reply with JSON: {{"move": "...", "explanation": "..."}}
"""

USER_PROMPT_RETRY_PARSE = """Current position (FEN): {fen}
{time_section}
Moves played so far: {move_history}

{legal_moves_section}It is {side}'s turn. Your previous response failed: {error_message}
{rejected_moves_section}

Please choose a move from the legal moves list above. Reply with JSON: {{"move": "...", "explanation": "..."}}
"""


def _rejected_moves_section(rejected: list[str]) -> str:
    """Format rejected moves for retry prompt so the LLM does not repeat them."""
    if not rejected:
        return ""
    return "Rejected moves this turn (do not repeat): " + ", ".join(rejected) + "\n\n"


def _legal_moves_section(legal_moves: Optional[list[str]]) -> str:
    """Format legal moves for the prompt; LLM must choose one. Strips + and # so we don't reveal check/checkmate."""
    if not legal_moves:
        return ""
    # Strip trailing + (check) and # (checkmate) so the LLM isn't told which moves are check/checkmate
    stripped = [m.rstrip("+#") for m in legal_moves]
    # Deduplicate in case two moves differ only by + vs # (shouldn't happen in SAN, but safe)
    unique = sorted(dict.fromkeys(stripped))
    return "Legal moves (you must choose exactly one): " + ", ".join(unique) + "\n\n"


def _format_move_history(moves: list[str]) -> str:
    """Format move history as a readable string (e.g. '1. e4 e5 2. Nf3')."""
    if not moves:
        return "(none)"
    # Group into pairs for White/Black
    result: list[str] = []
    for i in range(0, len(moves), 2):
        move_num = (i // 2) + 1
        white_move = moves[i] if i < len(moves) else ""
        black_move = moves[i + 1] if i + 1 < len(moves) else ""
        if black_move:
            result.append(f"{move_num}. {white_move} {black_move}")
        else:
            result.append(f"{move_num}. {white_move}")
    return " ".join(result)


def _build_time_section(request: MoveRequest) -> str:
    """Build the time section for the prompt, or empty string if no timer."""
    if (
        request.white_remaining is None
        or request.black_remaining is None
        or request.white_remaining == float("inf")
    ):
        return ""
    white_time = _format_time(request.white_remaining)
    black_time = _format_time(request.black_remaining)
    your_time = (
        white_time if request.side_to_move == "White" else black_time
    )
    return TIME_SECTION.format(
        white_time=white_time,
        black_time=black_time,
        side=request.side_to_move,
        your_time=your_time,
    ).strip() + "\n"


def build_user_prompt(request: MoveRequest) -> str:
    """
    Build the user prompt with board state and move request.

    Args:
        request: MoveRequest with FEN, history, side, and optional retry info.

    Returns:
        User prompt string.
    """
    move_history_str = _format_move_history(request.move_history)
    side = request.side_to_move
    time_section = _build_time_section(request)
    legal_moves_section = _legal_moves_section(request.legal_moves)
    rejected_section = _rejected_moves_section(request.rejected_moves or [])

    if request.is_retry and request.error_message:
        if request.is_parse_error:
            return USER_PROMPT_RETRY_PARSE.format(
                fen=request.fen,
                time_section=time_section,
                move_history=move_history_str,
                side=side,
                legal_moves_section=legal_moves_section,
                error_message=request.error_message,
                rejected_moves_section=rejected_section,
            )
        return USER_PROMPT_RETRY_ILLEGAL.format(
            fen=request.fen,
            time_section=time_section,
            move_history=move_history_str,
            side=side,
            legal_moves_section=legal_moves_section,
            previous_attempt=request.previous_attempt or "",
            error_message=request.error_message,
            rejected_moves_section=rejected_section,
        )

    if not request.move_history:
        return USER_PROMPT_FIRST_MOVE.format(
            fen=request.fen,
            time_section=time_section,
            legal_moves_section=legal_moves_section,
        )

    return USER_PROMPT_TEMPLATE.format(
        fen=request.fen,
        time_section=time_section,
        move_history=move_history_str,
        side=side,
        legal_moves_section=legal_moves_section,
    )


def build_prompts(request: MoveRequest) -> tuple[str, str]:
    """
    Build both system and user prompts for a move request.

    Args:
        request: MoveRequest with all context.

    Returns:
        Tuple of (system_prompt, user_prompt).
    """
    system_prompt = build_system_prompt(request.side_to_move)
    user_prompt = build_user_prompt(request)
    return system_prompt, user_prompt
