"""In-memory mock of the print MIS (Pace-like). DynamoDB replaces it on AWS.

Kept deliberately simple: customers, tickets, estimators and an audit log.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .schema import SPEC_FIELDS, Status, Ticket


@dataclass
class Customer:
    customer_id: str
    company: str
    contact: str
    email: str
    domain: str
    ship_to_default: str
    contract_discount_pct: float
    internal: dict  # never returned to the agent (R8)

    def public_view(self) -> dict:
        return {
            "customer_id": self.customer_id,
            "company": self.company,
            "contact": self.contact,
            "ship_to_default": self.ship_to_default,
        }


class Store:
    def __init__(self):
        self.customers: dict[str, Customer] = {}
        self.tickets: dict[str, Ticket] = {}
        self.estimators: set[str] = set()
        self.audit: list[dict] = []
        self._next = 100

    @classmethod
    def load(cls, customers_path: str | Path, archive_path: str | Path | None = None) -> "Store":
        s = cls()
        data = json.loads(Path(customers_path).read_text(encoding="utf-8"))
        s.estimators = set(data["estimators"])
        for c in data["customers"]:
            s.customers[c["customer_id"]] = Customer(**c)
        if archive_path:
            for t in json.loads(Path(archive_path).read_text(encoding="utf-8"))["tickets"]:
                s.tickets[t["ticket_id"]] = Ticket(
                    ticket_id=t["ticket_id"], customer_id=t["customer_id"], source_email_id="archive",
                    specs=t["specs"], price=None, status=Status(t["status"]), tainted=False, needs_review=False,
                )
        return s

    # customers
    def customer_by_email(self, email: str) -> Customer | None:
        email = (email or "").strip().casefold()
        return next((c for c in self.customers.values() if c.email.casefold() == email), None)

    def customer(self, customer_id: str) -> Customer:
        return self.customers[customer_id]

    def all_customers(self) -> list[Customer]:
        return list(self.customers.values())

    # tickets
    def new_ticket_id(self) -> str:
        self._next += 1
        return f"T-{self._next:04d}"

    def ticket(self, ticket_id: str) -> Ticket | None:
        return self.tickets.get(ticket_id)

    def put(self, ticket: Ticket) -> None:
        self.tickets[ticket.ticket_id] = ticket

    def tickets_for(self, customer_id: str) -> list[Ticket]:
        return [t for t in self.tickets.values() if t.customer_id == customer_id]

    def update_specs(self, ticket_id: str, changes: dict, actor: str) -> Ticket:
        """The supported way to change a ticket. Any change to job specs bumps
        the version and sends the ticket back to DRAFT, voiding approval."""
        t = self.tickets[ticket_id]
        changed = {k: v for k, v in changes.items() if k in SPEC_FIELDS and t.specs.get(k) != v}
        if not changed:
            return t
        t.specs.update(changed)
        t.version += 1
        t.status = Status.DRAFT
        t.reviewed_by = None
        t.approved_hash = None
        t.approved_version = None
        t.price = None  # must be re-estimated
        self.log(actor, "update_specs", "ALLOW", detail={"ticket_id": ticket_id, "changed": sorted(changed)})
        return t

    # audit
    def log(self, actor: str, action: str, decision: str, rule: str | None = None,
            control: str | None = None, detail: dict | None = None) -> None:
        self.audit.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "actor": actor, "action": action, "decision": decision,
            "rule": rule, "control": control, "detail": detail or {},
        })
