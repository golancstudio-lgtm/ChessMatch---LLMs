"""
Lambda handler for GET /api/state.
Reads game state from S3 (game_state/state.json) and returns JSON for the frontend.
Environment: STATE_BUCKET, STATE_KEY (default game_state/state.json).
"""
from __future__ import annotations

import json
import os
from typing import Any

import boto3

DEFAULT_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def _get_state_from_s3() -> dict[str, Any] | None:
    bucket = os.environ.get("STATE_BUCKET", "").strip()
    key = os.environ.get("STATE_KEY", "game_state/state.json").strip()
    if not bucket:
        return None
    try:
        s3 = boto3.client("s3")
        obj = s3.get_object(Bucket=bucket, Key=key)
        data = json.loads(obj["Body"].read().decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _move_log_camel(entries: list) -> list:
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


def _build_response(state: dict[str, Any]) -> dict:
    return {
        "fen": state.get("fen", DEFAULT_FEN),
        "moveHistory": state.get("move_history") or [],
        "whiteName": state.get("white_name"),
        "blackName": state.get("black_name"),
        "isGameOver": bool(state.get("is_game_over", False)),
        "winner": state.get("winner"),
        "terminationReason": state.get("termination_reason"),
        "moveLog": _move_log_camel(state.get("move_log")),
    }


def handler(event: dict, context: object) -> dict:
    state_data = _get_state_from_s3()
    if state_data is None:
        body = _build_response({})
    else:
        body = _build_response(state_data)

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-cache",
        },
        "body": json.dumps(body),
    }
