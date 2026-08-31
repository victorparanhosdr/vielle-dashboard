#!/usr/bin/env python3
import base64
import contextlib
import contextvars
import hashlib
import hmac
import html
import io
import json
import os
import secrets
import sqlite3
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DB_PATH = BASE_DIR / "kommo_report.sqlite3"
CURRENT_CLINIC_ID = contextvars.ContextVar("CURRENT_CLINIC_ID", default="vielle")
SUPPORTED_CLINICS = ("vielle", "inspire", "carla", "casa_vitalle")
CLINIC_ENV_PREFIXES = {"vielle": "", "inspire": "INSPIRE", "carla": "CARLA", "casa_vitalle": "CASA_VITALLE"}
CLINIC_SCOPED_CONFIG_KEYS = {
    "KOMMO_SUBDOMAIN",
    "KOMMO_CLIENT_ID",
    "KOMMO_CLIENT_SECRET",
    "KOMMO_LONG_LIVED_TOKEN",
    "KOMMO_REDIRECT_URI",
    "MIDAS_API_BASE_URL",
    "MIDAS_API_TOKEN",
    "MIDAS_LOCATION_ID",
    "MIDAS_HISTORY_START",
    "CLINICA_EXPERTS_TOKEN",
    "CLINICA_HISTORY_START",
    "CLINICA_WEBHOOK_SECRET",
}


def load_env():
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


load_env()

KOMMO_SUBDOMAIN = os.getenv("KOMMO_SUBDOMAIN", "").replace(".kommo.com", "")
KOMMO_CLIENT_ID = os.getenv("KOMMO_CLIENT_ID", "")
KOMMO_CLIENT_SECRET = os.getenv("KOMMO_CLIENT_SECRET", "")
KOMMO_LONG_LIVED_TOKEN = os.getenv("KOMMO_LONG_LIVED_TOKEN", "")
KOMMO_REDIRECT_URI = os.getenv("KOMMO_REDIRECT_URI", "http://localhost:8080/auth/callback")
CLINICA_EXPERTS_TOKEN = os.getenv("CLINICA_EXPERTS_TOKEN", "s9GCe9SqWy0tE8j0rilZMGXUoCEWuBWRM0AoluBp059c069c")
CLINICA_HISTORY_START = os.getenv("CLINICA_HISTORY_START", "2020-01-01")
CLINICA_RATE_LIMIT_DELAY = int(os.getenv("CLINICA_RATE_LIMIT_DELAY", "20"))
SYNC_INTERVAL_MINUTES = int(os.getenv("SYNC_INTERVAL_MINUTES", "30"))
APP_SECRET = os.getenv("APP_SECRET", "dev-secret-change-me")
CLINICA_WEBHOOK_SECRET = os.getenv("CLINICA_WEBHOOK_SECRET", "")
PORT = int(os.getenv("PORT", "8080"))
DASHBOARD_USER = os.getenv("DASHBOARD_USER", "")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")

CONFIG_DEFAULTS = {
    "KOMMO_SUBDOMAIN": KOMMO_SUBDOMAIN,
    "KOMMO_CLIENT_ID": KOMMO_CLIENT_ID,
    "KOMMO_CLIENT_SECRET": KOMMO_CLIENT_SECRET,
    "KOMMO_LONG_LIVED_TOKEN": KOMMO_LONG_LIVED_TOKEN,
    "KOMMO_REDIRECT_URI": KOMMO_REDIRECT_URI,
    "MIDAS_API_BASE_URL": os.getenv("MIDAS_API_BASE_URL", "https://app.midasone.com.br"),
    "MIDAS_API_TOKEN": os.getenv("MIDAS_API_TOKEN", ""),
    "MIDAS_LOCATION_ID": os.getenv("MIDAS_LOCATION_ID", ""),
    "MIDAS_HISTORY_START": os.getenv("MIDAS_HISTORY_START", "2020-01-01"),
    "CLINICA_EXPERTS_TOKEN": CLINICA_EXPERTS_TOKEN,
    "CLINICA_HISTORY_START": CLINICA_HISTORY_START,
    "CLINICA_RATE_LIMIT_DELAY": str(CLINICA_RATE_LIMIT_DELAY),
    "SYNC_INTERVAL_MINUTES": str(SYNC_INTERVAL_MINUTES),
    "APP_SECRET": APP_SECRET,
    "CLINICA_WEBHOOK_SECRET": CLINICA_WEBHOOK_SECRET,
    "DASHBOARD_USER": DASHBOARD_USER,
    "DASHBOARD_PASSWORD": DASHBOARD_PASSWORD,
    "MASTER_USER": os.getenv("MASTER_USER", "master"),
    "MASTER_PASSWORD": os.getenv("MASTER_PASSWORD", ""),
}

SECRET_CONFIG_KEYS = {
    "KOMMO_CLIENT_SECRET",
    "KOMMO_LONG_LIVED_TOKEN",
    "MIDAS_API_TOKEN",
    "CLINICA_EXPERTS_TOKEN",
    "APP_SECRET",
    "CLINICA_WEBHOOK_SECRET",
    "DASHBOARD_PASSWORD",
    "MASTER_PASSWORD",
}

EDITABLE_CONFIG_KEYS = set(CONFIG_DEFAULTS.keys())
KOMMO_CONFIG_KEYS = {
    "KOMMO_SUBDOMAIN",
    "KOMMO_CLIENT_ID",
    "KOMMO_CLIENT_SECRET",
    "KOMMO_LONG_LIVED_TOKEN",
    "KOMMO_REDIRECT_URI",
}
KOMMO_DISABLED_KEY = "KOMMO_DISABLED"
KOMMO_RESET_MARKER_KEY = "KOMMO_RESET_VERSION"
KOMMO_RESET_VERSION = "2026-08-31-reset-kommo-1"

PIPELINE_DOCTOR_MAP = {
    "Tráfego Paranhos": "Victor Paranhos de Andrade",
    "Organico Paranhos": "Victor Paranhos de Andrade",
    "Pac. Modelo Paranhos": "Victor Paranhos de Andrade",
    "Organico Di Lena": "Victor Di Lena",
    "Pac. Modelo Di Lena": "Victor Di Lena",
    "Organico Leticia": "Leticia Gomes",
    "Pac. Modelo Leticia": "Leticia Gomes",
    "Organico Marcela": "Dra. Marcela Yumi",
    "Orgânico Rafa": "Rafaela Amaro",
}

DOCTOR_PROFESSIONALS = {
    "Victor Paranhos de Andrade": "870941a9-f690-4016-a109-9641a671bc1d",
    "Victor Di Lena": "5c61909b-49d0-4fae-9db9-3b10008fc849",
    "Leticia Gomes": "6590b7ba-3f90-49bc-b3ec-64344edcd439",
    "Dra. Marcela Yumi": "589b97b5-2a0a-4808-ad74-63c3726e2edf",
    "Rafaela Amaro": "3f9ac58c-2427-46a6-9a41-166bf4ebddb5",
}

CLINIC_PIPELINE_DOCTOR_MAPS = {
    "vielle": PIPELINE_DOCTOR_MAP,
}

CLINIC_DOCTOR_PROFESSIONALS = {
    "vielle": DOCTOR_PROFESSIONALS,
    "inspire": {
        "Adrielli Gonzaga Da Silva": "833965e8-acc0-47d9-b15e-df749069ce95",
        "Ana Beatriz Ferreira da Silva": "fd8b2edf-3578-43cf-a1e7-87ea299cb419",
        "Giovanna Fogo": "205298fd-973c-494b-a7ea-c58b644ecee5",
        "Jessika de Paula Medeiros": "b8b09d3e-8998-44e0-bb56-16c46415287b",
        "Joilda De França Lima": "6fb360fc-2721-449e-8566-e4155dfe862d",
        "Leticia Araújo de Quevedo": "3535aff4-b079-4037-9e0c-17e08c0780ea",
        "Matheus Azevedo Silva": "382552a2-17b8-4d1c-92fe-24f859f86e3c",
        "Vinicyus Leite Moreira Faria": "d59079c4-6ab9-4d5a-9112-cfa1b4ba11fc",
    },
}

CLINIC_KOMMO_DOCTOR_ALIASES = {
    "inspire": {
        "Vinicyus Leite Moreira Faria": ["Dr. Vinicyus", "Dr Vinicyus", "Vinicyus"],
        "Leticia Araújo de Quevedo": ["Dra. Leticia", "Dra Leticia", "Leticia", "Letícia"],
        "Giovanna Fogo": ["Dra. Geovanna", "Dra Geovanna", "Dra. Giovanna", "Dra Giovanna", "Geovanna", "Giovanna"],
        "Jessika de Paula Medeiros": ["Dra.Jessica", "Dra. Jessica", "Dra Jessica", "Jessica", "Dra.Jessika", "Dra. Jessika", "Dra Jessika", "Jessika"],
        "Matheus Azevedo Silva": ["Dr. Matheus", "Dr Matheus", "Matheus", "Matheus Azevedo"],
    },
}

CLINIC_DOCTOR_CROSS_HIDDEN = {
    "inspire": {
        "Adrielli Gonzaga Da Silva",
        "Ana Beatriz Ferreira da Silva",
        "Joilda De França Lima",
    },
}

CLINIC_FORCED_PROFESSIONAL_MATCHES = {
    "victor": (("victor",), ("paranhos", "andrade")),
}

CLINIC_DISPLAY_NAMES = {
    "vielle": "Vielle Clinic",
    "inspire": "Clínica Inspire",
    "carla": "Dr. Carla Ferreira",
    "casa_vitalle": "Casa Vitalle",
}


PROCEDURE_CATEGORY_RULES = [
    ("Bioestimulador", ("bioestimulador", "stiim", "sculptra", "radiesse", "ellanse", "colágeno", "colageno", "neauvia")),
    ("Toxina Botulínica", ("toxina", "botox", "bruxismo")),
    ("Preenchimento", ("preenchimento", "ácido hialur", "acido hialur", "rinomodel", "labial", "malar", "mento", "olheira")),
    ("Tecnologias", ("co2", "laser", "blefaro", "ultrassom", "ultraformer", "liftera", "radiofrequ", "tecnologia")),
    ("Procedimentos Para Pele", ("peeling", "skinbooster", "melasma", "lhalapeel", "ácido tranex", "acido tranex", "limpeza")),
    ("Avaliação/Consulta", ("consulta", "avaliação", "avaliacao")),
    ("Produtos", ("produto", "home care", "protetor", "sérum", "serum")),
]


def configured(value):
    if not value:
        return False
    pending_markers = ("cole_", "COLE_", "troque_por", "suaempresa")
    return not value.startswith(pending_markers)


def procedure_category_from_name(name):
    text = (name or "").lower()
    for category, terms in PROCEDURE_CATEGORY_RULES:
        if any(term in text for term in terms):
            return category
    return "Sem categoria"


def normalize_lookup_text(value):
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    without_accents = "".join(char for char in normalized if not unicodedata.combining(char))
    return without_accents.lower().strip()


def matches_forced_professional_name(name, clinic_id=None):
    rule = CLINIC_FORCED_PROFESSIONAL_MATCHES.get(clinic_id or current_clinic_id())
    if not rule:
        return False
    normalized_name = normalize_lookup_text(name)
    return all(any(term in normalized_name for term in group) for group in rule)


def clinic_pipeline_doctor_map():
    return CLINIC_PIPELINE_DOCTOR_MAPS.get(current_clinic_id(), {})


def clinica_professionals_from_data(conn):
    rows = conn.execute(
        """
        select professional_uuid as uuid,
               coalesce(
                 json_extract(raw_json, '$.professional.name'),
                 json_extract(raw_json, '$.professional.full_name'),
                 json_extract(raw_json, '$.professional.title')
               ) as name
        from clinica_bookings
        where professional_uuid is not null
        union
        select json_extract(raw_json, '$.seller.uuid') as uuid,
               coalesce(
                 json_extract(raw_json, '$.seller.name'),
                 json_extract(raw_json, '$.seller.full_name'),
                 json_extract(raw_json, '$.seller.title')
               ) as name
        from clinica_sales
        where json_extract(raw_json, '$.seller.uuid') is not null
        order by name
        """
    ).fetchall()
    professionals = {}
    for row in rows:
        uuid = row["uuid"]
        name = row["name"]
        if uuid and name:
            professionals[str(name)] = str(uuid)
    return professionals


def clinica_sellers_from_data(conn):
    rows = conn.execute(
        """
        select json_extract(raw_json, '$.seller.uuid') as uuid,
               coalesce(
                 json_extract(raw_json, '$.seller.name'),
                 json_extract(raw_json, '$.seller.full_name'),
                 json_extract(raw_json, '$.seller.title')
               ) as name
        from clinica_sales
        where json_extract(raw_json, '$.seller.uuid') is not null
        group by uuid, name
        order by name
        """
    ).fetchall()
    sellers = {}
    for row in rows:
        uuid = row["uuid"]
        name = row["name"]
        if uuid and name:
            sellers[str(name)] = str(uuid)
    return sellers


def kommo_sellers_from_data(conn):
    rows = conn.execute(
        """
        select
          trim(coalesce(json_extract(custom_value.value, '$.value'), '')) as name
        from leads
        join json_each(leads.raw_json, '$.custom_fields_values') custom_field
        join json_each(custom_field.value, '$.values') custom_value
        where lower(coalesce(json_extract(custom_field.value, '$.field_name'), '')) = 'vendedor'
          and trim(coalesce(json_extract(custom_value.value, '$.value'), '')) != ''
        group by name
        order by name
        """
    ).fetchall()
    sellers = {}
    for row in rows:
        name = row["name"]
        if name:
            sellers[str(name)] = str(name)
    return sellers


def kommo_seller_clause(seller, prefix="leads"):
    seller = (seller or "").strip().lower()
    if not seller:
        return "", []
    return (
        f"""
        exists (
          select 1
          from json_each({prefix}.raw_json, '$.custom_fields_values') custom_field
          join json_each(custom_field.value, '$.values') custom_value
          where lower(coalesce(json_extract(custom_field.value, '$.field_name'), '')) = 'vendedor'
            and lower(trim(coalesce(json_extract(custom_value.value, '$.value'), ''))) = ?
        )
        """,
        [seller],
    )


def clinic_doctor_professionals(conn):
    clinic_id = current_clinic_id()
    dynamic_professionals = clinica_professionals_from_data(conn)
    if clinic_id in CLINIC_FORCED_PROFESSIONAL_MATCHES:
        return {
            name: uuid
            for name, uuid in dynamic_professionals.items()
            if matches_forced_professional_name(name, clinic_id)
        }
    static_professionals = CLINIC_DOCTOR_PROFESSIONALS.get(current_clinic_id())
    if static_professionals is not None:
        professionals = dict(static_professionals)
        professionals.update(dynamic_professionals)
        return professionals
    return dynamic_professionals


def forced_professional_uuids(conn):
    if current_clinic_id() not in CLINIC_FORCED_PROFESSIONAL_MATCHES:
        return []
    return sorted(set(clinic_doctor_professionals(conn).values()))


def clinic_kommo_doctor_aliases(doctor_name):
    aliases = CLINIC_KOMMO_DOCTOR_ALIASES.get(current_clinic_id(), {}).get(doctor_name, [])
    return [alias.lower() for alias in aliases if alias]


def clinic_kommo_doctor_alias_lookup():
    lookup = {}
    for doctor_name, aliases in CLINIC_KOMMO_DOCTOR_ALIASES.get(current_clinic_id(), {}).items():
        for alias in aliases:
            if alias:
                lookup[alias.lower()] = doctor_name
    return lookup


def clinic_has_kommo_doctor_aliases():
    return current_clinic_id() in CLINIC_KOMMO_DOCTOR_ALIASES


def lead_custom_field_value(raw_json, field_name):
    try:
        payload = json.loads(raw_json or "{}")
    except (TypeError, ValueError):
        return ""
    wanted = field_name.lower()
    for field in payload.get("custom_fields_values") or []:
        if str(field.get("field_name") or "").lower() != wanted:
            continue
        for value in field.get("values") or []:
            field_value = value.get("value")
            if field_value:
                return str(field_value)
    return ""


def kommo_doctor_clause(aliases, prefix="leads"):
    aliases = [alias.lower() for alias in aliases if alias]
    if not aliases:
        return "", []
    placeholders = ",".join("?" for _ in aliases)
    return (
        f"""
        exists (
          select 1
          from json_each({prefix}.raw_json, '$.custom_fields_values') custom_field
          join json_each(custom_field.value, '$.values') custom_value
          where lower(coalesce(json_extract(custom_field.value, '$.field_name'), '')) = 'profissional'
            and lower(coalesce(json_extract(custom_value.value, '$.value'), '')) in ({placeholders})
        )
        """,
        aliases,
    )


def current_clinic_id():
    clinic_id = CURRENT_CLINIC_ID.get()
    return clinic_id if clinic_id in SUPPORTED_CLINICS else "vielle"


def clinic_display_name(clinic_id=None):
    clinic_id = clinic_id or current_clinic_id()
    return CLINIC_DISPLAY_NAMES.get(normalize_clinic_id(clinic_id), "Vielle Clinic")


def clinic_db_path(clinic_id=None):
    clinic_id = clinic_id or current_clinic_id()
    if clinic_id == "vielle":
        return DB_PATH
    return BASE_DIR / f"kommo_report_{clinic_id}.sqlite3"


@contextlib.contextmanager
def clinic_context(clinic_id):
    token = CURRENT_CLINIC_ID.set(normalize_clinic_id(clinic_id))
    try:
        init_db()
        yield
    finally:
        CURRENT_CLINIC_ID.reset(token)


def clinic_env_value(key):
    prefix = CLINIC_ENV_PREFIXES.get(current_clinic_id(), "")
    if not prefix or key not in CLINIC_SCOPED_CONFIG_KEYS:
        return ""
    return os.getenv(f"{prefix}_{key}", "").strip()


