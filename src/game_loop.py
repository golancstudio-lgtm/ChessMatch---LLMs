"""
Game loop: alternate turns between two LLMs, validate moves, retry on illegal.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import chess

from .chess_engine import ChessEngine, MoveResult
from .llm_adapters import LLMAdapter
from .prompt_builder import MoveRequest, build_prompts
from .response_parser import extract_pgn_move


@dataclass
class GameResult:
    """Result of a completed game."""

    move_history: list[str] = field(default_factory=list)
    outcome: Optional[chess.Outcome] = None
    winner_name: Optional[str] = None  # Name of winning LLM, or None for draw
    loser_name: Optional[str] = None
    termination_reason: Optional[str] = None  # e.g. "checkmate", "stalemate", "draw", "time"
    forfeit_by: Optional[str] = None  # If game ended by forfeit (retries or time)


def _side_name(is_white: bool) -> str:
    return "White" if is_white else "Black"


def run_game(
    white_llm: LLMAdapter,
    black_llm: LLMAdapter,
    max_retries: int = 3,
    time_per_player_seconds: Optional[float] = None,
    on_move: Optional[Callable[[str, str, str, bool, list[tuple[str, str]]], None]] = None,
    on_time_update: Optional[Callable[[float, float], None]] = None,
) -> GameResult:
    """
    Run a chess game between two LLMs.

    Args:
        white_llm: LLM playing as White.
        black_llm: LLM playing as Black.
        max_retries: Maximum retries per move when the LLM makes an illegal move.
        time_per_player_seconds: Time limit per player in seconds. None or 0 = no limit.
        on_move: Optional callback(side_name, llm_name, move, is_retry, conversation)
                 for each move. conversation is a list of (script_prompt, llm_response) tuples
                 including any retry exchanges.
        on_time_update: Optional callback(white_remaining, black_remaining) after each move.

    Returns:
        GameResult with move history, outcome, and winner/loser names.
    """
    engine = ChessEngine()
    move_history: list[str] = []
    result = GameResult()

    use_timer = time_per_player_seconds is not None and time_per_player_seconds > 0
    white_remaining = time_per_player_seconds if use_timer else float("inf")
    black_remaining = time_per_player_seconds if use_timer else float("inf")

    while not engine.is_game_over:
        is_white = engine.turn == chess.WHITE
        llm = white_llm if is_white else black_llm
        side_name = _side_name(is_white)

        request = MoveRequest(
            fen=engine.fen,
            move_history=move_history.copy(),
            side_to_move=side_name,
            white_remaining=white_remaining if use_timer else None,
            black_remaining=black_remaining if use_timer else None,
        )

        retries_left = max_retries
        move_applied = False
        conversation: list[tuple[str, str]] = []

        while not move_applied and retries_left >= 0:
            system_prompt, user_prompt = build_prompts(request)

            # Timer: start before API call
            if use_timer:
                remaining = white_remaining if is_white else black_remaining
                if remaining <= 0:
                    result.forfeit_by = llm.name
                    result.move_history = move_history
                    result.termination_reason = "time"
                    result.loser_name = llm.name
                    result.winner_name = black_llm.name if is_white else white_llm.name
                    return result
                start_time = time.perf_counter()

            response = llm.send_prompt(system_prompt, user_prompt)
            conversation.append((user_prompt, response))

            # Timer: check elapsed and deduct
            if use_timer:
                elapsed = time.perf_counter() - start_time
                if is_white:
                    white_remaining -= elapsed
                    if white_remaining <= 0:
                        result.forfeit_by = llm.name
                        result.move_history = move_history
                        result.termination_reason = "time"
                        result.loser_name = llm.name
                        result.winner_name = black_llm.name
                        return result
                else:
                    black_remaining -= elapsed
                    if black_remaining <= 0:
                        result.forfeit_by = llm.name
                        result.move_history = move_history
                        result.termination_reason = "time"
                        result.loser_name = llm.name
                        result.winner_name = white_llm.name
                        return result

            pgn_move = extract_pgn_move(response)

            if pgn_move is None:
                # No parseable move - treat as illegal
                move_result = MoveResult(
                    success=False,
                    error_message="Could not parse your response. "
                    'Reply with JSON: {"move": "e4", "explanation": "..."}',
                )
                previous_attempt = "your response (no valid PGN move found)"
            else:
                move_result = engine.apply_pgn_move(pgn_move)
                previous_attempt = pgn_move

            if move_result.success:
                move_history.append(pgn_move)
                move_applied = True
                if on_move:
                    on_move(side_name, llm.name, pgn_move, request.is_retry, conversation)
                if on_time_update and use_timer:
                    on_time_update(white_remaining, black_remaining)
            else:
                retries_left -= 1
                if retries_left < 0:
                    # Forfeit: max retries exceeded
                    result.forfeit_by = llm.name
                    result.move_history = move_history
                    result.termination_reason = "forfeit"
                    result.loser_name = llm.name
                    result.winner_name = black_llm.name if is_white else white_llm.name
                    return result

                request = MoveRequest(
                    fen=engine.fen,
                    move_history=move_history.copy(),
                    side_to_move=side_name,
                    is_retry=True,
                    error_message=move_result.error_message,
                    previous_attempt=previous_attempt,
                    white_remaining=white_remaining if use_timer else None,
                    black_remaining=black_remaining if use_timer else None,
                )

    # Game over
    outcome = engine.outcome
    result.move_history = move_history
    result.outcome = outcome

    if outcome:
        result.termination_reason = outcome.termination.name.lower()
        if outcome.winner is not None:
            result.winner_name = white_llm.name if outcome.winner else black_llm.name
            result.loser_name = black_llm.name if outcome.winner else white_llm.name
        # outcome.winner is None for draws (stalemate, etc.)

    return result
