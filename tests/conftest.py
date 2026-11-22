import json
from datetime import date
from pathlib import Path

import pytest

from ticket_core.intake import Email, intake
from ticket_core.pricing import PricingTable
from ticket_core.store import Store
from ticket_core.tools import TicketTools

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 11, 23)
SECRET = b"test-only-approval-secret"


@pytest.fixture
def store():
    return Store.load(ROOT / "mock_pace/customers.json", ROOT / "samples/archive/tickets.json")


@pytest.fixture
def pricing():
    return PricingTable.load(ROOT / "mock_pace/pricing.json")


@pytest.fixture
def emails():
    data = json.loads((ROOT / "samples/emails/emails.json").read_text(encoding="utf-8"))
    return {e["message_id"]: e for e in data["emails"]}


@pytest.fixture
def session(store, pricing, emails):
    """session('E-001-benign-cards') -> (ctx, tools)"""
    def make(email_or_id):
        raw = emails[email_or_id] if isinstance(email_or_id, str) else email_or_id
        ctx = intake(Email.from_dict(raw), store)
        return ctx, TicketTools(store, pricing, ctx, today=lambda: TODAY)
    return make


CARDS_SPECS = {
    "product": "business_cards", "quantity": 1000, "finished_size": "3.5x2in",
    "stock": "16pt_matte", "colors": "4/4", "finishing": ["cut", "round_corners"],
    "due_date": "2026-12-12", "ship_to": "410 Harbor Rd, Portland, ME 04101",
}
CARDS_SOURCES = {
    "product": "quote 1,000 business cards", "quantity": "1,000 business cards",
    "finished_size": "3.5 x 2 in", "stock": "16pt matte", "colors": "full color both sides (4/4)",
    "finishing": "ticket:T-0007", "due_date": "We need them by December 12",
    "ship_to": "410 Harbor Rd, Portland, ME 04101",
}
