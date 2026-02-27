"""
Shared game state for the frontend API.

The game loop (main.py) updates state and writes it to .chess_match_state.json
in the project root. The API (uvicorn) runs in a separate process and reads
that file on each GET /api/state, so the frontend sees live progress.

When STATE_BUCKET (and optionally STATE_KEY) env vars are set, state is also
written to S3 so a deployed frontend (e.g. CloudFront + Lambda reading from S3)
can show live progress when the game runs on a backend server.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

# Standard starting FEN
DEFAULT_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

# Project root: src/game_state.py -> parent.parent
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_STATE_FILE = _PROJECT_ROOT / ".chess_match_state.json"
# In Lambda (STATE_BUCKET set), use /tmp so file writes succeed
if os.environ.get("STATE_BUCKET", "").strip():
    _STATE_FILE = Path("/tmp") / "chess_match_state.json"

@dataclass
class GameState:
    """Current state exposed to the frontend."""

    fen: str = DEFAULT_FEN
    move_history: list[str] = field(default_factory=list)
    white_name: Optional[str] = None
    black_name: Optional[str] = None
    is_game_over: bool = False
    winner: Optional[str] = None  # display name of winner, or None if draw/ongoing
    termination_reason: Optional[str] = None  # e.g. "checkmate", "stalemate", "time"
    move_log: list[dict[str, Any]] = field(default_factory=list)  # chat per move: move, side, llm_name, explanation, messages
    # Time remaining in seconds; None = unlimited (shown as ∞ on frontend)
    white_remaining_seconds: Optional[float] = None
    black_remaining_seconds: Optional[float] = None
    # Whether each side's clock has started (True after their first move)
    white_timer_started: bool = False
    black_timer_started: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GameState:
        raw_log = data.get("move_log") or []
        move_log = [x if isinstance(x, dict) else {} for x in raw_log]
        wr, br = data.get("white_remaining_seconds"), data.get("black_remaining_seconds")
        if wr is not None and (not isinstance(wr, (int, float)) or wr == float("inf") or wr < 0):
            wr = None
        if br is not None and (not isinstance(br, (int, float)) or br == float("inf") or br < 0):
            br = None
        return cls(
            fen=data.get("fen", DEFAULT_FEN),
            move_history=list(data.get("move_history") or []),
            white_name=data.get("white_name"),
            black_name=data.get("black_name"),
            is_game_over=bool(data.get("is_game_over", False)),
            winner=data.get("winner"),
            termination_reason=data.get("termination_reason"),
            move_log=move_log,
            white_remaining_seconds=wr,
            black_remaining_seconds=br,
            white_timer_started=bool(data.get("white_timer_started", False)),
            black_timer_started=bool(data.get("black_timer_started", False)),
        )


# In-memory state (used by game process; also fallback when file missing)
_state = GameState()

# Live countdown: updated every second by ticker so frontend sees remaining time each second
_live_white: float = 0.0
_live_black: float = 0.0
_live_side_to_move: Optional[str] = None  # "White" or "Black"
_live_last_update: Optional[float] = None  # time.time(); None = not ticking
_live_game_id: Optional[str] = None
_live_timer_active: bool = False  # False until the side-to-move has made their first move
_live_lock = threading.Lock()

# Cancel requested by user (e.g. Restart during game); game loop checks and exits.
_cancel_game_id: Optional[str] = None
_cancel_lock = threading.Lock()


def _state_bucket() -> str:
    return os.environ.get("STATE_BUCKET", "").strip()


def _cancel_s3_key(game_id: Optional[str] = None) -> Optional[str]:
    gid = (game_id or "").strip()
    if not gid:
        return None
    return f"game_state/{gid}.cancel"


def set_cancel_requested(game_id: Optional[str] = None) -> None:
    """Request the running game with this game_id to stop (used when user clicks Restart)."""
    global _cancel_game_id
    gid = (game_id or "").strip() or None
    with _cancel_lock:
        _cancel_game_id = gid
    bucket = _state_bucket()
    key = _cancel_s3_key(gid)
    if bucket and key:
        try:
            import boto3

            boto3.client("s3").put_object(
                Bucket=bucket,
                Key=key,
                Body=b"{}",
                ContentType="application/json",
            )
        except Exception:
            # Best-effort only; local in-memory flag still works for the API or Lambda process.
            pass


def clear_cancel_requested() -> None:
    """Clear any cancel request (e.g. when starting a new game)."""
    global _cancel_game_id
    with _cancel_lock:
        _cancel_game_id = None
    # Cancel markers in S3 are per-game and game_ids are unique, so we do not delete them.


def is_cancelled(game_id: Optional[str] = None) -> bool:
    """True if a cancel was requested for this game_id.

    - Locally: checks in-memory flag (used by uvicorn api.py + game loop).
    - On AWS: also checks for a cancel marker object in S3 so that api_reset Lambda
      can signal game_run Lambda to stop.
    """
    gid = (game_id or "").strip()
    with _cancel_lock:
        if _cancel_game_id and gid == _cancel_game_id:
            return True
    bucket = _state_bucket()
    key = _cancel_s3_key(gid)
    if not bucket or not key:
        return False
    try:
        import boto3

        boto3.client("s3").head_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False


def set_live_remaining(
    white_seconds: float,
    black_seconds: float,
    side_to_move: str,
    game_id: Optional[str] = None,
    timer_active: bool = True,
) -> None:
    """Set current remaining times for the ticker. timer_active=False pauses countdown (first move)."""
    global _live_white, _live_black, _live_side_to_move, _live_last_update, _live_game_id, _live_timer_active
    with _live_lock:
        _live_white = max(0.0, white_seconds)
        _live_black = max(0.0, black_seconds)
        _live_side_to_move = side_to_move
        _live_last_update = time.time()
        _live_game_id = game_id
        _live_timer_active = timer_active


def clear_live_remaining() -> None:
    """Stop the live ticker (call when game ends or no timer)."""
    global _live_last_update
    with _live_lock:
        _live_last_update = None


def has_live_remaining() -> bool:
    """True if the ticker should keep running."""
    with _live_lock:
        return _live_last_update is not None


def tick_live_remaining() -> None:
    """
    Decrement the current side's time by elapsed seconds.
    Call once per second from the ticker thread. Skips if timer_active is False (first move).
    """
    with _live_lock:
        if _live_last_update is None or not _live_timer_active:
            return
        now = time.time()
        elapsed = now - _live_last_update
        _live_last_update = now
        side = _live_side_to_move
        w, b = _live_white, _live_black
    if side == "White":
        w = max(0.0, w - elapsed)
    else:
        b = max(0.0, b - elapsed)
    with _live_lock:
        _live_white, _live_black = w, b
    # For local API server, timers are served via /api/tick from these live
    # values; we do not touch the main state file here so /api/state only
    # changes on moves. For AWS, the game_run Lambda writes timers to S3 via
    # its own mechanisms.


def get_live_remaining() -> tuple[Optional[float], Optional[float], bool]:
    """
    Return the latest live timer values and whether the game is over.
    Advances the in-memory timers by elapsed time since last update so that
    each /api/tick request sees up-to-date values even without the background
    ticker thread (e.g. single process, or when ticker is not running).
    """
    global _live_last_update, _live_white, _live_black
    with _live_lock:
        if _live_last_update is None:
            return _live_white, _live_black, _state.is_game_over
        if not _live_timer_active:
            return _live_white, _live_black, _state.is_game_over
        now = time.time()
        elapsed = now - _live_last_update
        _live_last_update = now
        side = _live_side_to_move
        w, b = _live_white, _live_black
    if side == "White":
        w = max(0.0, w - elapsed)
    else:
        b = max(0.0, b - elapsed)
    with _live_lock:
        _live_white, _live_black = w, b
    return w, b, _state.is_game_over


def start_ticker_thread() -> None:
    """Start a daemon thread that calls tick_live_remaining() every second until clear_live_remaining()."""
    def run() -> None:
        while True:
            time.sleep(1)
            if not has_live_remaining():
                break
            try:
                tick_live_remaining()
            except Exception:
                pass
    t = threading.Thread(target=run, daemon=True)
    t.start()


def _s3_state_key(game_id: Optional[str] = None) -> str:
    """S3 key for game state: per-game when game_id given, else default."""
    if game_id and game_id.strip():
        return f"game_state/{game_id.strip()}.json"
    return "game_state/state.json"


def _state_dict_for_persist(state: GameState) -> dict[str, Any]:
    """Build state dict for file/S3; add last_timer_update_utc when timers are set (for Lambda api_tick advance-on-read)."""
    d = state.to_dict()
    if d.get("white_remaining_seconds") is not None or d.get("black_remaining_seconds") is not None:
        d["last_timer_update_utc"] = time.time()
    return d


def _write_state_s3(data: dict[str, Any], game_id: Optional[str] = None) -> None:
    """If STATE_BUCKET is set, upload state JSON to S3 for deployed frontends."""
    bucket = _state_bucket()
    if not bucket:
        return
    try:
        import boto3
        key = os.environ.get("STATE_KEY", "").strip() or _s3_state_key(game_id)
        body = json.dumps(data, indent=0)
        boto3.client("s3").put_object(
            Bucket=bucket,
            Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/json",
        )
    except Exception:
        pass


def _write_state_file(state: GameState, game_id: Optional[str] = None) -> None:
    """Persist state to JSON so the API process can read it."""
    data = _state_dict_for_persist(state)
    try:
        with open(_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=0)
    except OSError:
        pass
    _write_state_s3(data, game_id)


def _read_state_file() -> GameState | None:
    """Load state from JSON if present and valid."""
    if not _STATE_FILE.exists():
        return None
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return GameState.from_dict(data) if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def get_state() -> GameState:
    """Return the current game state. Reads from file so API sees game process updates."""
    from_file = _read_state_file()
    return from_file if from_file is not None else _state


def get_state_file_mtime() -> float | None:
    """Return mtime of the state file, or None if missing. Used by API to detect when game process updated state."""
    if not _STATE_FILE.exists():
        return None
    try:
        return _STATE_FILE.stat().st_mtime
    except OSError:
        return None


def update_state(
    fen: Optional[str] = None,
    move_history: Optional[list[str]] = None,
    white_name: Optional[str] = None,
    black_name: Optional[str] = None,
    is_game_over: Optional[bool] = None,
    winner: Optional[str] = None,
    termination_reason: Optional[str] = None,
    chat_entry: Optional[dict[str, Any]] = None,
    game_id: Optional[str] = None,
    white_remaining_seconds: Optional[float] = None,
    black_remaining_seconds: Optional[float] = None,
    white_timer_started: Optional[bool] = None,
    black_timer_started: Optional[bool] = None,
) -> None:
    """Update the shared game state and persist to file/S3 for the API process."""
    global _state
    if fen is not None:
        _state.fen = fen
    if move_history is not None:
        _state.move_history = move_history
    if white_name is not None:
        _state.white_name = white_name
    if black_name is not None:
        _state.black_name = black_name
    if is_game_over is not None:
        _state.is_game_over = is_game_over
    if winner is not None:
        _state.winner = winner
    if termination_reason is not None:
        _state.termination_reason = termination_reason
    if chat_entry is not None:
        _state.move_log.append(chat_entry)
    if white_remaining_seconds is not None:
        _state.white_remaining_seconds = white_remaining_seconds if white_remaining_seconds >= 0 else None
    if black_remaining_seconds is not None:
        _state.black_remaining_seconds = black_remaining_seconds if black_remaining_seconds >= 0 else None
    if white_timer_started is not None:
        _state.white_timer_started = white_timer_started
    if black_timer_started is not None:
        _state.black_timer_started = black_timer_started
    _write_state_file(_state, game_id)


def reset_state(game_id: Optional[str] = None) -> None:
    """Reset state to default (new game) and persist to file/S3."""
    global _state
    _state = GameState()
    _write_state_file(_state, game_id)


def write_state_to_s3(bucket: str, key: str, state_dict: dict[str, Any]) -> None:
    """Write a state dict directly to S3 (e.g. from Lambda start handler)."""
    try:
        import boto3
        body = json.dumps(state_dict, indent=0)
        boto3.client("s3").put_object(
            Bucket=bucket,
            Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/json",
        )
    except Exception:
        pass


def set_state_from_dict(state_dict: dict[str, Any], game_id: Optional[str] = None) -> None:
    """Set the global state from a dict and persist (for Lambda resuming from S3 state)."""
    global _state
    _state = GameState.from_dict(state_dict)
    _write_state_file(_state, game_id)
