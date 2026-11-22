"""The only things the Ticket Agent can do.

Each public method here becomes one tool behind AgentCore Gateway. The checks
in this file run no matter what the model asks for: they read the customer
from the SessionContext that intake fixed, never from tool arguments.

Note what is missing on purpose: there is no approve, send_proof, email,
route or release tool. Rule R4 holds because the capability does not exist.
"""
from __future__ import annotations

import json
import re
from datetime import date
from typing import Callable

from . import prohibited
from .errors import Denied, PricingError
from .intake import SessionContext
from .pricing import PricingTable
from .schema import REQUIRED_SPECS, SPEC_FIELDS, Flag, Status, Ticket
from .store import Store
from .text import normalize

# Keys an attacker (or a confused model) might try to sneak into specs.
FORBIDDEN_KEYS = {
    "status", "price", "list_cents", "total_cents", "unit_cents", "unit_price", "total_price",
    "discount", "discount_pct", "approved", "approved_by", "approved_hash", "device", "route",
    "hotfolder", "hot_folder", "needs_review", "tainted", "customer_id", "version", "reviewed_by",
}
REFUSAL_CATEGORIES = {"CURRENCY", "GOVERNMENT_ID", "NEGOTIABLE_INSTRUMENT", "COUNTERFEIT_BRAND", "HATEFUL", "OTHER"}
UNUSUAL_QTY_FACTOR = 5
ARCHIVE_SOURCE = re.compile(r"^ticket:(T-\d{4})$")

AGENT = "ticket-agent"


