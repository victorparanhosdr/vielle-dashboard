"""Stable clinic keys shared by authentication and the existing application."""

CLINIC_DISPLAY_NAMES = {
    "vielle": "Vielle Clinic",
    "inspire": "Clínica Inspire",
    "carla": "Dr. Carla Ferreira",
}
SUPPORTED_CLINICS = tuple(CLINIC_DISPLAY_NAMES)


def validate_clinic_keys(keys):
    if not isinstance(keys, list) or any(not isinstance(key, str) or key not in SUPPORTED_CLINICS for key in keys):
        raise ValueError("Clínicas inválidas.")
    return [key for key in SUPPORTED_CLINICS if key in keys]
