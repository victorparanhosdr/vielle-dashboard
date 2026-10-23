"""Combined inbox; each doctor's existing permissions and history remain separate."""
import time

from crm_access import CRM_SCOPE, crm_access, pipeline_visible
from crm_filters import ordered, parse_filters


def lead_results(store, doctor, name, pipelines, channel, filters):
    from crm_store import THREAD_VISIBLE
    catalog = {p["id"]: p for p in pipelines if p["doctor_id"] == doctor}
    stages = {s["id"]: s["name"] for p in catalog.values() for s in p["stages"]}
    with store.db() as c:
        leads, _, _ = store.filtered_leads(c, doctor, name, filters)
        history = {r[0] for r in c.execute(f"""SELECT DISTINCT contact_id FROM messages
            WHERE channel_id IN (SELECT id FROM channels WHERE doctor_id=?)
            AND (?='' OR channel_id=?) AND {THREAD_VISIBLE}""", (doctor, channel, channel))}
    results = {}
    for lead in leads:
        if lead["contact_id"] in results:
            continue
        pipeline = catalog.get(lead["pipeline_id"])
        if pipeline is None:
            continue
        results[lead["contact_id"]] = {**lead, "doctor_name": name, "pipeline_name": pipeline["name"],
            "stage_name": stages.get(lead["stage_id"], ""), "has_conversation": lead["contact_id"] in history}
    return list(results.values())


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
                    from crm_clinic_journey import active_stages, catalog as journey_catalog
                    p["stages"] = active_stages(c, p["id"])
                    p["clinic_journey"] = journey_catalog(c, p["id"])
                    rule=c.execute("SELECT enabled,config FROM followup_policies WHERE pipeline_id=?",(p["id"],)).fetchone()
                    p["followup_stage_ids"]=json.loads(rule["config"])["stage_ids"] if rule and rule["enabled"] else []
                catalog.extend(pipelines)
                channels.extend(dict(r) for r in c.execute("SELECT id,doctor_id,name,phone,state,archived_at FROM channels WHERE doctor_id=? ORDER BY name", (doctor,)))
    selected_channel = next((c for c in channels if c["id"] == channel), None)
    if channel and selected_channel is None:
        raise ValueError("Numero indisponivel para os profissionais selecionados.")
    counts = dict.fromkeys(("all", "unread", "waiting"), 0)
    items, candidates, total, incomplete = [], [], 0, 0
    filters = parse_filters(filters)
    if search:
        filters = parse_filters({**filters, "search": search})
    for doctor in doctors:
        if selected_channel and selected_channel["doctor_id"] != doctor:
            continue
        with crm_access(scope, doctor):
            data = store.inbox(doctor, user, channel, kind, search, filters, business=business)
            if filters["search"]:
                allowed = [p for p in catalog if business == "all" or ("course" if p["course_key"] else "clinic") == business]
                candidates.extend(lead_results(store, doctor, next(d["name"] for d in professionals if d["id"] == doctor),
                                               allowed, channel, filters))
        items.extend(data["conversations"])
        total += data["total"]
        incomplete += data["filter_incomplete"]
        for key in counts:
            counts[key] += data["counts"][key]
    items.sort(key=lambda r: (-r["created_at"], -r["seq"]))
    items = ordered(items, filters)
    if kind == "waiting":
        items.sort(key=lambda r: r["waiting_since"] or float("inf"))
    visible = {(r["doctor_id"], r["contact_id"]) for r in items[:200]}
    candidates = ordered(sorted((r for r in candidates if (r["doctor_id"], r["contact_id"]) not in visible),
                                key=lambda r: (-r["updated_at"], r["id"])), filters)
    fields = ("id", "doctor_id", "doctor_name", "contact_id", "name", "phone", "phone_removed", "pipeline_id",
              "pipeline_name", "stage_id", "stage_name", "has_conversation")
    return {"conversations": items[:200], "total": total, "counts": counts,
            "lead_results": [{k: r[k] for k in fields} for r in candidates[:50]], "lead_results_total": len(candidates),
            "filter_incomplete": incomplete, "server_time": time.time(),
            "doctors": professionals, "channels": channels, "pipelines": catalog}
