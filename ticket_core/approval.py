"""Human review, proofs and client approval (rules R4 and R11).

None of this is reachable by the agent. An estimator reviews the draft, the
proof goes to the email address ON FILE (never one taken from the email), and
the client's approval token is an HMAC over the exact spec hash. Change one
job field after approval and the token no longer verifies.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Callable

from .errors import Denied
from .schema import Status, Ticket, spec_hash
from .store import Store

TOKEN_TTL_SECONDS = 7 * 24 * 3600


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def _unb64(s: str) -> str:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode()


def issue_token(ticket: Ticket, secret: bytes, now: float) -> str:
    msg = f"{ticket.ticket_id}|{ticket.version}|{spec_hash(ticket)}|{int(now) + TOKEN_TTL_SECONDS}"
    sig = hmac.new(secret, msg.encode(), hashlib.sha256).hexdigest()
    return f"{_b64(msg)}.{sig}"


def parse_token(token: str) -> tuple[str, int, str, int, str, str]:
    try:
        body, sig = token.rsplit(".", 1)
        msg = _unb64(body)
        ticket_id, version, h, exp = msg.split("|")
        return ticket_id, int(version), h, int(exp), msg, sig
    except Exception:
        raise Denied("R11", "approval-token", "malformed approval token") from None


def verify_token(token: str, ticket: Ticket, secret: bytes, now: float) -> None:
    ticket_id, version, h, exp, msg, sig = parse_token(token)
    expected = hmac.new(secret, msg.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise Denied("R11", "approval-token", "approval token signature is invalid")
    if now > exp:
        raise Denied("R11", "approval-token", "approval token has expired")
    if ticket_id != ticket.ticket_id:
        raise Denied("R11", "approval-token", "token is for a different ticket")
    if version != ticket.version or not hmac.compare_digest(h, spec_hash(ticket)):
        raise Denied("R11", "spec-hash-binding",
                     "ticket changed since the client approved it; a new proof is required")


class ApprovalService:
    def __init__(self, store: Store, secret: bytes, clock: Callable[[], float] = time.time):
        self.store = store
        self.secret = secret
        self.clock = clock
        self.outbox: list[dict] = []  # stands in for SES

    def _deny(self, actor, action, d: Denied):
        self.store.log(actor, action, "DENY", d.rule, d.control, {"reason": d.reason})
        raise d

    def mark_reviewed(self, ticket_id: str, estimator_id: str) -> Ticket:
        t = self.store.ticket(ticket_id)
        if estimator_id not in self.store.estimators:
            self._deny(estimator_id, "mark_reviewed", Denied("R4", "estimator-auth", "not an estimator"))
        if t is None or t.status != Status.DRAFT:
            self._deny(estimator_id, "mark_reviewed", Denied("R4", "status-guard", "only drafts can be reviewed"))
        t.status, t.reviewed_by = Status.IN_REVIEW, estimator_id
        self.store.log(estimator_id, "mark_reviewed", "ALLOW", detail={"ticket_id": ticket_id})
        return t

    def send_proof(self, ticket_id: str, estimator_id: str) -> str:
        t = self.store.ticket(ticket_id)
        if estimator_id not in self.store.estimators:
            self._deny(estimator_id, "send_proof", Denied("R4", "estimator-auth", "not an estimator"))
        if t is None or t.status != Status.IN_REVIEW or not t.reviewed_by:
            self._deny(estimator_id, "send_proof", Denied("R4", "status-guard", "ticket must be reviewed first"))
        if t.price is None or t.blocking_flags():
            codes = [f.code for f in t.blocking_flags()] or ["NO_PRICE"]
            self._deny(estimator_id, "send_proof", Denied("R9", "blocking-flags", f"unresolved: {codes}"))
        customer = self.store.customer(t.customer_id)
        token = issue_token(t, self.secret, self.clock())
        self.outbox.append({"to": customer.email, "ticket_id": t.ticket_id, "version": t.version,
                            "specs": t.specs, "price": t.price, "approve_token": token})
        t.status = Status.PROOF_SENT
        self.store.log(estimator_id, "send_proof", "ALLOW", detail={"ticket_id": ticket_id, "to": customer.email})
        return token

    def record_approval(self, token: str, approver_email: str) -> Ticket:
        ticket_id = parse_token(token)[0]
        t = self.store.ticket(ticket_id)
        if t is None:
            self._deny(approver_email, "record_approval", Denied("R11", "approval-token", "unknown ticket"))
        customer = self.store.customer(t.customer_id)
        if approver_email.strip().casefold() != customer.email.casefold():
            self._deny(approver_email, "record_approval",
                       Denied("R11", "approver-on-file", "approval must come from the address on file"))
        if t.status != Status.PROOF_SENT:
            self._deny(approver_email, "record_approval", Denied("R11", "status-guard", "no proof outstanding"))
        try:
            verify_token(token, t, self.secret, self.clock())
        except Denied as d:
            self._deny(approver_email, "record_approval", d)
        t.status = Status.APPROVED
        t.approved_hash = spec_hash(t)
        t.approved_version = t.version
        self.store.log(approver_email, "record_approval", "ALLOW", detail={"ticket_id": ticket_id})
        return t
