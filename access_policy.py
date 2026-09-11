"""Permission catalog and report projection for the existing dashboard views."""

from clinic_catalog import SUPPORTED_CLINICS

MODULES = {
    "dashboard": {"label": "Painel geral", "view": "generalView", "actions": ["view", "edit", "export"]},
    "commercial": {"label": "Comercial", "view": "commercialView", "actions": ["view", "export"]},
    "financial": {"label": "Financeiro", "view": "financialView", "actions": ["view", "export"]},
    "patient_followup": {"label": "Acompanhamento de Paciente", "view": "patientFollowupView", "actions": ["view", "create", "edit", "export"]},
    "budget_followup": {"label": "Acompanhamento de Orçamentos", "view": "quoteFollowupView", "actions": ["view", "create", "edit", "export"]},
    "paid_traffic": {"label": "Tráfego pago", "view": "trafficView", "actions": ["view", "edit", "export"]},
    "whatsapp_review": {"label": "Avaliação WhatsApp", "view": "whatsappAuditView", "actions": ["view", "edit", "export"]},
}
VIEW_MODULES = {item["view"]: key for key, item in MODULES.items()}
ACTION_LABELS = {"view": "Visualizar", "create": "Registrar contato", "edit": "Editar / executar", "export": "Exportar"}
REPORT_KEYS = {
    "dashboard": {"general_panel", "clinica_experts"},
    "commercial": {"totals", "by_pipeline", "interacted_leads", "by_status", "all_current_status", "daily_new_leads", "agendado_migrations", "kommo_panel", "clinica_experts"},
    "financial": {"financial"}, "patient_followup": {"patient_followup"},
    "budget_followup": {"quote_followup"}, "paid_traffic": {"paid_traffic"},
    "whatsapp_review": {"whatsapp_audit"},
}


def clinic_modules(clinic):
    return [key for key in MODULES if key != "patient_followup" or clinic == "vielle"] if clinic in SUPPORTED_CLINICS else []


def clinic_permissions(clinic):
    return [f"{key}.{action}" for key in clinic_modules(clinic) for action in MODULES[key]["actions"]]


def validate_permissions(clinic, permissions):
    if not isinstance(permissions, list) or any(not isinstance(value, str) or value not in clinic_permissions(clinic) for value in permissions):
        raise ValueError("Permissões inválidas para esta clínica.")
    values = set(permissions)
    if any(value.split(".")[0] + ".view" not in values for value in values):
        raise ValueError("Libere Visualizar antes das demais ações do módulo.")
    return sorted(values)


def permission_map(clinics, permissions):
    if permissions is None:
        return {clinic: clinic_permissions(clinic) for clinic in clinics}
    if not isinstance(permissions, dict) or set(permissions) - set(clinics):
        raise ValueError("Permissões devem corresponder às clínicas liberadas.")
    return {clinic: validate_permissions(clinic, permissions.get(clinic, [])) for clinic in clinics}


def project_report(report, module):
    result = {key: value for key, value in report.items() if key in REPORT_KEYS[module] | {"connected", "filters", "pipelines"}}
    if module == "commercial":
        intelligence = report.get("financial", {}).get("sales_intelligence", {})
        result["sales_intelligence"] = {
            key: value for key, value in intelligence.items()
            if key in {"top_patients", "top_procedures", "procedure_categories", "performance_daily", "basis"}
        }
    if module == "dashboard" and "clinica_experts" in result:
        result["clinica_experts"] = {"booking_registry_users": report["clinica_experts"].get("booking_registry_users", [])}
    return result


def profile_defaults():
    groups = {
        "Administrador da clínica": list(MODULES),
        "Gerente": ["dashboard", "commercial", "patient_followup", "budget_followup"],
        "Comercial": ["commercial", "patient_followup", "budget_followup"],
        "Financeiro": ["dashboard", "financial"],
        "Recepção": ["patient_followup", "budget_followup"],
    }
    return {name: [f"{key}.{action}" for key in keys for action in MODULES[key]["actions"]] for name, keys in groups.items()}
