"""
Lambda handler for running the full chess game (invoked async by api_game_start).
Reads payload, runs run_game with initial_state_dict. State is written to S3 after each move
via game_state (STATE_BUCKET/STATE_KEY). Timer values and last_timer_update_utc are written
so GET /api/tick can advance-on-read. Checks cancel marker (game_state/{id}.cancel) each turn.
LLM API keys from AWS Secrets Manager (llm-api-secrets).
"""
from __future__ import annotations

import json
import logging
import os

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Load .env when running locally (Lambda uses Secrets Manager)
try:
    import dotenv
    dotenv.load_dotenv()
except ImportError:
    pass

from src.game_state import DEFAULT_FEN
from src.game_loop import run_game
from src.llm_adapters import get_adapter_by_id

SECRET_NAME = "llm-api-secrets"

# Env vars used by adapters (for logging presence only; never log values)
LLM_KEY_VARS = (
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "ANTHROPIC_API_KEY",
    "MISTRAL_API_KEY",
    "COHERE_API_KEY",
    "GROQ_API_KEY",
    "XAI_API_KEY",
)


def _log_api_keys_present() -> None:
    """Log which LLM API keys are set (presence only) for debugging."""
    present = [k for k in LLM_KEY_VARS if os.environ.get(k)]
    logger.info("API keys present: %s", present or "none")


def _load_llm_secrets_into_env() -> None:
    """Fetch llm-api-secrets from AWS Secrets Manager and set API keys in os.environ."""
    region = os.environ.get("AWS_REGION", "us-east-1")
    session = boto3.session.Session()
    client = session.client(
        service_name="secretsmanager",
        region_name=region,
    )
    try:
        response = client.get_secret_value(SecretId=SECRET_NAME)
    except ClientError as e:
        raise RuntimeError(f"Failed to get secret {SECRET_NAME}: {e!s}") from e
    secret_str = response.get("SecretString")
    if not secret_str:
        return
    try:
        secrets = json.loads(secret_str)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Secret {SECRET_NAME} is not valid JSON: {e!s}") from e
    if isinstance(secrets, dict):
        for k, v in secrets.items():
            if v is not None and isinstance(v, str):
                os.environ.setdefault(k, v)


def handler(event: dict, context: object) -> dict:
    if not isinstance(event, dict):
        event = {}
    game_id = (event.get("game_id") or "").strip()
    if not game_id:
        return {"statusCode": 400, "body": json.dumps({"error": "game_id required"})}
    # So game_state writes go to game_state/{game_id}.json
    os.environ["STATE_KEY"] = f"game_state/{game_id}.json"
    logger.info("game_run invoked: game_id=%s white_llm_id=%s black_llm_id=%s", game_id, event.get("white_llm_id"), event.get("black_llm_id"))
    try:
        _load_llm_secrets_into_env()
        _log_api_keys_present()
        logger.info("Secrets loaded from Secrets Manager")
    except Exception as e:
        logger.exception("Failed to load secrets: %s", e)
        return {"statusCode": 500, "body": json.dumps({"error": "Secrets failed", "detail": str(e)})}
    white_id = (event.get("white_llm_id") or "").strip()
    black_id = (event.get("black_llm_id") or "").strip()
    if not white_id or not black_id:
        return {"statusCode": 400, "body": json.dumps({"error": "white_llm_id and black_llm_id required"})}

    white = get_adapter_by_id(white_id)
    black = get_adapter_by_id(black_id)
    if not white:
        return {"statusCode": 400, "body": json.dumps({"error": f"Unknown adapter: {white_id}"})}
    if not black:
        return {"statusCode": 400, "body": json.dumps({"error": f"Unknown adapter: {black_id}"})}
    logger.info("Adapters resolved: %s vs %s", white.name, black.name)

    max_retries = max(0, min(20, int(event.get("max_retries", 3))))
    time_sec = event.get("time_per_player_seconds")
    if time_sec is not None:
        try:
            time_sec = float(time_sec)
            if time_sec <= 0:
                time_sec = None
        except (TypeError, ValueError):
            time_sec = None
    starting_fen = (event.get("starting_fen") or "").strip() or None
    if starting_fen == "":
        starting_fen = None

    initial_state = {
        "fen": starting_fen or DEFAULT_FEN,
        "move_history": [],
        "white_name": white.name,
        "black_name": black.name,
        "is_game_over": False,
        "winner": None,
        "termination_reason": None,
        "move_log": [],
    }
    if time_sec is not None and time_sec > 0:
        initial_state["white_remaining_seconds"] = time_sec
        initial_state["black_remaining_seconds"] = time_sec

    try:
        logger.info("Starting run_game (first move will call LLM)...")
        run_game(
            white,
            black,
            max_retries=max_retries,
            time_per_player_seconds=time_sec,
            starting_fen=starting_fen,
            initial_state_dict=initial_state,
            game_id=game_id,
        )
        logger.info("run_game finished successfully")
    except Exception as e:
        logger.exception("Game run failed: %s", e)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Game run failed", "detail": str(e)}),
        }

    return {"statusCode": 200, "body": json.dumps({"message": "Game completed"})}
