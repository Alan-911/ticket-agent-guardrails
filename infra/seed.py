"""Load the fictional customers, estimators and archive tickets into DynamoDB.

    uv run --group infra python infra/seed.py
"""
import json
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]


def main():
    outputs = json.loads((ROOT / "cdk-outputs.json").read_text())["TicketAgent"]
    table = boto3.resource("dynamodb", region_name="us-east-1").Table(outputs["TableName"])
    data = json.loads((ROOT / "mock_pace/customers.json").read_text(encoding="utf-8"))
    archive = json.loads((ROOT / "samples/archive/tickets.json").read_text(encoding="utf-8"))["tickets"]

    with table.batch_writer() as w:
        w.put_item(Item={"pk": "CONFIG", "sk": "ESTIMATORS", "data": json.dumps(data["estimators"])})
        for c in data["customers"]:
            w.put_item(Item={"pk": f"CUSTOMER#{c['customer_id']}", "sk": "PROFILE", "data": json.dumps(c),
                             "gsi1pk": "CUSTOMERS", "gsi1sk": c["customer_id"]})
            w.put_item(Item={"pk": f"EMAIL#{c['email'].casefold()}", "sk": "CUSTOMER", "customer_id": c["customer_id"]})
        for t in archive:
            body = {"ticket_id": t["ticket_id"], "customer_id": t["customer_id"], "source_email_id": "archive",
                    "specs": t["specs"], "price": None, "status": t["status"], "version": 1, "tainted": False,
                    "needs_review": False, "flags": [], "sources": {}, "refusal": None}
            w.put_item(Item={"pk": f"TICKET#{t['ticket_id']}", "sk": "TICKET", "customer_id": t["customer_id"],
                             "status": t["status"], "version": 1, "data": json.dumps(body),
                             "gsi1pk": f"CUSTOMER#{t['customer_id']}", "gsi1sk": f"TICKET#{t['ticket_id']}"})
    print(f"Seeded {len(data['customers'])} customers, {len(archive)} archive tickets into {outputs['TableName']}")


if __name__ == "__main__":
    main()