def db():
    conn = sqlite3.connect(clinic_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def config_value(key, default=None):
    def clean_config(raw_value):
        value = "" if raw_value is None else str(raw_value).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            return value[1:-1].strip()
        return value.strip("'\"").strip()

    clinic_id = current_clinic_id()
    fallback = CONFIG_DEFAULTS.get(key, default or "")
    if key in KOMMO_CONFIG_KEYS:
        try:
            with db() as conn:
                disabled = conn.execute(
                    "select value from app_settings where key = ?",
                    (KOMMO_DISABLED_KEY,),
                ).fetchone()
                if disabled is not None and str(disabled["value"]).strip() == "1":
                    row = conn.execute("select value from app_settings where key = ?", (key,)).fetchone()
                    if row is not None:
                        row_value = clean_config(row["value"])
                        if configured(row_value):
                            return row_value
                        if key not in SECRET_CONFIG_KEYS and row_value:
                            return row_value
                    return ""
        except sqlite3.Error:
            pass
    if clinic_id != "vielle" and key in CLINIC_SCOPED_CONFIG_KEYS:
        fallback = default or ""
        if key in {"MIDAS_API_BASE_URL", "MIDAS_HISTORY_START"}:
            fallback = CONFIG_DEFAULTS.get(key, fallback)
        try:
            with db() as conn:
                row = conn.execute("select value from app_settings where key = ?", (key,)).fetchone()
                if row is not None and configured(row["value"]):
                    return clean_config(row["value"])
        except sqlite3.Error:
            pass
    scoped_env = clinic_env_value(key)
    if scoped_env:
        return clean_config(scoped_env)
    try:
        with db() as conn:
            row = conn.execute("select value from app_settings where key = ?", (key,)).fetchone()
            if row is not None:
                row_value = clean_config(row["value"])
                if configured(row_value):
                    return row_value
                if key not in SECRET_CONFIG_KEYS and row_value:
                    return row_value
    except sqlite3.Error:
        pass
    return clean_config(fallback)


def config_int(key, default):
    try:
        return int(config_value(key, str(default)))
    except (TypeError, ValueError):
        return default


def save_config_values(values):
    now = int(time.time())
    with db() as conn:
        kommo_has_new_value = any(
            key in KOMMO_CONFIG_KEYS and configured(str(value or "").strip())
            for key, value in values.items()
        )
        if kommo_has_new_value:
            conn.execute("delete from app_settings where key = ?", (KOMMO_DISABLED_KEY,))
        for key, value in values.items():
            if key not in EDITABLE_CONFIG_KEYS:
                continue
            conn.execute(
                """
                insert into app_settings (key, value, updated_at)
                values (?, ?, ?)
                on conflict(key) do update set
                    value=excluded.value,
                    updated_at=excluded.updated_at
                """,
                (key, str(value or ""), now),
            )


def mask_secret(value):
    if not configured(value):
        return ""
    text = str(value)
    if len(text) <= 10:
        return "configurado"
    return f"{text[:4]}...{text[-4:]}"


def settings_payload():
    config = {}
    for key in sorted(EDITABLE_CONFIG_KEYS):
        value = config_value(key, "")
        is_secret = key in SECRET_CONFIG_KEYS
        config[key] = {
            "configured": configured(value),
            "secret": is_secret,
            "value": "" if is_secret else value,
            "masked": mask_secret(value) if is_secret else "",
        }
    return {"ok": True, "config": config}


def init_db():
    with db() as conn:
        conn.executescript(
            """
            create table if not exists oauth_tokens (
                id integer primary key check (id = 1),
                account_domain text not null,
                access_token text not null,
                refresh_token text not null,
                expires_at integer not null,
                updated_at integer not null
            );

            create table if not exists oauth_states (
                state text primary key,
                expires_at integer not null
            );

            create table if not exists app_settings (
                key text primary key,
                value text not null,
                updated_at integer not null
            );

            create table if not exists leads (
                id integer primary key,
                name text,
                price real,
                status_id integer,
                pipeline_id integer,
                responsible_user_id integer,
                created_at integer,
                updated_at integer,
                closed_at integer,
                raw_json text not null,
                synced_at integer not null
            );

            create table if not exists sync_log (
                id integer primary key autoincrement,
                started_at integer not null,
                finished_at integer,
                ok integer not null default 0,
                message text
            );

            create table if not exists pipelines (
                id integer primary key,
                name text not null,
                sort integer,
                is_main integer not null default 0,
                raw_json text not null,
                synced_at integer not null
            );

            create table if not exists pipeline_statuses (
                id integer primary key,
                pipeline_id integer not null,
                name text not null,
                sort integer,
                color text,
                type integer,
                raw_json text not null,
                synced_at integer not null
            );

            create table if not exists lead_status_events (
                id text primary key,
                lead_id integer not null,
                created_at integer not null,
                before_status_id integer,
                before_pipeline_id integer,
                after_status_id integer,
                after_pipeline_id integer,
                raw_json text not null,
                synced_at integer not null
            );

            create table if not exists lead_interaction_events (
                id text primary key,
                lead_id integer not null,
                type text not null,
                created_at integer not null,
                raw_json text not null,
                synced_at integer not null
            );

            create table if not exists clinica_patients (
                uuid text primary key,
                name text,
                phone text,
                email text,
                origin text,
                active integer,
                raw_json text not null,
                synced_at integer not null
            );

            create table if not exists clinica_bookings (
                uuid text primary key,
                patient_uuid text,
                professional_uuid text,
                procedure_uuid text,
                status text,
                starts_at text,
                ends_at text,
                registry_user_name text,
                raw_json text not null,
                synced_at integer not null
            );

            create table if not exists clinica_sales (
                uuid text primary key,
                patient_uuid text,
                type text,
                sale_date text,
                total real,
                raw_json text not null,
                synced_at integer not null
            );

            create table if not exists clinica_procedures (
                uuid text primary key,
                name text not null,
                category_name text,
                duration integer,
                price real,
                active integer,
                raw_json text not null,
                synced_at integer not null
            );

            create table if not exists clinica_bills (
                uuid text primary key,
                type text,
                status text,
                description text,
                category_name text,
                account_name text,
                emission_date text,
                due_date text,
                paid_at text,
                amount real,
                raw_json text not null,
                synced_at integer not null
            );

            create table if not exists clinica_parcels (
                uuid text primary key,
                bill_uuid text,
                type text,
                status text,
                description text,
                category_name text,
                account_name text,
                due_date text,
                paid_at text,
                amount real,
                raw_json text not null,
                synced_at integer not null
            );

            create table if not exists clinica_sync_log (
                id integer primary key autoincrement,
                started_at integer not null,
                finished_at integer,
                ok integer not null default 0,
                message text
            );
            """
        )
        for statement in (
            "alter table clinica_bookings add column registry_user_name text",
        ):
            try:
                conn.execute(statement)
            except sqlite3.OperationalError:
                pass


def json_response(handler, payload, status=HTTPStatus.OK, headers=None):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    for key, value in (headers or {}).items():
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(body)


def binary_response(handler, content, filename, content_type):
    handler.send_response(HTTPStatus.OK)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Disposition", f'attachment; filename="{filename}"')
    handler.send_header("Content-Length", str(len(content)))
    handler.end_headers()
    handler.wfile.write(content)


def html_response(handler, content, status=HTTPStatus.OK):
    body = content.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def redirect(handler, location):
    handler.send_response(HTTPStatus.FOUND)
    handler.send_header("Location", location)
    handler.end_headers()


CLINIC_ACCESS_ENV = {
    "vielle": "VIELLE_ACCESS_CODE",
    "inspire": "INSPIRE_ACCESS_CODE",
    "carla": "CARLA_ACCESS_CODE",
    "casa_vitalle": "CASA_VITALLE_ACCESS_CODE",
}


def normalize_clinic_id(value):
    clinic_id = (value or "vielle").strip().lower()
    return clinic_id if clinic_id in CLINIC_ACCESS_ENV else "vielle"


def clinic_access_code(clinic_id):
    normalized = normalize_clinic_id(clinic_id)
    env_key = CLINIC_ACCESS_ENV.get(normalized, "VIELLE_ACCESS_CODE")
    default_code = "admin" if normalized == "casa_vitalle" else ""
    return os.getenv(env_key, default_code).strip()


def clinic_access_cookie_name(clinic_id):
    return f"clinic_access_{normalize_clinic_id(clinic_id)}"


def clinic_access_signature(clinic_id):
    clinic_id = normalize_clinic_id(clinic_id)
    secret = config_value("APP_SECRET", "dev-secret-change-me")
    code = clinic_access_code(clinic_id)
    message = f"{clinic_id}:{code}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def clinic_access_cookie(clinic_id):
    clinic_id = normalize_clinic_id(clinic_id)
    return (
        f"{clinic_access_cookie_name(clinic_id)}={clinic_access_signature(clinic_id)}; "
        "Path=/; Max-Age=43200; HttpOnly; SameSite=Lax"
    )


def account_domain(referrer=None):
    if referrer:
        return referrer.replace("https://", "").replace("http://", "").strip("/")
    subdomain = config_value("KOMMO_SUBDOMAIN", "").replace(".kommo.com", "")
    if not subdomain:
        raise RuntimeError("KOMMO_SUBDOMAIN nao foi configurado.")
    return f"{subdomain}.kommo.com"


def kommo_request(method, path, token=None, body=None, domain=None):
    target_domain = domain or account_domain()
    url = f"https://{target_domain}{path}"
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            content = res.read().decode("utf-8")
            return json.loads(content) if content else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        if exc.code == 401:
            raise RuntimeError(
                "Kommo retornou 401: token inválido, expirado ou de outra conta. "
                "Revise a chave/token da integração desta clínica."
            ) from exc
        raise RuntimeError(f"Kommo retornou {exc.code}: {detail}") from exc


def clinica_request(path, api_prefix="/api/v1"):
    token = config_value("CLINICA_EXPERTS_TOKEN", "")
    if not configured(token):
        raise RuntimeError("CLINICA_EXPERTS_TOKEN nao foi configurado.")
    url = f"https://api.clinicaexperts.com.br{api_prefix}{path}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "KommoReport/1.0 (+local-dashboard)",
        },
        method="GET",
    )
    for attempt in range(1, 7):
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                content = res.read().decode("utf-8")
                return json.loads(content) if content else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code in (429, 502, 503, 504) and attempt < 6:
                time.sleep(config_int("CLINICA_RATE_LIMIT_DELAY", 20) * attempt)
                continue
            if exc.code in (502, 503, 504):
                raise RuntimeError(
                    "Clínica Experts está temporariamente indisponível. Tente atualizar novamente em alguns minutos."
                ) from exc
            if exc.code == 403 and "cloudflare" in detail.lower():
                raise RuntimeError(
                    "Clínica Experts bloqueou esta origem via Cloudflare. O token foi configurado, "
                    "mas a API recusou o cliente local."
                ) from exc
            if exc.code == 403 and "sales-quotes" in path:
                raise RuntimeError(
                    "Clínica Experts negou acesso aos orçamentos (sales-quotes). "
                    "Libere a permissão de visualizar orçamentos/vendas para o token da integração."
                ) from exc
            raise RuntimeError(f"Clínica Experts retornou {exc.code}: {detail}") from exc


def extract_items(payload, preferred_keys):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in preferred_keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value
    for value in payload.values():
        if isinstance(value, list):
            return value
    return []


def first_value(data, keys):
    for key in keys:
        if isinstance(data, dict) and data.get(key) is not None:
            return data.get(key)
    return None


