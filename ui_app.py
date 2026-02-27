"""
Web UI launcher for LLM Chess Match.

Runs the same FastAPI app as api.py (single entry point: api.py has all routes
including /api/state, /api/analyze, /api/game/start, etc.). Use this to start
the server with uvicorn reload, or run: python ui_app.py
"""
from __future__ import annotations

import uvicorn

# Re-use the full app from api so we have one codebase
from api import app

if __name__ == "__main__":
    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
