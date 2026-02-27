# Frontend – Board view and Stockfish (browser)

This folder contains the static frontend for the LLM Chess Match: a single-page UI that shows the current game and runs Stockfish in the browser via WebAssembly.

## What it does

- **Game state**: Fetches `GET /api/state` from the API and displays FEN, move history, and match names. Use “Refresh from backend” to reload (e.g. after a move in `main.py`).
- **Board**: Renders the position with [chessboard.js](https://github.com/oakmac/chessboardjs/) and [chess.js](https://github.com/jhlywa/chess.js). FEN is shown in a read-only field.
- **Stockfish analysis**: Runs Stockfish 16 (single-threaded WASM) from `static/`. “Analyze position” sends the current FEN to the worker, runs a depth search, and shows best move and evaluation. All analysis runs in the browser; no server engine is used.

## Serving

The frontend is served by the FastAPI app in the project root:

```bash
uvicorn api:app --reload
```

Then open http://localhost:8000. The API serves `index.html` at `/` and static assets under `/static/`.

## Static assets

- `app.js` – Fetches `/api/state`, drives the board and Stockfish worker, parses UCI output for best move and score.
- `style.css` – Layout and dark theme.
- `stockfish-nnue-16-single.js` / `stockfish-nnue-16-single.wasm` – Stockfish 16 worker (same-origin to avoid CORS). From the npm `stockfish` package.

Evaluation is shown from White’s perspective (+ = White). It may differ from the terminal (main.py) evaluation because the terminal uses your local Stockfish binary (possibly a different version).
