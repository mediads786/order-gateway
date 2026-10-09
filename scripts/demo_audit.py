import argparse
import json
import sys
import uuid
from datetime import timezone

from sqlalchemy import select

from app.db.models import AuditEvent
from app.db.session import SessionLocal


def fetch_audit_lines(order_id: str | uuid.UUID) -> list[str]:
    parsed_order_id = uuid.UUID(str(order_id))
    with SessionLocal() as db:
        events = db.scalars(
            select(AuditEvent)
            .where(AuditEvent.order_id == parsed_order_id)
            .order_by(AuditEvent.created_at, AuditEvent.event_id)
        ).all()

    lines = []
    for event in events:
        created_at = event.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        timestamp = created_at.astimezone(timezone.utc).isoformat(timespec="microseconds")
        details = json.dumps(event.details, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        lines.append(f"{timestamp} {event.event_type} {details}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="Print an order's append-only audit events.")
    parser.add_argument("order_id", type=uuid.UUID)
    args = parser.parse_args()
    lines = fetch_audit_lines(args.order_id)
    if not lines:
        print(f"No audit events found for order {args.order_id}.")
        return 1
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
