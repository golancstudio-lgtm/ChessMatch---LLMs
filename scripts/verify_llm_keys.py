#!/usr/bin/env python3
"""
Verify that OpenAI and Google (Gemini) API keys work by making minimal API calls.
Use this to confirm the app can "talk" to the LLMs before running a game.

Usage:
  # From project root, using .env (local)
  python scripts/verify_llm_keys.py

  # From project root, load keys from AWS Secrets Manager (llm-api-secrets)
  python scripts/verify_llm_keys.py --aws

  # Test only one provider
  python scripts/verify_llm_keys.py --openai
  python scripts/verify_llm_keys.py --gemini
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Project root (parent of scripts/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.chdir(PROJECT_ROOT)

# Load .env
try:
    import dotenv
    dotenv.load_dotenv()
except ImportError:
    pass


def load_secrets_from_aws() -> None:
    """Load llm-api-secrets from AWS Secrets Manager into os.environ."""
    import json
    try:
        import boto3
        from botocore.exceptions import ClientError
    except ImportError:
        print("  ERROR: boto3 is required for --aws. Run: pip install boto3")
        sys.exit(1)
    secret_name = "llm-api-secrets"
    region = os.environ.get("AWS_REGION", "us-east-1")
    client = boto3.client("secretsmanager", region_name=region)
    try:
        resp = client.get_secret_value(SecretId=secret_name)
    except ClientError as e:
        print(f"  ERROR: Could not get secret: {e}")
        sys.exit(1)
    secret_str = resp.get("SecretString")
    if not secret_str:
        print("  ERROR: Secret has no SecretString")
        sys.exit(1)
    try:
        data = json.loads(secret_str)
    except json.JSONDecodeError as e:
        print(f"  ERROR: Secret is not valid JSON: {e}")
        sys.exit(1)
    if isinstance(data, dict):
        for k, v in data.items():
            if v is not None and isinstance(v, str):
                os.environ.setdefault(k, v)
        print(f"  Loaded {len(data)} keys from Secrets Manager.")


def test_openai() -> bool:
    """Make a minimal OpenAI chat completion. Returns True if success."""
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        print("  OPENAI_API_KEY not set (add to .env or llm-api-secrets).")
        return False
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key)
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "Reply with exactly: OK"}],
            max_tokens=10,
        )
        text = (r.choices[0].message.content or "").strip()
        print(f"  Response: {text!r}")
        return True
    except Exception as e:
        print(f"  ERROR: {e}")
        return False


def test_gemini() -> bool:
    """Make a minimal Gemini generate_content call. Returns True if success."""
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key or not key.strip():
        print("  GEMINI_API_KEY / GOOGLE_API_KEY not set (add to .env or llm-api-secrets).")
        return False
    key = key.strip()
    try:
        from google import genai
        client = genai.Client(api_key=key)
        from google.genai import types
        r = client.models.generate_content(
            model="gemini-2.5-flash",
            contents="Reply with exactly: OK",
            config=types.GenerateContentConfig(max_output_tokens=10),
        )
        text = (r.text or "").strip()
        print(f"  Response: {text!r}")
        return True
    except Exception as e:
        print(f"  ERROR: {e}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify OpenAI and Gemini API keys.")
    parser.add_argument("--aws", action="store_true", help="Load keys from AWS Secrets Manager (llm-api-secrets)")
    parser.add_argument("--openai", action="store_true", help="Test only OpenAI")
    parser.add_argument("--gemini", action="store_true", help="Test only Gemini")
    args = parser.parse_args()
    if args.aws:
        print("Loading keys from AWS Secrets Manager (llm-api-secrets)...")
        load_secrets_from_aws()
    test_both = not args.openai and not args.gemini
    ok = True
    if test_both or args.openai:
        print("\nOpenAI (OPENAI_API_KEY):")
        if not test_openai():
            ok = False
    if test_both or args.gemini:
        print("\nGemini (GEMINI_API_KEY / GOOGLE_API_KEY):")
        if not test_gemini():
            ok = False
    print()
    if ok:
        print("All checked APIs responded successfully. The app can talk to these LLMs.")
    else:
        print("One or more checks failed. Fix keys in .env or llm-api-secrets and redeploy.")
        sys.exit(1)


if __name__ == "__main__":
    main()
