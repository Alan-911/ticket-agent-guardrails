"""Job ticket model and the spec hash that approvals are bound to (rule R11)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum


class Status(str, Enum):
    DRAFT = "DRAFT"            # created by the agent
    REFUSED = "REFUSED"        # prohibited work, created by the agent
    IN_REVIEW = "IN_REVIEW"    # an estimator has reviewed it
    PROOF_SENT = "PROOF_SENT"  # proof emailed to the address on file
    APPROVED = "APPROVED"      # client approved this exact version
    RELEASED = "RELEASED"      # written to a hot folder by the release gate


# Fields the agent may fill in. Everything else is set by code or by people.
SPEC_FIELDS = (
    "product", "quantity", "finished_size", "stock", "colors",
    "finishing", "due_date", "ship_to",
)
REQUIRED_SPECS = ("product", "quantity", "finished_size", "stock", "colors")
PRICE_FIELDS = ("list_cents", "discount_pct", "total_cents", "unit_cents", "rush")

# Codes that stop a proof from being sent until a person resolves them.
BLOCKING_FLAG_PREFIXES = ("MISSING_", "UNPRICEABLE", "SENDER_", "LOOKALIKE_")


@dataclass
class Flag:
    code: str
    message: str
    rule: str
    raised_by: str = "code"  # "code" flags can't be removed by the agent

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Ticket:
    ticket_id: str
    customer_id: str
    source_email_id: str
    specs: dict
    price: dict | None
    status: Status = Status.DRAFT
    version: int = 1
    tainted: bool = True          # built from untrusted customer content
    needs_review: bool = True
    flags: list[Flag] = field(default_factory=list)
    sources: dict = field(default_factory=dict)
    reviewed_by: str | None = None
    approved_hash: str | None = None
    approved_version: int | None = None
    refusal: dict | None = None

    def blocking_flags(self) -> list[Flag]:
        return [f for f in self.flags if f.code.startswith(BLOCKING_FLAG_PREFIXES)]


def spec_hash(ticket: Ticket) -> str:
    """SHA-256 over everything that changes what gets printed or charged."""
    payload = {
        "ticket_id": ticket.ticket_id,
        "customer_id": ticket.customer_id,
        "specs": {k: ticket.specs.get(k) for k in SPEC_FIELDS},
        "price": {k: (ticket.price or {}).get(k) for k in PRICE_FIELDS},
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
