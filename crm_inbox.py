"""Combined inbox; each doctor's existing permissions and history remain separate."""
import time

from crm_access import CRM_SCOPE, crm_access, pipeline_visible
from crm_filters import ordered, parse_filters


def combined(store, doctors, user, channel="", kind="all", search="", filters=None, *, business="all"):
    if (not isinstance(doctors, list) or not 1 <= len(doctors) <= 100
            or any(not isinstance(d, str) or not d or len(d) > 64 for d in doctors)):
        raise ValueError("Selecione ao menos um profissional valido.")
    doctors = list(dict.fromkeys(doctors))
    scope = CRM_SCOPE.get()
    catalog, channels, professionals = [], [], []
    # Validate the entire selection before returning any owner's data.
    with store.db() as c:
        for doctor in doctors:
            with crm_access(scope, doctor):
                professionals.append(store.row(c, "doctors", doctor))
                pipelines = [dict(r) for r in c.execute("SELECT * FROM pipelines WHERE doctor_id=? ORDER BY name", (doctor,))
                             if pipeline_visible(dict(r))]
                for p in pipelines:
                    p["stages"] = [dict(r) for r in c.execute("SELECT * FROM stages WHERE pipeline_id=? ORDER BY position", (p["id"],))]
                catalog.extend(pipelines)
                channels.extend(dict(r) for r in c.execute("SELECT id,doctor_id,name,phone,state,archived_at FROM channels WHERE doctor_id=? ORDER BY name", (doctor,)))
    selected_channel = next((c for c in channels if c["id"] == channel), None)
    if channel and selected_channel is None:
        raise ValueError("Numero indisponivel para os profissionais selecionados.")
    counts = dict.fromkeys(("all", "unread", "waiting"), 0)
    items, total, incomplete = [], 0, 0
    filters = parse_filters(filters)
    for doctor in doctors:
        if selected_channel and selected_channel["doctor_id"] != doctor:
            continue
        with crm_access(scope, doctor):
            data = store.inbox(doctor, user, channel, kind, search, filters, business=business)
        items.extend(data["conversations"])
        total += data["total"]
        incomplete += data["filter_incomplete"]
        for key in counts:
            counts[key] += data["counts"][key]
    items.sort(key=lambda r: (-r["created_at"], -r["seq"]))
    items = ordered(items, filters)
    if kind == "waiting":
        items.sort(key=lambda r: r["waiting_since"] or float("inf"))
    return {"conversations": items[:200], "total": total, "counts": counts,
            "filter_incomplete": incomplete, "server_time": time.time(),
            "doctors": professionals, "channels": channels, "pipelines": catalog}
