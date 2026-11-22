"""Estimator and client actions. Never reachable by the agent.

Its role is the only one (with release) that can read the approval secret and
write the approval attributes on a ticket.
"""
import os

import boto3

from ticket_core.approval import ApprovalService
from ticket_core.ddb_store import DynamoStore
from ticket_core.errors import Denied

_secret = None


def secret() -> bytes:
    global _secret
    if _secret is None:
        sm = boto3.client("secretsmanager")
        _secret = sm.get_secret_value(SecretId=os.environ["APPROVAL_SECRET_ARN"])["SecretString"].encode()
    return _secret


def handler(event, context):
    store = DynamoStore(os.environ["TABLE_NAME"], writer="approval")
    svc = ApprovalService(store, secret())
    action = event.get("action")
    try:
        if action in ("mark_reviewed", "send_proof"):
            store.ticket(event["ticket_id"])  # warm cache
        if action == "mark_reviewed":
            t = svc.mark_reviewed(event["ticket_id"], event["estimator_id"])
            store.put(t)
            return {"ok": True, "status": t.status.value}
        if action == "send_proof":
            token = svc.send_proof(event["ticket_id"], event["estimator_id"])
            store.put(store.tickets[event["ticket_id"]])
            # Stand-in for SES: the proof goes only to the address on file.
            return {"ok": True, "proof_email": svc.outbox[-1]}
        if action == "record_approval":
            from ticket_core.approval import parse_token
            store.ticket(parse_token(event["token"])[0])
            t = svc.record_approval(event["token"], event["approver_email"])
            store.put(t)
            return {"ok": True, "status": t.status.value, "approved_hash": t.approved_hash}
        if action == "update_specs":
            t = store.update_specs(event["ticket_id"], event["changes"], event["actor"])
            return {"ok": True, "status": t.status.value, "version": t.version}
    except Denied as d:
        return {"denied": True, "rule": d.rule, "control": d.control, "reason": d.reason}
    return {"error": f"unknown action {action!r}"}
