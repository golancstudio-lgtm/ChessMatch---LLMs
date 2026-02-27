# LLM Chess Match — Technical Documentation

A Python application where **two Large Language Models (LLMs) play chess against each other**. Each LLM receives the current board position (FEN), responds with a move in PGN (Standard Algebraic Notation), and the engine validates and applies the move—or requests a retry (with feedback) if the move is illegal or unparseable. The system supports a **console-only** game, a **browser frontend** (live board, chat log, Stockfish evaluation), and **AWS deployment** (S3, API Gateway, Lambda, optional CloudFront).

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Entry Points](#entry-points)
4. [Core Modules (`src/`)](#core-modules-src)
5. [API Server](#api-server)
6. [Frontend](#frontend)
7. [Game State and Persistence](#game-state-and-persistence)
8. [AWS Deployment](#aws-deployment)
9. [Environment Variables](#environment-variables)
10. [Scripts](#scripts)
11. [Dependencies](#dependencies)
12. [Project Structure](#project-structure)
13. [Usage](#usage)

---

## Overview

- **Purpose**: Run a full chess game between two LLMs (e.g. ChatGPT vs Gemini). Moves are requested via prompts, parsed from LLM responses (JSON or fallback regex), and validated with `python-chess`.
- **Modes**:
  - **Console**: `main.py` — interactive prompts for LLM selection, retries, timer; prints board and optional Stockfish eval each move.
  - **Web UI**: `ui_app.py` or `uvicorn api:app` — single-page app: board, move history, chat (LLM exchange), Stockfish evaluation (server or WASM), start/reset game from the browser.
  - **AWS**: CloudFormation deploys S3 (frontend + state), API Gateway, and Lambdas; game runs inside a Lambda (`game_run`), state is stored in S3; frontend can be served from S3 or CloudFront.
- **LLM adapters**: ChatGPT (OpenAI), Gemini (Google), Claude (Anthropic), Mistral, Cohere, Llama via Groq, Grok (xAI). Each adapter implements a common interface: `send_prompt(system_prompt, user_prompt)` → raw text; the response is parsed for a single PGN move and optional explanation.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CONSOLE (main.py)                               │
│  Prompts: White/Black LLM, retries, time limit → run_game() → print board   │
│  Optional: Stockfish binary (STOCKFISH_PATH/DEPTH) for terminal eval          │
└─────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         CORE (src/)                                          │
│  game_loop.run_game()  ←→  chess_engine (FEN, legal moves, apply_pgn_move)   │
│       ↑                           ↑                                          │
│  prompt_builder (system/user)     response_parser (JSON/regex → move)        │
│  llm_adapters (ChatGPT, Gemini, …)                                           │
│  game_state (in-memory + .chess_match_state.json + optional S3)              │
└─────────────────────────────────────────────────────────────────────────────┘
                                        │
          ┌─────────────────────────────┼─────────────────────────────┐
          ▼                             ▼                             ▼
┌──────────────────┐         ┌──────────────────┐         ┌──────────────────┐
│ LOCAL API        │         │ STATE FILE        │         │ AWS              │
│ (api.py)         │         │ .chess_match_     │         │ game_run Lambda  │
│ Serves frontend  │◄────────│ state.json        │         │ writes to S3     │
│ GET /api/state   │         │ (written by       │         │ api_state/tick   │
│ GET /api/tick    │         │  main or in-API   │         │ read from S3     │
│ POST game/start  │         │  game thread)     │         │                  │
│ POST game/reset  │         └──────────────────┘         └──────────────────┘
│ GET /api/events  │
│ GET /api/analyze │
└──────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ FRONTEND (frontend/)                                                          │
│ index.html: board, chat, eval panel, start game, timers, export PGN/chat     │
│ review.html: upload PGN + chat .txt, step through moves                      │
│ app.js: SSE or polling → /api/state, /api/tick, Stockfish server or WASM    │
│ static: style.css, config.js (API_BASE), Stockfish WASM (nnue-16-single)    │
└─────────────────────────────────────────────────────────────────────────────┘
```

- **Console** and **in-API game** (when you start a game from the UI) both call `run_game()` from `src/game_loop.py`. The loop alternates turns, builds prompts via `prompt_builder`, calls the chosen LLM via `llm_adapters`, parses the response with `response_parser`, and applies the move with `chess_engine`. State is updated through `game_state` (file and optionally S3).
- **Frontend** never runs the game itself; it only consumes the API (state, tick, analyze, start, reset) and displays the board, chat, and evaluation.

---

## Entry Points

| File        | Role |
|------------|------|
| `main.py`  | Console entry point. Loads `.env`, prompts for White/Black LLM, max retries, time per player, optional starting FEN. Creates a `ChessEngine`, then calls `run_game()` with an `on_move` callback that prints the board (and optional Stockfish line) and an `on_time_update` for clocks. Reads `STOCKFISH_DEPTH` / `STOCKFISH_PATH` from env for terminal eval. |
| `ui_app.py`| Thin launcher: runs `uvicorn` with `api:app`, host `0.0.0.0`, port 8000, reload. All routes and logic live in `api.py`. |
| `api.py`   | FastAPI app: serves `frontend/index.html` at `/`, `frontend/review.html` at `/review`, static files from `frontend/static/`, favicon and logo. Exposes REST and SSE endpoints for state, tick, adapters, game start/reset, analyze, events. When a game is started from the UI, it runs `run_game()` in a background thread and writes state to the same `.chess_match_state.json` (and S3 if `STATE_BUCKET` is set). |

---

## Core Modules (`src/`)

### `chess_engine.py`

- **Role**: Single place for chess rules and board state. Wraps `python-chess` (`chess.Board`).
- **`ChessEngine(fen=None)`**: Builds a board from FEN or default start. Exposes `board`, `fen`, `is_game_over`, `outcome`, `turn`.
- **`apply_pgn_move(pgn_move: str) → MoveResult`**: Parses a PGN string (e.g. `e4`, `Nf3`, `O-O`), applies the move if legal. Returns `MoveResult(success, new_fen, error_message, san_move)`. Handles `InvalidMoveError`, `AmbiguousMoveError`, `IllegalMoveError`, empty move.
- **`get_legal_moves_san() → list[str]`**: All legal moves in SAN for the current position (used in prompts so the LLM must choose from this list).
- **`reset(fen=None)`**: Resets the board to start or given FEN.

### `game_loop.py`

- **Role**: Runs the full game: alternate turns, call LLM, parse response, validate move, update state, handle retries and time forfeit.
- **`run_game(white_llm, black_llm, max_retries=3, time_per_player_seconds=None, on_move=None, on_time_update=None, starting_fen=None, initial_state_dict=None, game_id=None) → GameResult`**:
  - If `initial_state_dict` is provided (e.g. from S3), state is restored and the game can resume; otherwise state is reset and initial FEN/history/names are set.
  - Each turn: build `MoveRequest` (FEN, move history, side, legal moves, optional retry/error/rejected list, optional timer). Get system + user prompts from `prompt_builder`, call `llm.send_prompt()`, then `parse_llm_response()`.
  - If the parsed move is `None` (invalid JSON or no move): treat as parse error, add to forfeit attempts, and retry with an error message (or forfeit if retries exhausted).
  - If the move is present: `engine.apply_pgn_move()`. On success: append to move history, call `update_state()` (and optionally S3), run `on_move` and `on_time_update`. On failure: record forfeit attempt, retry with illegal-move message (or forfeit).
  - Timer: per-player clock; only starts after that side’s first move. Time is decremented after each LLM call; if it hits zero, the game ends with a time forfeit.
  - Cancel: `is_cancelled(game_id)` is checked each turn (in-memory for local API, or S3 cancel marker for Lambda); if set, the loop exits and state is updated as stopped.
- **`GameResult`**: `move_history`, `outcome`, `winner_name`, `loser_name`, `termination_reason`, `forfeit_by`, `forfeit_attempts` (list of (prompt, response, rejection_reason) for forfeit-by-retries).

### `game_state.py`

- **Role**: Shared state for the frontend and persistence. One in-memory `GameState` plus a JSON file (and optional S3).
- **`GameState`** (dataclass): `fen`, `move_history`, `white_name`, `black_name`, `is_game_over`, `winner`, `termination_reason`, `move_log` (list of per-move entries: move, side, llm_name, explanation, messages), `white_remaining_seconds`, `black_remaining_seconds`, `white_timer_started`, `black_timer_started`.
- **Persistence**: Default state file is `.chess_match_state.json` in project root. If `STATE_BUCKET` is set, state is also written to S3 (key from `STATE_KEY` or `game_state/{game_id}.json`). In Lambda, the state file path is switched to `/tmp/chess_match_state.json` so writes succeed.
- **Read path**: `get_state()` returns the state from file (or in-memory fallback if file missing). Used by the API so the HTTP process sees updates written by the game process (or by another Lambda).
- **Write path**: `update_state(...)` updates the in-memory state and calls `_write_state_file()` → JSON file + `_write_state_s3()` if bucket set. `reset_state(game_id)` clears state and persists. State dict can include `last_timer_update_utc` for advance-on-read timer in Lambda.
- **Live timers**: For the local API, a ticker thread and in-memory `_live_white` / `_live_black` are updated every second; `get_live_remaining()` returns current values (and optionally advances them by elapsed time). `/api/tick` serves these so the frontend can show a countdown without polling full state every second.
- **Cancel**: `set_cancel_requested(game_id)` sets an in-memory flag and optionally writes a cancel object to S3 (`game_state/{game_id}.cancel`). `is_cancelled(game_id)` checks both so the running game (e.g. `game_run` Lambda) can stop when the user hits Restart.

### `llm_adapters.py`

- **Role**: Abstract interface and concrete adapters for each LLM provider. All use env-based API keys (or, in Lambda, keys injected from Secrets Manager by the game_run handler).
- **`LLMAdapter`** (Protocol): `name`, `id`, `send_prompt(system_prompt, user_prompt) → str`.
- **`BaseLLMAdapter`**: ABC with `name`, `id`; subclasses implement `send_prompt`.
- **Implementations**: `ChatGPTAdapter` (OpenAI, `gpt-5.2-chat-latest`), `GeminiAdapter` (Google GenAI, `gemini-2.5-flash`), `ClaudeAdapter` (Anthropic, `claude-sonnet-4-5-20250929`), `MistralAdapter`, `CohereAdapter` (ClientV2), `LlamaGroqAdapter` (Groq, `llama-3.3-70b-versatile`), `GrokAdapter` (xAI via OpenAI-compatible client). Each lazy-initializes the client and reads the corresponding env var.
- **Registry**: `get_available_adapters()` returns a list of all adapter instances; `get_adapter_by_id(id)` returns one by id (e.g. `"chatgpt"`, `"gemini"`). Used by `main.py` for console selection and by the API/frontend for the start-game dropdown.

### `prompt_builder.py`

- **Role**: Build system and user prompts for each move so the LLM returns a single move in the required format.
- **`MoveRequest`**: Holds FEN, move history, side to move, legal moves, and optionally retry flag, error message, previous attempt, parse-error flag, rejected moves list, and remaining times.
- **System prompt**: Template instructs the model to play as White or Black, reply with a JSON object `{"move": "...", "explanation": "..."}`, use PGN (K/Q/R/B/N, pawns without letter, O-O / O-O-O), and choose only from the given legal moves list. No check/checkmate suffix in the list so the engine doesn’t reveal that.
- **User prompt**: First-move variant (no history) or normal/retry. Includes FEN, optional time-remaining line, move history, legal moves list, and for retries the error and rejected moves. Time section uses `_format_time()` (e.g. `M:SS` or ∞).
- **`build_prompts(request) → (system_prompt, user_prompt)`**: Used by the game loop each turn.

### `response_parser.py`

- **Role**: From raw LLM text, extract one PGN move and optional explanation. Prefers JSON; falls back to regex and literal `"move": "..."` search.
- **`parse_llm_response(response) → ParsedResponse`**: Returns `ParsedResponse(move, explanation, error_type)`. `move` is `None` if no valid move found; `error_type` can be `"invalid_json"` or `"no_move"`.
- **JSON extraction**: Tries code blocks (```json ... ```), then full string, then tail of string after last `}`. Expects a dict with `"move"` (string). Castling `0-0` is normalized to `O-O`.
- **Fallback**: Regex for castling, piece moves, pawn moves (PGN-like tokens); takes the last match by position. Also `_extract_move_from_json_literal` for the last `"move": "X"` in the text.
- **`format_for_display(response)`**: For console: show explanation and move in a readable way instead of raw JSON.

---

## API Server

**`api.py`** — FastAPI app, run with `uvicorn api:app` or `python ui_app.py`.

| Method + Path            | Description |
|--------------------------|-------------|
| `GET /`                  | Serves `frontend/index.html`. |
| `GET /review`            | Serves `frontend/review.html`. |
| `GET /favicon.ico`, `GET /logo.png` | Static assets from `frontend/`. |
| `GET /static/*`          | StaticFiles from `frontend/static/` (JS, CSS, Stockfish WASM). |
| `GET /api/state?game_id=`| Returns current game state: `fen`, `moveHistory`, `whiteName`, `blackName`, `isGameOver`, `winner`, `terminationReason`, `moveLog` (camelCase). State comes from `get_state()` (file or in-memory). Timer fields are not included; use `/api/tick` for clocks. |
| `GET /api/tick?game_id=` | Returns live timer only: `whiteRemainingSeconds`, `blackRemainingSeconds`, `isGameOver`. For local server this uses the in-memory ticker; for Lambda, the api_tick handler reads from S3 and advances time by `last_timer_update_utc`. |
| `GET /api/adapters`      | List of `{id, name}` for LLM adapters (for start-game dropdown). |
| `POST /api/game/start`   | Body: `white_llm_id`, `black_llm_id`, optional `max_retries`, `time_per_player_seconds`, `starting_fen`. Validates, ensures no game is already running, clears cancel flag, starts a daemon thread that calls `run_game(..., game_id=LOCAL_GAME_ID)`. Returns 202 and `game_id`. |
| `GET /api/game/status`   | Returns `{running: bool}`. |
| `POST /api/game/reset`   | Sets cancel (if game running), resets state, clears `_game_running`. Returns JSON `{status, message}`. |
| `GET /api/analyze?fen=&depth=` | Runs Stockfish (binary from `STOCKFISH_PATH`) on the given FEN at given depth. Returns `ok`, `bestMove`, `scoreCp`, `mate`, `pv`, `depth` or error. |
| `GET /api/stockfish-available` | Returns `{available: bool}` (engine can be spawned). |
| `GET /api/events`        | SSE stream: sends one initial `state_updated` then keeps checking state file mtime and sends `state_updated` when it changes. Frontend uses this locally to refetch state without polling; on AWS, API Gateway doesn’t support long-lived SSE so the frontend falls back to polling. |

All JSON responses use camelCase for the frontend where documented (e.g. `moveLog`). CORS and cache headers are set as needed (e.g. no-cache for state/tick).

---

## Frontend

- **Root**: `frontend/`. Static assets in `frontend/static/`.
- **`index.html`**: Single-page app: header (logo, “Start a game” panel with White/Black dropdowns, time per player, max retries, optional FEN, Start button; match header with White vs Black and timers; Restart). Main: left panel “Chat (LLM exchange)”, center board with eval bar and rank/file labels, right panel “Evaluation” (Stockfish result + best move) and “Game” (status, move history, Export game). Scripts: chess.js (CDN), `config.js`, `app.js`.
- **`review.html`**: Upload PGN and optional chat .txt, then step through moves. Uses `review.js`, same CSS. Link back to “live match” (`/`).
- **`static/config.js`**: Defines `window.CHESSMATCH_API_BASE`. Empty = same origin; when frontend is on S3 and API on API Gateway, the deploy script or you set this to the API base URL so `/api/*` requests go to the right host.
- **`static/app.js`** (main UI logic):
  - **Board**: Renders 8×8 from FEN; uses `chess.js` for legality and UCI↔PGN. Piece images from a CDN (e.g. chessboardjs Wikipedia set). Highlights last move; animates piece move on FEN change when possible.
  - **State**: When not deployed, opens SSE to `/api/events` and on `state_updated` fetches `GET /api/state`. When deployed (`API_BASE` set), uses polling (e.g. every 2s when game active, 5s idle). On load, calls `POST /api/game/reset` then fetches state so the UI starts clean.
  - **Timers**: Fetches `GET /api/tick` every second when a game is active; updates only the timer spans, not the full state (so clocks don’t jump).
  - **Evaluation**: After each new move, or on “Re-analyze”, calls `GET /api/stockfish-available`. If available, `GET /api/analyze?fen=&depth=`; else loads Stockfish WASM worker (`stockfish-nnue-16-single.js`), sends UCI `position fen ...` and `go depth N`, parses `info`/`bestmove` for score and best move. Eval bar and “Evaluation” panel updated; detail line indicates “Browser engine” when using WASM.
  - **Start game**: POST `/api/game/start` with selected adapters and options; on 202, stores `game_id` and starts state/tick polling.
  - **Restart**: POST `/api/game/reset` (body with `game_id` when present); then refetches state. Export: fetches state and builds PGN + chat .txt and triggers download.
- **`static/style.css`**: Dark theme (CSS variables for bg, surface, accent, squares, pieces). Layout: header, main grid (chat | board | eval+game), responsive board size via `--board-size`. Styles for panels, buttons, chat entries, move history, eval bar.
- **Stockfish WASM**: `stockfish-nnue-16-single.js` (worker) and `stockfish-nnue-16-single.wasm` in `frontend/static/` (Stockfish 16 single-threaded, e.g. from npm `stockfish`). Used when server-side Stockfish is not configured or fails.

---

## Game State and Persistence

- **Local (no S3)**:
  - Game process (console or API thread) calls `update_state()` / `reset_state()` in `game_state`. State is kept in memory and written to `.chess_match_state.json`.
  - API process (or same process when game runs in-thread) calls `get_state()` which reads from that file. So the frontend always sees the latest FEN, moves, and move log.
  - Timers: ticker thread updates `_live_white` / `_live_black` every second; `get_live_remaining()` is used by `/api/tick`. State file does not need to be updated every second.
- **With S3 (`STATE_BUCKET` set)**:
  - Every `update_state()` also uploads the state dict to S3 (key from `STATE_KEY` or `game_state/{game_id}.json`). State dict may include `last_timer_update_utc` when timers are in use.
  - On AWS, the game runs in the `game_run` Lambda; it sets `STATE_KEY` to `game_state/{game_id}.json` and uses the same `update_state()` so each move is written to S3. The api_state and api_tick Lambdas read from S3. Cancel is implemented by writing `game_state/{game_id}.cancel`; the game loop checks `is_cancelled(game_id)` which does a head_object on that key.

---

## AWS Deployment

- **Templates**: `deploy/cloudformation.yaml` (full: S3, CloudFront, API Gateway, Lambdas); `deploy/cloudformation-no-cloudfront.yaml` (S3 static website + API Gateway + Lambdas, no CloudFront).
- **Resources**: S3 bucket (frontend + state objects); optional CloudFront with OAC, default origin S3 and behavior for `/api/*` to API Gateway; API Gateway REST API with integration to Lambda for each route; Lambdas: api_state, api_tick, api_adapters, api_game_start, api_reset, api_events, game_run. IAM roles and permissions for Lambda (S3 read/write, Secrets Manager get, Lambda invoke for game_run).
- **Lambdas** (in `deploy/lambda/`):
  - **api_state**: GET /api/state. Reads state from S3 (key from query `game_id` or env `STATE_KEY`). Returns JSON (fen, moveHistory, whiteName, blackName, isGameOver, winner, terminationReason, moveLog). No timer in body.
  - **api_tick**: GET /api/tick. Reads same S3 state, returns whiteRemainingSeconds, blackRemainingSeconds, isGameOver (and terminationReason/winner if over). Advances time by elapsed since `last_timer_update_utc` (advance-on-read) so the UI can poll once per second and see countdown.
  - **api_adapters**: GET /api/adapters. Returns static list of adapter id/name.
  - **api_game_start**: POST /api/game/start. Parses body (white_llm_id, black_llm_id, max_retries, time_per_player_seconds, starting_fen). Writes initial state to S3 at `game_state/{game_id}.json` (game_id = new UUID), invokes **game_run** Lambda asynchronously with payload (game_id, white_llm_id, black_llm_id, …). Returns 202 with game_id.
  - **game_run**: Invoked by api_game_start. Loads LLM API keys from AWS Secrets Manager (`llm-api-secrets` JSON). Sets STATE_KEY to `game_state/{game_id}.json`, builds initial_state and calls `run_game(..., initial_state_dict=initial_state, game_id=game_id)`. Game loop writes state to S3 after each move and checks S3 cancel key each turn.
  - **api_reset**: POST /api/game/reset. Writes default empty state to S3 (key from body game_id or default) and writes the cancel object so game_run exits on next check.
  - **api_events**: GET /api/events. Returns a single SSE event (API Gateway doesn’t support streaming); frontend uses polling when API_BASE is set.
- **Frontend on S3**: Upload/sync `index.html`, `review.html`, `static/` (including config.js with API_BASE if needed). CloudFront template includes a function to rewrite `/review` → `/review.html`.
- **Secrets**: Create secret `llm-api-secrets` (JSON) with keys such as `OPENAI_API_KEY`, `GEMINI_API_KEY`, etc. game_run Lambda needs `secretsmanager:GetSecretValue` on that secret.
- **Deploy script**: `deploy/Deploy-Aws.ps1` (PowerShell). Parameters: StackName, ProjectName, EnvironmentName, Region, Template, SkipStack, SyncState, InvalidateCloudFront. Builds/packages Lambda code from repo, updates stack, syncs frontend to S3, optionally invalidates CloudFront. See `deploy/README.md` for full usage and troubleshooting.

---

## Environment Variables

| Variable               | Required / Use | Description |
|------------------------|----------------|-------------|
| `OPENAI_API_KEY`       | For ChatGPT    | OpenAI API key. |
| `GEMINI_API_KEY` or `GOOGLE_API_KEY` | For Gemini | Google API key. |
| `ANTHROPIC_API_KEY`    | For Claude     | Anthropic API key. |
| `MISTRAL_API_KEY`      | For Mistral    | Mistral API key. |
| `COHERE_API_KEY`       | For Cohere     | Cohere API key. |
| `GROQ_API_KEY`        | For Llama (Groq) | Groq API key. |
| `XAI_API_KEY`         | For Grok       | xAI API key. |
| `STATE_BUCKET`        | Optional       | S3 bucket for game state; when set, state is written to S3 (and in Lambda, state file path is /tmp). |
| `STATE_KEY`           | Optional       | S3 key for state; default `game_state/state.json` or per-game `game_state/{game_id}.json`. |
| `STOCKFISH_DEPTH`     | Optional       | 0 or unset = no terminal/server eval; 1–50 = analysis depth (e.g. 20). |
| `STOCKFISH_PATH`      | Optional       | Path to Stockfish binary; default `stockfish`. |

Only the keys for the LLMs you use are required. For Lambda, keys are typically in Secrets Manager, not env.

---

## Scripts

- **`scripts/verify_llm_keys.py`**: Checks that the app can call OpenAI and Gemini (or only one with `--openai` / `--gemini`). Uses `.env` by default; with `--aws` loads from AWS Secrets Manager `llm-api-secrets`. Run from project root. Exits with 1 if any check fails.

---

## Dependencies

See `requirements.txt`:

- **Chess**: `python-chess>=1.999` (board, FEN, SAN, UCI engine for Stockfish).
- **Env**: `python-dotenv>=1.0.0`.
- **AWS**: `boto3>=1.34.0` (S3 state sync, Secrets Manager in game_run Lambda).
- **LLM clients**: `openai`, `google-genai`, `anthropic`, `mistralai`, `cohere`, `groq` (versions in requirements).
- **Web**: `streamlit` (optional), `fastapi`, `uvicorn[standard]`.

---

## Project Structure

```
.
├── main.py                    # Console game entry point
├── ui_app.py                  # Uvicorn launcher for api:app
├── api.py                     # FastAPI app: frontend + all /api/* routes
├── requirements.txt
├── .env.example               # Template for .env (API keys, STOCKFISH_*, STATE_*)
├── .chess_match_state.json    # Runtime state file (gitignored; created when game runs)
├── frontend/
│   ├── index.html             # Main UI: board, chat, eval, start game
│   ├── review.html            # Review: upload PGN + chat, step through
│   ├── favicon.ico, logo.png
│   ├── README.md
│   └── static/
│       ├── app.js             # State/SSE/polling, board, eval (server or WASM), start/reset/export
│       ├── review.js          # Review page logic
│       ├── style.css          # Dark theme, layout, board
│       ├── config.js          # window.CHESSMATCH_API_BASE
│       ├── stockfish-nnue-16-single.js
│       └── stockfish-nnue-16-single.wasm
├── src/
│   ├── __init__.py
│   ├── chess_engine.py        # FEN, move validation, PGN (python-chess wrapper)
│   ├── game_loop.py           # Turn loop, retries, timer, state updates
│   ├── game_state.py          # Shared state, .chess_match_state.json, S3, live timers, cancel
│   ├── llm_adapters.py        # LLM API wrappers (ChatGPT, Gemini, Claude, …)
│   ├── prompt_builder.py      # System and user prompts per move
│   └── response_parser.py     # Parse LLM JSON/regex for move and explanation
├── deploy/
│   ├── cloudformation.yaml    # Full stack (S3, CloudFront, API Gateway, Lambdas)
│   ├── cloudformation-no-cloudfront.yaml
│   ├── Deploy-Aws.ps1         # Deploy script
│   ├── README.md              # Deploy and debug instructions
│   └── lambda/
│       ├── api_state/handler.py
│       ├── api_tick/handler.py
│       ├── api_adapters/handler.py
│       ├── api_game_start/handler.py
│       ├── api_reset/handler.py
│       ├── api_events/handler.py
│       └── game_run/handler.py # Loads secrets, runs run_game(), state to S3
└── scripts/
    └── verify_llm_keys.py      # Verify OpenAI/Gemini keys (.env or --aws)
```

---

## Usage

### Console game

```bash
python main.py
```

You will be prompted for: LLM for White, LLM for Black, max attempts per move, time per player (0 = no limit), optional starting FEN. The game runs in the terminal; after each move the board is printed and, if `STOCKFISH_DEPTH` is set, a Stockfish evaluation line is shown.

### Web UI (local)

1. Start the API (one terminal):

   ```bash
   python ui_app.py
   ```
   Or: `uvicorn api:app --reload`

2. Optionally start a game from the browser (“Start a game” panel) or run a game in another terminal with `python main.py` (state is shared via `.chess_match_state.json`).

3. Open **http://localhost:8000**. The UI subscribes to `/api/events` (SSE), fetches `/api/state` on each event and `/api/tick` every second when a game is active. After each new move it requests evaluation from `/api/analyze` or falls back to Stockfish WASM. Use “Re-analyze” to re-run evaluation on the current position. “Export game” downloads PGN and chat .txt.

### AWS

1. Configure AWS CLI and create `llm-api-secrets` in Secrets Manager (same region as Lambdas).
2. From project root run the deploy script, e.g.:

   ```powershell
   .\deploy\Deploy-Aws.ps1 -StackName chessmatch-dev
   ```

3. Set the frontend’s API base (e.g. in `config.js` or via deploy) to the API Gateway URL (e.g. CloudFormation output `ApiEndpoint`).
4. Open the frontend URL (CloudFront or S3 website). Start a game from the UI; the game runs in the game_run Lambda and state appears in S3 and in the UI via api_state / api_tick.

For more detail (parameters, no-CloudFront, debugging “Waiting for first move”, Review/Restart on prod), see **`deploy/README.md`**.

---

## License

MIT
