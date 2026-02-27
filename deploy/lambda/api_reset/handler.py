"""
Lambda handler for POST /api/game/reset.
Writes default (empty) game state to S3 and a cancel marker so the game_run Lambda
stops on its next iteration. GET /api/state then returns a cleared board.
Environment: STATE_BUCKET, STATE_KEY (default game_state/state.json).
"""
from __future__ import annotations

import json
import os

import boto3

DEFAULT_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def _get_state_key(event: dict) -> str:
    try:
        body = json.loads(event.get("body") or "{}")
        game_id = (body.get("game_id") or "").strip()
        if game_id:
            return f"game_state/{game_id}.json"
    except json.JSONDecodeError:
        pass
    return os.environ.get("STATE_KEY", "game_state/state.json").strip() or "game_state/state.json"


def handler(event: dict, context: object) -> dict:
    bucket = os.environ.get("STATE_BUCKET", "").strip()
    key = _get_state_key(event)
    if not bucket:
        return {
            "statusCode": 500,
            "headers": {"Access-Control-Allow-Origin": "*"},
            "body": json.dumps({"detail": "STATE_BUCKET not configured"}),
        }
    default_state = {
        "fen": DEFAULT_FEN,
        "move_history": [],
        "white_name": None,
        "black_name": None,
        "is_game_over": False,
        "winner": None,
        "termination_reason": None,
        "move_log": [],
    }
    try:
        # Write cleared state so frontend sees an empty board.
        body = json.dumps(default_state, indent=0)
        boto3.client("s3").put_object(
            Bucket=bucket,
            Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/json",
        )
        # Also write a cancel marker for this game so the game_run Lambda can
        # notice and stop on its next loop iteration.
        # The cancel key is derived from the per-game state key.
        if key.startswith("game_state/") and key.endswith(".json"):
            cancel_key = key[:-5] + ".cancel"
            try:
                boto3.client("s3").put_object(
                    Bucket=bucket,
                    Key=cancel_key,
                    Body=b"{}",
                    ContentType="application/json",
                )
            except Exception:
                # Best-effort only; the reset still succeeded.
                pass
        return {
            "statusCode": 200,
            "headers": {"Access-Control-Allow-Origin": "*", "Content-Type": "application/json"},
            "body": json.dumps({"status": "reset", "message": "Game state cleared."}),
        }
    except Exception as e:
        return {
            "statusCode": 500,
            "headers": {"Access-Control-Allow-Origin": "*", "Content-Type": "application/json"},
            "body": json.dumps({"detail": str(e)}),
        }
