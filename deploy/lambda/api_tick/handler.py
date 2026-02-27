"""
Lambda handler for GET /api/tick.
Returns timer values (whiteRemainingSeconds, blackRemainingSeconds, isGameOver).
When isGameOver, also returns terminationReason and winner for the frontend.
Advances timers by elapsed time since last_timer_update_utc (advance-on-read) so
clocks count down every second without game_run writing every second.
Environment: STATE_BUCKET, STATE_KEY (default game_state/state.json).
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import boto3


def _get_state_key(event: dict) -> str:
    """State key from query game_id or default."""
    params = event.get("queryStringParameters") or {}
    game_id = (params.get("game_id") or "").strip()
    if game_id:
        return f"game_state/{game_id}.json"
    return os.environ.get("STATE_KEY", "game_state/state.json").strip() or "game_state/state.json"


def _get_state_from_s3(event: dict) -> dict[str, Any] | None:
    bucket = os.environ.get("STATE_BUCKET", "").strip()
    if not bucket:
        return None
    key = _get_state_key(event)
    try:
        s3 = boto3.client("s3")
        obj = s3.get_object(Bucket=bucket, Key=key)
        data = json.loads(obj["Body"].read().decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _side_to_move_from_fen(fen: str) -> str | None:
    """Return 'White' or 'Black' from FEN (e.g. '... w KQkq' -> White)."""
    if not fen or not isinstance(fen, str):
        return None
    parts = fen.split()
    if len(parts) >= 2 and parts[1].lower() == "b":
        return "Black"
    return "White"


def handler(event: dict, context: object) -> dict:
    state = _get_state_from_s3(event) or {}
    is_game_over = bool(state.get("is_game_over", False))
    wr = state.get("white_remaining_seconds")
    br = state.get("black_remaining_seconds")
    if wr is not None and (not isinstance(wr, (int, float)) or wr < 0):
        wr = None
    if br is not None and (not isinstance(br, (int, float)) or br < 0):
        br = None

    term_reason = state.get("termination_reason")
    winner = state.get("winner")

    if not is_game_over and (wr is not None or br is not None):
        white_started = bool(state.get("white_timer_started", False))
        black_started = bool(state.get("black_timer_started", False))
        side = _side_to_move_from_fen(state.get("fen") or "")
        side_started = (side == "White" and white_started) or (side == "Black" and black_started)
        last_utc = state.get("last_timer_update_utc")
        if side_started and last_utc is not None and isinstance(last_utc, (int, float)):
            elapsed = time.time() - float(last_utc)
            if side == "White" and wr is not None:
                wr = max(0.0, float(wr) - elapsed)
            elif side == "Black" and br is not None:
                br = max(0.0, float(br) - elapsed)
        if wr is not None:
            wr = max(0.0, float(wr))
        if br is not None:
            br = max(0.0, float(br))
        if wr is not None and wr <= 0:
            is_game_over = True
            term_reason = "time"
            winner = state.get("black_name") or "Black"
        elif br is not None and br <= 0:
            is_game_over = True
            term_reason = "time"
            winner = state.get("white_name") or "White"

    body = {
        "whiteRemainingSeconds": wr,
        "blackRemainingSeconds": br,
        "isGameOver": is_game_over,
    }
    if is_game_over:
        if term_reason is not None:
            body["terminationReason"] = term_reason
        if winner is not None:
            body["winner"] = winner

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-cache",
        },
        "body": json.dumps(body),
    }

