"""Detect instruction-like text inside customer content (rule R7).

This scanner never blocks on its own. A hit adds an INJECTION_SUSPECT flag
that the agent cannot remove, so a human sees it. The hard protection is that
the instructions have nothing to act on: there is no approve, route or price
tool for the agent to call.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .text import scan_view

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("OVERRIDE", re.compile(
        r"\b(ignore|disregard|forget|override|bypass)\b.{0,40}?\b(previous|prior|above|all|your|system|earlier)\b"
        r".{0,20}?\b(instructions?|rules?|prompts?|guidelines|policies)\b")),
    ("ROLE_CHANGE", re.compile(
        r"\byou are now\b|\bact as\b|\bnew instructions\b|\bsystem (prompt|note|message)\b"
        r"|\bdeveloper mode\b|\bnote to (the )?(ai|assistant|agent|estimator|model)\b|\bai estimator\b")),
    ("APPROVAL_CLAIM", re.compile(
        r"\bpre-?approved\b|\balready approved\b|\bapproved by\b|\bskip (the )?proofs?\b"
        r"|\bno proofs? (needed|required)\b|\bwaive (the )?proofs?\b|\bmark (it |this |the job )?(as )?approved\b")),
    ("RELEASE_REQUEST", re.compile(
        r"\b(send|route|push|release|submit|forward)\b.{0,30}?\b(press|fiery|caldera|prinergy|pitstop|hot ?folders?|production)\b")),
    ("PRICE_DIRECTIVE", re.compile(
        r"\b(set|change|make|override|update)\b.{0,25}?\b(price|cost|total|rate)\b.{0,25}?\b(to|at)\b"
        r"|\$0(\.0+)?\b|\bfree of charge\b|\b([2-9]\d|100)\s?% (off|discount|contract discount)\b")),
    ("HIDE_FROM_REVIEW", re.compile(
        r"\b(do not|don't|never)\b.{0,15}?\b(flag|mention|report|tell)\b")),
    # BREAKLOG #13: requests to run commands or call internal tools by name.
    ("TOOL_REQUEST", re.compile(
        r"\b(run|execute)\b.{0,25}?\b(shell|command|script|terminal)\b|\baws (s3|lambda|dynamodb) \w+"
        r"|\b(call|use|invoke|trigger)\b.{0,20}?\b[a-z]+_[a-z_]+\b.{0,10}?\btool\b"
        r"|\b(release_job|set_status|send_proof|mark_reviewed|update_specs)\b")),
    ("TOOL_SYNTAX", re.compile(
        r"</?(system|assistant|tool|instructions?|function_calls?)>|\{\s*\"(tool|function|name|action)\"\s*:")),
    # A few non-English variants. Bedrock Guardrails' prompt-attack filter covers more languages.
    ("OVERRIDE_FR", re.compile(r"\bignore[zr]\b.{0,40}?\b(instructions|règles|consignes)\b")),
    ("OVERRIDE_ES", re.compile(r"\bignorar?\b.{0,40}?\b(instrucciones|reglas)\b")),
]


@dataclass(frozen=True)
class InjectionHit:
    code: str
    snippet: str


def scan(text: str) -> list[InjectionHit]:
    view = scan_view(text)
    hits = []
    for code, pattern in _PATTERNS:
        match = pattern.search(view)
        if match:
            hits.append(InjectionHit(code, match.group(0)[:120]))
    return hits
