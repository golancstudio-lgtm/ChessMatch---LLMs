"""
Game loop: alternate turns between two LLMs, validate moves, retry on illegal.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import chess

from .chess_engine import ChessEngine, MoveResult
from .game_state import clear_live_remaining, is_cancelled, reset_state, set_live_remaining, set_state_from_dict, start_ticker_thread, update_state
from .llm_adapters import LLMAdapter
from .prompt_builder import MoveRequest, build_prompts
from .response_parser import parse_llm_response


@dataclass
class GameResult:
    """Result of a completed game."""

    move_history: list[str] = field(default_factory=list)
    outcome: Optional[chess.Outcome] = None
    winner_name: Optional[str] = None  # Name of winning LLM, or None for draw
    loser_name: Optional[str] = None
    termination_reason: Optional[str] = None  # e.g. "checkmate", "stalemate", "draw", "time"
    forfeit_by: Optional[str] = None  # If game ended by forfeit (retries or time)
    forfeit_attempts: list[tuple[str, str, str]] = field(default_factory=list)
    # When forfeit by retries: (user_prompt, llm_response, rejection_reason) for each failed attempt


def _side_name(is_white: bool) -> str:
    return "White" if is_white else "Black"


def _end_time_forfeit(
    result: GameResult,
    move_history: list[str],
    loser_name: str,
    winner_name: str,
    game_id: Optional[str],
) -> GameResult:
    """Record time forfeit, update state, clear live timers, and return result."""
    result.forfeit_by = loser_name
    result.move_history = move_history
    result.termination_reason = "time"
    result.loser_name = loser_name
    result.winner_name = winner_name
    update_state(is_game_over=True, winner=winner_name, termination_reason="time", game_id=game_id)
    clear_live_remaining()
    return result


def run_game(
    white_llm: LLMAdapter,
    black_llm: LLMAdapter,
    max_retries: int = 3,
    time_per_player_seconds: Optional[float] = None,
    on_move: Optional[Callable[[str, str, str, bool, list[tuple[str, str]]], None]] = None,
    on_time_update: Optional[Callable[[float, float], None]] = None,
    starting_fen: Optional[str] = None,
    initial_state_dict: Optional[dict] = None,
    game_id: Optional[str] = None,
) -> GameResult:
    """
    Run a chess game between two LLMs.

    Args:
        white_llm: LLM playing as White.
        black_llm: LLM playing as Black.
        max_retries: Maximum retries per turn (resets each turn). Applies to illegal
            moves, invalid JSON, and unparseable moves.
        time_per_player_seconds: Time limit per player in seconds. None or 0 = no limit.
        on_move: Optional callback(side_name, llm_name, move, is_retry, conversation)
                 for each move. conversation is a list of (script_prompt, llm_response) tuples
                 including any retry exchanges.
        on_time_update: Optional callback(white_remaining, black_remaining) after each move.
        starting_fen: Optional FEN to start from (for debugging). If None, use standard start.
        initial_state_dict: Optional state dict (e.g. from S3) to resume from; used by Lambda game runner.
        game_id: Optional ID for this game; used for per-game state storage (e.g. S3 key).

    Returns:
        GameResult with move history, outcome, and winner/loser names.
    """
    result = GameResult()
    use_timer = time_per_player_seconds is not None and time_per_player_seconds > 0
    white_remaining = time_per_player_seconds if use_timer else float("inf")
    black_remaining = time_per_player_seconds if use_timer else float("inf")

    if initial_state_dict is not None:
        set_state_from_dict(initial_state_dict, game_id)
        fen = initial_state_dict.get("fen") or (starting_fen if starting_fen else None)
        engine = ChessEngine(fen) if fen else ChessEngine()
        move_history = list(initial_state_dict.get("move_history") or [])
    else:
        engine = ChessEngine(starting_fen) if starting_fen else ChessEngine()
        move_history = []
        reset_state(game_id)
        update_state(
            fen=engine.fen,
            move_history=[],
            white_name=white_llm.name,
            black_name=black_llm.name,
            is_game_over=False,
            game_id=game_id,
            white_remaining_seconds=white_remaining if (use_timer and white_remaining != float("inf")) else None,
            black_remaining_seconds=black_remaining if (use_timer and black_remaining != float("inf")) else None,
            white_timer_started=False,
            black_timer_started=False,
        )

    # Each side's clock only starts after their first move
    white_has_moved = len(move_history) >= 1
    black_has_moved = len(move_history) >= 2

    if use_timer:
        side = _side_name(engine.turn) if initial_state_dict is not None else "White"
        side_active = (engine.turn == chess.WHITE and white_has_moved) or (engine.turn == chess.BLACK and black_has_moved)
        set_live_remaining(white_remaining, black_remaining, side, game_id, timer_active=side_active)
        start_ticker_thread()

    while not engine.is_game_over:
        if is_cancelled(game_id):
            clear_live_remaining()
            return result
        is_white = engine.turn == chess.WHITE
        llm = white_llm if is_white else black_llm
        side_name = _side_name(is_white)
        is_first_move = (is_white and not white_has_moved) or (not is_white and not black_has_moved)
        if use_timer:
            set_live_remaining(white_remaining, black_remaining, side_name, game_id, timer_active=not is_first_move)

        # Only keep failed attempts for the current move; clear from previous turn
        result.forfeit_attempts.clear()

        request = MoveRequest(
            fen=engine.fen,
            move_history=move_history.copy(),
            side_to_move=side_name,
            legal_moves=engine.get_legal_moves_san(),
            white_remaining=white_remaining if use_timer else None,
            black_remaining=black_remaining if use_timer else None,
        )

        # Max retries: 0 means unlimited attempts (no forfeit by invalid moves).
        retries_left = max_retries if max_retries > 0 else float("inf")
        move_applied = False
        conversation: list[tuple[str, str]] = []
        rejected_moves_this_turn: list[str] = []

        while not move_applied and retries_left > 0:
            system_prompt, user_prompt = build_prompts(request)

            # Timer: start before API call (skip on first move — clock hasn't started)
            should_time = use_timer and not is_first_move
            if should_time:
                remaining = white_remaining if is_white else black_remaining
                if remaining <= 0:
                    return _end_time_forfeit(
                        result, move_history, llm.name,
                        black_llm.name if is_white else white_llm.name,
                        game_id,
                    )
                start_time = time.perf_counter()

            response = llm.send_prompt(system_prompt, user_prompt)
            conversation.append((user_prompt, response))

            if is_cancelled(game_id):
                result.move_history = move_history
                result.termination_reason = "stopped"
                update_state(is_game_over=True, winner=None, termination_reason="stopped", game_id=game_id)
                clear_live_remaining()
                return result

            # Timer: check elapsed and deduct (only when clock is running)
            if should_time:
                elapsed = time.perf_counter() - start_time
                if is_white:
                    white_remaining -= elapsed
                    if white_remaining <= 0:
                        return _end_time_forfeit(result, move_history, llm.name, black_llm.name, game_id)
                else:
                    black_remaining -= elapsed
                    if black_remaining <= 0:
                        return _end_time_forfeit(result, move_history, llm.name, white_llm.name, game_id)

            parsed = parse_llm_response(response)
            pgn_move = parsed.move

            if pgn_move is None:
                # Invalid JSON or unparseable move - treat as illegal, tell LLM the mistake
                if parsed.error_type == "invalid_json":
                    error_msg = (
                        "Your response was not valid JSON. "
                        'Please reply with exactly: {"move": "e4", "explanation": "..."}'
                    )
                else:
                    error_msg = (
                        "Could not parse your move from your response. "
                        "Please use PGN format (e.g. e4, Nf3, O-O, exd5)."
                    )
                move_result = MoveResult(success=False, error_message=error_msg)
                previous_attempt = "your response (no valid move found)"
                is_parse_error = True
            else:
                move_result = engine.apply_pgn_move(pgn_move)
                previous_attempt = pgn_move
                is_parse_error = False

            if move_result.success:
                canonical_move = move_result.san_move or pgn_move
                move_history.append(canonical_move)
                move_applied = True
                if is_white:
                    white_has_moved = True
                else:
                    black_has_moved = True
                messages = []
                for user_prompt, llm_response in conversation:
                    messages.append({"type": "prompt", "content": user_prompt or ""})
                    messages.append({"type": "response", "content": llm_response or ""})
                chat_entry = {
                    "move": canonical_move,
                    "side": side_name,
                    "llm_name": llm.name,
                    "explanation": (parsed.explanation or "").strip(),
                    "messages": messages,
                }
                update_state(
                    fen=engine.fen,
                    move_history=move_history.copy(),
                    chat_entry=chat_entry,
                    game_id=game_id,
                    white_remaining_seconds=white_remaining if (use_timer and white_remaining != float("inf")) else None,
                    black_remaining_seconds=black_remaining if (use_timer and black_remaining != float("inf")) else None,
                    white_timer_started=white_has_moved if use_timer else None,
                    black_timer_started=black_has_moved if use_timer else None,
                )
                if use_timer:
                    next_side = engine.turn
                    next_active = (next_side == chess.WHITE and white_has_moved) or (next_side == chess.BLACK and black_has_moved)
                    set_live_remaining(white_remaining, black_remaining, _side_name(next_side), game_id, timer_active=next_active)
                if on_move:
                    on_move(side_name, llm.name, canonical_move, request.is_retry, conversation)
                if on_time_update and use_timer:
                    on_time_update(white_remaining, black_remaining)
            else:
                # Record failed attempt for forfeit display
                result.forfeit_attempts.append(
                    (user_prompt, response, move_result.error_message or "Unknown error")
                )
                if previous_attempt and previous_attempt != "your response (no valid move found)":
                    rejected_moves_this_turn.append(previous_attempt)
                if retries_left != float("inf"):
                    retries_left -= 1
                if retries_left != float("inf") and retries_left <= 0:
                    # Forfeit: max retries exceeded
                    result.forfeit_by = llm.name
                    result.move_history = move_history
                    result.termination_reason = "forfeit"
                    result.loser_name = llm.name
                    result.winner_name = black_llm.name if is_white else white_llm.name
                    update_state(is_game_over=True, winner=result.winner_name, termination_reason="forfeit", game_id=game_id)
                    clear_live_remaining()
                    return result

                request = MoveRequest(
                    fen=engine.fen,
                    move_history=move_history.copy(),
                    side_to_move=side_name,
                    legal_moves=engine.get_legal_moves_san(),
                    is_retry=True,
                    error_message=move_result.error_message,
                    previous_attempt=previous_attempt,
                    is_parse_error=is_parse_error,
                    rejected_moves=rejected_moves_this_turn.copy(),
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

    update_state(is_game_over=True, winner=result.winner_name, termination_reason=result.termination_reason, game_id=game_id)
    clear_live_remaining()
    return result
