"""Email intake: decides who the customer is before the agent sees anything.

The agent never chooses the customer. Intake resolves it from the verified
sender address (rule R2), marks the session as tainted (rule R7), and attaches
code-raised flags the agent can't remove.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from . import injection, prohibited
from .schema import Flag
from .store import Store
from .text import normalize


@dataclass
class Email:
    message_id: str
    from_addr: str
    subject: str
    body: str
    auth: dict
    reply_to: str | None = None
    attachments: list[dict] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Email":
        keys = ("message_id", "from_addr", "subject", "body", "auth", "reply_to", "attachments")
        return cls(**{k: d.get(k) for k in keys if k in d})

    def full_text(self) -> str:
        parts = [f"Subject: {self.subject}", self.body]
        for a in self.attachments or []:
            parts.append(f"[attachment {a.get('name')}]\n{a.get('text', '')}")
        return "\n\n".join(parts)


@dataclass
class SessionContext:
    """Facts fixed by code before the model runs. Tools trust only this."""
    session_id: str
    email_id: str
    customer_id: str | None
    email_text: str
    email_norm: str
    tainted: bool = True
    intake_flags: list[Flag] = field(default_factory=list)


def _domain(addr: str | None) -> str:
    return (addr or "").rsplit("@", 1)[-1].strip().casefold()


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def intake(email: Email, store: Store) -> SessionContext:
    flags: list[Flag] = []
    auth = email.auth or {}
    verified = auth.get("dkim") == "pass" and auth.get("dmarc") == "pass"
    customer = store.customer_by_email(email.from_addr) if verified else None

    if not verified:
        flags.append(Flag("SENDER_UNVERIFIED", "DKIM/DMARC did not pass; sender identity unknown.", "R2"))
    all_customers = store.all_customers()
    if verified and customer is None:
        dom = normalize(_domain(email.from_addr))
        near = [c for c in all_customers if 0 < _edit_distance(dom, normalize(c.domain)) <= 2]
        if near:
            flags.append(Flag("LOOKALIKE_DOMAIN",
                              f"Sender domain '{_domain(email.from_addr)}' looks like customer domain '{near[0].domain}'.", "R2"))
        else:
            flags.append(Flag("SENDER_NOT_CUSTOMER", "Sender is not a customer on file.", "R2"))

    if email.reply_to and _domain(email.reply_to) != _domain(email.from_addr):
        flags.append(Flag("REPLY_TO_MISMATCH",
                          f"Reply-To domain '{_domain(email.reply_to)}' differs from sender; replies go to the address on file only.", "R2"))

    text = email.full_text()
    norm = normalize(text)
    if customer is not None:
        for other in all_customers:
            if other.customer_id == customer.customer_id:
                continue
            first_word = normalize(other.company).split()[0]
            if normalize(other.company) in norm or (len(first_word) > 4 and first_word in norm) \
                    or normalize(other.domain) in norm:
                flags.append(Flag("MENTIONS_OTHER_CUSTOMER",
                                  f"Email refers to another customer ({other.company}); their data is not shared.", "R2"))
    for hit in injection.scan(text):
        flags.append(Flag("INJECTION_SUSPECT", f"{hit.code}: \"{hit.snippet}\"", "R7"))
    category = prohibited.classify(text)
    if category:
        flags.append(Flag("PROHIBITED_CONTENT", f"Email matches prohibited category {category}.", "R6"))

    return SessionContext(
        session_id=str(uuid.uuid4()),
        email_id=email.message_id,
        customer_id=customer.customer_id if customer else None,
        email_text=text,
        email_norm=norm,
        tainted=True,
        intake_flags=flags,
    )
