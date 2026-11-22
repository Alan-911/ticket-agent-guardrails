"""MCP tool definitions for the gateway target (CloudFormation PascalCase shape)."""

TOKEN = {"Type": "string", "Description": "The session_token from your instructions. Identifies the sender's account."}

SPEC_PROPS = {
    "product": {"Type": "string", "Description": "business_cards | flyers | brochures | postcards | banners"},
    "quantity": {"Type": "integer", "Description": "Number of finished pieces"},
    "finished_size": {"Type": "string", "Description": "e.g. 3.5x2in, 8.5x11in, 6x4in"},
    "stock": {"Type": "string", "Description": "Paper/substrate code, e.g. 16pt_matte"},
    "colors": {"Type": "string", "Description": "4/0 | 4/4 | 1/0 | 1/1"},
    "finishing": {"Type": "array", "Items": {"Type": "string"}, "Description": "Finishing codes"},
    "due_date": {"Type": "string", "Description": "YYYY-MM-DD"},
    "ship_to": {"Type": "string", "Description": "Ship-to address"},
}

TOOLS = [
    {
        "Name": "lookup_customer",
        "Description": "Get the sender's own customer account (company, contact, default ship-to).",
        "InputSchema": {"Type": "object", "Properties": {"session_token": TOKEN}, "Required": ["session_token"]},
    },
    {
        "Name": "search_past_tickets",
        "Description": "List the sender's own past tickets, optionally for one product. Use for 'same as last time'.",
        "InputSchema": {"Type": "object", "Properties": {
            "session_token": TOKEN,
            "product": {"Type": "string", "Description": "Optional product code filter"},
        }, "Required": ["session_token"]},
    },
    {
        "Name": "estimate_price",
        "Description": "Price a set of specs from the shop's pricing table. You cannot set or override prices.",
        "InputSchema": {"Type": "object", "Properties": {
            "session_token": TOKEN,
            "specs": {"Type": "object", "Properties": SPEC_PROPS},
        }, "Required": ["session_token", "specs"]},
    },
    {
        "Name": "save_draft_ticket",
        "Description": ("Create or update a DRAFT job ticket for human review. Every non-empty spec needs a source: "
                        "an exact short quote from the email, or 'ticket:T-xxxx' for one of the sender's past tickets."),
        "InputSchema": {"Type": "object", "Properties": {
            "session_token": TOKEN,
            "specs": {"Type": "object", "Properties": SPEC_PROPS},
            "sources": {"Type": "object", "Description": "Map of spec name -> exact quote or ticket:T-xxxx",
                        "Properties": {k: {"Type": "string"} for k in SPEC_PROPS}},
            "notes": {"Type": "array", "Items": {"Type": "string"}, "Description": "Questions/concerns for the estimator"},
            "requested_discount_pct": {"Type": "integer", "Description": "Discount the customer asked for, if any (0 if none). Recorded, not applied."},
            "ticket_id": {"Type": "string", "Description": "Only to update a draft you created in this session"},
        }, "Required": ["session_token", "specs", "sources"]},
    },
    {
        "Name": "flag_for_review",
        "Description": "Add a question or concern for the estimator to a draft you created in this session.",
        "InputSchema": {"Type": "object", "Properties": {
            "session_token": TOKEN,
            "ticket_id": {"Type": "string"},
            "message": {"Type": "string"},
        }, "Required": ["session_token", "ticket_id", "message"]},
    },
    {
        "Name": "refuse_job",
        "Description": "Record a refusal for prohibited work instead of creating a ticket.",
        "InputSchema": {"Type": "object", "Properties": {
            "session_token": TOKEN,
            "category": {"Type": "string", "Description": "CURRENCY | GOVERNMENT_ID | NEGOTIABLE_INSTRUMENT | COUNTERFEIT_BRAND | HATEFUL | OTHER"},
            "explanation": {"Type": "string"},
        }, "Required": ["session_token", "category", "explanation"]},
    },
]
