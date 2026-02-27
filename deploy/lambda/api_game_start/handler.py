"""
Lambda handler for POST /api/game/start.
Validates input, writes initial state to S3 (per-game key, no timer fields; game_run sets those).
Invokes game_run Lambda async, returns 202 with game_id.
Env: STATE_BUCKET, STATE_KEY, GAME_RUN_LAMBDA_NAME.
"""
from __future__ import annotations

import json
import logging
import os
import uuid

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Must match api_adapters ADAPTERS and src/llm_adapters
ADAPTERS = [
    {"id": "chatgpt", "name": "ChatGPT 5.2"},
    {"id": "gemini", "name": "Gemini"},
    {"id": "claude", "name": "Claude"},
    {"id": "mistral", "name": "Mistral"},
    {"id": "cohere", "name": "Cohere"},
    {"id": "llama_groq", "name": "Llama (Groq)"},
    {"id": "grok", "name": "Grok"},
]

DEFAULT_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def _adapter_name(adapter_id: str) -> str | None:
    for a in ADAPTERS:
        if a["id"] == adapter_id:
            return a.get("name") or adapter_id
    return None


def handler(event: dict, context: object) -> dict:
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _response(400, {"detail": "Invalid JSON body"})

    white_id = (body.get("white_llm_id") or "").strip()
    black_id = (body.get("black_llm_id") or "").strip()
    if not white_id or not black_id:
        return _response(400, {"detail": "white_llm_id and black_llm_id are required"})

    white_name = _adapter_name(white_id)
    black_name = _adapter_name(black_id)
    if not white_name:
        return _response(400, {"detail": f"Unknown White adapter: {white_id}"})
    if not black_name:
        return _response(400, {"detail": f"Unknown Black adapter: {black_id}"})

    max_retries = max(0, min(20, int(body.get("max_retries", 3))))
    time_sec = body.get("time_per_player_seconds")
    if time_sec is not None:
        try:
            time_sec = float(time_sec)
            if time_sec <= 0:
                time_sec = None
        except (TypeError, ValueError):
            time_sec = None
    starting_fen = (body.get("starting_fen") or "").strip() or None
    if starting_fen == "":
        starting_fen = None

    bucket = os.environ.get("STATE_BUCKET", "").strip()
    run_lambda = os.environ.get("GAME_RUN_LAMBDA_NAME", "").strip()
    if not bucket or not run_lambda:
        return _response(503, {"detail": "Game start not configured (STATE_BUCKET / GAME_RUN_LAMBDA_NAME)"})

    game_id = str(uuid.uuid4())
    state_key = f"game_state/{game_id}.json"
    initial_state = {
        "fen": starting_fen or DEFAULT_FEN,
        "move_history": [],
        "white_name": white_name,
        "black_name": black_name,
        "is_game_over": False,
        "winner": None,
        "termination_reason": None,
        "move_log": [],
    }

    try:
        s3 = boto3.client("s3")
        s3.put_object(
            Bucket=bucket,
            Key=state_key,
            Body=json.dumps(initial_state, indent=0).encode("utf-8"),
            ContentType="application/json",
        )
        logger.info("Wrote initial state to s3://%s/%s", bucket, state_key)
    except Exception as e:
        logger.exception("Failed to write initial state: %s", e)
        return _response(500, {"detail": f"Failed to write initial state: {e!s}"})

    payload = {
        "game_id": game_id,
        "white_llm_id": white_id,
        "black_llm_id": black_id,
        "max_retries": max_retries,
        "time_per_player_seconds": time_sec,
        "starting_fen": starting_fen,
    }
    try:
        boto3.client("lambda").invoke(
            FunctionName=run_lambda,
            InvocationType="Event",
            Payload=json.dumps(payload),
        )
        logger.info("Invoked game_run Lambda: %s", run_lambda)
    except Exception as e:
        logger.exception("Failed to invoke game_run: %s", e)
        return _response(500, {"detail": f"Failed to start game runner: {e!s}"})

    return _response(202, {"message": "Game started", "game_id": game_id, "white": white_name, "black": black_name})


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type, Accept",
        },
        "body": json.dumps(body),
    }
