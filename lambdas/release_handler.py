"""Release gate Lambda: the only principal allowed to write to the hot folders."""
import json
import os

import boto3

from approval_handler import secret
from ticket_core.ddb_store import DynamoStore
from ticket_core.errors import Denied
from ticket_core.release import ReleaseGate

s3 = boto3.client("s3")


class S3HotFolder:
    def __init__(self, bucket: str):
        self.bucket = bucket

    def write(self, device: str, name: str, payload: dict) -> str:
        key = f"{device}/{name}"
        s3.put_object(Bucket=self.bucket, Key=key, Body=json.dumps(payload, indent=2).encode(),
                      ContentType="application/json")
        return f"s3://{self.bucket}/{key}"


def handler(event, context):
    store = DynamoStore(os.environ["TABLE_NAME"], writer="approval")
    store.ticket(event["ticket_id"])
    gate = ReleaseGate(store, S3HotFolder(os.environ["HOTFOLDER_BUCKET"]), secret())
    try:
        path = gate.release(event["ticket_id"], event["token"])
    except Denied as d:
        return {"denied": True, "rule": d.rule, "control": d.control, "reason": d.reason}
    store.put(store.tickets[event["ticket_id"]])
    return {"ok": True, "released_to": path}
