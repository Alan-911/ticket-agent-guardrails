"""Send one sample email through intake -> Guardrails -> Ticket Agent.

    uv run --group infra python infra/run_email.py E-001-benign-cards [--model us.anthropic.claude-sonnet-4-6]
"""
import argparse
import json
from pathlib import Path

import boto3
from botocore.config import Config

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("message_id")
    ap.add_argument("--model")
    a = ap.parse_args()
    outputs = json.loads((ROOT / "cdk-outputs.json").read_text())["TicketAgent"]
    emails = {e["message_id"]: e for e in json.loads((ROOT / "samples/emails/emails.json").read_text(encoding="utf-8"))["emails"]}
    payload = {"email": emails[a.message_id]}
    if a.model:
        payload["model_id"] = a.model
    lam = boto3.client("lambda", region_name="us-east-1", config=Config(read_timeout=320, retries={"max_attempts": 0}))
    resp = lam.invoke(FunctionName=outputs["IntakeFn"], Payload=json.dumps(payload).encode())
    body = json.loads(resp["Payload"].read())
    print(json.dumps(body, indent=2))


if __name__ == "__main__":
    main()