def money_value(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number / 100


def stable_numeric_id(value, namespace="midas"):
    text = f"{namespace}:{value or ''}"
    return int(hashlib.sha1(text.encode("utf-8")).hexdigest()[:15], 16)


def parse_any_timestamp(value):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 100000000000:
            number = number / 1000
        return int(number)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        number = int(text)
        return int(number / 1000) if number > 100000000000 else number
    normalized = text.replace("Z", "+00:00")
    try:
        return int(datetime.fromisoformat(normalized).timestamp())
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return int(datetime.strptime(text[:19], fmt).timestamp())
        except ValueError:
            continue
    return None


def nested_name(data, key):
    value = first_value(data, [key])
    if isinstance(value, dict):
        return first_value(value, ["name", "title", "description"])
    return value


def deep_first_name(data, paths):
    for path in paths:
        value = data
        for key in path:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
        if isinstance(value, dict):
            name = first_value(value, ["name", "full_name", "title", "description", "email"])
            if name:
                return str(name)
        elif value:
            return str(value)
    return ""


def named_value(value):
    if isinstance(value, dict):
        name = first_value(value, ["name", "full_name", "title", "description", "email"])
        return str(name) if name else ""
    return str(value) if value else ""


def recursive_named_value(data, key_names, depth=0):
    if depth > 5:
        return ""
    if isinstance(data, dict):
        for key, value in data.items():
            normalized = str(key).lower()
            if normalized in key_names:
                found = named_value(value)
                if found:
                    return found
            found = recursive_named_value(value, key_names, depth + 1)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = recursive_named_value(item, key_names, depth + 1)
            if found:
                return found
    return ""


def booking_registry_user_name(raw_json):
    try:
        booking = json.loads(raw_json or "{}")
    except (TypeError, ValueError):
        return ""
    direct = deep_first_name(
        booking,
        [
            ["created_by"],
            ["created_by_user"],
            ["created_by_professional"],
            ["creator"],
            ["creator_user"],
            ["action_by"],
            ["performed_by"],
            ["triggered_by"],
            ["registered_by"],
            ["registered_by_user"],
            ["scheduled_by"],
            ["scheduled_by_user"],
            ["user"],
            ["author"],
            ["owner"],
        ],
    )
    if direct:
        return direct
    return recursive_named_value(
        booking,
        {
            "created_by",
            "created_by_user",
            "created_by_professional",
            "creator",
            "creator_user",
            "action_by",
            "performed_by",
            "triggered_by",
            "registered_by",
            "registered_by_user",
            "scheduled_by",
            "scheduled_by_user",
            "user",
            "author",
            "owner",
        },
    )


def save_state(state):
    with db() as conn:
        conn.execute(
            "insert or replace into oauth_states (state, expires_at) values (?, ?)",
            (state, int(time.time()) + 20 * 60),
        )


def consume_state(state):
    now = int(time.time())
    with db() as conn:
        row = conn.execute(
            "select state from oauth_states where state = ? and expires_at > ?",
            (state, now),
        ).fetchone()
        conn.execute("delete from oauth_states where state = ? or expires_at <= ?", (state, now))
    return row is not None


def save_tokens(payload, domain):
    expires_in = int(payload.get("expires_in", 86400))
    with db() as conn:
        conn.execute(
            """
            insert or replace into oauth_tokens
            (id, account_domain, access_token, refresh_token, expires_at, updated_at)
            values (1, ?, ?, ?, ?, ?)
            """,
            (
                domain,
                payload["access_token"],
                payload["refresh_token"],
                int(time.time()) + expires_in - 120,
                int(time.time()),
            ),
        )


def get_tokens():
    with db() as conn:
        return conn.execute("select * from oauth_tokens where id = 1").fetchone()


def get_access_context():
    long_token = config_value("KOMMO_LONG_LIVED_TOKEN", "")
    if configured(long_token):
        return {
            "access_token": long_token,
            "account_domain": account_domain(),
        }
    tokens = refresh_tokens_if_needed()
    return {
        "access_token": tokens["access_token"],
        "account_domain": tokens["account_domain"],
    }


def exchange_code(code, referrer):
    domain = account_domain(referrer)
    payload = kommo_request(
        "POST",
        "/oauth2/access_token",
        body={
            "client_id": config_value("KOMMO_CLIENT_ID", ""),
            "client_secret": config_value("KOMMO_CLIENT_SECRET", ""),
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config_value("KOMMO_REDIRECT_URI", ""),
        },
        domain=domain,
    )
    save_tokens(payload, domain)


def refresh_tokens_if_needed():
    tokens = get_tokens()
    if not tokens:
        raise RuntimeError("Kommo ainda nao foi conectado.")
    if int(tokens["expires_at"]) > int(time.time()):
        return tokens
    payload = kommo_request(
        "POST",
        "/oauth2/access_token",
        body={
            "client_id": config_value("KOMMO_CLIENT_ID", ""),
            "client_secret": config_value("KOMMO_CLIENT_SECRET", ""),
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "redirect_uri": config_value("KOMMO_REDIRECT_URI", ""),
        },
        domain=tokens["account_domain"],
    )
    save_tokens(payload, tokens["account_domain"])
    return get_tokens()


def sync_pipelines(access):
    result = kommo_request(
        "GET",
        "/api/v4/leads/pipelines",
        token=access["access_token"],
        domain=access["account_domain"],
    )
    pipelines = result.get("_embedded", {}).get("pipelines", [])
    now = int(time.time())
    with db() as conn:
        for pipeline in pipelines:
            conn.execute(
                """
                insert into pipelines (id, name, sort, is_main, raw_json, synced_at)
                values (?, ?, ?, ?, ?, ?)
                on conflict(id) do update set
                    name=excluded.name,
                    sort=excluded.sort,
                    is_main=excluded.is_main,
                    raw_json=excluded.raw_json,
                    synced_at=excluded.synced_at
                """,
                (
                    pipeline.get("id"),
                    pipeline.get("name") or f"Funil {pipeline.get('id')}",
                    pipeline.get("sort"),
                    1 if pipeline.get("is_main") else 0,
                    json.dumps(pipeline, ensure_ascii=False),
                    now,
                ),
            )
            for status in pipeline.get("_embedded", {}).get("statuses", []):
                conn.execute(
                    """
                    insert into pipeline_statuses
                    (id, pipeline_id, name, sort, color, type, raw_json, synced_at)
                    values (?, ?, ?, ?, ?, ?, ?, ?)
                    on conflict(id) do update set
                        pipeline_id=excluded.pipeline_id,
                        name=excluded.name,
                        sort=excluded.sort,
                        color=excluded.color,
                        type=excluded.type,
                        raw_json=excluded.raw_json,
                        synced_at=excluded.synced_at
                    """,
                    (
                        status.get("id"),
                        pipeline.get("id"),
                        status.get("name") or f"Fase {status.get('id')}",
                        status.get("sort"),
                        status.get("color"),
                        status.get("type"),
                        json.dumps(status, ensure_ascii=False),
                        now,
                    ),
                )
    return len(pipelines)


def save_clinica_patient(conn, patient, synced_at):
    uuid = first_value(patient, ["uuid", "id", "patient_uuid"])
    if not uuid:
        return False
    conn.execute(
        """
        insert into clinica_patients
        (uuid, name, phone, email, origin, active, raw_json, synced_at)
        values (?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(uuid) do update set
            name=excluded.name,
            phone=excluded.phone,
            email=excluded.email,
            origin=excluded.origin,
            active=excluded.active,
            raw_json=excluded.raw_json,
            synced_at=excluded.synced_at
        """,
        (
            str(uuid),
            first_value(patient, ["name", "full_name"]),
            first_value(patient, ["phone", "cellphone", "mobile", "telephone"]),
            first_value(patient, ["email"]),
            first_value(patient, ["origin", "source"]),
            1 if first_value(patient, ["active"]) is True else 0 if first_value(patient, ["active"]) is False else None,
            json.dumps(patient, ensure_ascii=False),
            synced_at,
        ),
    )
    return True


def save_clinica_booking(conn, booking, synced_at):
    uuid = first_value(booking, ["uuid", "id", "booking_uuid"])
    if not uuid:
        return False
    patient = first_value(booking, ["patient", "patient_uuid"])
    professional = first_value(booking, ["professional", "professional_uuid"])
    procedure = first_value(booking, ["procedure", "procedure_uuid"])
    registry_user_name = booking_registry_user_name(json.dumps(booking, ensure_ascii=False))
    conn.execute(
        """
        insert into clinica_bookings
        (uuid, patient_uuid, professional_uuid, procedure_uuid, status, starts_at, ends_at, registry_user_name, raw_json, synced_at)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(uuid) do update set
            patient_uuid=excluded.patient_uuid,
            professional_uuid=excluded.professional_uuid,
            procedure_uuid=excluded.procedure_uuid,
            status=excluded.status,
            starts_at=excluded.starts_at,
            ends_at=excluded.ends_at,
            registry_user_name=coalesce(excluded.registry_user_name, clinica_bookings.registry_user_name),
            raw_json=excluded.raw_json,
            synced_at=excluded.synced_at
        """,
        (
            str(uuid),
            str(patient.get("uuid") or patient.get("id")) if isinstance(patient, dict) else str(patient) if patient else None,
            str(professional.get("uuid") or professional.get("id")) if isinstance(professional, dict) else str(professional) if professional else None,
            str(procedure.get("uuid") or procedure.get("id")) if isinstance(procedure, dict) else str(procedure) if procedure else None,
            first_value(booking, ["status"]),
            first_value(booking, ["starts_at", "start_at", "scheduled_at"]),
            first_value(booking, ["ends_at", "end_at"]),
            registry_user_name or None,
            json.dumps(booking, ensure_ascii=False),
            synced_at,
        ),
    )
    return True


def find_booking_payload(payload):
    if not isinstance(payload, dict):
        return None
    candidates = [
        payload,
        payload.get("data"),
        payload.get("booking"),
        payload.get("resource"),
        payload.get("model"),
        payload.get("object"),
        payload.get("payload"),
    ]
    for candidate in candidates:
        if isinstance(candidate, dict) and first_value(candidate, ["uuid", "id", "booking_uuid"]):
            if first_value(candidate, ["starts_at", "start_at", "scheduled_at"]) or str(payload.get("event", "")).startswith("booking."):
                return candidate
    for value in payload.values():
        if isinstance(value, dict):
            found = find_booking_payload(value)
            if found:
                return found
        elif isinstance(value, list):
            for item in value:
                found = find_booking_payload(item) if isinstance(item, dict) else None
                if found:
                    return found
    return None


def find_sale_quote_payload(payload):
    if not isinstance(payload, dict):
        return None
    candidates = [
        payload,
        payload.get("data"),
        payload.get("sale_quote"),
        payload.get("quote"),
        payload.get("resource"),
        payload.get("model"),
        payload.get("object"),
        payload.get("payload"),
    ]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if first_value(candidate, ["uuid", "id", "sale_quote_uuid"]) and (
            first_value(candidate, ["quote_date", "final_amount", "nominal_amount"])
            or first_value(candidate, ["type"]) == "sale_quote"
            or "sale_quote" in str(first_value(payload, ["event", "type", "name"]) or "")
        ):
            return candidate
    for value in payload.values():
        if isinstance(value, dict):
            found = find_sale_quote_payload(value)
            if found:
                return found
        elif isinstance(value, list):
            for item in value:
                found = find_sale_quote_payload(item) if isinstance(item, dict) else None
                if found:
                    return found
    return None


def save_clinica_booking_webhook(payload):
    booking = find_booking_payload(payload)
    if not booking:
        return {"ok": False, "saved": False, "error": "Payload sem agendamento reconhecido."}
    registry_user_name = booking_registry_user_name(json.dumps(payload, ensure_ascii=False))
    if registry_user_name and not booking_registry_user_name(json.dumps(booking, ensure_ascii=False)):
        booking = {**booking, "registered_by": {"name": registry_user_name}}
    with db() as conn:
        saved = save_clinica_booking(conn, booking, int(time.time()))
    return {
        "ok": bool(saved),
        "saved": bool(saved),
        "booking_uuid": str(first_value(booking, ["uuid", "id", "booking_uuid"]) or ""),
        "registry_user_name": registry_user_name,
    }


def save_clinica_sale_quote_webhook(payload):
    sale_quote = find_sale_quote_payload(payload)
    if not sale_quote:
        return {"ok": False, "saved": False, "error": "Payload sem orçamento reconhecido."}
    sale_quote = {**sale_quote, "type": first_value(sale_quote, ["type"]) or "sale_quote"}
    with db() as conn:
        saved = save_clinica_sale(conn, sale_quote, int(time.time()))
    return {
        "ok": bool(saved),
        "saved": bool(saved),
        "sale_quote_uuid": str(first_value(sale_quote, ["uuid", "id", "sale_quote_uuid"]) or ""),
    }


def save_clinica_sale(conn, sale, synced_at):
    uuid = first_value(sale, ["uuid", "id", "sale_uuid"])
    if not uuid:
        return False
    patient = first_value(sale, ["patient", "patient_uuid", "buyer", "buyer_person", "person"])
    title = first_value(sale, ["title"])
    total = first_value(sale, ["final_amount", "total", "amount", "value", "price"])
    if total is None and isinstance(title, dict):
        total = first_value(title, ["final_amount", "total", "amount", "value", "nominal_amount"])
    total = money_value(total)
    sale_type = first_value(sale, ["type"]) or ("sale_quote" if "quote_date" in sale else None)
    sale_date = first_value(sale, ["sale_date", "quote_date", "date", "created_at"])
    conn.execute(
        """
        insert into clinica_sales
        (uuid, patient_uuid, type, sale_date, total, raw_json, synced_at)
        values (?, ?, ?, ?, ?, ?, ?)
        on conflict(uuid) do update set
            patient_uuid=excluded.patient_uuid,
            type=excluded.type,
            sale_date=excluded.sale_date,
            total=excluded.total,
            raw_json=excluded.raw_json,
            synced_at=excluded.synced_at
        """,
        (
            str(uuid),
            str(patient.get("uuid") or patient.get("id")) if isinstance(patient, dict) else str(patient) if patient else None,
            sale_type,
            sale_date,
            total,
            json.dumps(sale, ensure_ascii=False),
            synced_at,
        ),
    )
    return True


def save_clinica_procedure(conn, procedure, synced_at):
    uuid = first_value(procedure, ["uuid", "id", "procedure_uuid"])
    name = first_value(procedure, ["name", "title", "description"])
    if not uuid or not name:
        return False
    price = money_value(first_value(procedure, ["price", "amount", "value", "unit_amount"]))
    category = nested_name(procedure, "category") or nested_name(procedure, "procedure_category")
    conn.execute(
        """
        insert into clinica_procedures
        (uuid, name, category_name, duration, price, active, raw_json, synced_at)
        values (?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(uuid) do update set
            name=excluded.name,
            category_name=excluded.category_name,
            duration=excluded.duration,
            price=excluded.price,
            active=excluded.active,
            raw_json=excluded.raw_json,
            synced_at=excluded.synced_at
        """,
        (
            str(uuid),
            str(name),
            category,
            first_value(procedure, ["duration", "duration_minutes", "time"]),
            price,
            1 if first_value(procedure, ["active"]) is True else 0 if first_value(procedure, ["active"]) is False else None,
            json.dumps(procedure, ensure_ascii=False),
            synced_at,
        ),
    )
    return True


def save_clinica_bill(conn, bill, synced_at):
    uuid = first_value(bill, ["uuid", "id", "bill_uuid"])
    if not uuid:
        return False
    amount = money_value(first_value(bill, [
        "amount",
        "value",
        "total",
        "final_amount",
        "nominal_amount",
        "paid_amount",
        "net_amount",
    ]))
    conn.execute(
        """
        insert into clinica_bills
        (uuid, type, status, description, category_name, account_name, emission_date, due_date, paid_at, amount, raw_json, synced_at)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(uuid) do update set
            type=excluded.type,
            status=excluded.status,
            description=excluded.description,
            category_name=excluded.category_name,
            account_name=excluded.account_name,
            emission_date=excluded.emission_date,
            due_date=excluded.due_date,
            paid_at=excluded.paid_at,
            amount=excluded.amount,
            raw_json=excluded.raw_json,
            synced_at=excluded.synced_at
        """,
        (
            str(uuid),
            first_value(bill, ["type"]),
            first_value(bill, ["status"]),
            first_value(bill, ["description", "name", "title"]),
            nested_name(bill, "category") or nested_name(bill, "financial_category"),
            nested_name(bill, "account") or nested_name(bill, "financial_account"),
            first_value(bill, ["emission_date", "created_at", "date"]),
            first_value(bill, ["due_date", "expires_at", "date"]),
            first_value(bill, ["paid_at", "payment_date", "received_at", "paid_date"]),
            amount,
            json.dumps(bill, ensure_ascii=False),
            synced_at,
        ),
    )
    for payment_method in first_value(bill, ["payment_methods"]) or []:
        if not isinstance(payment_method, dict):
            continue
        for parcel in payment_method.get("parcels") or []:
            if not isinstance(parcel, dict):
                continue
            enriched = {
                **parcel,
                "bill": {"uuid": str(uuid)},
                "type": first_value(bill, ["type"]),
                "description": first_value(bill, ["description", "name", "title"]),
                "category": first_value(bill, ["category"]) or first_value(bill, ["financial_category"]),
                "financial_account": first_value(parcel, ["financial_account"]) or first_value(bill, ["financial_account"]) or first_value(bill, ["account"]),
                "raw_bill": bill,
            }
            save_clinica_parcel(conn, enriched, synced_at)
    return True


def save_clinica_parcel(conn, parcel, synced_at):
    uuid = first_value(parcel, ["uuid", "id", "parcel_uuid"])
    if not uuid:
        return False
    bill = first_value(parcel, ["bill", "bill_uuid"])
    amount = money_value(first_value(parcel, [
        "amount",
        "value",
        "total",
        "final_amount",
        "nominal_amount",
        "paid_amount",
        "received_amount",
        "net_amount",
    ]))
    conn.execute(
        """
        insert into clinica_parcels
        (uuid, bill_uuid, type, status, description, category_name, account_name, due_date, paid_at, amount, raw_json, synced_at)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(uuid) do update set
            bill_uuid=coalesce(excluded.bill_uuid, clinica_parcels.bill_uuid),
            type=coalesce(excluded.type, clinica_parcels.type),
            status=coalesce(excluded.status, clinica_parcels.status),
            description=coalesce(excluded.description, clinica_parcels.description),
            category_name=coalesce(excluded.category_name, clinica_parcels.category_name),
            account_name=coalesce(excluded.account_name, clinica_parcels.account_name),
            due_date=coalesce(excluded.due_date, clinica_parcels.due_date),
            paid_at=coalesce(excluded.paid_at, clinica_parcels.paid_at),
            amount=coalesce(excluded.amount, clinica_parcels.amount),
            raw_json=excluded.raw_json,
            synced_at=excluded.synced_at
        """,
        (
            str(uuid),
            str(bill.get("uuid") or bill.get("id")) if isinstance(bill, dict) else str(bill) if bill else None,
            first_value(parcel, ["type"]),
            first_value(parcel, ["status"]),
            first_value(parcel, ["description", "name", "title"]),
            nested_name(parcel, "category") or nested_name(parcel, "financial_category"),
            nested_name(parcel, "account") or nested_name(parcel, "financial_account"),
            first_value(parcel, ["due_date", "expires_at", "date"]),
            first_value(parcel, [
                "paid_at",
                "payment_date",
                "received_at",
                "paid_date",
                "execution_date",
                "compensation_date",
                "calc_compensation_date",
            ]),
            amount,
            json.dumps(parcel, ensure_ascii=False),
            synced_at,
        ),
    )
    return True


def sync_clinica_list(path, preferred_keys, saver, max_pages=100):
    total = 0
    synced_at = int(time.time())
    for page in range(1, max_pages + 1):
        sep = "&" if "?" in path else "?"
        payload = clinica_request(f"{path}{sep}page={page}")
        items = extract_items(payload, preferred_keys)
        if not items:
            break
        with db() as conn:
            for item in items:
                if saver(conn, item, synced_at):
                    total += 1
        if len(items) < 100:
            break
    return total


def sync_clinica_sales_period(date_from, date_to):
    starts_at = f"{date_from}T00:00:00-03:00"
    ends_at = f"{date_to}T23:59:59-03:00"
    total = 0
    seen = set()
    synced_at = int(time.time())
    for sort_column in ("sale_date", "created_at", "updated_at"):
        path = (
            f"/sales?starts_at={starts_at}&ends_at={ends_at}"
            f"&sort_column={sort_column}&per_page=100"
        )
        for page in range(1, 101):
            payload = clinica_request(f"{path}&page={page}")
            items = extract_items(payload, ["data", "sales"])
            if not items:
                break
            with db() as conn:
                for item in items:
                    uuid = first_value(item, ["uuid", "id", "sale_uuid"])
                    if save_clinica_sale(conn, item, synced_at) and uuid and str(uuid) not in seen:
                        seen.add(str(uuid))
                        total += 1
            if len(items) < 100:
                break
    return total


def sync_clinica_sale_quotes_period(date_from, date_to):
    starts_at = f"{date_from}T00:00:00-03:00"
    ends_at = f"{date_to}T23:59:59-03:00"
    total = 0
    seen = set()
    synced_at = int(time.time())
    for sort_column in ("quote_date", "created_at", "updated_at"):
        path = (
            f"/sales-quotes?starts_at={starts_at}&ends_at={ends_at}"
            f"&sort_column={sort_column}&per_page=100"
        )
        for page in range(1, 101):
            payload = clinica_request(f"{path}&page={page}", api_prefix="/api")
            items = extract_items(payload, ["data", "sales_quotes", "sale_quotes"])
            if not items:
                break
            with db() as conn:
                for item in items:
                    item = {**item, "type": first_value(item, ["type"]) or "sale_quote"}
                    uuid = first_value(item, ["uuid", "id", "sale_quote_uuid"])
                    if save_clinica_sale(conn, item, synced_at) and uuid and str(uuid) not in seen:
                        seen.add(str(uuid))
                        total += 1
            if len(items) < 100:
                break
    return total


def month_ranges(date_from, date_to):
    current = datetime.strptime(date_from, "%Y-%m-%d").replace(day=1)
    end = datetime.strptime(date_to, "%Y-%m-%d")
    while current <= end:
        next_month = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
        chunk_start = max(current, datetime.strptime(date_from, "%Y-%m-%d"))
        chunk_end = min(next_month - timedelta(days=1), end)
        yield chunk_start.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")
        current = next_month


def sync_clinica_period(date_from, date_to):
    starts_at = f"{date_from}T00:00:00-03:00"
    ends_at = f"{date_to}T23:59:59-03:00"
    bookings = sync_clinica_list(
        f"/bookings?starts_at={starts_at}&ends_at={ends_at}&per_page=100",
        ["data", "bookings"],
        save_clinica_booking,
    )
    sales = sync_clinica_sales_period(date_from, date_to)
    sale_quote_warnings = []
    try:
        sale_quotes = sync_clinica_sale_quotes_period(date_from, date_to)
    except RuntimeError as exc:
        if "sales-quotes" not in str(exc) and "orçamentos" not in str(exc):
            raise
        sale_quotes = 0
        sale_quote_warnings.append(str(exc))
    bills = sync_clinica_list(
        f"/bills?starts_at={starts_at}&ends_at={ends_at}&per_page=100",
        ["data", "bills"],
        save_clinica_bill,
    )
    parcels = sync_clinica_list(
        f"/parcels?starts_at={starts_at}&ends_at={ends_at}&per_page=100",
        ["data", "parcels"],
        save_clinica_parcel,
    )
    return bookings, sales, sale_quotes, bills, parcels, sale_quote_warnings


def sync_clinica_experts(date_from=None, date_to=None, historical=False):
    started_at = int(time.time())
    with db() as conn:
        cur = conn.execute(
            "insert into clinica_sync_log (started_at, ok, message) values (?, 0, ?)",
            (started_at, "Sincronizacao Clínica Experts iniciada"),
        )
        log_id = cur.lastrowid
    try:
        if historical:
            date_from = date_from or config_value("CLINICA_HISTORY_START", "2020-01-01")
            date_to = date_to or datetime.now().strftime("%Y-%m-%d")
        elif not date_from or not date_to:
            date_from, date_to = default_period()

        patients = sync_clinica_list("/patients?per_page=100", ["data", "patients"], save_clinica_patient)
        try:
            procedures = sync_clinica_list("/procedures?per_page=100", ["data", "procedures"], save_clinica_procedure)
        except Exception:
            procedures = 0
        bookings = 0
        sales = 0
        sale_quotes = 0
        bills = 0
        parcels = 0
        warnings = []
        periods = list(month_ranges(date_from, date_to)) if historical else [(date_from, date_to)]
        for period_from, period_to in periods:
            period_bookings, period_sales, period_sale_quotes, period_bills, period_parcels, period_warnings = sync_clinica_period(period_from, period_to)
            bookings += period_bookings
            sales += period_sales
            sale_quotes += period_sale_quotes
            bills += period_bills
            parcels += period_parcels
            warnings.extend(period_warnings)
            if historical:
                time.sleep(0.25)

        scope = "historico" if historical else "periodo"
        message = (
            f"{patients} pacientes, {procedures} procedimentos, {bookings} agendamentos, "
            f"{sales} vendas, {sale_quotes} orçamentos, "
            f"{bills} contas e {parcels} parcelas sincronizados ({scope}: {date_from} a {date_to})"
        )
        if warnings:
            message += " Aviso: " + "; ".join(sorted(set(warnings)))
        with db() as conn:
            conn.execute(
                "update clinica_sync_log set finished_at = ?, ok = 1, message = ? where id = ?",
                (int(time.time()), message, log_id),
            )
        return {
            "ok": True,
            "patients": patients,
            "procedures": procedures,
            "bookings": bookings,
            "sales": sales,
            "sale_quotes": sale_quotes,
            "bills": bills,
            "parcels": parcels,
            "warnings": sorted(set(warnings)),
            "historical": historical,
            "date_from": date_from,
            "date_to": date_to,
        }
    except Exception as exc:
        with db() as conn:
            conn.execute(
                "update clinica_sync_log set finished_at = ?, ok = 0, message = ? where id = ?",
                (int(time.time()), str(exc), log_id),
            )
        raise


def clear_local_data(clear_oauth=False):
    tables = [
        "leads",
        "pipeline_statuses",
        "pipelines",
        "lead_status_events",
        "lead_interaction_events",
        "sync_log",
        "clinica_patients",
        "clinica_procedures",
        "clinica_bookings",
        "clinica_sales",
        "clinica_bills",
        "clinica_parcels",
        "clinica_sync_log",
    ]
    with db() as conn:
        for table in tables:
            conn.execute(f"delete from {table}")
        if clear_oauth:
            conn.execute("delete from oauth_tokens")


def clear_kommo_connection_state():
    tables = [
        "leads",
        "pipeline_statuses",
        "pipelines",
        "lead_status_events",
        "lead_interaction_events",
        "sync_log",
        "oauth_tokens",
    ]
    now = int(time.time())
    with db() as conn:
        for table in tables:
            conn.execute(f"delete from {table}")
        keys = list(KOMMO_CONFIG_KEYS) + [KOMMO_DISABLED_KEY]
        placeholders = ",".join("?" for _ in keys)
        conn.execute(f"delete from app_settings where key in ({placeholders})", keys)
        conn.execute(
            """
            insert or replace into app_settings (key, value, updated_at)
            values (?, ?, ?)
            """,
            (KOMMO_DISABLED_KEY, "1", now),
        )


def apply_kommo_reset_if_needed():
    with db() as conn:
        row = conn.execute(
            "select value from app_settings where key = ?",
            (KOMMO_RESET_MARKER_KEY,),
        ).fetchone()
    if row is not None and row["value"] == KOMMO_RESET_VERSION:
        return
    clear_kommo_connection_state()
    with db() as conn:
        conn.execute(
            """
            insert or replace into app_settings (key, value, updated_at)
            values (?, ?, ?)
            """,
            (KOMMO_RESET_MARKER_KEY, KOMMO_RESET_VERSION, int(time.time())),
        )


def sync_all(historical=True, reset_data=False, reset_oauth=False):
    if reset_data:
        clear_local_data(clear_oauth=reset_oauth)
    result = {"ok": True, "reset_data": reset_data, "kommo": None, "midas": None, "clinica_experts": None}
    if current_clinic_id() == "victor":
        try:
            result["midas"] = sync_midas()
        except Exception as exc:
            result["midas"] = {"ok": False, "error": str(exc)}
    else:
        try:
            result["kommo"] = sync_leads()
        except Exception as exc:
            result["kommo"] = {"ok": False, "error": str(exc)}
    try:
        result["clinica_experts"] = sync_clinica_experts(historical=historical)
    except Exception as exc:
        result["clinica_experts"] = {"ok": False, "error": str(exc)}
    result["ok"] = any(
        bool(result.get(key) and result[key].get("ok"))
        for key in ("kommo", "midas", "clinica_experts")
    )
    return result


def midas_api_base_url():
    configured_base = config_value("MIDAS_API_BASE_URL", "").strip().rstrip("/")
    if "leadconnectorhq.com" in configured_base:
        return configured_base
    return "https://services.leadconnectorhq.com"


def midas_request(path, params=None):
    token = config_value("MIDAS_API_TOKEN", "")
    if not configured(token):
        raise RuntimeError("MIDAS_API_TOKEN nao foi configurado.")
    query = urllib.parse.urlencode(params or {}, doseq=True)
    url = f"{midas_api_base_url()}{path}"
    if query:
        url = f"{url}?{query}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Version": "2021-07-28",
            "User-Agent": "VielleDashboard/1.0",
        },
        method="GET",
    )
    for attempt in range(1, 5):
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                content = res.read().decode("utf-8")
                return json.loads(content) if content else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code in (429, 502, 503, 504) and attempt < 4:
                time.sleep(5 * attempt)
                continue
            if exc.code == 403 and "cloudflare" in detail.lower():
                raise RuntimeError(
                    "A API Midas/LeadConnector bloqueou esta origem via Cloudflare. "
                    "Tente novamente no Railway ou libere a origem no provedor."
                ) from exc
            raise RuntimeError(f"Midas retornou {exc.code}: {detail}") from exc


