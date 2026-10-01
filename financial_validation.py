"""Safe, read-only details for financial records omitted from a calculation."""
from decimal import Decimal, InvalidOperation
from math import isfinite


def text(value):
    return str(value).strip()[:500] if isinstance(value, (str, int, float)) else ""


def money(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
        number = float(amount / 100) if amount.is_finite() and amount >= 0 else None
        return number if number is not None and isfinite(number) else None
    except (InvalidOperation, ValueError, OverflowError):
        return None


def issue(uuid, reason, raw, *, bill=None, source="title", title_id=None,
          date=None, date_source=None, status=None, gross=None, net=None):
    bill = bill if isinstance(bill, dict) else {}
    raw = raw if isinstance(raw, dict) else {}
    person = raw.get("person") or bill.get("person") or {}
    person = person if isinstance(person, dict) else {}
    return {
        "id": text(uuid), "title_id": text(title_id or bill.get("uuid") or uuid),
        "source": source, "reason": reason, "date": date, "date_source": text(date_source),
        "description": text(raw.get("description") or bill.get("description") or raw.get("type") or bill.get("type")),
        "contact": text(person.get("name") or person.get("full_name")),
        "status": text(status or raw.get("status") or bill.get("status")),
        "gross": money(gross), "net": money(net),
    }
