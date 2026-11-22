"""DynamoDB-backed Store, used by the Lambdas on AWS.

Single-table layout (pk / sk):
  CUSTOMER#<id>   PROFILE   data=<customer json>
  CONFIG          ESTIMATORS data=<list json>
  TICKET#<id>     TICKET    customer_id, status, version, data=<ticket json>,
                            gsi1pk=CUSTOMER#<id>, gsi1sk=TICKET#<id>
                            + approval attributes (reviewed_by, approved_hash,
                              approved_version) written ONLY by the approval role
  SESSION#<token> SESSION   written ONLY by the intake role
  COUNTER         TICKET    n
  AUDIT#<date>    <ts>#<id> data=<audit json>

Why approval fields live in their own attributes: the tools Lambda's IAM policy
uses a dynamodb:Attributes condition that omits them, so the agent's tool code
cannot write an approval even if it were fully compromised.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key

from .schema import Status, Ticket, ticket_from_dict, ticket_to_dict
from .store import Customer, Store

APPROVAL_ATTRS = ("reviewed_by", "approved_hash", "approved_version")
TOOLS_TICKET_ATTRS = ("pk", "sk", "customer_id", "status", "version", "data", "gsi1pk", "gsi1sk")


class DynamoStore(Store):
    """Same interface as Store, backed by DynamoDB. `writer` decides which
    attributes this process is allowed to write ("tools" or "approval")."""

    def __init__(self, table_name: str, writer: str = "tools", region: str | None = None):
        super().__init__()
        self.table = boto3.resource("dynamodb", region_name=region).Table(table_name)
        self.writer = writer
        self._loaded_estimators = False

    # ---- customers ----
    def customer(self, customer_id):
        if customer_id not in self.customers:
            item = self.table.get_item(Key={"pk": f"CUSTOMER#{customer_id}", "sk": "PROFILE"}).get("Item")
            if not item:
                raise KeyError(customer_id)
            self.customers[customer_id] = Customer(**json.loads(item["data"]))
        return self.customers[customer_id]

    def customer_by_email(self, email):
        email = (email or "").strip().casefold()
        item = self.table.get_item(Key={"pk": f"EMAIL#{email}", "sk": "CUSTOMER"}).get("Item")
        return self.customer(item["customer_id"]) if item else None

    def all_customers(self) -> list[Customer]:
        """Used by intake for look-alike and cross-customer checks."""
        resp = self.table.query(IndexName="gsi1", KeyConditionExpression=Key("gsi1pk").eq("CUSTOMERS"))
        for item in resp.get("Items", []):
            c = Customer(**json.loads(item["data"]))
            self.customers[c.customer_id] = c
        return list(self.customers.values())

    @property
    def estimators(self):
        if not self._loaded_estimators:
            item = self.table.get_item(Key={"pk": "CONFIG", "sk": "ESTIMATORS"}).get("Item")
            self._estimators = set(json.loads(item["data"])) if item else set()
            self._loaded_estimators = True
        return self._estimators

    @estimators.setter
    def estimators(self, value):
        self._estimators = set(value)

    # ---- tickets ----
    def new_ticket_id(self) -> str:
        resp = self.table.update_item(
            Key={"pk": "COUNTER", "sk": "TICKET"},
            UpdateExpression="ADD n :one", ExpressionAttributeValues={":one": 1},
            ReturnValues="UPDATED_NEW",
        )
        return f"T-{100 + int(resp['Attributes']['n']):04d}"

    def ticket(self, ticket_id):
        item = self.table.get_item(Key={"pk": f"TICKET#{ticket_id}", "sk": "TICKET"}).get("Item")
        return self._from_item(item) if item else None

    def tickets_for(self, customer_id):
        resp = self.table.query(IndexName="gsi1", KeyConditionExpression=Key("gsi1pk").eq(f"CUSTOMER#{customer_id}"))
        return [self._from_item(i) for i in resp.get("Items", [])]

    def put(self, t: Ticket) -> None:
        body = ticket_to_dict(t)
        for a in APPROVAL_ATTRS:
            body.pop(a, None)
        item = {
            "pk": f"TICKET#{t.ticket_id}", "sk": "TICKET",
            "customer_id": t.customer_id, "status": t.status.value, "version": t.version,
            "data": json.dumps(body, default=str),
            "gsi1pk": f"CUSTOMER#{t.customer_id}", "gsi1sk": f"TICKET#{t.ticket_id}",
        }
        if self.writer == "tools":
            # Agent-side writes: drafts and refusals only, never approval data.
            if t.status not in (Status.DRAFT, Status.REFUSED):
                raise PermissionError("tools writer may only store DRAFT or REFUSED tickets")
            self.table.put_item(Item=item)
            return
        for a in APPROVAL_ATTRS:
            item[a] = getattr(t, a)
        self.table.put_item(Item=item)

    def update_specs(self, ticket_id, changes, actor):
        if ticket_id not in self.tickets:
            self.ticket(ticket_id)
        t = super().update_specs(ticket_id, changes, actor)
        self.put(t)
        return t

    def _from_item(self, item) -> Ticket:
        d = json.loads(item["data"])
        for a in APPROVAL_ATTRS:
            v = item.get(a)
            d[a] = int(v) if a == "approved_version" and v is not None else v
        d["status"] = item["status"]
        d["version"] = int(item["version"])
        t = ticket_from_dict(d)
        self.tickets[t.ticket_id] = t
        return t

    # ---- sessions (intake writes, tools read) ----
    def put_session(self, ctx, ttl_seconds: int = 3600) -> str:
        token = "st_" + uuid.uuid4().hex + uuid.uuid4().hex[:8]
        self.table.put_item(Item={
            "pk": f"SESSION#{token}", "sk": "SESSION",
            "data": json.dumps({
                "session_id": ctx.session_id, "email_id": ctx.email_id, "customer_id": ctx.customer_id,
                "email_text": ctx.email_text, "email_norm": ctx.email_norm, "tainted": ctx.tainted,
                "intake_flags": [f.to_dict() for f in ctx.intake_flags],
            }),
            "expires_at": int(datetime.now(timezone.utc).timestamp()) + ttl_seconds,
        })
        return token

    def get_session(self, token: str):
        from .intake import SessionContext
        from .schema import Flag
        if not isinstance(token, str) or not token.startswith("st_"):
            return None
        item = self.table.get_item(Key={"pk": f"SESSION#{token}", "sk": "SESSION"}).get("Item")
        if not item or int(item["expires_at"]) < datetime.now(timezone.utc).timestamp():
            return None
        d = json.loads(item["data"])
        d["intake_flags"] = [Flag(**f) for f in d["intake_flags"]]
        return SessionContext(**d)

    # ---- audit ----
    def log(self, actor, action, decision, rule=None, control=None, detail=None):
        super().log(actor, action, decision, rule, control, detail)
        entry = self.audit[-1]
        print(json.dumps({"audit": entry}, default=str))  # CloudWatch copy
        self.table.put_item(Item={
            "pk": f"AUDIT#{entry['ts'][:10]}", "sk": f"{entry['ts']}#{uuid.uuid4().hex[:8]}",
            "data": json.dumps(entry, default=str),
        })
