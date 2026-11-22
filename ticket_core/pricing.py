"""Deterministic estimating (rule R5).

The agent never supplies a price. It supplies specs; this module turns them
into a price from the shop's table. Discounts come only from the customer's
contract on file and are capped. A final floor check guarantees no ticket is
ever priced below cost plus minimum margin, even if the table is edited badly.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .errors import PricingError
from .schema import Flag


@dataclass
class Estimate:
    list_cents: int
    discount_pct: float
    total_cents: int
    unit_cents: int
    rush: bool
    flags: list[Flag] = field(default_factory=list)

    def as_price(self) -> dict:
        return {
            "list_cents": self.list_cents,
            "discount_pct": self.discount_pct,
            "total_cents": self.total_cents,
            "unit_cents": self.unit_cents,
            "rush": self.rush,
        }


class PricingTable:
    def __init__(self, table: dict):
        self.t = table
        self.max_discount_pct = float(table["max_discount_pct"])

    @classmethod
    def load(cls, path: str | Path) -> "PricingTable":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def product(self, name: str) -> dict:
        try:
            return self.t["products"][name]
        except KeyError:
            raise PricingError(f"unknown product '{name}'") from None

    def is_rush(self, product: str, due: str | None, today: date) -> bool:
        if not due:
            return False
        try:
            due_date = date.fromisoformat(due)
        except ValueError:
            raise PricingError(f"due date '{due}' is not YYYY-MM-DD") from None
        if due_date < today:
            raise PricingError(f"due date {due} is in the past")
        return (due_date - today).days < self.product(product)["min_turnaround_days"]

    def estimate(self, specs: dict, contract_discount_pct: float, today: date) -> Estimate:
        name = specs.get("product")
        p = self.product(name)
        qty = specs.get("quantity")
        if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
            raise PricingError(f"quantity must be a positive whole number, got {qty!r}")
        stock, colors = specs.get("stock"), specs.get("colors")
        if stock not in p["stocks"]:
            raise PricingError(f"stock '{stock}' is not offered for {name}")
        if colors not in p["colors"]:
            raise PricingError(f"colors '{colors}' not offered for {name}")
        finishing = specs.get("finishing") or []
        for f in finishing:
            if f not in p["finishing"]:
                raise PricingError(f"finishing '{f}' not offered for {name}")

        per_100 = max((tier for tier in p["per_100_cents"] if qty >= tier[0]), key=lambda t: t[0])[1]
        run = per_100 * qty / 100
        run *= self.t["stock_multipliers"][stock] * self.t["color_multipliers"][colors]
        fin = sum(self.t["finishing_per_100_cents"][f] * qty / 100 for f in finishing)
        base = p["setup_cents"] + run + fin

        rush = self.is_rush(name, specs.get("due_date"), today)
        list_cents = math.ceil(base * (self.t["rush_multiplier"] if rush else 1.0))

        discount = min(max(float(contract_discount_pct), 0.0), self.max_discount_pct)
        total = math.ceil(list_cents * (1 - discount / 100))

        floor = math.ceil(base * p["cost_ratio"] * (1 + self.t["min_margin_pct"] / 100))
        if total < floor:
            raise PricingError("price would fall below cost floor")

        flags = []
        if rush:
            flags.append(Flag("RUSH", f"Due date is inside the {p['min_turnaround_days']}-day turnaround; rush pricing applied.", "R10"))
        return Estimate(list_cents, discount, total, math.ceil(total / qty), rush, flags)
