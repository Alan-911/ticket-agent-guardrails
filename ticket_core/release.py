"""The release gate: the only code that writes to production hot folders.

On AWS this is a Lambda whose role is the only principal the hot-folder bucket
policy allows to PutObject. It re-checks everything at the last moment, so a
ticket that was tampered with after approval (even by a direct database write)
is stopped here.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Protocol

from .approval import verify_token
from .errors import Denied
from .schema import Status, Ticket, spec_hash
from .store import Store

WIDE_FORMAT = {"banners"}
OFFSET_MIN_QTY = 5000
OFFSET_PRODUCTS = {"brochures", "flyers", "postcards"}


def route(ticket: Ticket) -> str:
    """Deterministic device routing. The model never picks a device."""
    product, qty = ticket.specs.get("product"), ticket.specs.get("quantity") or 0
    if product in WIDE_FORMAT:
        return "caldera"
    if product in OFFSET_PRODUCTS and qty >= OFFSET_MIN_QTY:
        return "prinergy"
    return "fiery"


class HotFolder(Protocol):
    def write(self, device: str, name: str, payload: dict) -> str: ...


class LocalHotFolder:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def write(self, device: str, name: str, payload: dict) -> str:
        folder = self.root / device
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return str(path)


class ReleaseGate:
    def __init__(self, store: Store, hotfolder: HotFolder, secret: bytes,
                 clock: Callable[[], float] = time.time):
        self.store = store
        self.hotfolder = hotfolder
        self.secret = secret
        self.clock = clock

    def release(self, ticket_id: str, token: str, actor: str = "release-lambda") -> str:
        try:
            t = self.store.ticket(ticket_id)
            if t is None:
                raise Denied("R4", "release-gate", "unknown ticket")
            if t.status != Status.APPROVED:
                raise Denied("R4", "release-gate", f"ticket is {t.status.value}, not APPROVED")
            if t.tainted and not t.reviewed_by:
                raise Denied("R7", "release-gate", "ticket from customer content was never human-reviewed")
            verify_token(token, t, self.secret, self.clock())
            if t.approved_hash != spec_hash(t) or t.approved_version != t.version:
                raise Denied("R11", "spec-hash-binding", "ticket differs from the approved version")
        except Denied as d:
            self.store.log(actor, "release", "DENY", d.rule, d.control, {"ticket_id": ticket_id, "reason": d.reason})
            raise
        device = route(t)
        path = self.hotfolder.write(device, f"{t.ticket_id}-v{t.version}.json", {
            "ticket_id": t.ticket_id, "version": t.version, "device": device,
            "customer_id": t.customer_id, "specs": t.specs, "approved_hash": t.approved_hash,
            "reviewed_by": t.reviewed_by,
        })
        t.status = Status.RELEASED
        self.store.log(actor, "release", "ALLOW", detail={"ticket_id": ticket_id, "device": device, "path": path})
        return path