class TicketTools:
    """One instance per agent session. `ctx` is set by intake, not by the model."""

    def __init__(self, store: Store, pricing: PricingTable, ctx: SessionContext,
                 today: Callable[[], date] = date.today):
        self.store = store
        self.pricing = pricing
        self.ctx = ctx
        self.today = today

    # ---------- read tools ----------

    def lookup_customer(self, customer_id: str | None = None) -> dict:
        """Return the sender's own account. Any other customer id is refused."""
        return self._run("lookup_customer", {"customer_id": customer_id}, lambda: self._lookup(customer_id))

    def search_past_tickets(self, product: str | None = None, customer_id: str | None = None) -> list[dict]:
        """Past tickets for the sender's account only, newest first."""
        return self._run("search_past_tickets", {"product": product, "customer_id": customer_id},
                         lambda: self._search(product, customer_id))

    def estimate_price(self, specs: dict) -> dict:
        """Price specs from the shop's table. The agent can't supply a price or discount."""
        return self._run("estimate_price", {"specs": specs}, lambda: self._estimate_view(specs))

    # ---------- write tools ----------

    def save_draft_ticket(self, specs: dict, sources: dict | None = None, notes: list[str] | None = None,
                          requested_discount_pct: float = 0, ticket_id: str | None = None) -> dict:
        """Create or update a DRAFT ticket. Every non-empty spec needs a source:
        a quote from the email, or 'ticket:T-xxxx' for one of this customer's past tickets."""
        args = {"specs": specs, "sources": sources, "requested_discount_pct": requested_discount_pct,
                "ticket_id": ticket_id}
        return self._run("save_draft_ticket", args,
                         lambda: self._save(specs, sources or {}, notes or [], requested_discount_pct, ticket_id))

    def flag_for_review(self, ticket_id: str, message: str) -> dict:
        """Add a question or concern for the estimator on one of this session's tickets."""
        def go():
            t = self._own_ticket(ticket_id)
            t.flags.append(Flag("AGENT_NOTE", message[:500], "R9", raised_by="agent"))
            return self._view(t)
        return self._run("flag_for_review", {"ticket_id": ticket_id, "message": message}, go)

    def refuse_job(self, category: str, explanation: str) -> dict:
        """Record a refusal instead of a ticket (rule R6)."""
        def go():
            self._require_customer()
            cat = category if category in REFUSAL_CATEGORIES else "OTHER"
            t = Ticket(ticket_id=self.store.new_ticket_id(), customer_id=self.ctx.customer_id,
                       source_email_id=self.ctx.email_id, specs={}, price=None, status=Status.REFUSED,
                       refusal={"category": cat, "explanation": explanation[:1000]},
                       flags=list(self.ctx.intake_flags))
            self.store.put(t)
            return self._view(t)
        return self._run("refuse_job", {"category": category}, go)

    # ---------- implementation ----------

    def _run(self, tool: str, args: dict, fn):
        try:
            result = fn()
        except Denied as d:
            self.store.log(AGENT, tool, "DENY", d.rule, d.control,
                           {"session": self.ctx.session_id, "args": _safe(args), "reason": d.reason})
            raise
        self.store.log(AGENT, tool, "ALLOW", detail={"session": self.ctx.session_id, "args": _safe(args)})
        return result

    def _require_customer(self) -> str:
        if not self.ctx.customer_id:
            raise Denied("R2", "intake-sender-verification",
                         "sender is not a verified customer; only flagging is possible")
        return self.ctx.customer_id

    def _check_scope(self, customer_id: str | None) -> None:
        own = self._require_customer()
        if customer_id is not None and customer_id != own:
            raise Denied("R2", "tool-customer-scope", "requested another customer's data")

    def _lookup(self, customer_id):
        self._check_scope(customer_id)
        return self.store.customer(self.ctx.customer_id).public_view()

    def _search(self, product, customer_id):
        self._check_scope(customer_id)
        tickets = [t for t in self.store.tickets_for(self.ctx.customer_id)
                   if t.status != Status.REFUSED and (product is None or t.specs.get("product") == product)]
        tickets.sort(key=lambda t: t.ticket_id, reverse=True)
        return [{"ticket_id": t.ticket_id, "status": t.status.value, "specs": t.specs} for t in tickets[:10]]

    def _estimate_view(self, specs):
        self._require_customer()
        self._reject_forbidden(specs)
        est, flags = self._price(specs)
        return {"price": est.as_price() if est else None, "flags": [f.to_dict() for f in flags]}

    def _reject_forbidden(self, specs: dict) -> None:
        if not isinstance(specs, dict):
            raise Denied("R3", "tool-schema", "specs must be an object")
        bad = FORBIDDEN_KEYS & set(specs)
        if bad:
            raise Denied("R3", "tool-schema", f"agent may not set {sorted(bad)}")
        unknown = set(specs) - set(SPEC_FIELDS)
        if unknown:
            raise Denied("R3", "tool-schema", f"unknown fields {sorted(unknown)}")

    def _price(self, specs: dict):
        """Returns (estimate or None, flags). Missing/unknown specs become flags, never guesses."""
        flags = [Flag(f"MISSING_{k.upper()}", f"No {k} given; ask the customer.", "R9")
                 for k in REQUIRED_SPECS if specs.get(k) in (None, "", [])]
        if flags:
            return None, flags
        customer = self.store.customer(self.ctx.customer_id)
        try:
            est = self.pricing.estimate(specs, customer.contract_discount_pct, self.today())
        except PricingError as e:
            return None, [Flag("UNPRICEABLE", str(e), "R5")]
        return est, est.flags

    def _check_sources(self, specs: dict, sources: dict) -> None:
        for key, value in specs.items():
            if value in (None, "", []):
                continue
            src = sources.get(key)
            if not src or not isinstance(src, str):
                raise Denied("R9", "source-trace", f"'{key}' has no source; values must come from the email or a past ticket")
            archived = ARCHIVE_SOURCE.match(src.strip())
            if archived:
                past = self.store.ticket(archived.group(1))
                if past is None or past.customer_id != self.ctx.customer_id:
                    raise Denied("R2", "tool-customer-scope", f"source {src} is not one of this customer's tickets")
                if past.specs.get(key) != value:
                    raise Denied("R9", "source-trace", f"'{key}' does not match {src}")
                continue
            quote = normalize(src)
            if quote not in self.ctx.email_norm:
                raise Denied("R9", "source-trace", f"quote for '{key}' is not in the email")
            if key == "quantity" and str(value) not in re.sub(r"[^\d]", "", quote):
                raise Denied("R9", "source-trace", f"quantity {value} does not appear in its quote")

    def _save(self, specs, sources, notes, requested_discount_pct, ticket_id):
        customer_id = self._require_customer()
        self._reject_forbidden(specs)

        category = prohibited.classify(json.dumps(specs) + "\n" + self.ctx.email_text)
        if category:
            raise Denied("R6", "prohibited-classifier", f"prohibited work ({category}); use refuse_job")

        if not isinstance(requested_discount_pct, (int, float)) or requested_discount_pct < 0:
            raise Denied("R5", "pricing-code", "invalid discount request")
        if requested_discount_pct > self.pricing.max_discount_pct:
            raise Denied("R5", "pricing-code",
                         f"discount {requested_discount_pct}% exceeds the {self.pricing.max_discount_pct:g}% cap")

        self._check_sources(specs, sources)

        existing = None
        if ticket_id:
            existing = self._own_ticket(ticket_id)
            if existing.status != Status.DRAFT:
                raise Denied("R3", "tool-status-guard", f"ticket is {existing.status.value}; agent may only edit drafts")

        clean = {k: specs.get(k) for k in SPEC_FIELDS}
        est, flags = self._price(clean)
        flags = list(self.ctx.intake_flags) + flags

        customer = self.store.customer(customer_id)
        if requested_discount_pct > customer.contract_discount_pct:
            flags.append(Flag("DISCOUNT_REQUEST",
                              f"Customer asked for {requested_discount_pct:g}%; contract on file is "
                              f"{customer.contract_discount_pct:g}%. Only the contract rate was applied.", "R5"))

        qty = clean.get("quantity")
        past = [t.specs.get("quantity") for t in self.store.tickets_for(customer_id)
                if t.specs.get("product") == clean.get("product") and isinstance(t.specs.get("quantity"), int)]
        if isinstance(qty, int) and past and qty > UNUSUAL_QTY_FACTOR * max(past):
            flags.append(Flag("UNUSUAL_QUANTITY",
                              f"Quantity {qty:,} is over {UNUSUAL_QTY_FACTOR}x this customer's largest past order ({max(past):,}).", "R10"))

        flags += [Flag("AGENT_NOTE", n[:500], "R9", raised_by="agent") for n in notes]

        if existing:
            existing.specs, existing.price, existing.flags = clean, est.as_price() if est else None, flags
            existing.sources = dict(sources)
            existing.version += 1
            t = existing
        else:
            t = Ticket(ticket_id=self.store.new_ticket_id(), customer_id=customer_id,
                       source_email_id=self.ctx.email_id, specs=clean,
                       price=est.as_price() if est else None, flags=flags, sources=dict(sources))
        # Forced by code regardless of anything above:
        t.status = Status.DRAFT
        t.tainted = t.tainted or self.ctx.tainted
        t.needs_review = True
        self.store.put(t)
        return self._view(t)

    def _own_ticket(self, ticket_id: str) -> Ticket:
        t = self.store.ticket(ticket_id)
        if t is None or t.customer_id != self._require_customer() or t.source_email_id != self.ctx.email_id:
            raise Denied("R2", "tool-customer-scope", "ticket does not belong to this session")
        return t

    @staticmethod
    def _view(t: Ticket) -> dict:
        return {
            "ticket_id": t.ticket_id, "status": t.status.value, "version": t.version,
            "specs": t.specs, "price": t.price, "needs_review": t.needs_review,
            "flags": [f.to_dict() for f in t.flags], "refusal": t.refusal,
        }


def _safe(args: dict) -> dict:
    return json.loads(json.dumps(args, default=str))
