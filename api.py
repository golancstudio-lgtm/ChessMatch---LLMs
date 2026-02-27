"""
API server for the browser frontend.

- Serves the frontend: GET / returns index.html; GET /static/* serves JS, CSS, Stockfish WASM.
- GET /api/state returns the current game state (FEN, move history, names, etc.).
  State is read from .chess_match_state.json (written by main.py or the in-API game runner).
- GET /api/events is an SSE stream: emits "state_updated" when the state file changes.
- GET /api/adapters returns available LLM adapters (id, name) for starting a game from the UI.
- POST /api/game/start starts a game in the background (body: white_llm_id, black_llm_id, options).

Run: uvicorn api:app --reload
Then open http://localhost:8000
"""

from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path

import dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.game_state import GameState, clear_cancel_requested, get_live_remaining, get_state, get_state_file_mtime, reset_state, set_cancel_requested
from src.game_loop import run_game
from src.llm_adapters import get_available_adapters, get_adapter_by_id

dotenv.load_dotenv()

# Local server uses a single "game"; no per-game isolation
LOCAL_GAME_ID = "local"

app = FastAPI(title="LLM Chess Match API", version="0.1.0")

# One game at a time; True while a game is running in the background
_game_running = False
_game_running_lock = threading.Lock()

FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"
STATIC_DIR = FRONTEND_DIR / "static"


def _move_log_camel(entries: list) -> list:
    """Convert move_log entries to camelCase for the frontend."""
    result = []
    for e in entries or []:
        if not isinstance(e, dict):
            result.append({})
            continue
        result.append({
            "move": e.get("move"),
            "side": e.get("side"),
            "llmName": e.get("llm_name") or e.get("llmName"),
            "explanation": e.get("explanation"),
        })
    return result


# --- Adapters and game start ---


class GameStartRequest(BaseModel):
    white_llm_id: str = Field(..., description="Adapter id for White (e.g. chatgpt, gemini)")
    black_llm_id: str = Field(..., description="Adapter id for Black")
    max_retries: int = Field(default=3, ge=0, le=20, description="Max retries per move")
    time_per_player_seconds: float | None = Field(default=300.0, ge=0, description="Seconds per player; 0 or null = no limit")
    starting_fen: str | None = Field(default=None, description="Optional FEN to start from")


@app.get("/api/adapters")
def api_adapters() -> list[dict]:
    """Return available LLM adapters for the start-game UI (id, name)."""
    return [{"id": a.id, "name": a.name} for a in get_available_adapters()]


def _run_game_in_thread(
    white_llm_id: str,
    black_llm_id: str,
    max_retries: int,
    time_per_player_seconds: float | None,
    starting_fen: str | None,
    game_id: str = LOCAL_GAME_ID,
) -> None:
    global _game_running
    white = get_adapter_by_id(white_llm_id)
    black = get_adapter_by_id(black_llm_id)
    if not white or not black:
        with _game_running_lock:
            _game_running = False
        return
    time_sec = time_per_player_seconds if time_per_player_seconds and time_per_player_seconds > 0 else None
    try:
        run_game(
            white,
            black,
            max_retries=max_retries,
            time_per_player_seconds=time_sec,
            starting_fen=starting_fen or None,
            game_id=game_id,
        )
    finally:
        with _game_running_lock:
            _game_running = False


def _validate_fen(fen: str | None) -> None:
    """Validate FEN; raise HTTPException if invalid."""
    if not fen or not fen.strip():
        return
    try:
        from src.chess_engine import ChessEngine
        ChessEngine(fen.strip())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid starting FEN")


@app.post("/api/game/start")
def api_game_start(body: GameStartRequest) -> dict:
    """Start a game in the background. Returns 202 with game_id; poll GET /api/state?game_id= for progress."""
    global _game_running
    white = get_adapter_by_id(body.white_llm_id)
    black = get_adapter_by_id(body.black_llm_id)
    if not white:
        raise HTTPException(status_code=400, detail=f"Unknown White adapter: {body.white_llm_id}")
    if not black:
        raise HTTPException(status_code=400, detail=f"Unknown Black adapter: {body.black_llm_id}")
    if body.white_llm_id == body.black_llm_id:
        raise HTTPException(status_code=400, detail="White and Black must be different adapters")
    _validate_fen(body.starting_fen)
    with _game_running_lock:
        if _game_running:
            raise HTTPException(status_code=409, detail="A game is already running")
        _game_running = True
        clear_cancel_requested()
    thread = threading.Thread(
        target=_run_game_in_thread,
        kwargs={
            "white_llm_id": body.white_llm_id,
            "black_llm_id": body.black_llm_id,
            "max_retries": body.max_retries,
            "time_per_player_seconds": body.time_per_player_seconds,
            "starting_fen": body.starting_fen,
            "game_id": LOCAL_GAME_ID,
        },
        daemon=True,
    )
    thread.start()
    return JSONResponse(
        content={
            "status": "started",
            "message": "Game started in background; poll /api/state for progress.",
            "game_id": LOCAL_GAME_ID,
        },
        status_code=202,
    )


@app.get("/api/game/status")
def api_game_status() -> dict:
    """Return whether a game is currently running (for UI to disable Start or show progress)."""
    with _game_running_lock:
        running = _game_running
    return {"running": running}


