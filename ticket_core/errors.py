"""Every refusal names the rule it protects and the control that stopped it.

The red-team report is built from these two fields, so each attack can be
attributed to the exact layer that held.
"""
from __future__ import annotations


class Denied(Exception):
    def __init__(self, rule: str, control: str, reason: str):
        super().__init__(f"[{rule}] {reason} (control: {control})")
        self.rule = rule
        self.control = control
        self.reason = reason


class PricingError(Exception):
    """Raised when a price can't be computed safely. Never shown as a number."""
