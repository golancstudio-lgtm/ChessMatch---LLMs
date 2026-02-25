"""
Lambda handler for GET /api/events.
Returns a single SSE event (state_updated). API Gateway REST does not support
long-lived streams; the frontend can poll GET /api/state when using this API.
"""
from __future__ import annotations


def handler(event: dict, context: object) -> dict:
    body = "event: state_updated\ndata: {}\n\n"
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        },
        "body": body,
    }