@app.post("/api/game/reset")
def api_game_reset() -> dict:
    """Clear current game state and show start panel. If a game is running, request cancel and reset immediately."""
    global _game_running
    with _game_running_lock:
        if _game_running:
            set_cancel_requested(LOCAL_GAME_ID)
            reset_state(LOCAL_GAME_ID)
            _game_running = False
        else:
            reset_state(LOCAL_GAME_ID)
    return {"status": "reset", "message": "Game state cleared. You can start a new game."}


@app.get("/api/state")
def api_state(game_id: str | None = Query(None, description="Game ID (used on Lambda; local server uses single game)")) -> dict:
    """Return current game state for the frontend (FEN, moves, names, etc.). Timer values are provided via /api/tick."""
    state: GameState = get_state()
    return {
        "fen": state.fen,
        "moveHistory": state.move_history,
        "whiteName": state.white_name,
        "blackName": state.black_name,
        "isGameOver": state.is_game_over,
        "winner": state.winner,
        "terminationReason": state.termination_reason,
        "moveLog": _move_log_camel(getattr(state, "move_log", None)),
    }


@app.get("/api/tick")
def api_tick(game_id: str | None = Query(None, description="Game ID (used on Lambda; local server uses single game)")) -> dict:
    """Return current timer values only (pure timer endpoint). Local server ignores game_id."""
    white, black, is_over = get_live_remaining()
    return {
        "whiteRemainingSeconds": white,
        "blackRemainingSeconds": black,
        "isGameOver": is_over,
    }


def _stockfish_path() -> str:
    return os.environ.get("STOCKFISH_PATH", "").strip() or "stockfish"


def _stockfish_depth_default() -> int:
    try:
        s = os.environ.get("STOCKFISH_DEPTH", "").strip()
        if not s:
            return 0
        return max(0, min(int(s), 50))
    except ValueError:
        return 0


@app.get("/api/analyze")
def api_analyze(
    fen: str = Query(..., description="FEN position"),
    depth: int | None = Query(None, ge=1, le=25, description="Analysis depth"),
) -> dict:
    """Run Stockfish on the server for the given position. Requires STOCKFISH_PATH in .env."""
    analysis_depth = depth if depth is not None else max(1, min(_stockfish_depth_default() or 15, 25))
    engine_path = _stockfish_path()
    try:
        import chess
        import chess.engine
    except ImportError:
        return {"ok": False, "error": "chess engine not available", "bestMove": None, "scoreCp": None, "mate": None, "pv": []}
    try:
        engine = chess.engine.SimpleEngine.popen_uci(engine_path)
    except (FileNotFoundError, chess.engine.EngineTerminatedError, OSError):
        return {"ok": False, "error": "Stockfish not found", "bestMove": None, "scoreCp": None, "mate": None, "pv": []}
    try:
        board = chess.Board(fen)
        info = engine.analyse(board, chess.engine.Limit(depth=analysis_depth))
        score = info.get("score")
        pv = info.get("pv") or []
        best_move = pv[0] if pv else None
        best_san = board.san(best_move) if best_move else None
        score_cp = None
        mate = None
        if score is not None:
            white_score = score.white()
            if white_score.is_mate():
                mate = white_score.mate()
            else:
                cp = white_score.score()
                if cp is not None:
                    score_cp = cp
        pv_san = []
        b = board.copy()
        for m in pv[:5]:
            pv_san.append(b.san(m))
            b.push(m)
        return {"ok": True, "bestMove": best_san, "scoreCp": score_cp, "mate": mate, "pv": pv_san, "depth": analysis_depth}
    except Exception as e:
        return {"ok": False, "error": str(e), "bestMove": None, "scoreCp": None, "mate": None, "pv": []}
    finally:
        try:
            engine.quit()
        except Exception:
            pass


@app.get("/api/stockfish-available")
def api_stockfish_available() -> dict:
    """Check if server-side Stockfish is available."""
    try:
        import chess.engine
        engine = chess.engine.SimpleEngine.popen_uci(_stockfish_path())
        engine.quit()
        return {"available": True}
    except Exception:
        return {"available": False}


async def _state_events():
    """Async generator: yield SSE events when the state file mtime changes."""
    last_mtime: float | None = None
    while True:
        mtime = get_state_file_mtime()
        if mtime is not None:
            if last_mtime is not None and mtime != last_mtime:
                yield "event: state_updated\ndata: {}\n\n"
            last_mtime = mtime
        await asyncio.sleep(0.4)


@app.get("/api/events")
async def api_events():
    """SSE stream: sends 'state_updated' when the game state file changes (e.g. after each move)."""
    async def stream():
        # Send one event immediately so the client fetches initial state
        yield "event: state_updated\ndata: {}\n\n"
        async for chunk in _state_events():
            yield chunk
    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# Serve static assets (JS, CSS, worker, WASM) from /static
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/favicon.ico")
def favicon() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "favicon.ico", media_type="image/x-icon")


@app.get("/logo.png")
def logo() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "logo.png", media_type="image/png")


@app.get("/")
def index() -> FileResponse:
    """Serve the frontend index page."""
    index_path = FRONTEND_DIR / "index.html"
    if not index_path.exists():
        raise FileNotFoundError(
            "frontend/index.html not found. Create the frontend first."
        )
    return FileResponse(index_path)


@app.get("/review")
def review() -> FileResponse:
    """Serve the review game page (upload PGN + chat, step through moves)."""
    review_path = FRONTEND_DIR / "review.html"
    if not review_path.exists():
        raise FileNotFoundError(
            "frontend/review.html not found."
        )
    return FileResponse(review_path)