def midas_location_id():
    location_id = config_value("MIDAS_LOCATION_ID", "").strip()
    if location_id:
        return location_id
    payload = midas_request("/locations/search")
    locations = extract_items(payload, ["locations", "data"])
    if len(locations) == 1:
        location_id = str(first_value(locations[0], ["id", "_id", "locationId"]) or "")
        if location_id:
            save_config_values({"MIDAS_LOCATION_ID": location_id})
            return location_id
    raise RuntimeError("MIDAS_LOCATION_ID nao foi configurado.")


def sync_midas_pipelines(location_id):
    payload = midas_request("/opportunities/pipelines", {"locationId": location_id})
    pipelines = extract_items(payload, ["pipelines", "data"])
    now = int(time.time())
    with db() as conn:
        for index, pipeline in enumerate(pipelines):
            source_pipeline_id = first_value(pipeline, ["id", "_id"])
            if not source_pipeline_id:
                continue
            pipeline_id = stable_numeric_id(source_pipeline_id, "midas-pipeline")
            conn.execute(
                """
                insert into pipelines (id, name, sort, is_main, raw_json, synced_at)
                values (?, ?, ?, ?, ?, ?)
                on conflict(id) do update set
                    name=excluded.name,
                    sort=excluded.sort,
                    is_main=excluded.is_main,
                    raw_json=excluded.raw_json,
                    synced_at=excluded.synced_at
                """,
                (
                    pipeline_id,
                    first_value(pipeline, ["name", "title"]) or f"Funil {source_pipeline_id}",
                    first_value(pipeline, ["sort", "position"]) or index,
                    1 if index == 0 else 0,
                    json.dumps(pipeline, ensure_ascii=False),
                    now,
                ),
            )
            stages = pipeline.get("stages") or pipeline.get("pipelineStages") or []
            for stage_index, stage in enumerate(stages):
                source_stage_id = first_value(stage, ["id", "_id"])
                if not source_stage_id:
                    continue
                stage_id = stable_numeric_id(source_stage_id, "midas-stage")
                conn.execute(
                    """
                    insert into pipeline_statuses
                    (id, pipeline_id, name, sort, color, type, raw_json, synced_at)
                    values (?, ?, ?, ?, ?, ?, ?, ?)
                    on conflict(id) do update set
                        pipeline_id=excluded.pipeline_id,
                        name=excluded.name,
                        sort=excluded.sort,
                        color=excluded.color,
                        type=excluded.type,
                        raw_json=excluded.raw_json,
                        synced_at=excluded.synced_at
                    """,
                    (
                        stage_id,
                        pipeline_id,
                        first_value(stage, ["name", "title"]) or f"Fase {source_stage_id}",
                        first_value(stage, ["sort", "position"]) or stage_index,
                        first_value(stage, ["color"]),
                        None,
                        json.dumps(stage, ensure_ascii=False),
                        now,
                    ),
                )
    return len(pipelines)


def midas_opportunity_to_lead(opportunity):
    source_id = first_value(opportunity, ["id", "_id"])
    pipeline_source_id = first_value(opportunity, ["pipelineId", "pipeline_id", "pipeline"])
    status_source_id = first_value(opportunity, ["pipelineStageId", "pipeline_stage_id", "stageId", "stage_id"])
    contact = opportunity.get("contact") if isinstance(opportunity.get("contact"), dict) else {}
    assigned_to = first_value(opportunity, ["assignedTo", "assigned_to", "userId", "ownerId"])
    created_at = parse_any_timestamp(first_value(opportunity, ["createdAt", "created_at", "dateAdded", "date_added"]))
    updated_at = parse_any_timestamp(first_value(opportunity, ["updatedAt", "updated_at", "lastStatusChangeAt"]))
    closed_at = parse_any_timestamp(first_value(opportunity, ["closedAt", "closed_at"]))
    value = first_value(opportunity, ["monetaryValue", "monetary_value", "value", "price"])
    raw = dict(opportunity)
    raw["custom_fields_values"] = []
    seller_name = first_value(opportunity, ["assignedToName", "assigned_to_name", "ownerName", "userName"])
    professional_name = first_value(opportunity, ["professional", "doctor", "provider"])
    if seller_name:
        raw["custom_fields_values"].append({"field_name": "vendedor", "values": [{"value": seller_name}]})
    if professional_name:
        raw["custom_fields_values"].append({"field_name": "profissional", "values": [{"value": professional_name}]})
    return {
        "id": stable_numeric_id(source_id, "midas-opportunity"),
        "name": first_value(opportunity, ["name", "title"]) or first_value(contact, ["name", "contactName"]) or f"Lead {source_id}",
        "price": value or 0,
        "status_id": stable_numeric_id(status_source_id, "midas-stage") if status_source_id else None,
        "pipeline_id": stable_numeric_id(pipeline_source_id, "midas-pipeline") if pipeline_source_id else None,
        "responsible_user_id": stable_numeric_id(assigned_to, "midas-user") if assigned_to else None,
        "created_at": created_at or updated_at or int(time.time()),
        "updated_at": updated_at or created_at or int(time.time()),
        "closed_at": closed_at,
        "raw_json": json.dumps(raw, ensure_ascii=False),
    }


def sync_midas_opportunities(location_id, history_start):
    page = 1
    total = 0
    synced_at = int(time.time())
    start_ts = parse_date(history_start) if history_start else None
    while page <= 100:
        payload = midas_request(
            "/opportunities/search",
            {
                "location_id": location_id,
                "limit": 100,
                "page": page,
            },
        )
        opportunities = extract_items(payload, ["opportunities", "data"])
        if not opportunities:
            break
        with db() as conn:
            for opportunity in opportunities:
                lead = midas_opportunity_to_lead(opportunity)
                if start_ts and lead["created_at"] < start_ts:
                    continue
                conn.execute(
                    """
                    insert into leads
                    (id, name, price, status_id, pipeline_id, responsible_user_id,
                     created_at, updated_at, closed_at, raw_json, synced_at)
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    on conflict(id) do update set
                        name=excluded.name,
                        price=excluded.price,
                        status_id=excluded.status_id,
                        pipeline_id=excluded.pipeline_id,
                        responsible_user_id=excluded.responsible_user_id,
                        created_at=excluded.created_at,
                        updated_at=excluded.updated_at,
                        closed_at=excluded.closed_at,
                        raw_json=excluded.raw_json,
                        synced_at=excluded.synced_at
                    """,
                    (
                        lead["id"],
                        lead["name"],
                        lead["price"],
                        lead["status_id"],
                        lead["pipeline_id"],
                        lead["responsible_user_id"],
                        lead["created_at"],
                        lead["updated_at"],
                        lead["closed_at"],
                        lead["raw_json"],
                        synced_at,
                    ),
                )
                total += 1
        meta = payload.get("meta") if isinstance(payload, dict) else {}
        if len(opportunities) < 100:
            break
        if isinstance(meta, dict) and meta.get("nextPageUrl") is None and page >= int(meta.get("totalPages") or page):
            break
        page += 1
    return total


def sync_midas():
    started_at = int(time.time())
    log_id = None
    with db() as conn:
        cur = conn.execute(
            "insert into sync_log (started_at, ok, message) values (?, 0, ?)",
            (started_at, "Sincronizacao Midas iniciada"),
        )
        log_id = cur.lastrowid
    try:
        location_id = midas_location_id()
        history_start = config_value("MIDAS_HISTORY_START", "2020-01-01")
        pipelines_total = sync_midas_pipelines(location_id)
        opportunities_total = sync_midas_opportunities(location_id, history_start)
        with db() as conn:
            conn.execute(
                "update sync_log set finished_at = ?, ok = 1, message = ? where id = ?",
                (
                    int(time.time()),
                    f"{opportunities_total} oportunidades, {pipelines_total} funis Midas sincronizados",
                    log_id,
                ),
            )
        return {
            "ok": True,
            "synced": opportunities_total,
            "pipelines": pipelines_total,
            "location_id": location_id,
        }
    except Exception as exc:
        with db() as conn:
            conn.execute(
                "update sync_log set finished_at = ?, ok = 0, message = ? where id = ?",
                (int(time.time()), str(exc), log_id),
            )
        raise


def parse_date(value, end_of_day=False):
    if not value:
        return None
    try:
        dt = datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("Data invalida. Use AAAA-MM-DD.") from exc
    if end_of_day:
        dt = dt + timedelta(days=1) - timedelta(seconds=1)
    return int(dt.timestamp())


def default_period():
    today = datetime.now()
    start = today - timedelta(days=29)
    return start.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")


def fill_daily_series(rows, date_from, date_to):
    totals = {row["day"]: row["total"] for row in rows}
    start = datetime.strptime(date_from, "%Y-%m-%d")
    end = datetime.strptime(date_to, "%Y-%m-%d")
    days = []
    current = start
    while current <= end:
        key = current.strftime("%Y-%m-%d")
        days.append({"day": key, "total": totals.get(key, 0)})
        current += timedelta(days=1)
    return days


def save_status_event(conn, event, synced_at):
    before = (event.get("value_before") or [{}])[0].get("lead_status", {})
    after = (event.get("value_after") or [{}])[0].get("lead_status", {})
    conn.execute(
        """
        insert into lead_status_events
        (id, lead_id, created_at, before_status_id, before_pipeline_id,
         after_status_id, after_pipeline_id, raw_json, synced_at)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(id) do update set
            lead_id=excluded.lead_id,
            created_at=excluded.created_at,
            before_status_id=excluded.before_status_id,
            before_pipeline_id=excluded.before_pipeline_id,
            after_status_id=excluded.after_status_id,
            after_pipeline_id=excluded.after_pipeline_id,
            raw_json=excluded.raw_json,
            synced_at=excluded.synced_at
        """,
        (
            event.get("id"),
            event.get("entity_id"),
            event.get("created_at"),
            before.get("id"),
            before.get("pipeline_id"),
            after.get("id"),
            after.get("pipeline_id"),
            json.dumps(event, ensure_ascii=False),
            synced_at,
        ),
    )


def save_interaction_event(conn, event, synced_at):
    if event.get("entity_type") != "lead" or not event.get("entity_id"):
        return
    conn.execute(
        """
        insert into lead_interaction_events
        (id, lead_id, type, created_at, raw_json, synced_at)
        values (?, ?, ?, ?, ?, ?)
        on conflict(id) do update set
            lead_id=excluded.lead_id,
            type=excluded.type,
            created_at=excluded.created_at,
            raw_json=excluded.raw_json,
            synced_at=excluded.synced_at
        """,
        (
            event.get("id"),
            event.get("entity_id"),
            event.get("type") or "unknown",
            event.get("created_at"),
            json.dumps(event, ensure_ascii=False),
            synced_at,
        ),
    )


def sync_lead_interaction_events(access, start_ts=None, end_ts=None, max_pages=20):
    page = 1
    total = 0
    synced_at = int(time.time())
    while page <= max_pages:
        query = urllib.parse.urlencode(
            {
                "limit": 250,
                "page": page,
                "filter[entity]": "lead",
            }
        )
        result = kommo_request(
            "GET",
            f"/api/v4/events?{query}",
            token=access["access_token"],
            domain=access["account_domain"],
        )
        events = result.get("_embedded", {}).get("events", [])
        if not events:
            break
        with db() as conn:
            for event in events:
                created_at = event.get("created_at") or 0
                if end_ts and created_at > end_ts:
                    continue
                if start_ts and created_at < start_ts:
                    continue
                save_interaction_event(conn, event, synced_at)
                total += 1
        oldest = min((event.get("created_at") or 0 for event in events), default=0)
        if start_ts and oldest < start_ts:
            break
        if len(events) < 250:
            break
        page += 1
    return total


def sync_status_events(access, start_ts=None, end_ts=None, max_pages=20):
    page = 1
    total = 0
    synced_at = int(time.time())
    while page <= max_pages:
        query = urllib.parse.urlencode(
            {
                "limit": 250,
                "page": page,
                "filter[type][]": "lead_status_changed",
                "filter[entity]": "lead",
            }
        )
        result = kommo_request(
            "GET",
            f"/api/v4/events?{query}",
            token=access["access_token"],
            domain=access["account_domain"],
        )
        events = result.get("_embedded", {}).get("events", [])
        if not events:
            break
        with db() as conn:
            for event in events:
                created_at = event.get("created_at") or 0
                if end_ts and created_at > end_ts:
                    continue
                if start_ts and created_at < start_ts:
                    continue
                save_status_event(conn, event, synced_at)
                total += 1
        oldest = min((event.get("created_at") or 0 for event in events), default=0)
        if start_ts and oldest < start_ts:
            break
        if len(events) < 250:
            break
        page += 1
    return total


def sync_leads():
    started_at = int(time.time())
    log_id = None
    with db() as conn:
        cur = conn.execute(
            "insert into sync_log (started_at, ok, message) values (?, 0, ?)",
            (started_at, "Sincronizacao iniciada"),
        )
        log_id = cur.lastrowid
    try:
        access = get_access_context()
        pipelines_total = sync_pipelines(access)
        page = 1
        total = 0
        while True:
            result = kommo_request(
                "GET",
                f"/api/v4/leads?limit=250&page={page}",
                token=access["access_token"],
                domain=access["account_domain"],
            )
            leads = result.get("_embedded", {}).get("leads", [])
            if not leads:
                break
            now = int(time.time())
            with db() as conn:
                for lead in leads:
                    conn.execute(
                        """
                        insert into leads
                        (id, name, price, status_id, pipeline_id, responsible_user_id,
                         created_at, updated_at, closed_at, raw_json, synced_at)
                        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        on conflict(id) do update set
                            name=excluded.name,
                            price=excluded.price,
                            status_id=excluded.status_id,
                            pipeline_id=excluded.pipeline_id,
                            responsible_user_id=excluded.responsible_user_id,
                            created_at=excluded.created_at,
                            updated_at=excluded.updated_at,
                            closed_at=excluded.closed_at,
                            raw_json=excluded.raw_json,
                            synced_at=excluded.synced_at
                        """,
                        (
                            lead.get("id"),
                            lead.get("name"),
                            lead.get("price") or 0,
                            lead.get("status_id"),
                            lead.get("pipeline_id"),
                            lead.get("responsible_user_id"),
                            lead.get("created_at"),
                            lead.get("updated_at"),
                            lead.get("closed_at"),
                            json.dumps(lead, ensure_ascii=False),
                            now,
                        ),
                    )
            total += len(leads)
            if len(leads) < 250:
                break
            page += 1
        events_start = int((datetime.now() - timedelta(days=180)).timestamp())
        events_total = sync_status_events(access, events_start, int(time.time()))
        interaction_events_total = sync_lead_interaction_events(access, events_start, int(time.time()))
        with db() as conn:
            conn.execute(
                "update sync_log set finished_at = ?, ok = 1, message = ? where id = ?",
                (
                    int(time.time()),
                    f"{total} leads, {pipelines_total} funis, {events_total} mudanças de fase e {interaction_events_total} interações sincronizadas",
                    log_id,
                ),
            )
        return {
            "ok": True,
            "synced": total,
            "pipelines": pipelines_total,
            "status_events": events_total,
            "interaction_events": interaction_events_total,
        }
    except Exception as exc:
        with db() as conn:
            conn.execute(
                "update sync_log set finished_at = ?, ok = 0, message = ? where id = ?",
                (int(time.time()), str(exc), log_id),
            )
        raise


