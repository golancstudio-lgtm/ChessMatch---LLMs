"""
Chess engine layer: FEN handling, move validation, PGN parsing.

Uses python-chess for all chess logic. Supports PGN (Standard Algebraic Notation) only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import chess
from chess import Board, Move


@dataclass
class MoveResult:
    """Result of attempting to apply a move."""

    success: bool
    new_fen: Optional[str] = None
    error_message: Optional[str] = None


class ChessEngine:
    """
    Wraps python-chess for FEN handling, move validation, and PGN parsing.
    """

    def __init__(self, fen: Optional[str] = None) -> None:
        """
        Initialize the board. If fen is None, uses the standard starting position.
        """
        self._board = Board(fen) if fen else Board()

    @property
    def board(self) -> Board:
        """Access the underlying chess.Board for display or inspection."""
        return self._board

    @property
    def fen(self) -> str:
        """Current position in FEN format."""
        return self._board.fen()

    @property
    def is_game_over(self) -> bool:
        """True if the game has ended (checkmate, stalemate, draw)."""
        return self._board.is_game_over()

    @property
    def outcome(self) -> Optional[chess.Outcome]:
        """The game outcome if over, else None."""
        return self._board.outcome()

    @property
    def turn(self) -> chess.Color:
        """Whose turn it is (chess.WHITE or chess.BLACK)."""
        return self._board.turn

    def apply_pgn_move(self, pgn_move: str) -> MoveResult:
        """
        Parse a PGN (Standard Algebraic Notation) move and apply it if legal.

        Args:
            pgn_move: Move string in PGN format (e.g. "e4", "Nf3", "O-O", "exd5")

        Returns:
            MoveResult with success=True and new_fen if legal,
            or success=False and error_message if illegal.
        """
        pgn_clean = pgn_move.strip()
        if not pgn_clean:
            return MoveResult(
                success=False,
                error_message="Empty move received.",
            )

        try:
            move: Move = self._board.parse_san(pgn_clean)
        except chess.InvalidMoveError as e:
            return MoveResult(
                success=False,
                error_message=f"Invalid move: {e}",
            )
        except chess.AmbiguousMoveError as e:
            return MoveResult(
                success=False,
                error_message=f"Ambiguous move (specify disambiguation): {e}",
            )
        except chess.IllegalMoveError as e:
            return MoveResult(
                success=False,
                error_message=f"Illegal move: {e}",
            )
        except ValueError as e:
            return MoveResult(
                success=False,
                error_message=f"Could not parse move '{pgn_clean}': {e}",
            )

        self._board.push(move)
        return MoveResult(success=True, new_fen=self._board.fen())

    def get_legal_moves_san(self) -> list[str]:
        """Return all legal moves in PGN format for the current position."""
        return [self._board.san(m) for m in self._board.legal_moves]

    def reset(self, fen: Optional[str] = None) -> None:
        """Reset the board to the starting position or the given FEN."""
        self._board = Board(fen) if fen else Board()
