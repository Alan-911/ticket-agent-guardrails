"""Gateway Lambda target: the Ticket Agent's six tools.

The gateway gives this Lambda no session id or caller identity, so every tool
takes a `session_token`. Intake created that token (128+ random bits) and
stored the session facts behind it: verified customer, email text, flags.
The model can only act on the session it was handed; it can't pick a customer.
"""
import json
import os
from pathlib import Path

from ticket_core.ddb_store import DynamoStore
from ticket_core.errors import Denied
from ticket_core.pricing import PricingTable
from ticket_core.tools import TicketTools

PRICING = PricingTable.load(Path(__file__).with_name("pricing.json"))
TOOLS = {"lookup_customer", "search_past_tickets", "estimate_price",
         "save_draft_ticket", "flag_for_review", "refuse_job"}


def _tool_name(context) -> str:
    try:
        full = context.client_context.custom["bedrockAgentCoreToolName"]
    except (AttributeError, KeyError, TypeError):
        return ""
    return full.split("___", 1)[-1]


def handler(event, context):
    tool = _tool_name(context)
    if tool not in TOOLS:
        return {"denied": True, "rule": "R4", "control": "tools-lambda", "reason": f"unknown tool '{tool}'"}

    store = DynamoStore(os.environ["TABLE_NAME"], writer="tools")
    args = dict(event or {})
    ctx = store.get_session(args.pop("session_token", None))
    if ctx is None:
        store.log("ticket-agent", tool, "DENY", "R2", "session-token", {"reason": "missing/expired session token"})
        return {"denied": True, "rule": "R2", "control": "session-token",
                "reason": "missing or expired session token; you can only act on the email you were given"}

    tools = TicketTools(store, PRICING, ctx)
    try:
        result = getattr(tools, tool)(**args)
    except Denied as d:
        # BREAKLOG #9: fixable input problems get a fix-it hint; boundary denials say stop.
        fixable = d.control in ("source-trace", "tool-schema")
        return {"denied": True, "rule": d.rule, "control": d.control, "reason": d.reason,
                "saved": False,
                "instruction": ("Nothing was saved. Fix the arguments and call the tool again: values the customer "
                                "stated must be quoted exactly from the email; use ticket:T-xxxx only for values "
                                "taken unchanged from that past ticket. Leave unknown values empty.")
                if fixable else
                ("Nothing was saved. This action is not allowed. Do not retry it; say so in your summary.")}
    except TypeError as e:
        return {"error": f"bad arguments for {tool}: {e}"}
    return {"ok": True, "result": json.loads(json.dumps(result, default=str))}