def build_filters(pipeline_ids, start_ts, end_ts, prefix="leads", date_column="created_at", extra_clauses=None, extra_params=None):
    clauses = []
    params = []
    if pipeline_ids:
        placeholders = ",".join("?" for _ in pipeline_ids)
        clauses.append(f"{prefix}.pipeline_id in ({placeholders})")
        params.extend(pipeline_ids)
    if start_ts:
        clauses.append(f"{prefix}.{date_column} >= ?")
        params.append(start_ts)
    if end_ts:
        clauses.append(f"{prefix}.{date_column} <= ?")
        params.append(end_ts)
    if extra_clauses:
        clauses.extend(extra_clauses)
        params.extend(extra_params or [])
    return ("where " + " and ".join(clauses)) if clauses else "", params


def report_data(pipeline_ids=None, date_from=None, date_to=None, doctor=None, seller=None):
    pipeline_ids = pipeline_ids or []
    clinic_id = current_clinic_id()
    pipeline_doctor_map = clinic_pipeline_doctor_map()
    if not date_from or not date_to:
        default_from, default_to = default_period()
        date_from = date_from or default_from
        date_to = date_to or default_to
    start_ts = parse_date(date_from)
    end_ts = parse_date(date_to, end_of_day=True)
    selected_pipeline_filter = ""
    selected_pipeline_params = []
    with db() as conn:
        doctor_professionals = clinic_doctor_professionals(conn)
        forced_clinica_professional_uuids = forced_professional_uuids(conn)
        sellers = kommo_sellers_from_data(conn)
        selected_doctor = doctor if doctor in doctor_professionals else ""
        selected_seller = seller if seller in sellers else ""
        if pipeline_doctor_map:
            considered_pipeline_names = [
                name for name, mapped_doctor in pipeline_doctor_map.items()
                if not selected_doctor or mapped_doctor == selected_doctor
            ]
            if considered_pipeline_names:
                considered_pipeline_rows = conn.execute(
                    f"""
                    select id, name
                    from pipelines
                    where name in ({",".join("?" for _ in considered_pipeline_names)})
                    order by coalesce(sort, 999999), name
                    """,
                    considered_pipeline_names,
                ).fetchall()
            else:
                considered_pipeline_rows = []
        else:
            considered_pipeline_rows = conn.execute(
                """
                select id, name
                from pipelines
                order by coalesce(sort, 999999), name
                """
            ).fetchall()
    considered_pipeline_ids = [row["id"] for row in considered_pipeline_rows]
    effective_pipeline_ids = [pid for pid in pipeline_ids if pid in considered_pipeline_ids] if pipeline_ids else considered_pipeline_ids
    effective_pipeline_doctors = sorted({
        pipeline_doctor_map.get(row["name"])
        for row in considered_pipeline_rows
        if row["id"] in effective_pipeline_ids and pipeline_doctor_map.get(row["name"])
    })
    if selected_doctor:
        effective_professional_uuids = [doctor_professionals[selected_doctor]]
    elif effective_pipeline_doctors:
        effective_professional_uuids = [
            doctor_professionals[doctor_name]
            for doctor_name in effective_pipeline_doctors
            if doctor_name in doctor_professionals
        ]
    else:
        effective_professional_uuids = []
    if forced_clinica_professional_uuids:
        if effective_professional_uuids:
            effective_professional_uuids = [
                uuid for uuid in effective_professional_uuids
                if uuid in forced_clinica_professional_uuids
            ]
        else:
            effective_professional_uuids = forced_clinica_professional_uuids
    selected_kommo_aliases = clinic_kommo_doctor_aliases(selected_doctor) if selected_doctor else []
    if selected_doctor and clinic_has_kommo_doctor_aliases() and not selected_kommo_aliases:
        lead_doctor_clause, lead_doctor_params = "0 = 1", []
    else:
        lead_doctor_clause, lead_doctor_params = kommo_doctor_clause(selected_kommo_aliases)
    lead_extra_clauses = [lead_doctor_clause] if lead_doctor_clause else []
    lead_extra_params = list(lead_doctor_params)
    lead_seller_clause, lead_seller_params = kommo_seller_clause(selected_seller)
    if lead_seller_clause:
        lead_extra_clauses.append(lead_seller_clause)
        lead_extra_params.extend(lead_seller_params)
    pipeline_filter, params = build_filters(
        effective_pipeline_ids,
        start_ts,
        end_ts,
        extra_clauses=lead_extra_clauses,
        extra_params=lead_extra_params,
    )
    all_status_filter, all_status_params = build_filters(
        effective_pipeline_ids,
        None,
        None,
        extra_clauses=lead_extra_clauses,
        extra_params=lead_extra_params,
    )
    update_filter, update_params = build_filters(
        effective_pipeline_ids,
        start_ts,
        end_ts,
        date_column="updated_at",
        extra_clauses=lead_extra_clauses,
        extra_params=lead_extra_params,
    )
    join_extra_clause = "".join(f" and {clause}" for clause in lead_extra_clauses)
    if effective_pipeline_ids:
        placeholders = ",".join("?" for _ in effective_pipeline_ids)
        selected_pipeline_filter = f"where pipelines.id in ({placeholders})"
        selected_pipeline_params = list(effective_pipeline_ids)
    with db() as conn:
        totals = conn.execute(
            f"""
            select
              count(*) as total_leads,
              count(distinct pipeline_id) as total_pipelines,
              count(distinct status_id) as total_statuses,
              max(synced_at) as last_synced_at
            from leads
            {pipeline_filter}
            """,
            params,
        ).fetchone()
        pipelines = conn.execute(
            f"""
            select
              pipelines.id,
              pipelines.name,
              coalesce(pipelines.sort, 999999) as sort,
              count(leads.id) as total
            from pipelines
            left join leads
              on leads.pipeline_id = pipelines.id
             and leads.created_at >= ?
             and leads.created_at <= ?
             {join_extra_clause}
            {selected_pipeline_filter}
            group by pipelines.id
            order by sort, pipelines.name
            """,
            [start_ts, end_ts, *lead_extra_params, *selected_pipeline_params],
        ).fetchall()
        daily = conn.execute(
            f"""
            select date(leads.created_at, 'unixepoch', 'localtime') as day, count(*) as total
            from leads
            {pipeline_filter}
            group by day
            order by day
            """,
            params,
        ).fetchall()
        daily_lead_details = conn.execute(
            f"""
            select
              date(leads.created_at, 'unixepoch', 'localtime') as day,
              coalesce(pipelines.name, '') as pipeline_name,
              leads.raw_json
            from leads
            left join pipelines on pipelines.id = leads.pipeline_id
            {pipeline_filter}
            order by day
            """,
            params,
        ).fetchall()
        by_status = conn.execute(
            f"""
            select
              leads.status_id,
              leads.pipeline_id,
              coalesce(pipeline_statuses.name, 'Fase ' || leads.status_id) as status_name,
              coalesce(pipelines.name, 'Funil ' || leads.pipeline_id) as pipeline_name,
              coalesce(pipeline_statuses.sort, 999999) as sort,
              count(leads.id) as total
            from leads
            left join pipelines on pipelines.id = leads.pipeline_id
            left join pipeline_statuses
             on pipeline_statuses.id = leads.status_id
             and pipeline_statuses.pipeline_id = leads.pipeline_id
            {pipeline_filter}
            group by leads.pipeline_id, leads.status_id
            order by total desc, pipeline_name, sort, status_name
            """,
            params,
        ).fetchall()
        all_current_status = conn.execute(
            f"""
            select
              leads.status_id,
              leads.pipeline_id,
              coalesce(pipeline_statuses.name, 'Fase ' || leads.status_id) as status_name,
              coalesce(pipelines.name, 'Funil ' || leads.pipeline_id) as pipeline_name,
              coalesce(pipeline_statuses.sort, 999999) as sort,
              count(leads.id) as total
            from leads
            left join pipelines on pipelines.id = leads.pipeline_id
            left join pipeline_statuses
             on pipeline_statuses.id = leads.status_id
             and pipeline_statuses.pipeline_id = leads.pipeline_id
            {all_status_filter}
            group by leads.pipeline_id, leads.status_id
            order by total desc, pipeline_name, sort, status_name
            """,
            all_status_params,
        ).fetchall()
        by_pipeline = conn.execute(
            f"""
            select
              leads.pipeline_id,
              coalesce(pipelines.name, 'Funil ' || leads.pipeline_id) as pipeline_name,
              coalesce(pipelines.sort, 999999) as sort,
              count(leads.id) as total
            from leads
            left join pipelines on pipelines.id = leads.pipeline_id
            {pipeline_filter}
            group by leads.pipeline_id
            order by total desc, pipeline_name
            """,
            params,
        ).fetchall()
        interacted = conn.execute(
            f"""
            select count(*) as total
            from leads
            {update_filter}
            """,
            update_params,
        ).fetchone()
        interacted_by_pipeline = conn.execute(
            f"""
            select
              leads.pipeline_id,
              coalesce(pipelines.name, 'Funil ' || leads.pipeline_id) as pipeline_name,
              coalesce(pipelines.sort, 999999) as sort,
              count(leads.id) as total
            from leads
            left join pipelines on pipelines.id = leads.pipeline_id
            {update_filter}
            group by leads.pipeline_id
            order by total desc, pipeline_name
            """,
            update_params,
        ).fetchall()
        daily_interacted = conn.execute(
            f"""
            select date(leads.updated_at, 'unixepoch', 'localtime') as day, count(*) as total
            from leads
            {update_filter}
            group by day
            order by day
            """,
            update_params,
        ).fetchall()
        agendado_like = "%agend%"
        event_clauses = [
            "lead_status_events.created_at >= ?",
            "lead_status_events.created_at <= ?",
            "lower(pipeline_statuses.name) like ?",
        ]
        event_params = [start_ts, end_ts, agendado_like]
        if effective_pipeline_ids:
            placeholders = ",".join("?" for _ in effective_pipeline_ids)
            event_clauses.append(f"lead_status_events.after_pipeline_id in ({placeholders})")
            event_params.extend(effective_pipeline_ids)
        if lead_extra_clauses:
            event_clauses.extend(lead_extra_clauses)
            event_params.extend(lead_extra_params)
        agendado = conn.execute(
            f"""
            select count(distinct lead_status_events.lead_id) as total
            from lead_status_events
            join leads on leads.id = lead_status_events.lead_id
            join pipeline_statuses
              on pipeline_statuses.id = lead_status_events.after_status_id
             and pipeline_statuses.pipeline_id = lead_status_events.after_pipeline_id
            where {" and ".join(event_clauses)}
            """,
            event_params,
        ).fetchone()
        lead_sources = conn.execute(
            f"""
            select
              coalesce(pipelines.name, 'Funil ' || leads.pipeline_id) as name,
              count(leads.id) as total
            from leads
            left join pipelines on pipelines.id = leads.pipeline_id
            {pipeline_filter}
            group by leads.pipeline_id
            order by total desc, name
            limit 8
            """,
            params,
        ).fetchall()
        booking_scope = "substr(starts_at, 1, 10) >= ? and substr(starts_at, 1, 10) <= ?"
        booking_params = [date_from, date_to]
        sales_scope = "substr(sale_date, 1, 10) >= ? and substr(sale_date, 1, 10) <= ?"
        sales_params = [date_from, date_to]
        if effective_professional_uuids:
            professional_placeholders = ",".join("?" for _ in effective_professional_uuids)
            booking_scope += f" and professional_uuid in ({professional_placeholders})"
            booking_params.extend(effective_professional_uuids)
            sales_scope += f" and json_extract(raw_json, '$.seller.uuid') in ({professional_placeholders})"
            sales_params.extend(effective_professional_uuids)
        clinica_totals = conn.execute(
            f"""
            select
              (select count(distinct patient_uuid) from clinica_bookings where {booking_scope}) as patients,
              (select count(*) from clinica_bookings where {booking_scope}) as bookings,
              (select count(*) from clinica_sales where {sales_scope}) as sales,
              (select coalesce(sum(total), 0) from clinica_sales where {sales_scope}) as sales_total
            """,
            [*booking_params, *booking_params, *sales_params, *sales_params],
        ).fetchone()
        clinica_bookings_by_status = conn.execute(
            f"""
            select coalesce(status, 'sem status') as status, count(*) as total
            from clinica_bookings
            where {booking_scope}
            group by status
            order by total desc
            """,
            booking_params,
        ).fetchall()
        clinica_daily_bookings = conn.execute(
            f"""
            select substr(starts_at, 1, 10) as day, count(*) as total
            from clinica_bookings
            where {booking_scope}
            group by day
            order by day
            """,
            booking_params,
        ).fetchall()
        clinica_daily_bookings_by_doctor = conn.execute(
            f"""
            select
              substr(starts_at, 1, 10) as day,
              professional_uuid,
              count(*) as total
            from clinica_bookings
            where {booking_scope}
            group by day, professional_uuid
            order by day, total desc
            """,
            booking_params,
        ).fetchall()
        clinica_daily_booking_registry_rows = conn.execute(
            f"""
            select
              substr(starts_at, 1, 10) as day,
              professional_uuid,
              registry_user_name
            from clinica_bookings
            where {booking_scope}
            order by day
            """,
            booking_params,
        ).fetchall()
        bill_date_expr = "substr(coalesce(paid_at, due_date, emission_date), 1, 10)"
        bill_amount_expr = "coalesce(json_extract(raw_json, '$.final_amount') / 100.0, amount, 0)"
        bill_balance_expr = "coalesce(json_extract(raw_json, '$.balance') / 100.0, 0)"
        bill_settled_expr = (
            f"case when {bill_balance_expr} > 0 and {bill_balance_expr} < {bill_amount_expr} "
            f"then {bill_amount_expr} - {bill_balance_expr} "
            f"when {bill_balance_expr} <= 0 then {bill_amount_expr} else 0 end"
        )
        bill_open_expr = (
            f"case when {bill_balance_expr} > 0 then {bill_balance_expr} else 0 end"
        )
        income_type_filter = (
            "(lower(coalesce(type, '')) in ('venda', 'sale', 'receita') "
            "or lower(coalesce(category_name, '')) like '%receita%')"
        )
        expense_type_filter = (
            "lower(coalesce(type, '')) not in ('venda', 'sale', 'receita') "
            "and lower(coalesce(category_name, '')) not like '%receita%'"
        )
        parcel_date_expr = "substr(coalesce(paid_at, due_date), 1, 10)"
        parcel_amount_expr = "coalesce(json_extract(raw_json, '$.final_amount') / 100.0, amount, 0)"
        parcel_balance_expr = "coalesce(json_extract(raw_json, '$.balance') / 100.0, 0)"
        parcel_paid_filter = "lower(coalesce(status, '')) in ('paid', 'received', 'settled', 'done')"
        parcel_settled_expr = (
            f"case when {parcel_paid_filter} then {parcel_amount_expr} "
            f"when {parcel_balance_expr} > 0 and {parcel_balance_expr} < {parcel_amount_expr} "
            f"then {parcel_amount_expr} - {parcel_balance_expr} "
            f"when {parcel_balance_expr} <= 0 then {parcel_amount_expr} else 0 end"
        )
        parcel_open_expr = (
            f"case when {parcel_paid_filter} then 0 "
            f"when {parcel_balance_expr} > 0 then {parcel_balance_expr} else 0 end"
        )
        manual_income_parcel_filter = (
            f"{expense_type_filter} and lower(coalesce(status, '')) = 'received'"
        )
        parcel_expense_filter = (
            f"{expense_type_filter} and lower(coalesce(status, '')) != 'received'"
        )
        financial_income_extra_clause = ""
        financial_income_extra_params = []
        financial_parcel_extra_clause = ""
        financial_parcel_extra_params = []
        if current_clinic_id() == "victor" and effective_professional_uuids:
            professional_placeholders = ",".join("?" for _ in effective_professional_uuids)
            financial_income_extra_clause = f"""
              and exists (
                select 1
                from clinica_sales financial_sale
                where json_extract(financial_sale.raw_json, '$.seller.uuid') in ({professional_placeholders})
                  and json_extract(financial_sale.raw_json, '$.buyer.uuid') = json_extract(clinica_bills.raw_json, '$.person.uuid')
                  and (
                    abs(coalesce(json_extract(financial_sale.raw_json, '$.final_amount') / 100.0, financial_sale.total, 0) - {bill_amount_expr}) < 0.01
                    or substr(coalesce(financial_sale.sale_date, json_extract(financial_sale.raw_json, '$.created_at')), 1, 10)
                       = substr(coalesce(clinica_bills.emission_date, json_extract(clinica_bills.raw_json, '$.created_at')), 1, 10)
                  )
              )
            """
            financial_income_extra_params = list(effective_professional_uuids)
            parcel_owner_text_expr = """
                lower(
                  coalesce(json_extract(clinica_parcels.raw_json, '$.person.name'), '') || ' ' ||
                  coalesce(json_extract(clinica_parcels.raw_json, '$.person.full_name'), '') || ' ' ||
                  coalesce(json_extract(clinica_parcels.raw_json, '$.raw_bill.person.name'), '') || ' ' ||
                  coalesce(json_extract(clinica_parcels.raw_json, '$.raw_bill.person.full_name'), '') || ' ' ||
                  coalesce(clinica_parcels.raw_json, '')
                )
            """
            owner_bill_text_expr = """
                lower(
                  coalesce(json_extract(owner_bill.raw_json, '$.person.name'), '') || ' ' ||
                  coalesce(json_extract(owner_bill.raw_json, '$.person.full_name'), '') || ' ' ||
                  coalesce(owner_bill.description, '') || ' ' ||
                  coalesce(owner_bill.raw_json, '')
                )
            """
            financial_parcel_extra_clause = f"""
              and (
                json_extract(clinica_parcels.raw_json, '$.person.uuid') in ({professional_placeholders})
                or json_extract(clinica_parcels.raw_json, '$.raw_bill.person.uuid') in ({professional_placeholders})
                or ({parcel_owner_text_expr} like '%victor%' and {parcel_owner_text_expr} like '%paranhos%')
                or exists (
                  select 1
                  from clinica_bills owner_bill
                  where owner_bill.uuid = clinica_parcels.bill_uuid
                    and (
                      json_extract(owner_bill.raw_json, '$.person.uuid') in ({professional_placeholders})
                      or ({owner_bill_text_expr} like '%victor%' and {owner_bill_text_expr} like '%paranhos%')
                    )
                )
                or (
                  lower(
                    coalesce(clinica_parcels.description, '') || ' ' ||
                    coalesce(clinica_parcels.category_name, '') || ' ' ||
                    coalesce(clinica_parcels.account_name, '') || ' ' ||
                    coalesce(clinica_parcels.raw_json, '')
                  ) like '%victor%'
                  and lower(
                    coalesce(clinica_parcels.description, '') || ' ' ||
                    coalesce(clinica_parcels.category_name, '') || ' ' ||
                    coalesce(clinica_parcels.account_name, '') || ' ' ||
                    coalesce(clinica_parcels.raw_json, '')
                  ) like '%paranhos%'
                )
              )
            """
            financial_parcel_extra_params = (
                list(effective_professional_uuids)
                + list(effective_professional_uuids)
                + list(effective_professional_uuids)
            )
        financial_income_row = conn.execute(
            f"""
            select
              count(*) as total,
              coalesce(sum({bill_amount_expr}), 0) as amount,
              coalesce(sum({bill_settled_expr}), 0) as settled,
              coalesce(sum({bill_open_expr}), 0) as open_amount
            from clinica_bills
            where {bill_date_expr} >= ?
              and {bill_date_expr} <= ?
              and {income_type_filter}
              {financial_income_extra_clause}
            """,
            [date_from, date_to, *financial_income_extra_params],
        ).fetchone()
        financial_income_by_day = conn.execute(
            f"""
            select {bill_date_expr} as day, coalesce(sum({bill_amount_expr}), 0) as total
            from clinica_bills
            where {bill_date_expr} >= ?
              and {bill_date_expr} <= ?
              and {income_type_filter}
              {financial_income_extra_clause}
            group by day
            order by day
            """,
            [date_from, date_to, *financial_income_extra_params],
        ).fetchall()
        financial_manual_income_row = conn.execute(
            f"""
            select
              count(*) as total,
              coalesce(sum({parcel_amount_expr}), 0) as amount,
              coalesce(sum({parcel_settled_expr}), 0) as settled,
              coalesce(sum({parcel_open_expr}), 0) as open_amount
            from clinica_parcels
            where {parcel_date_expr} >= ?
              and {parcel_date_expr} <= ?
              and amount is not null
              and {manual_income_parcel_filter}
              {financial_parcel_extra_clause}
            """,
            [date_from, date_to, *financial_parcel_extra_params],
        ).fetchone()
        financial_manual_income_by_day = conn.execute(
            f"""
            select {parcel_date_expr} as day, coalesce(sum({parcel_amount_expr}), 0) as total
            from clinica_parcels
            where {parcel_date_expr} >= ?
              and {parcel_date_expr} <= ?
              and amount is not null
              and {manual_income_parcel_filter}
              {financial_parcel_extra_clause}
            group by day
            order by day
            """,
            [date_from, date_to, *financial_parcel_extra_params],
        ).fetchall()
        financial_manual_income_by_type = conn.execute(
            f"""
            select coalesce(category_name, type, 'Receitas manuais') as type,
                   count(*) as total,
                   coalesce(sum({parcel_amount_expr}), 0) as amount
            from clinica_parcels
            where {parcel_date_expr} >= ?
              and {parcel_date_expr} <= ?
              and amount is not null
              and {manual_income_parcel_filter}
              {financial_parcel_extra_clause}
            group by type
            order by amount desc
            """,
            [date_from, date_to, *financial_parcel_extra_params],
        ).fetchall()
        financial_sales_by_type = conn.execute(
            f"""
            select coalesce(type, 'sem tipo') as type, count(*) as total, coalesce(sum({bill_amount_expr}), 0) as amount
            from clinica_bills
            where {bill_date_expr} >= ?
              and {bill_date_expr} <= ?
              and {income_type_filter}
              {financial_income_extra_clause}
            group by type
            order by amount desc
            """,
            [date_from, date_to, *financial_income_extra_params],
        ).fetchall()
        financial_expense_row = conn.execute(
            f"""
            select
              count(*) as total,
              coalesce(sum({parcel_amount_expr}), 0) as amount,
              coalesce(sum({parcel_settled_expr}), 0) as settled,
              coalesce(sum({parcel_open_expr}), 0) as open_amount
            from clinica_parcels
            where {parcel_date_expr} >= ?
              and {parcel_date_expr} <= ?
              and amount is not null
              and {parcel_expense_filter}
              {financial_parcel_extra_clause}
            """,
            [date_from, date_to, *financial_parcel_extra_params],
        ).fetchone()
        financial_expense_by_day = conn.execute(
            f"""
            select {parcel_date_expr} as day, coalesce(sum({parcel_amount_expr}), 0) as total
            from clinica_parcels
            where {parcel_date_expr} >= ?
              and {parcel_date_expr} <= ?
              and amount is not null
              and {parcel_expense_filter}
              {financial_parcel_extra_clause}
            group by day
            order by day
            """,
            [date_from, date_to, *financial_parcel_extra_params],
        ).fetchall()
        financial_expense_by_category = conn.execute(
            f"""
            select
              coalesce(category_name, 'Sem categoria') as category,
              count(*) as total,
              coalesce(sum({parcel_amount_expr}), 0) as amount,
              coalesce(sum({parcel_settled_expr}), 0) as settled,
              coalesce(sum({parcel_open_expr}), 0) as open_amount
            from clinica_parcels
            where {parcel_date_expr} >= ?
              and {parcel_date_expr} <= ?
              and amount is not null
              and {parcel_expense_filter}
              {financial_parcel_extra_clause}
            group by category_name
            order by amount desc
            """,
            [date_from, date_to, *financial_parcel_extra_params],
        ).fetchall()
        financial_recent_sales = conn.execute(
            f"""
            select
              'entrada' as direction,
              {bill_date_expr} as date,
              coalesce(description, category_name, type, 'Venda') as description,
              coalesce(category_name, type, 'Clínica Experts') as detail,
              {bill_amount_expr} as amount,
              {bill_settled_expr} as settled,
              {bill_open_expr} as open_amount
            from clinica_bills
            where {bill_date_expr} >= ?
              and {bill_date_expr} <= ?
              and {income_type_filter}
              {financial_income_extra_clause}
            order by {bill_date_expr} desc
            limit 6
            """,
            [date_from, date_to, *financial_income_extra_params],
        ).fetchall()
        financial_recent_manual_income = conn.execute(
            f"""
            select
              'entrada' as direction,
              {parcel_date_expr} as date,
              coalesce(description, category_name, type, 'Receita') as description,
              coalesce(category_name, account_name, status, 'Clínica Experts') as detail,
              {parcel_amount_expr} as amount,
              {parcel_settled_expr} as settled,
              {parcel_open_expr} as open_amount
            from clinica_parcels
            where {parcel_date_expr} >= ?
              and {parcel_date_expr} <= ?
              and amount is not null
              and {manual_income_parcel_filter}
              {financial_parcel_extra_clause}
            order by {parcel_date_expr} desc
            limit 6
            """,
            [date_from, date_to, *financial_parcel_extra_params],
        ).fetchall()
        financial_recent_expenses = conn.execute(
            f"""
            select
              'saida' as direction,
              {parcel_date_expr} as date,
              coalesce(description, category_name, type, 'Saída') as description,
              coalesce(account_name, category_name, status, 'Clínica Experts') as detail,
              {parcel_amount_expr} as amount,
              {parcel_settled_expr} as settled,
              {parcel_open_expr} as open_amount
            from clinica_parcels
            where {parcel_date_expr} >= ?
              and {parcel_date_expr} <= ?
              and amount is not null
              and {parcel_expense_filter}
              {financial_parcel_extra_clause}
            order by {parcel_date_expr} desc
            limit 6
            """,
            [date_from, date_to, *financial_parcel_extra_params],
        ).fetchall()
        financial_detail_sales = conn.execute(
            f"""
            select
              'entrada' as direction,
              {bill_date_expr} as date,
              coalesce(description, category_name, type, 'Venda') as description,
              coalesce(category_name, type, 'Clínica Experts') as detail,
              {bill_amount_expr} as amount,
              {bill_settled_expr} as settled,
              {bill_open_expr} as open_amount
            from clinica_bills
            where {bill_date_expr} >= ?
              and {bill_date_expr} <= ?
              and {income_type_filter}
              {financial_income_extra_clause}
            order by {bill_date_expr} desc, amount desc
            """,
            [date_from, date_to, *financial_income_extra_params],
        ).fetchall()
        financial_detail_manual_income = conn.execute(
            f"""
            select
              'entrada' as direction,
              {parcel_date_expr} as date,
              coalesce(description, category_name, type, 'Receita') as description,
              coalesce(category_name, account_name, status, 'Clínica Experts') as detail,
              {parcel_amount_expr} as amount,
              {parcel_settled_expr} as settled,
              {parcel_open_expr} as open_amount
            from clinica_parcels
            where {parcel_date_expr} >= ?
              and {parcel_date_expr} <= ?
              and amount is not null
              and {manual_income_parcel_filter}
              {financial_parcel_extra_clause}
            order by {parcel_date_expr} desc, amount desc
            """,
            [date_from, date_to, *financial_parcel_extra_params],
        ).fetchall()
        financial_detail_expenses = conn.execute(
            f"""
            select
              'saida' as direction,
              {parcel_date_expr} as date,
              coalesce(description, category_name, type, 'Saída') as description,
              coalesce(category_name, account_name, status, 'Clínica Experts') as detail,
              {parcel_amount_expr} as amount,
              {parcel_settled_expr} as settled,
              {parcel_open_expr} as open_amount
            from clinica_parcels
            where {parcel_date_expr} >= ?
              and {parcel_date_expr} <= ?
              and amount is not null
              and {parcel_expense_filter}
              {financial_parcel_extra_clause}
            order by {parcel_date_expr} desc, amount desc
            """,
            [date_from, date_to, *financial_parcel_extra_params],
        ).fetchall()
        financial_income_total = (financial_income_row["amount"] or 0) + (financial_manual_income_row["amount"] or 0)
        financial_expense_total = financial_expense_row["amount"] or 0
        financial_income_settled = (financial_income_row["settled"] or 0) + (financial_manual_income_row["settled"] or 0)
        financial_income_open = (financial_income_row["open_amount"] or 0) + (financial_manual_income_row["open_amount"] or 0)
        financial_expense_settled = financial_expense_row["settled"] or 0
        financial_expense_open = financial_expense_row["open_amount"] or 0
        financial_daily_lookup = {}
        for row in fill_daily_series([dict(row) for row in financial_income_by_day], date_from, date_to):
            financial_daily_lookup[row["day"]] = {"day": row["day"], "income": row["total"], "expenses": 0}
        for row in fill_daily_series([dict(row) for row in financial_manual_income_by_day], date_from, date_to):
            current = financial_daily_lookup.setdefault(row["day"], {"day": row["day"], "income": 0, "expenses": 0})
            current["income"] += row["total"]
        for row in fill_daily_series([dict(row) for row in financial_expense_by_day], date_from, date_to):
            current = financial_daily_lookup.setdefault(row["day"], {"day": row["day"], "income": 0, "expenses": 0})
            current["expenses"] = row["total"]
        financial_daily = [
            {**item, "balance": item["income"] - item["expenses"]}
            for item in sorted(financial_daily_lookup.values(), key=lambda value: value["day"])
        ]
        financial_recent = sorted(
            [dict(row) for row in financial_recent_sales]
            + [dict(row) for row in financial_recent_manual_income]
            + [dict(row) for row in financial_recent_expenses],
            key=lambda row: row.get("date") or "",
            reverse=True,
        )[:10]
        financial_details_by_day = {}
        for row in (
            [dict(row) for row in financial_detail_sales]
            + [dict(row) for row in financial_detail_manual_income]
            + [dict(row) for row in financial_detail_expenses]
        ):
            financial_details_by_day.setdefault(row["date"], []).append(row)
        for rows in financial_details_by_day.values():
            rows.sort(key=lambda row: (row["direction"] != "entrada", -(row["amount"] or 0)))
        financial_income_count = (financial_income_row["total"] or 0) + (financial_manual_income_row["total"] or 0)
        financial_income_by_type_lookup = {}
        for row in [dict(row) for row in financial_sales_by_type] + [dict(row) for row in financial_manual_income_by_type]:
            item = financial_income_by_type_lookup.setdefault(row["type"], {"type": row["type"], "total": 0, "amount": 0})
            item["total"] += row["total"] or 0
            item["amount"] += row["amount"] or 0
        financial_income_by_type = sorted(
            financial_income_by_type_lookup.values(),
            key=lambda row: row["amount"],
            reverse=True,
        )

        patient_lookup = {
            row["uuid"]: row["name"] or "Paciente sem nome"
            for row in conn.execute("select uuid, name from clinica_patients").fetchall()
        }
        procedure_catalog = {
            (row["name"] or "").strip().lower(): row["category_name"]
            for row in conn.execute("select name, category_name from clinica_procedures").fetchall()
            if row["name"]
        }
        sales_rows = conn.execute(
            f"""
            select patient_uuid, sale_date, total, raw_json
            from clinica_sales
            where {sales_scope}
            """,
            sales_params,
        ).fetchall()
        top_patient_lookup = {}
        procedure_lookup = {}
        category_lookup = {}
        performance_lookup = {
            row["day"]: {"day": row["day"], "revenue": 0, "sales": 0, "quoted": 0, "quotes": 0}
            for row in fill_daily_series([], date_from, date_to)
        }
        for sale_row in sales_rows:
            try:
                sale = json.loads(sale_row["raw_json"])
            except json.JSONDecodeError:
                sale = {}
            day = (sale_row["sale_date"] or "")[:10]
            status_group = sale_status_group(first_value(sale, ["status"]), first_value(sale, ["type"]))
            final_amount = money_value(first_value(sale, ["final_amount", "total", "amount"])) or sale_row["total"] or 0
            nominal_amount = money_value(first_value(sale, ["nominal_amount", "budget_amount", "quoted_amount"])) or final_amount
            day_bucket = performance_lookup.setdefault(day, {"day": day, "revenue": 0, "sales": 0, "quoted": 0, "quotes": 0})
            if status_group == "venda":
                day_bucket["revenue"] += final_amount
                day_bucket["sales"] += 1
                buyer = first_value(sale, ["buyer", "patient"])
                patient_uuid = sale_row["patient_uuid"]
                patient_name = (
                    patient_lookup.get(patient_uuid)
                    or (first_value(buyer, ["name", "full_name"]) if isinstance(buyer, dict) else None)
                    or "Paciente sem nome"
                )
                patient_bucket = top_patient_lookup.setdefault(
                    patient_uuid or patient_name,
                    {"patient": patient_name, "sales": 0, "amount": 0},
                )
                patient_bucket["sales"] += 1
                patient_bucket["amount"] += final_amount
                for procedure in first_value(sale, ["procedures"]) or []:
                    if not isinstance(procedure, dict):
                        continue
                    procedure_name = first_value(procedure, ["name", "title", "description"]) or "Procedimento sem nome"
                    quantity = first_value(procedure, ["quantity"]) or 1
                    try:
                        quantity = float(quantity)
                    except (TypeError, ValueError):
                        quantity = 1
                    procedure_amount = money_value(first_value(procedure, ["final_amount", "nominal_amount", "amount"]))
                    if procedure_amount is None:
                        unit_amount = money_value(first_value(procedure, ["unit_amount", "unit_price", "price"])) or 0
                        procedure_amount = unit_amount * quantity
                    category = procedure_catalog.get(procedure_name.strip().lower()) or procedure_category_from_name(procedure_name)
                    procedure_bucket = procedure_lookup.setdefault(
                        procedure_name,
                        {"procedure": procedure_name, "category": category, "quantity": 0, "sales": 0, "amount": 0},
                    )
                    procedure_bucket["quantity"] += quantity
                    procedure_bucket["sales"] += 1
                    procedure_bucket["amount"] += procedure_amount
                    category_bucket = category_lookup.setdefault(
                        category,
                        {"category": category, "quantity": 0, "procedures": 0, "amount": 0},
                    )
                    category_bucket["quantity"] += quantity
                    category_bucket["procedures"] += 1
                    category_bucket["amount"] += procedure_amount
            elif status_group == "orcamento":
                day_bucket["quoted"] += nominal_amount
                day_bucket["quotes"] += 1

        extra_quote_clauses = [
            """
            (
              lower(coalesce(json_extract(raw_json, '$.status'), '')) in
              ('inactive', 'budget', 'quote', 'proposal', 'quoted', 'open', 'opened', 'aberto')
              or lower(coalesce(type, json_extract(raw_json, '$.type'), '')) in
              ('sale_quote', 'quote', 'budget', 'proposal')
            )
            """,
            "substr(coalesce(json_extract(raw_json, '$.created_at'), json_extract(raw_json, '$.updated_at'), sale_date), 1, 10) >= ?",
            "substr(coalesce(json_extract(raw_json, '$.created_at'), json_extract(raw_json, '$.updated_at'), sale_date), 1, 10) <= ?",
            "not (substr(sale_date, 1, 10) >= ? and substr(sale_date, 1, 10) <= ?)",
        ]
        extra_quote_params = [date_from, date_to, date_from, date_to]
        if effective_professional_uuids:
            professional_placeholders = ",".join("?" for _ in effective_professional_uuids)
            extra_quote_clauses.append(
                f"""(
                json_extract(raw_json, '$.seller.uuid') in ({professional_placeholders})
                or json_extract(raw_json, '$.professional.uuid') in ({professional_placeholders})
                or json_extract(raw_json, '$.responsible.uuid') in ({professional_placeholders})
                or json_extract(raw_json, '$.seller_person_id') in ({professional_placeholders})
                or json_extract(raw_json, '$.professional_id') in ({professional_placeholders})
                )"""
            )
            extra_quote_params.extend(effective_professional_uuids * 5)
        extra_quote_rows = conn.execute(
            f"""
            select sale_date, total, raw_json
            from clinica_sales
            where {" and ".join(extra_quote_clauses)}
            """,
            extra_quote_params,
        ).fetchall()
        for sale_row in extra_quote_rows:
            try:
                sale = json.loads(sale_row["raw_json"])
            except json.JSONDecodeError:
                sale = {}
            quote_date = first_value(sale, ["quote_date", "created_at", "updated_at", "sale_date"]) or sale_row["sale_date"] or ""
            day = str(quote_date)[:10]
            if not (date_from <= day <= date_to):
                continue
            title = first_value(sale, ["title"])
            final_amount = money_value(first_value(sale, ["final_amount", "total", "amount"])) or sale_row["total"] or 0
            if not final_amount and isinstance(title, dict):
                final_amount = money_value(first_value(title, ["final_amount", "total", "amount", "nominal_amount"])) or 0
            nominal_amount = money_value(first_value(sale, ["nominal_amount", "budget_amount", "quoted_amount"])) or final_amount
            day_bucket = performance_lookup.setdefault(day, {"day": day, "revenue": 0, "sales": 0, "quoted": 0, "quotes": 0})
            day_bucket["quoted"] += nominal_amount
            day_bucket["quotes"] += 1

        top_patients = sorted(top_patient_lookup.values(), key=lambda row: row["amount"], reverse=True)[:10]
        top_procedures = sorted(procedure_lookup.values(), key=lambda row: row["amount"], reverse=True)[:10]
        procedure_categories = sorted(category_lookup.values(), key=lambda row: row["amount"], reverse=True)
        sales_performance = [
            {
                **item,
                "conversion_rate": (item["sales"] / (item["sales"] + item["quotes"])) if (item["sales"] + item["quotes"]) else None,
            }
            for item in sorted(performance_lookup.values(), key=lambda row: row["day"])
        ]
        kommo_alias_lookup = clinic_kommo_doctor_alias_lookup()
        daily_doctors = {}
        for row in daily_lead_details:
            day = row["day"]
            if not day:
                continue
            doctor_name = pipeline_doctor_map.get(row["pipeline_name"])
            if not doctor_name:
                field_value = lead_custom_field_value(row["raw_json"], "profissional")
                doctor_name = kommo_alias_lookup.get(field_value.lower(), field_value) if field_value else ""
            if not doctor_name:
                doctor_name = "Sem profissional"
            daily_doctors.setdefault(day, {})
            daily_doctors[day][doctor_name] = daily_doctors[day].get(doctor_name, 0) + 1
        daily_new_leads = fill_daily_series([dict(row) for row in daily], date_from, date_to)
        for row in daily_new_leads:
            breakdown = daily_doctors.get(row["day"], {})
            row["by_doctor"] = [
                {"doctor": doctor_name, "total": total}
                for doctor_name, total in sorted(breakdown.items(), key=lambda item: (-item[1], item[0]))
            ]
        professional_doctor_lookup = {uuid: name for name, uuid in doctor_professionals.items()}
        daily_booking_doctors = {}
        for row in clinica_daily_bookings_by_doctor:
            day = row["day"]
            if not day:
                continue
            doctor_name = professional_doctor_lookup.get(row["professional_uuid"], "Sem profissional")
            daily_booking_doctors.setdefault(day, {})
            daily_booking_doctors[day][doctor_name] = daily_booking_doctors[day].get(doctor_name, 0) + row["total"]
        booking_registry_users = set()
        daily_booking_registry_users = {}
        for row in clinica_daily_booking_registry_rows:
            day = row["day"]
            if not day:
                continue
            user_name = row["registry_user_name"]
            if not user_name:
                continue
            doctor_name = professional_doctor_lookup.get(row["professional_uuid"], "Sem profissional")
            booking_registry_users.add(user_name)
            daily_booking_registry_users.setdefault(day, {})
            daily_booking_registry_users[day].setdefault(user_name, {"total": 0, "by_doctor": {}})
            daily_booking_registry_users[day][user_name]["total"] += 1
            daily_booking_registry_users[day][user_name]["by_doctor"][doctor_name] = (
                daily_booking_registry_users[day][user_name]["by_doctor"].get(doctor_name, 0) + 1
            )
        daily_bookings = fill_daily_series([dict(row) for row in clinica_daily_bookings], date_from, date_to)
        for row in daily_bookings:
            breakdown = daily_booking_doctors.get(row["day"], {})
            row["by_doctor"] = [
                {"doctor": doctor_name, "total": total}
                for doctor_name, total in sorted(breakdown.items(), key=lambda item: (-item[1], item[0]))
            ]
            creator_breakdown = daily_booking_registry_users.get(row["day"], {})
            row["by_registry_user"] = [
                {
                    "user": user_name,
                    "total": info["total"],
                    "by_doctor": [
                        {"doctor": doctor_name, "total": total}
                        for doctor_name, total in sorted(info["by_doctor"].items(), key=lambda item: (-item[1], item[0]))
                    ],
                }
                for user_name, info in sorted(creator_breakdown.items(), key=lambda item: (-item[1]["total"], item[0]))
            ]
        doctor_rows = []
        hidden_cross_doctors = CLINIC_DOCTOR_CROSS_HIDDEN.get(current_clinic_id(), set())
        pipeline_lookup = {row["id"]: row["name"] for row in considered_pipeline_rows}
        for doctor_name, professional_uuid in doctor_professionals.items():
            if doctor_name in hidden_cross_doctors:
                continue
            if selected_doctor and doctor_name != selected_doctor:
                continue
            doctor_pipeline_ids = [
                pipeline_id
                for pipeline_id, pipeline_name in pipeline_lookup.items()
                if pipeline_doctor_map.get(pipeline_name) == doctor_name and pipeline_id in effective_pipeline_ids
            ]
            if pipeline_doctor_map and not doctor_pipeline_ids:
                continue
            if doctor_pipeline_ids:
                placeholders = ",".join("?" for _ in doctor_pipeline_ids)
                lead_total = conn.execute(
                    f"""
                    select count(*) from leads
                    where pipeline_id in ({placeholders})
                      and created_at >= ?
                      and created_at <= ?
                    """,
                    [*doctor_pipeline_ids, start_ts, end_ts],
                ).fetchone()[0]
                interacted_total = conn.execute(
                    f"""
                    select count(*) from leads
                    where pipeline_id in ({placeholders})
                      and updated_at >= ?
                      and updated_at <= ?
                    """,
                    [*doctor_pipeline_ids, start_ts, end_ts],
                ).fetchone()[0]
                agendado_total = conn.execute(
                    f"""
                    select count(distinct lead_status_events.lead_id)
                    from lead_status_events
                    join pipeline_statuses
                      on pipeline_statuses.id = lead_status_events.after_status_id
                     and pipeline_statuses.pipeline_id = lead_status_events.after_pipeline_id
                    where lead_status_events.after_pipeline_id in ({placeholders})
                      and lead_status_events.created_at >= ?
                      and lead_status_events.created_at <= ?
                      and lower(pipeline_statuses.name) like '%agend%'
                    """,
                    [*doctor_pipeline_ids, start_ts, end_ts],
                ).fetchone()[0]
            else:
                doctor_aliases = clinic_kommo_doctor_aliases(doctor_name)
                doctor_clause, doctor_params = kommo_doctor_clause(doctor_aliases)
                if doctor_clause:
                    lead_total = conn.execute(
                        f"""
                        select count(*) from leads
                        where created_at >= ?
                          and created_at <= ?
                          and {doctor_clause}
                        """,
                        [start_ts, end_ts, *doctor_params],
                    ).fetchone()[0]
                    interacted_total = conn.execute(
                        f"""
                        select count(*) from leads
                        where updated_at >= ?
                          and updated_at <= ?
                          and {doctor_clause}
                        """,
                        [start_ts, end_ts, *doctor_params],
                    ).fetchone()[0]
                    agendado_total = conn.execute(
                        f"""
                        select count(distinct lead_status_events.lead_id)
                        from lead_status_events
                        join leads on leads.id = lead_status_events.lead_id
                        join pipeline_statuses
                          on pipeline_statuses.id = lead_status_events.after_status_id
                         and pipeline_statuses.pipeline_id = lead_status_events.after_pipeline_id
                        where lead_status_events.created_at >= ?
                          and lead_status_events.created_at <= ?
                          and lower(pipeline_statuses.name) like '%agend%'
                          and {doctor_clause}
                        """,
                        [start_ts, end_ts, *doctor_params],
                    ).fetchone()[0]
                else:
                    lead_total = 0
                    interacted_total = 0
                    agendado_total = 0
            bookings_total = conn.execute(
                """
                select count(*) from clinica_bookings
                where professional_uuid = ?
                  and substr(starts_at, 1, 10) >= ?
                  and substr(starts_at, 1, 10) <= ?
                """,
                (professional_uuid, date_from, date_to),
            ).fetchone()[0]
            bookings_done = conn.execute(
                """
                select count(*) from clinica_bookings
                where professional_uuid = ?
                  and status = 'done'
                  and substr(starts_at, 1, 10) >= ?
                  and substr(starts_at, 1, 10) <= ?
                """,
                (professional_uuid, date_from, date_to),
            ).fetchone()[0]
            sales_row = conn.execute(
                """
                select count(*) as total, coalesce(sum(total), 0) as amount
                from clinica_sales
                where json_extract(raw_json, '$.seller.uuid') = ?
                  and substr(sale_date, 1, 10) >= ?
                  and substr(sale_date, 1, 10) <= ?
                """,
                (professional_uuid, date_from, date_to),
            ).fetchone()
            doctor_rows.append(
                {
                    "doctor": doctor_name,
                    "professional_uuid": professional_uuid,
                    "pipelines": [pipeline_lookup[pipeline_id] for pipeline_id in doctor_pipeline_ids],
                    "new_leads": lead_total,
                    "interacted_leads": interacted_total,
                    "kommo_agendado_migrations": agendado_total,
                    "bookings": bookings_total,
                    "bookings_done": bookings_done,
                    "sales": sales_row["total"],
                    "sales_total": sales_row["amount"],
                    "lead_to_booking_rate": (bookings_total / lead_total) if lead_total else None,
                }
            )
        log = conn.execute("select * from sync_log order by id desc limit 1").fetchone()
        clinica_log = conn.execute("select * from clinica_sync_log order by id desc limit 1").fetchone()
        if current_clinic_id() == "victor":
            connected = (
                configured(config_value("MIDAS_API_BASE_URL", ""))
                and configured(config_value("MIDAS_API_TOKEN", ""))
                and configured(config_value("MIDAS_LOCATION_ID", ""))
            )
        else:
            connected = configured(config_value("KOMMO_LONG_LIVED_TOKEN", "")) or get_tokens() is not None
    return {
        "connected": connected,
        "filters": {
            "pipeline_ids": pipeline_ids,
            "doctor": selected_doctor,
            "seller": selected_seller,
            "date_from": date_from,
            "date_to": date_to,
            "doctors": list(doctor_professionals.keys()),
            "sellers": list(sellers.keys()),
        },
        "totals": dict(totals),
        "pipelines": [dict(row) for row in pipelines],
        "by_pipeline": [dict(row) for row in by_pipeline],
        "interacted_leads": {
            "total": interacted["total"] if interacted else 0,
            "by_pipeline": [dict(row) for row in interacted_by_pipeline],
            "daily": fill_daily_series([dict(row) for row in daily_interacted], date_from, date_to),
            "basis": "Última modificação / updated_at",
        },
        "by_status": [dict(row) for row in by_status],
        "all_current_status": [dict(row) for row in all_current_status],
        "daily_new_leads": daily_new_leads,
        "agendado_migrations": {
            "total": agendado["total"] if agendado else 0,
            "status_match": "fase contendo 'agend'",
        },
        "kommo_panel": {
            "active_conversations": interacted["total"] if interacted else 0,
            "unanswered_conversations": None,
            "response_time_minutes": None,
            "longest_wait_minutes": None,
            "lead_sources": [dict(row) for row in lead_sources],
            "note": "Conversas não respondidas e tempos dependem da leitura das mensagens/conversas.",
        },
        "clinica_experts": {
            "connected": configured(config_value("CLINICA_EXPERTS_TOKEN", "")),
            "totals": dict(clinica_totals),
            "bookings_by_status": [dict(row) for row in clinica_bookings_by_status],
            "daily_bookings": daily_bookings,
            "booking_registry_users": sorted(booking_registry_users),
            "doctor_cross": doctor_rows,
            "last_sync": dict(clinica_log) if clinica_log else None,
        },
        "financial": {
            "basis": "Clínica Experts: vendas e contas financeiras",
            "expense_source": "categorias",
            "totals": {
                "income": financial_income_total,
                "income_received": financial_income_settled,
                "income_pending": financial_income_open,
                "expenses": financial_expense_total,
                "expenses_paid": financial_expense_settled,
                "expenses_pending": financial_expense_open,
                "balance": financial_income_total - financial_expense_total,
                "cash_balance": financial_income_settled - financial_expense_settled,
                "sales_count": financial_income_count,
                "expenses_count": financial_expense_row["total"] or 0,
                "average_ticket": (financial_income_total / financial_income_count) if financial_income_count else 0,
            },
            "daily": financial_daily,
            "daily_details": financial_details_by_day,
            "income_by_type": financial_income_by_type,
            "expenses_by_category": [dict(row) for row in financial_expense_by_category],
            "recent": financial_recent,
            "sales_intelligence": {
                "top_patients": top_patients,
                "top_procedures": top_procedures,
                "procedure_categories": procedure_categories,
                "performance_daily": sales_performance,
                "basis": "Vendas ativas; orçado usa vendas inativas/orçamentos quando retornados pela API.",
            },
        },
        "last_sync": dict(log) if log else None,
    }


