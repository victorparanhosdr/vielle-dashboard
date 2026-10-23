"""Validated CRM segmentation, shared by the funnel and inbox."""
import re
import unicodedata
from datetime import date


OPTIONS = {
    "tier": {"", "bronze", "silver", "gold", "unclassified"},
    "identity": {"", "matched", "new", "review"},
    "sales": {"", "yes", "no"},
    "quotes": {"", "active", "pending", "expired", "lost", "none"},
    "sort": {"updated", "spent", "sales", "course_paid", "course_value"},
    "course_status": {"", "paid", "pending", "refunded", "none"},
    "appointment_status": {"", "any", "completed", "scheduled", "missed", "none"},
    "service_location": {"", "capelinha", "belo-horizonte", "sao-paulo", "pending"},
}
NUMBERS = {"min_spent_cents", "max_spent_cents", "min_sales"}
DATES = {"appointment_from", "appointment_to"}
FLAGS = {"appointment_no_upcoming", "journey_review"}


def parse_filters(value=None):
    value = {} if value is None else value
    if not isinstance(value, dict) or set(value) - (set(OPTIONS) | NUMBERS | DATES | FLAGS | {"search"}):
        raise ValueError("Filtros invalidos.")
    result = {}
    for name, choices in OPTIONS.items():
        item = value.get(name, "updated" if name == "sort" else "")
        if not isinstance(item, str) or item not in choices:
            raise ValueError("Filtro invalido: " + name)
        result[name] = item
    for name in NUMBERS:
        number = value.get(name)
        if number is not None and (type(number) is not int or not 0 <= number <= (100000 if name == "min_sales" else 10000000000)):
            raise ValueError("Valor de filtro invalido.")
        result[name] = number
    if result["min_spent_cents"] is not None and result["max_spent_cents"] is not None and result["min_spent_cents"] > result["max_spent_cents"]:
        raise ValueError("O valor minimo nao pode superar o maximo.")
    for name in DATES:
        day = value.get(name, "")
        if not isinstance(day, str) or (day and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day)):
            raise ValueError("Data do agendamento invalida.")
        if day:
            try:
                date.fromisoformat(day)
            except ValueError:
                raise ValueError("Data do agendamento invalida.") from None
        result[name] = day
    if result["appointment_from"] and result["appointment_to"] and result["appointment_from"] > result["appointment_to"]:
        raise ValueError("O inicio do periodo deve ser anterior ao fim.")
    for name in FLAGS:
        flag = value.get(name, False)
        if type(flag) is not bool:
            raise ValueError("Filtro de agendamento invalido.")
        result[name] = flag
    search = value.get("search", "")
    if not isinstance(search, str) or len(search) > 160:
        raise ValueError("Busca invalida.")
    result["search"] = search.strip()
    return result


def fold(value):
    return "".join(c for c in unicodedata.normalize("NFKD", str(value)) if not unicodedata.combining(c)).casefold()


def financial_ready(item, filters):
    history = item.get("customer_history", {})
    sales_filter = filters["sales"] or any(filters[k] is not None for k in NUMBERS)
    return (not sales_filter or history.get("sales_state") == "ready") and (not filters["quotes"] or history.get("quotes_state") == "ready")


def appointment_active(filters):
    return bool(filters["appointment_status"] or filters["appointment_from"] or filters["appointment_to"] or filters["appointment_no_upcoming"])


def appointment_ready(item, filters):
    return not appointment_active(filters) or item.get("appointment_filter", {}).get("state") == "ready"


def matches(item, filters, extra_search=""):
    if filters.get("journey_review") and not item.get("journey_state", {}).get("needs_review"):
        return False
    course_status = filters["course_status"]
    if course_status:
        course = item.get("course_data", {})
        if course.get("state") != "ready":
            return False
        if course_status == "none" and course.get("order_count", 0):
            return False
        if course_status != "none" and not course.get({"paid": "paid_count", "pending": "pending_count", "refunded": "refunded_count"}[course_status], 0):
            return False
    location = filters["service_location"]
    if location and (item.get("service_location") or "pending") != location:
        return False
    if filters["tier"] and (item.get("customer_value", {}).get("tier") or "unclassified") != filters["tier"]:
        return False
    identity = item.get("experts_status")
    if filters["identity"]:
        allowed = {"review", "unchecked"} if filters["identity"] == "review" else {filters["identity"]}
        if identity not in allowed:
            return False
    if not financial_ready(item, filters) or not appointment_ready(item, filters):
        return False
    if appointment_active(filters):
        agenda = item["appointment_filter"]
        if filters["appointment_status"] == "none":
            if agenda["count"]:
                return False
        elif filters["appointment_status"] or filters["appointment_from"] or filters["appointment_to"]:
            if not agenda["count"]:
                return False
        if filters["appointment_no_upcoming"] and agenda["has_upcoming"]:
            return False
    history = item.get("customer_history", {})
    count, amount = history.get("sales_count", 0), history.get("sales_cents", 0)
    if filters["sales"] == "yes" and count == 0 or filters["sales"] == "no" and count != 0:
        return False
    if filters["min_sales"] is not None and count < filters["min_sales"]:
        return False
    if filters["min_spent_cents"] is not None and amount < filters["min_spent_cents"]:
        return False
    if filters["max_spent_cents"] is not None and amount > filters["max_spent_cents"]:
        return False
    quote = filters["quotes"]
    if quote == "none" and history.get("quotes_count", 0) != 0:
        return False
    if quote and quote != "none":
        field = {"active": "open_quotes_count", "pending": "quotes_count", "expired": "expired_quotes_count", "lost": "lost_quotes_count"}[quote]
        if not history.get(field, 0):
            return False
    search = fold(filters["search"])
    if search:
        blob = fold(" ".join(str(item.get(k) or "") for k in ("name", "phone", "email", "title", "_experts_search")) + " " + extra_search)
        digits = re.sub(r"\D", "", search)
        if search not in blob and not (len(digits) >= 3 and re.fullmatch(r"[\d\s()+.\-/]+", search) and digits in re.sub(r"\D", "", blob)):
            return False
    return True


def ordered(items, filters):
    key = filters["sort"]
    if key == "updated":
        return items
    if key in {"course_paid", "course_value"}:
        return sorted(items, key=lambda row: (-(row.get("course_data", {}).get("paid_cents", 0) if key == "course_paid" else row.get("value_cents", 0)), row.get("id", "")))
    field = "sales_cents" if key == "spent" else "sales_count"
    return sorted(items, key=lambda row: (row.get("customer_history", {}).get("sales_state") != "ready",
                                          -row.get("customer_history", {}).get(field, 0),
                                          -row.get("updated_at", row.get("created_at", 0)), row.get("id", "")))
