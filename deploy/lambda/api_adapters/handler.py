"""
Lambda handler for GET /api/adapters.
Returns a static list of adapter id/name for the start-game UI (matches src/llm_adapters.py).
"""
from __future__ import annotations

import json

# Static list matching get_available_adapters() display names and ids
ADAPTERS = [
    {"id": "chatgpt", "name": "ChatGPT 5.2"},
    {"id": "gemini", "name": "Gemini"},
    {"id": "claude", "name": "Claude"},
    {"id": "mistral", "name": "Mistral"},
    {"id": "cohere", "name": "Cohere"},
    {"id": "llama_groq", "name": "Llama (Groq)"},
    {"id": "grok", "name": "Grok"},
]


def handler(event: dict, context: object) -> dict:
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-cache",
        },
        "body": json.dumps(ADAPTERS),
    }