def sign_revoke_query(params):
    signature = params.get("signature", [""])[0]
    client_uuid = params.get("client_uuid", [""])[0]
    account_id = params.get("account_id", [""])[0]
    message = f"{client_uuid}:{account_id}".encode("utf-8")
    expected = hmac.new(config_value("APP_SECRET", "dev-secret-change-me").encode("utf-8"), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def brl(value):
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0
    text = f"R$ {number:,.0f}"
    return text.replace(",", "X").replace(".", ",").replace("X", ".")


def percent(value):
    if value is None:
        return "-"
    return f"{value * 100:.1f}%".replace(".", ",")


def sale_status_group(status, sale_type=None):
    type_text = (sale_type or "").lower()
    if type_text in ("sale_quote", "quote", "budget", "proposal"):
        return "orcamento"
    text = (status or "").lower()
    if text in ("active", "paid", "received", "done", "completed"):
        return "venda"
    if text in ("inactive", "budget", "quote", "proposal", "quoted", "open", "opened", "aberto"):
        return "orcamento"
    return text or "sem status"


def day_label(value):
    if not value:
        return "-"
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return str(value)


def query_report_args(params):
    pipeline_values = []
    for value in params.get("pipeline_ids", []):
        pipeline_values.extend(item for item in value.split(",") if item)
    if not pipeline_values:
        legacy_pipeline = params.get("pipeline_id", [""])[0]
        if legacy_pipeline:
            pipeline_values = [legacy_pipeline]
    pipeline_ids = [int(value) for value in pipeline_values]
    return {
        "pipeline_ids": pipeline_ids,
        "date_from": params.get("date_from", [""])[0],
        "date_to": params.get("date_to", [""])[0],
        "doctor": params.get("doctor", [""])[0],
        "seller": params.get("seller", [""])[0],
    }


def pdf_table(data, widths=None, header=True):
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    table = Table(data, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    style = [
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d9e6ee")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
    ]
    if header:
        style.extend([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#172338")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ])
    table.setStyle(TableStyle(style))
    return table


def metric_cards(items):
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    row = []
    for label, value in items:
        row.append(f"{label}\n{value}")
    table = Table([row], colWidths=[150] * len(row), hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#eef7fb")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbe4ef")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d9e6ee")),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#172338")),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("LEADING", (0, 0), (-1, -1), 15),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    return table


def generate_report_pdf(report, view="commercial"):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
        title=f"DASHBOARD ESTRATEGICO - {clinic_display_name()}",
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="DashTitle", fontName="Helvetica-Bold", fontSize=22, textColor=colors.HexColor("#172338"), leading=26))
    styles.add(ParagraphStyle(name="SectionTitle", fontName="Helvetica-Bold", fontSize=12, textColor=colors.HexColor("#087793"), spaceBefore=8, spaceAfter=8))
    styles.add(ParagraphStyle(name="SmallMuted", fontName="Helvetica", fontSize=8, textColor=colors.HexColor("#68788d"), leading=11))

    filters = report.get("filters", {})
    story = [
        Paragraph(clinic_display_name(), styles["SmallMuted"]),
        Paragraph("DASHBOARD ESTRATEGICO", styles["DashTitle"]),
        Paragraph(
            f"Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')} - "
            f"Periodo: {day_label(filters.get('date_from'))} a {day_label(filters.get('date_to'))} - "
            f"Doutor(a): {filters.get('doctor') or 'Todos'} - "
            f"Vendedor: {filters.get('seller') or 'Todos'}",
            styles["SmallMuted"],
        ),
        Spacer(1, 8),
    ]

    totals = report.get("totals", {})
    clinica = report.get("clinica_experts", {})
    clinica_totals = clinica.get("totals", {})
    financial = report.get("financial", {})
    financial_totals = financial.get("totals", {})

    if view == "financial":
        story.append(metric_cards([
            ("Faturamento", brl(financial_totals.get("income"))),
            ("Recebido", brl(financial_totals.get("income_received"))),
            ("A receber", brl(financial_totals.get("income_pending"))),
            ("Despesas", brl(financial_totals.get("expenses"))),
            ("Em aberto", brl(financial_totals.get("expenses_pending"))),
        ]))
        story.append(Spacer(1, 10))
        story.append(Paragraph("Entradas x saidas por dia", styles["SectionTitle"]))
        daily_rows = [["Dia", "Entradas", "Saidas", "Saldo"]]
        for item in financial.get("daily", []):
            if item.get("income") or item.get("expenses"):
                daily_rows.append([day_label(item.get("day")), brl(item.get("income")), brl(item.get("expenses")), brl(item.get("balance"))])
        story.append(pdf_table(daily_rows[:32], widths=[88, 95, 95, 95]))
        story.append(Spacer(1, 10))
        story.append(Paragraph("Saidas por categoria", styles["SectionTitle"]))
        expense_rows = [["Categoria", "Total", "Pago", "Em aberto", "Lancamentos"]]
        for item in financial.get("expenses_by_category", [])[:20]:
            expense_rows.append([
                item.get("category") or "Sem categoria",
                brl(item.get("amount")),
                brl(item.get("settled")),
                brl(item.get("open_amount")),
                item.get("total") or 0,
            ])
        story.append(pdf_table(expense_rows, widths=[250, 90, 90, 90, 70]))
        story.append(Spacer(1, 10))
        story.append(Paragraph("Ultimos lancamentos", styles["SectionTitle"]))
        recent_rows = [["Data", "Descricao", "Detalhe", "Valor"]]
        for item in financial.get("recent", [])[:25]:
            sign = "-" if item.get("direction") == "saida" else "+"
            recent_rows.append([day_label(item.get("date")), item.get("description") or "-", item.get("detail") or "-", f"{sign}{brl(item.get('amount'))}"])
        story.append(pdf_table(recent_rows, widths=[78, 250, 170, 90]))
    else:
        story.append(metric_cards([
            ("Novos leads", totals.get("total_leads") or 0),
            ("Leads com interacao", report.get("interacted_leads", {}).get("total") or 0),
            ("Agendamentos", clinica_totals.get("bookings") or 0),
            ("Vendas", clinica_totals.get("sales") or 0),
            ("Total vendido", brl(clinica_totals.get("sales_total"))),
        ]))
        story.append(Spacer(1, 10))
        story.append(Paragraph("Leads x agendamentos por doutor(a)", styles["SectionTitle"]))
        doctor_rows = [["Doutor(a)", "Leads", "Agendamentos", "Feitos", "Vendas", "Conversao", "Vendido"]]
        for item in clinica.get("doctor_cross", []):
            doctor_rows.append([
                item.get("doctor") or "-",
                item.get("new_leads") or 0,
                item.get("bookings") or 0,
                item.get("bookings_done") or 0,
                item.get("sales") or 0,
                percent(item.get("lead_to_booking_rate")),
                brl(item.get("sales_total")),
            ])
        story.append(pdf_table(doctor_rows, widths=[210, 60, 85, 60, 55, 70, 90]))
        story.append(Spacer(1, 10))
        story.append(Paragraph("Entrada de novos leads dia a dia", styles["SectionTitle"]))
        lead_rows = [["Dia", "Novos leads", "Agendamentos", "Ultima interacao"]]
        booking_lookup = {item.get("day"): item.get("total", 0) for item in clinica.get("daily_bookings", [])}
        interaction_lookup = {item.get("day"): item.get("total", 0) for item in report.get("interacted_leads", {}).get("daily", [])}
        for item in report.get("daily_new_leads", []):
            if item.get("total") or booking_lookup.get(item.get("day")) or interaction_lookup.get(item.get("day")):
                lead_rows.append([
                    day_label(item.get("day")),
                    item.get("total") or 0,
                    booking_lookup.get(item.get("day"), 0),
                    interaction_lookup.get(item.get("day"), 0),
                ])
        story.append(pdf_table(lead_rows[:32], widths=[100, 100, 100, 110]))
        story.append(Spacer(1, 10))
        story.append(Paragraph("Fases atuais de todos os leads", styles["SectionTitle"]))
        status_rows = [["Fase", "Funil", "Leads"]]
        for item in report.get("all_current_status", [])[:25]:
            status_rows.append([item.get("status_name") or "-", item.get("pipeline_name") or "-", item.get("total") or 0])
        story.append(pdf_table(status_rows, widths=[230, 230, 60]))

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#68788d"))
        canvas.drawRightString(282 * mm, 8 * mm, f"Pagina {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def kommo_widget_period(period):
    today = datetime.now()
    period = (period or "month").lower()
    if period in ("day", "today", "hoje"):
        start = today
    elif period in ("week", "semana"):
        start = today - timedelta(days=6)
    else:
        start = today.replace(day=1)
    return start.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")


def compact_number(value):
    value = float(value or 0)
    if value >= 1000000:
        return f"{value / 1000000:.1f}mi".replace(".", ",")
    if value >= 1000:
        return f"{value / 1000:.1f}k".replace(".", ",")
    return str(int(value))


def render_kommo_widget(report, clinic_id, period):
    clinic_name = clinic_display_name(clinic_id)
    financial = report.get("financial", {})
    financial_totals = financial.get("totals", {})
    clinica = report.get("clinica_experts", {})
    totals = report.get("totals", {})
    leads = totals.get("total_leads") or 0
    bookings = clinica.get("totals", {}).get("bookings") or 0
    sales = financial_totals.get("sales_count") or clinica.get("totals", {}).get("sales") or 0
    income = financial_totals.get("income_received") or financial_totals.get("income") or 0
    dashboard_url = f"/?clinic={clinic_id}"
    title = html.escape(f"{clinic_name} · {period}")
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    * {{ box-sizing: border-box; }}
    html, body {{
      margin: 0;
      padding: 0;
      font-family: 'PT Sans', 'Open Sans', Arial, sans-serif;
      color: #17233a;
      background: transparent;
    }}
    a {{ color: inherit; text-decoration: none; }}
    .widget {{
      min-height: 100vh;
      padding: 16px;
      border-radius: 14px;
      background:
        radial-gradient(circle at 92% 8%, rgba(113,103,232,.22), transparent 32%),
        linear-gradient(135deg, #f8fcff 0%, #eef9fc 54%, #ffffff 100%);
      border: 1px solid rgba(20, 174, 204, .18);
      overflow: hidden;
    }}
    .top {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 12px;
    }}
    .eyebrow {{
      font-size: 11px;
      font-weight: 800;
      color: #0d7891;
      letter-spacing: .08em;
      text-transform: uppercase;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .period {{
      padding: 3px 8px;
      border-radius: 999px;
      background: rgba(24,185,212,.12);
      color: #0d7891;
      font-size: 11px;
      font-weight: 800;
      text-transform: uppercase;
    }}
    .main {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 9px;
      margin-bottom: 12px;
    }}
    .metric {{
      padding: 11px 10px;
      border-radius: 12px;
      background: rgba(255,255,255,.82);
      border: 1px solid rgba(20,174,204,.12);
      box-shadow: 0 10px 28px rgba(23,35,56,.08);
    }}
    .label {{
      margin-bottom: 4px;
      color: #65748a;
      font-size: 11px;
      font-weight: 800;
      text-transform: uppercase;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .value {{
      color: #17233a;
      font-size: 28px;
      line-height: 1;
      font-weight: 900;
      font-family: 'Open Sans', Arial, sans-serif;
    }}
    .value.money {{
      color: #087f69;
      font-size: 24px;
    }}
    .footer {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding-top: 10px;
      border-top: 1px solid rgba(101,116,138,.16);
      color: #65748a;
      font-size: 12px;
      font-weight: 800;
    }}
    .open {{
      color: #0d7891;
      white-space: nowrap;
    }}
  </style>
</head>
<body>
  <a href="{dashboard_url}" target="_blank" rel="noopener">
    <section class="widget">
      <div class="top">
        <div class="eyebrow">{html.escape(clinic_name)}</div>
        <div class="period">{html.escape(period)}</div>
      </div>
      <div class="main">
        <div class="metric">
          <div class="label">Leads</div>
          <div class="value">{compact_number(leads)}</div>
        </div>
        <div class="metric">
          <div class="label">Agend.</div>
          <div class="value">{compact_number(bookings)}</div>
        </div>
        <div class="metric">
          <div class="label">Vendas</div>
          <div class="value">{compact_number(sales)}</div>
        </div>
        <div class="metric">
          <div class="label">Recebido</div>
          <div class="value money">R$ {compact_number(income)}</div>
        </div>
      </div>
      <div class="footer">
        <span>Dashboard estratégico</span>
        <span class="open">abrir</span>
      </div>
    </section>
  </a>
</body>
</html>"""


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def require_dashboard_auth(self):
        return True

    def request_clinic_id(self, parsed):
        params = urllib.parse.parse_qs(parsed.query)
        return normalize_clinic_id(params.get("clinic", ["vielle"])[0])

    def has_clinic_access(self, clinic_id):
        expected_code = clinic_access_code(clinic_id)
        if not expected_code:
            return False
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get(clinic_access_cookie_name(clinic_id))
        return bool(morsel) and hmac.compare_digest(morsel.value, clinic_access_signature(clinic_id))

    def require_clinic_access(self, parsed):
        clinic_id = self.request_clinic_id(parsed)
        if self.has_clinic_access(clinic_id):
            return True
        return json_response(
            self,
            {"ok": False, "error": "Código de acesso obrigatório para esta clínica."},
            HTTPStatus.UNAUTHORIZED,
        )

    def require_master_auth(self):
        expected_user = config_value("MASTER_USER", "master") or "master"
        expected_password = (
            config_value("MASTER_PASSWORD", "")
            or config_value("DASHBOARD_PASSWORD", "")
        )
        username = self.headers.get("X-Master-User", "")
        password = self.headers.get("X-Master-Password", "")
        if not expected_password:
            return hmac.compare_digest(username, expected_user)
        if hmac.compare_digest(username, expected_user) and hmac.compare_digest(password, expected_password):
            return True
        json_response(
            self,
            {"ok": False, "error": "Acesso master inválido."},
            HTTPStatus.UNAUTHORIZED,
        )
        return False

    def read_json_body(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if not length:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw else {}

    def do_GET(self):
        if not self.require_dashboard_auth():
            return
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/kommo-widget":
            params = urllib.parse.parse_qs(parsed.query)
            clinic_id = self.request_clinic_id(parsed)
            period = params.get("kommo_period", params.get("period", ["month"]))[0]
            date_from, date_to = kommo_widget_period(period)
            with clinic_context(clinic_id):
                try:
                    report = report_data(date_from=date_from, date_to=date_to)
                    return html_response(self, render_kommo_widget(report, clinic_id, period))
                except Exception as exc:
                    return html_response(
                        self,
                        f"<html><body style='font-family:Arial;padding:14px;color:#2E3640'>Widget indisponível: {html.escape(str(exc))}</body></html>",
                        HTTPStatus.BAD_REQUEST,
                    )
        if parsed.path == "/api/report":
            with clinic_context(self.request_clinic_id(parsed)):
                if not self.require_clinic_access(parsed):
                    return
                params = urllib.parse.parse_qs(parsed.query)
                try:
                    args = query_report_args(params)
                except ValueError:
                    return json_response(self, {"ok": False, "error": "pipeline_ids invalido"}, HTTPStatus.BAD_REQUEST)
                try:
                    return json_response(self, report_data(**args))
                except ValueError as exc:
                    return json_response(self, {"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        if parsed.path == "/api/export-pdf":
            with clinic_context(self.request_clinic_id(parsed)):
                if not self.require_clinic_access(parsed):
                    return
                params = urllib.parse.parse_qs(parsed.query)
                try:
                    args = query_report_args(params)
                    report = report_data(**args)
                    view = params.get("view", ["commercial"])[0]
                    pdf = generate_report_pdf(report, view="financial" if view == "financialView" else "commercial")
                    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
                    return binary_response(self, pdf, f"{current_clinic_id()}-dashboard-{stamp}.pdf", "application/pdf")
                except ValueError as exc:
                    return json_response(self, {"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
                except Exception as exc:
                    return json_response(self, {"ok": False, "error": f"Não foi possível gerar o PDF: {exc}"}, HTTPStatus.BAD_REQUEST)
        if parsed.path == "/api/sync":
            with clinic_context(self.request_clinic_id(parsed)):
                if not self.require_clinic_access(parsed):
                    return
                try:
                    if current_clinic_id() == "victor":
                        return json_response(self, sync_midas())
                    return json_response(self, sync_leads())
                except Exception as exc:
                    return json_response(self, {"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        if parsed.path == "/api/sync-clinica":
            with clinic_context(self.request_clinic_id(parsed)):
                if not self.require_clinic_access(parsed):
                    return
                params = urllib.parse.parse_qs(parsed.query)
                try:
                    return json_response(
                        self,
                        sync_clinica_experts(
                            date_from=params.get("date_from", [""])[0],
                            date_to=params.get("date_to", [""])[0],
                            historical=params.get("historical", [""])[0] in ("1", "true", "yes"),
                        ),
                    )
                except Exception as exc:
                    return json_response(self, {"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        if parsed.path == "/api/settings":
            if not self.require_master_auth():
                return
            with clinic_context(self.request_clinic_id(parsed)):
                return json_response(self, settings_payload())
        if parsed.path == "/auth/start":
            with clinic_context(self.request_clinic_id(parsed)):
                client_id = config_value("KOMMO_CLIENT_ID", "")
                if not client_id:
                    return json_response(self, {"ok": False, "error": "KOMMO_CLIENT_ID nao configurado."}, HTTPStatus.BAD_REQUEST)
                state = secrets.token_urlsafe(24)
                save_state(state)
                query = urllib.parse.urlencode(
                    {"client_id": client_id, "state": state, "mode": "popup"}
                )
                return redirect(self, f"https://www.kommo.com/oauth?{query}")
        if parsed.path == "/auth/callback":
            params = urllib.parse.parse_qs(parsed.query)
            if "error" in params:
                return redirect(self, f"/?error={urllib.parse.quote(params['error'][0])}")
            state = params.get("state", [""])[0]
            if not consume_state(state):
                return redirect(self, "/?error=estado_de_autorizacao_invalido")
            code = params.get("code", [""])[0]
            referrer = params.get("referer", params.get("referrer", [""]))[0]
            try:
                exchange_code(code, referrer)
                sync_leads()
                return redirect(self, "/?connected=1")
            except Exception as exc:
                return redirect(self, f"/?error={urllib.parse.quote(str(exc))}")
        if parsed.path == "/webhooks/revoked":
            params = urllib.parse.parse_qs(parsed.query)
            with db() as conn:
                conn.execute("delete from oauth_tokens where id = 1")
            ok = sign_revoke_query(params)
            return json_response(self, {"ok": ok, "message": "Integracao marcada como desconectada."})
        return super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/clinica-webhook":
            params = urllib.parse.parse_qs(parsed.query)
            with clinic_context(self.request_clinic_id(parsed)):
                expected_secret = config_value("CLINICA_WEBHOOK_SECRET", "") or config_value("APP_SECRET", "")
                received_secret = (
                    self.headers.get("X-Clinica-Webhook-Secret", "")
                    or params.get("secret", [""])[0]
                )
                if not expected_secret or not hmac.compare_digest(str(received_secret), str(expected_secret)):
                    return json_response(self, {"ok": False, "error": "Webhook não autorizado."}, HTTPStatus.UNAUTHORIZED)
                try:
                    payload = self.read_json_body()
                except Exception:
                    return json_response(self, {"ok": False, "error": "JSON inválido."}, HTTPStatus.BAD_REQUEST)
                event_name = str(payload.get("event") or payload.get("type") or payload.get("name") or "")
                if "sale_quote" in event_name:
                    result = save_clinica_sale_quote_webhook(payload)
                    return json_response(self, result, HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST)
                if event_name and not event_name.startswith("booking."):
                    return json_response(self, {"ok": True, "ignored": True, "event": event_name})
                result = save_clinica_booking_webhook(payload)
                return json_response(self, result, HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST)
        if not self.require_dashboard_auth():
            return
        if parsed.path == "/api/clinic-access":
            try:
                payload = self.read_json_body()
            except Exception:
                return json_response(self, {"ok": False, "error": "JSON inválido."}, HTTPStatus.BAD_REQUEST)
            clinic_id = normalize_clinic_id(payload.get("clinic_id", "vielle"))
            expected_code = clinic_access_code(clinic_id)
            received_code = str(payload.get("access_code", "")).strip()
            if not expected_code:
                return json_response(
                    self,
                    {"ok": False, "error": "Código desta clínica ainda não foi configurado no servidor."},
                    HTTPStatus.BAD_REQUEST,
                )
            if not hmac.compare_digest(received_code, expected_code):
                return json_response(self, {"ok": False, "error": "Código incorreto. Confira e tente novamente."}, HTTPStatus.UNAUTHORIZED)
            return json_response(
                self,
                {"ok": True, "clinic_id": clinic_id},
                headers={"Set-Cookie": clinic_access_cookie(clinic_id)},
            )
        if parsed.path == "/api/settings":
            if not self.require_master_auth():
                return
            try:
                payload = self.read_json_body()
            except Exception:
                return json_response(self, {"ok": False, "error": "JSON inválido."}, HTTPStatus.BAD_REQUEST)
            values = payload.get("values", {})
            if not isinstance(values, dict):
                return json_response(self, {"ok": False, "error": "Configurações inválidas."}, HTTPStatus.BAD_REQUEST)
            clean_values = {}
            for key, value in values.items():
                if key not in EDITABLE_CONFIG_KEYS:
                    continue
                text = str(value or "").strip()
                if key in SECRET_CONFIG_KEYS and not text:
                    continue
                clean_values[key] = text
            with clinic_context(self.request_clinic_id(parsed)):
                save_config_values(clean_values)
                if payload.get("reset_kommo_oauth"):
                    with db() as conn:
                        conn.execute("delete from oauth_tokens")
                return json_response(self, settings_payload())
        if parsed.path == "/api/sync-all":
            if not self.require_master_auth():
                return
            try:
                payload = self.read_json_body()
            except Exception:
                return json_response(self, {"ok": False, "error": "JSON inválido."}, HTTPStatus.BAD_REQUEST)
            with clinic_context(self.request_clinic_id(parsed)):
                return json_response(
                    self,
                    sync_all(
                        historical=payload.get("historical", True),
                        reset_data=payload.get("reset_data", False),
                        reset_oauth=payload.get("reset_oauth", False),
                    ),
                )
        if parsed.path == "/api/clear-data":
            if not self.require_master_auth():
                return
            try:
                payload = self.read_json_body()
            except Exception:
                return json_response(self, {"ok": False, "error": "JSON inválido."}, HTTPStatus.BAD_REQUEST)
            if payload.get("confirm") != "LIMPAR":
                return json_response(self, {"ok": False, "error": "Confirmação obrigatória."}, HTTPStatus.BAD_REQUEST)
            with clinic_context(self.request_clinic_id(parsed)):
                clear_local_data(clear_oauth=payload.get("clear_oauth", False))
                return json_response(self, {"ok": True, "clinic_id": current_clinic_id()})
        if parsed.path == "/api/reset-kommo":
            if not self.require_master_auth():
                return
            with clinic_context(self.request_clinic_id(parsed)):
                clear_kommo_connection_state()
                with db() as conn:
                    conn.execute(
                        """
                        insert or replace into app_settings (key, value, updated_at)
                        values (?, ?, ?)
                        """,
                        (KOMMO_RESET_MARKER_KEY, KOMMO_RESET_VERSION, int(time.time())),
                    )
                return json_response(self, {"ok": True, "clinic_id": current_clinic_id(), "kommo_disabled": True})
        return json_response(self, {"ok": False, "error": "Rota não encontrada."}, HTTPStatus.NOT_FOUND)


def background_sync():
    while True:
        time.sleep(max(1, config_int("SYNC_INTERVAL_MINUTES", 30)) * 60)
        for clinic_id in SUPPORTED_CLINICS:
            try:
                with clinic_context(clinic_id):
                    if current_clinic_id() == "victor":
                        continue
                    if configured(config_value("KOMMO_LONG_LIVED_TOKEN", "")) or get_tokens():
                        sync_leads()
            except Exception:
                pass


if __name__ == "__main__":
    for clinic_id in SUPPORTED_CLINICS:
        with clinic_context(clinic_id):
            apply_kommo_reset_if_needed()
    thread = threading.Thread(target=background_sync, daemon=True)
    thread.start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Relatorio Kommo rodando em http://localhost:{PORT}")
    server.serve_forever()
