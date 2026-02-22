"""
LLM Chess Match - Console entry point.

Run a chess game between two LLMs. Prompts for LLM selection, retry config,
then runs the game and displays the outcome.
"""

from __future__ import annotations

import sys

# Load .env file so API keys work when run from IDE or different shell
import dotenv

dotenv.load_dotenv()

import chess

from src.chess_engine import ChessEngine
from src.game_loop import GameResult, run_game
from src.llm_adapters import get_available_adapters
from src.response_parser import format_for_display


def _format_board(fen: str) -> str:
    """Format a FEN position as a readable ASCII board with labels."""
    engine = ChessEngine(fen)
    board = engine.board
    lines = ["  a b c d e f g h", "  " + "-" * 17]
    for rank in range(7, -1, -1):
        row = [str(rank + 1)]
        for file in range(8):
            sq = chess.square(file, rank)
            piece = board.piece_at(sq)
            row.append(piece.symbol() if piece else ".")
        lines.append(" ".join(row))
    lines.append("  " + "-" * 17)
    return "\n".join(lines)


def _select_llm(prompt: str, exclude_id: str | None = None):
    """Prompt user to select an LLM by number. Returns the adapter or None."""
    adapters = get_available_adapters()
    options = [a for a in adapters if a.id != exclude_id]

    if not options:
        print("No LLMs available.")
        return None

    print(f"\n{prompt}")
    for i, adapter in enumerate(options, 1):
        print(f"  {i}. {adapter.name}")

    while True:
        try:
            choice = input("Choice (number): ").strip()
            if not choice:
                return None
            idx = int(choice)
            if 1 <= idx <= len(options):
                return options[idx - 1]
        except ValueError:
            pass
        print("Invalid choice. Try again.")


def _prompt_retries() -> int:
    """Prompt for max retries per illegal move. Returns default if invalid."""
    default = 3
    try:
        s = input(f"Max retries per illegal move [{default}]: ").strip()
        if not s:
            return default
        n = int(s)
        if n >= 0:
            return n
    except ValueError:
        pass
    print(f"Using default: {default}")
    return default


def _prompt_timer() -> float | None:
    """Prompt for time per player in seconds. 0 = no limit."""
    default = 300  # 5 minutes
    try:
        s = input(
            f"Time per player in seconds (0 = no limit) [{default}]: "
        ).strip()
        if not s:
            return default
        n = float(s)
        if n <= 0:
            return None
        return n
    except ValueError:
        pass
    print(f"Using default: {default} seconds")
    return default


def _format_time(seconds: float) -> str:
    """Format seconds as M:SS."""
    if seconds == float("inf") or seconds < 0:
        return "∞"
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m}:{s:02d}"


def _format_move_history(moves: list[str]) -> str:
    """Format move history as PGN-style (e.g. '1. e4 e5 2. Nf3')."""
    if not moves:
        return "(none)"
    result = []
    for i in range(0, len(moves), 2):
        num = (i // 2) + 1
        w = moves[i] if i < len(moves) else ""
        b = moves[i + 1] if i + 1 < len(moves) else ""
        if b:
            result.append(f"{num}. {w} {b}")
        else:
            result.append(f"{num}. {w}")
    return " ".join(result)


def _print_result(result: GameResult) -> None:
    """Print the game result."""
    print("\n" + "=" * 50)
    print("GAME OVER")
    print("=" * 50)
    print(f"Moves: {_format_move_history(result.move_history)}")
    print(f"Termination: {result.termination_reason or 'unknown'}")

    if result.forfeit_by:
        reason = "Time forfeit" if result.termination_reason == "time" else "Forfeit"
        print(f"{reason} by: {result.forfeit_by}")
        print(f"Winner: {result.winner_name}")

        if result.termination_reason == "forfeit" and result.forfeit_attempts:
            print("\n--- Failed attempts (what the LLM sent and why it was rejected) ---")
            for i, (prompt, response, rejection_reason) in enumerate(
                result.forfeit_attempts, 1
            ):
                print(f"\nAttempt {i}:")
                print("  Prompt sent:")
                for line in prompt.strip().split("\n"):
                    print(f"    {line}")
                print("  LLM response (raw):")
                for line in (response or "(empty)").strip().split("\n"):
                    print(f"    {line}")
                print(f"  Rejected because: {rejection_reason}")
            print("-" * 50)
    elif result.winner_name:
        print(f"Winner: {result.winner_name}")
        if result.loser_name:
            print(f"Loser: {result.loser_name}")
    else:
        print("Result: Draw")


def main() -> int:
    """Run the LLM chess match."""
    print("=" * 50)
    print("LLM Chess Match")
    print("=" * 50)

    white_llm = _select_llm("Select LLM for White:", exclude_id=None)
    if not white_llm:
        print("Aborted.")
        return 1

    black_llm = _select_llm("Select LLM for Black:", exclude_id=white_llm.id)
    if not black_llm:
        print("Aborted.")
        return 1

    max_retries = _prompt_retries()
    time_per_player = _prompt_timer()

    print(f"\nStarting game: {white_llm.name} (White) vs {black_llm.name} (Black)")
    print(f"Max retries per illegal move: {max_retries}")
    if time_per_player:
        print(f"Time per player: {_format_time(time_per_player)} (0 = loss)")
    else:
        print("Time limit: none")
    print("-" * 50)

    # Track position for board display
    engine = ChessEngine()
    move_history: list[str] = []

    def on_move(
        side_name: str,
        llm_name: str,
        move: str,
        is_retry: bool,
        conversation: list[tuple[str, str]],
    ) -> None:
        engine.apply_pgn_move(move)
        move_history.append(move)

        # 1. At the top: LLM full answer (explanation + back-and-forth if retries)
        print("\n" + "-" * 50)
        print("LLM full answer (explanation + chat):")
        for i, (script_prompt, llm_response) in enumerate(conversation, 1):
            if i > 1:
                print()
            print("--- Script ---")
            print(script_prompt.strip())
            print("--- LLM ---")
            print(format_for_display(llm_response))
        print("-" * 50)

        # Move and board
        retry_note = " (after retry)" if is_retry else ""
        print(f"{side_name} ({llm_name}): {move}{retry_note}")
        print(_format_board(engine.fen))

        # 2. At the bottom: move history
        print("Move history:", _format_move_history(move_history))

    def on_time_update(white_remaining: float, black_remaining: float) -> None:
        print(f"Time remaining: White {_format_time(white_remaining)} | Black {_format_time(black_remaining)}")

    try:
        result = run_game(
            white_llm,
            black_llm,
            max_retries=max_retries,
            time_per_player_seconds=time_per_player,
            on_move=on_move,
            on_time_update=on_time_update if time_per_player else None,
        )
    except KeyboardInterrupt:
        print("\n\nGame interrupted by user.")
        return 130
    except Exception as e:
        print(f"\nError: {e}")
        return 1

    _print_result(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
