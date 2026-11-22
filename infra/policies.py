"""Cedar policies enforced by AgentCore Policy at the gateway (default deny).

Only these tool calls can pass. Anything else, including any tool that might
be added to the target later, is denied until a policy permits it.
"""

TARGET = "TicketTools"


def act(tool: str) -> str:
    return f'AgentCore::Action::"{TARGET}___{tool}"'


def policies(gateway_arn: str) -> dict[str, str]:
    gw = f'AgentCore::Gateway::"{gateway_arn}"'
    every_tool = ", ".join(act(t) for t in (
        "lookup_customer", "search_past_tickets", "estimate_price",
        "save_draft_ticket", "flag_for_review", "refuse_job"))
    return {
        # R1/R2: read-only lookups, scoped to the session by the tools Lambda.
        "permit_read_tools": f"""permit(principal,
  action in [{act("lookup_customer")}, {act("search_past_tickets")}, {act("estimate_price")}, {act("flag_for_review")}],
  resource == {gw});""",

        # R3/R5/R10: drafts only, never with a discount request above the 10% cap,
        # and never with an absurd quantity. (BREAKLOG #3: as a separate forbid,
        # the quantity rule was rejected by the policy validator as "overly restrictive".)
        "permit_save_draft_within_limits": f"""permit(principal,
  action == {act("save_draft_ticket")},
  resource == {gw})
when {{
  (if context.input has requested_discount_pct then context.input.requested_discount_pct <= 10 else true) &&
  (if context.input.specs has quantity then context.input.specs.quantity <= 1000000 else true)
}};""",

        # R6: refusals must use a known category.
        "permit_refuse_known_category": f"""permit(principal,
  action == {act("refuse_job")},
  resource == {gw})
when {{ ["CURRENCY", "GOVERNMENT_ID", "NEGOTIABLE_INSTRUMENT", "COUNTERFEIT_BRAND", "HATEFUL", "OTHER"].contains(context.input.category) }};""",

        # R2: every call must carry an intake-issued session token.
        "forbid_missing_session_token": f"""forbid(principal,
  action in [{every_tool}],
  resource == {gw})
unless {{ context.input has session_token && context.input.session_token like "st_*" }};""",
    }
