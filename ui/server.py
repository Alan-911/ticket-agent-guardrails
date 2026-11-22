"""Estimator console: a local-only web UI over the deployed stack.

    uv run --group infra python ui/server.py        # http://127.0.0.1:8765

Binds to 127.0.0.1 only and uses your local AWS credentials. Every action goes
through the same deployed Lambdas a real shop would use: intake (agent),
approval (estimator + client) and release (hot folders).
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.config import Config

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "redteam")]
REGION = "us-east-1"
HOST, PORT = "127.0.0.1", 8765
ESTIMATOR = "est-rosa"

OUT = json.loads((ROOT / "cdk-outputs.json").read_text())["TicketAgent"]
table = boto3.resource("dynamodb", region_name=REGION).Table(OUT["TableName"])
lam = boto3.client("lambda", region_name=REGION, config=Config(read_timeout=320, retries={"max_attempts": 0}))
s3 = boto3.client("s3", region_name=REGION)


def inbox() -> list[dict]:
    from attacks import CASES
    samples = json.loads((ROOT / "samples/emails/emails.json").read_text(encoding="utf-8"))["emails"]
    items = [{"id": e["message_id"], "kind": e.get("kind", "sample"), "category": "Sample", "goal": "", "email": e}
             for e in samples]
    items += [{"id": c["id"], "kind": "baseline" if c["category"] == "Baseline" else "attack",
               "category": c["category"], "goal": c["goal"], "email": c["email"]} for c in CASES]
    return items


def invoke(fn: str, payload: dict) -> dict:
    resp = lam.invoke(FunctionName=OUT[fn], Payload=json.dumps(payload).encode())
    return json.loads(resp["Payload"].read())


def tickets() -> list[dict]:
    items, kw = [], {"FilterExpression": Attr("pk").begins_with("TICKET#")}
    while True:
        r = table.scan(**kw)
        items += r.get("Items", [])
        if "LastEvaluatedKey" not in r:
            break
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    out = []
    for i in items:
        d = json.loads(i["data"])
        d["status"] = i["status"]
        d["version"] = int(i["version"])
        for a in ("reviewed_by", "approved_hash", "approved_version"):
            d[a] = str(i[a]) if i.get(a) is not None else None
        out.append(d)
    out.sort(key=lambda t: t["ticket_id"], reverse=True)
    return out


def audit(limit: int = 200) -> list[dict]:
    days = sorted({i["pk"] for i in table.scan(ProjectionExpression="pk",
                   FilterExpression=Attr("pk").begins_with("AUDIT#")).get("Items", [])}, reverse=True)[:2]
    entries = []
    for day in days:
        entries += [json.loads(i["data"]) for i in
                    table.query(KeyConditionExpression=Key("pk").eq(day), ScanIndexForward=False).get("Items", [])]
    entries.sort(key=lambda e: e["ts"], reverse=True)
    return entries[:limit]


def hotfolders() -> list[dict]:
    r = s3.list_objects_v2(Bucket=OUT["HotFolderBucket"])
    return [{"key": o["Key"], "size": o["Size"], "at": o["LastModified"].isoformat()} for o in r.get("Contents", [])]


def customers() -> dict:
    r = table.query(IndexName="gsi1", KeyConditionExpression=Key("gsi1pk").eq("CUSTOMERS"))
    return {json.loads(i["data"])["customer_id"]: {k: v for k, v in json.loads(i["data"]).items() if k != "internal"}
            for i in r.get("Items", [])}


ACTIONS = {
    "run_email": lambda b: invoke("IntakeFn", {"email": b["email"]}),
    "review": lambda b: invoke("ApprovalFn", {"action": "mark_reviewed", "ticket_id": b["ticket_id"], "estimator_id": ESTIMATOR}),
    "send_proof": lambda b: invoke("ApprovalFn", {"action": "send_proof", "ticket_id": b["ticket_id"], "estimator_id": ESTIMATOR}),
    "approve": lambda b: invoke("ApprovalFn", {"action": "record_approval", "token": b["token"], "approver_email": b["approver_email"]}),
    "update": lambda b: invoke("ApprovalFn", {"action": "update_specs", "ticket_id": b["ticket_id"], "changes": b["changes"], "actor": "csr-ui"}),
    "release": lambda b: invoke("ReleaseFn", {"ticket_id": b["ticket_id"], "token": b["token"]}),
}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        routes = {"/api/inbox": inbox, "/api/tickets": tickets, "/api/audit": audit,
                  "/api/hotfolders": hotfolders, "/api/customers": customers}
        if self.path in ("/", "/index.html"):
            return self._send(200, (ROOT / "ui" / "index.html").read_bytes(), "text/html; charset=utf-8")
        if self.path in routes:
            try:
                return self._send(200, routes[self.path]())
            except Exception as e:  # noqa: BLE001
                return self._send(500, {"error": str(e)})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.headers.get("Origin") not in (None, f"http://{HOST}:{PORT}", f"http://localhost:{PORT}"):
            return self._send(403, {"error": "cross-origin request refused"})
        name = self.path.removeprefix("/api/")
        if name not in ACTIONS:
            return self._send(404, {"error": "unknown action"})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        try:
            self._send(200, ACTIONS[name](body))
        except Exception as e:  # noqa: BLE001
            self._send(500, {"error": str(e)})

    def log_message(self, fmt, *args):
        sys.stderr.write("%s\n" % (fmt % args))


if __name__ == "__main__":
    print(f"Estimator console on http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
