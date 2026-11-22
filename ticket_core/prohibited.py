"""Prohibited print work (rule R6).

This is the code layer. Bedrock Guardrails denied topics are the second layer,
and a human sees every refusal. The list is deliberately strict: a false
positive costs an estimator a minute; a false negative could mean printing
counterfeit currency or a fake ID.
"""
from __future__ import annotations

import re

from .text import scan_view

_CATEGORIES: list[tuple[str, re.Pattern[str]]] = [
    ("CURRENCY", re.compile(
        r"\bbank ?notes?\b|\bpaper currency\b|\bcurrency (notes?|design|replica)\b|\bdollar bills?\b"
        r"|\$\s?\d+ bills?\b|\bprop money\b|\bplay money\b|\bmovie money\b"
        r"|\b(replica|fake|counterfeit|realistic)\b.{0,20}?\b(bills?|money|cash|banknotes?|notes)\b"
        r"|\b(euro|pound|franc) notes?\b")),
    ("GOVERNMENT_ID", re.compile(
        r"\bpassports?\b|\bdriver'?s? licen[cs]es?\b|\bnational id\b|\bidentity cards?\b"
        r"|\bsocial security cards?\b|\bbirth certificates?\b|\bgreen cards?\b|\bvisa stamps?\b"
        r"|\bgovernment[- ]issued id\b")),
    ("NEGOTIABLE_INSTRUMENT", re.compile(
        r"\bcheques?\b|\bcheck stock\b|\bblank checks?\b|\bmicr\b|\bmoney orders?\b"
        r"|\bpostage stamps?\b|\blottery tickets?\b")),
    ("COUNTERFEIT_BRAND", re.compile(
        r"\b(counterfeit|knock-?off|replica|fake|bootleg|unauthori[sz]ed)\b.{0,30}?\b(labels?|packaging|logos?|tags?|boxes|brand(ed|ing)?|merch)\b"
        r"|\b(labels?|packaging|logos?|tags?)\b.{0,30}?\b(counterfeit|knock-?off|replica|bootleg)\b")),
]


def classify(text: str) -> str | None:
    """Return the prohibited category the text matches, or None."""
    view = scan_view(text)
    for category, pattern in _CATEGORIES:
        if pattern.search(view):
            return category
    return None
