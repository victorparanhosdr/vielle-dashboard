"""Institute data is independent of all clinic databases and permissions."""
import contextlib
import json
import os
import re
import sqlite3
from pathlib import Path

INSTITUTES = {"victor-paranhos": "Instituto Dr. Victor Paranhos"}
DEFAULT_SHEET = "1r5B3L4nbPkCasGYS-Y1LMvFfadOrDt-eAD1IIkUFCLA"
SECRET_KEYS = {"kiwify_client_secret", "meta_access_token"}
CONFIG_KEYS = SECRET_KEYS | {"kiwify_client_id", "kiwify_account_id", "meta_account_id", "meta_version"}


def key(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,69}", value):
        raise ValueError("Identificador inválido.")
    return value


class InstituteStore:
    def __init__(self, path):
        self.path = Path(path)

    @contextlib.contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS institutes(key TEXT PRIMARY KEY, name TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS institute_members(institute_key TEXT NOT NULL REFERENCES institutes(key),
                    user_id INTEGER NOT NULL, PRIMARY KEY(institute_key,user_id));
                CREATE TABLE IF NOT EXISTS institute_settings(institute_key TEXT NOT NULL REFERENCES institutes(key),
                    key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(institute_key,key));
                CREATE TABLE IF NOT EXISTS courses(institute_key TEXT NOT NULL REFERENCES institutes(key),
                    key TEXT NOT NULL, name TEXT NOT NULL, product_ids TEXT NOT NULL DEFAULT '[]',
                    sheet_id TEXT NOT NULL DEFAULT '', sheet_gid TEXT NOT NULL DEFAULT '0',
                    pipeline_name TEXT NOT NULL DEFAULT '', PRIMARY KEY(institute_key,key));
                CREATE TABLE IF NOT EXISTS course_records(institute_key TEXT NOT NULL, course_key TEXT NOT NULL,
                    source TEXT NOT NULL, record_id TEXT NOT NULL, data_json TEXT NOT NULL,
                    PRIMARY KEY(institute_key,course_key,source,record_id),
                    FOREIGN KEY(institute_key,course_key) REFERENCES courses(institute_key,key));
                CREATE TABLE IF NOT EXISTS course_campaigns(institute_key TEXT NOT NULL, course_key TEXT NOT NULL,
                    campaign_id TEXT NOT NULL, campaign_name TEXT NOT NULL, aliases_json TEXT NOT NULL,
                    PRIMARY KEY(institute_key,campaign_id),
                    FOREIGN KEY(institute_key,course_key) REFERENCES courses(institute_key,key));
                CREATE TABLE IF NOT EXISTS institute_sync(institute_key TEXT NOT NULL, course_key TEXT NOT NULL,
                    source TEXT NOT NULL, data_json TEXT NOT NULL, PRIMARY KEY(institute_key,course_key,source),
                    FOREIGN KEY(institute_key,course_key) REFERENCES courses(institute_key,key));
            """)
            for ident, name in INSTITUTES.items():
                conn.execute("INSERT OR IGNORE INTO institutes VALUES (?,?)", (ident, name))
            conn.execute("INSERT OR IGNORE INTO courses VALUES (?,?,?,?,?,?,?)", (
                "victor-paranhos", "regen-code", "REGEN.CODE · CO₂ Avançado", "[]", DEFAULT_SHEET, "0", "REGENCODE"))
            conn.execute("INSERT OR IGNORE INTO courses VALUES (?,?,?,?,?,?,?)", (
                "victor-paranhos", "regen-code-presencial", "REGEN.CODE · Módulo Presencial",
                json.dumps(["0bc9de00-4cd5-11f1-ab31-f9ae6e042afe", "09acee40-4cd1-11f1-8f1e-03c7d68b7a1a"]),
                "", "0", ""))
        os.chmod(self.path, 0o600)

    def allowed(self, user):
        if user["is_master"]:
            return [row["key"] for row in self.institutes()]
        with self.connection() as conn:
            return [r[0] for r in conn.execute("SELECT institute_key FROM institute_members WHERE user_id=?", (user["id"],))]

    def institutes(self):
        with self.connection() as conn:
            return [dict(row) for row in conn.execute("SELECT key,name FROM institutes ORDER BY name")]

    def courses(self, institute):
        with self.connection() as conn:
            return [{**dict(r), "product_ids": json.loads(r["product_ids"])} for r in conn.execute(
                "SELECT * FROM courses WHERE institute_key=? ORDER BY name", (institute,))]

    def course(self, institute, course):
        if course == "all":
            courses = self.courses(institute)
            return {"institute_key": institute, "key": "all", "name": "Todos os cursos", "is_all": True,
                    "product_ids": list(dict.fromkeys(p for c in courses for p in c["product_ids"])),
                    "sheet_id": "", "sheet_gid": "0", "pipeline_name": ""} if courses else None
        return next((r for r in self.courses(institute) if r["key"] == course), None)

    def courses_for(self, institute, course):
        if course == "all":
            return self.courses(institute)
        info = self.course(institute, course)
        return [info] if info else []

    def settings(self, institute):
        with self.connection() as conn:
            values = {r[0]: r[1] for r in conn.execute("SELECT key,value FROM institute_settings WHERE institute_key=?", (institute,))}
        for field in CONFIG_KEYS:
            env = os.getenv("INSTITUTE_" + institute.replace("-", "_").upper() + "_" + field.upper())
            if env:
                values[field] = env.strip()
        return values

    def save_settings(self, institute, values):
        if not isinstance(values, dict) or set(values) - CONFIG_KEYS:
            raise ValueError("Configuração inválida.")
        for field, value in values.items():
            if not isinstance(value, str) or len(value) > 8192 or any(ord(c) < 32 for c in value):
                raise ValueError("Configuração inválida.")
            if field == "meta_account_id" and value and not re.fullmatch(r"(?:act_)?[0-9]{3,30}", value):
                raise ValueError("Informe o ID numérico da conta de anúncios.")
            if field == "meta_version" and value and not re.fullmatch(r"v[0-9]{2}\.0", value):
                raise ValueError("Versão da API Meta inválida.")
        previous = self.settings(institute)
        changed = {field for field, value in values.items()
                   if field not in SECRET_KEYS and previous.get(field, "") != value.strip()}
        with self.connection() as conn:
            for field, value in values.items():
                if field in SECRET_KEYS and not value.strip():
                    continue
                conn.execute("INSERT INTO institute_settings VALUES (?,?,?) ON CONFLICT(institute_key,key) DO UPDATE SET value=excluded.value",
                             (institute, field, value.strip()))
            # Imported caches belong to an account, not just to its integration name.
            for source, identity in (("kiwify", {"kiwify_client_id", "kiwify_account_id"}),
                                     ("meta", {"meta_account_id"})):
                if not changed.intersection(identity):
                    continue
                conn.execute("DELETE FROM course_records WHERE institute_key=? AND source=?", (institute, source))
                conn.execute("DELETE FROM institute_sync WHERE institute_key=? AND source=?", (institute, source))
                if source == "meta":
                    conn.execute("DELETE FROM course_campaigns WHERE institute_key=?", (institute,))
                else:
                    conn.execute("UPDATE courses SET product_ids='[]' WHERE institute_key=?", (institute,))

    def public_settings(self, institute):
        values = self.settings(institute)
        return {**{k: v for k, v in values.items() if k not in SECRET_KEYS},
                **{k + "_configured": bool(values.get(k)) for k in SECRET_KEYS}}

    def save_course(self, institute, values):
        if not isinstance(values, dict) or set(values) - {"key", "name", "product_ids", "sheet_id", "sheet_gid", "pipeline_name"}:
            raise ValueError("Curso inválido.")
        ident = key(values.get("key"))
        if ident == "all":
            raise ValueError("Todos os cursos é um filtro, não um curso editável.")
        name, pipeline = values.get("name", ""), values.get("pipeline_name", "")
        products = values.get("product_ids", [])
        sheet, gid = values.get("sheet_id", ""), values.get("sheet_gid", "0")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 160 or not isinstance(pipeline, str) or len(pipeline) > 160:
            raise ValueError("Nome do curso ou funil inválido.")
        if not isinstance(products, list) or len(products) > 20 or any(not isinstance(p, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", p) for p in products):
            raise ValueError("Produtos inválidos.")
        if not isinstance(sheet, str) or (sheet and not re.fullmatch(r"[a-zA-Z0-9_-]{20,150}", sheet)) or not isinstance(gid, str) or not gid.isdigit():
            raise ValueError("Planilha inválida.")
        with self.connection() as conn:
            old = conn.execute("SELECT * FROM courses WHERE institute_key=? AND key=?", (institute, ident)).fetchone()
            if old:
                if json.loads(old["product_ids"]) != products:
                    conn.execute("DELETE FROM course_records WHERE institute_key=? AND course_key=? AND source='kiwify'", (institute, ident))
                    conn.execute("DELETE FROM institute_sync WHERE institute_key=? AND course_key=? AND source='kiwify'", (institute, ident))
                if (old["sheet_id"], old["sheet_gid"]) != (sheet, gid):
                    conn.execute("DELETE FROM course_records WHERE institute_key=? AND course_key=? AND source='sheets'", (institute, ident))
                    conn.execute("DELETE FROM institute_sync WHERE institute_key=? AND course_key=? AND source='sheets'", (institute, ident))
                if old["pipeline_name"] != pipeline.strip():
                    conn.execute("DELETE FROM course_records WHERE institute_key=? AND course_key=? AND source='kommo'", (institute, ident))
                    conn.execute("DELETE FROM institute_sync WHERE institute_key=? AND course_key=? AND source='kommo'", (institute, ident))
            conn.execute("INSERT INTO courses VALUES (?,?,?,?,?,?,?) ON CONFLICT(institute_key,key) DO UPDATE SET name=excluded.name,product_ids=excluded.product_ids,sheet_id=excluded.sheet_id,sheet_gid=excluded.sheet_gid,pipeline_name=excluded.pipeline_name",
                         (institute, ident, name.strip(), json.dumps(list(dict.fromkeys(products))), sheet, gid, pipeline.strip()))

    def records(self, institute, course, source):
        with self.connection() as conn:
            return [json.loads(r[0]) for r in conn.execute("SELECT data_json FROM course_records WHERE institute_key=? AND course_key=? AND source=?",
                                                       (institute, course, source))]

    def save_records(self, institute, course, source, rows, snapshot=False, window=None):
        with self.connection() as conn:
            if snapshot:
                conn.execute("DELETE FROM course_records WHERE institute_key=? AND course_key=? AND source=?", (institute, course, source))
            elif window:
                field, start, end = window
                for old in conn.execute("SELECT record_id,data_json FROM course_records WHERE institute_key=? AND course_key=? AND source=?", (institute, course, source)).fetchall():
                    if start <= json.loads(old["data_json"]).get(field, "") <= end:
                        conn.execute("DELETE FROM course_records WHERE institute_key=? AND course_key=? AND source=? AND record_id=?", (institute, course, source, old["record_id"]))
            for row in rows:
                conn.execute("INSERT INTO course_records VALUES (?,?,?,?,?) ON CONFLICT(institute_key,course_key,source,record_id) DO UPDATE SET data_json=excluded.data_json",
                             (institute, course, source, str(row["id"]), json.dumps(row, ensure_ascii=False)))

    def campaigns(self, institute, course):
        with self.connection() as conn:
            return [{"id": r["campaign_id"], "name": r["campaign_name"], "aliases": json.loads(r["aliases_json"])} for r in conn.execute(
                "SELECT * FROM course_campaigns WHERE institute_key=? AND course_key=?", (institute, course))]

    def save_campaigns(self, institute, course, rows):
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError("Campanhas inválidas.")
        ids, aliases = set(), set()
        for row in rows:
            if not isinstance(row, dict) or set(row) - {"id", "name", "aliases"} or not re.fullmatch(r"[0-9]{3,30}", str(row.get("id", ""))):
                raise ValueError("Campanha inválida.")
            if not isinstance(row.get("name"), str) or not 1 <= len(row["name"]) <= 200 or not isinstance(row.get("aliases"), list):
                raise ValueError("Nome ou UTMs inválidos.")
            if len(row["aliases"]) > 100 or any(not isinstance(a, str) for a in row["aliases"]):
                raise ValueError("UTMs inválidas.")
            tokens = [str(row["id"]), *(a.strip() for a in row["aliases"])]
            if any(not 1 <= len(a) <= 250 for a in tokens) or len(set(tokens)) != len(tokens):
                raise ValueError("UTMs inválidas ou repetidas.")
            if ids.intersection({str(row["id"])}) or aliases.intersection(tokens):
                raise ValueError("Uma UTM não pode apontar para duas campanhas.")
            ids.add(str(row["id"]))
            aliases.update(tokens)
        with self.connection() as conn:
            for row in rows:
                owner = conn.execute("SELECT course_key FROM course_campaigns WHERE institute_key=? AND campaign_id=?", (institute, str(row["id"]))).fetchone()
                if owner and owner[0] != course:
                    raise ValueError("Esta campanha já pertence a outro curso.")
            conn.execute("DELETE FROM course_campaigns WHERE institute_key=? AND course_key=?", (institute, course))
            for row in rows:
                conn.execute("INSERT INTO course_campaigns VALUES (?,?,?,?,?)", (institute, course, str(row["id"]), row["name"], json.dumps([a.strip() for a in row["aliases"]])))

    def members(self, institute):
        with self.connection() as conn:
            return [r[0] for r in conn.execute("SELECT user_id FROM institute_members WHERE institute_key=?", (institute,))]

    def save_members(self, institute, ids):
        if not isinstance(ids, list) or any(type(v) is not int or v < 1 for v in ids) or len(ids) > 1000:
            raise ValueError("Usuários inválidos.")
        with self.connection() as conn:
            conn.execute("DELETE FROM institute_members WHERE institute_key=?", (institute,))
            conn.executemany("INSERT INTO institute_members VALUES (?,?)", [(institute, v) for v in set(ids)])

    def sync_state(self, institute, course):
        with self.connection() as conn:
            return {r[0]: json.loads(r[1]) for r in conn.execute("SELECT source,data_json FROM institute_sync WHERE institute_key=? AND course_key=?", (institute, course))}

    def set_sync_state(self, institute, course, source, state):
        with self.connection() as conn:
            conn.execute("INSERT INTO institute_sync VALUES (?,?,?,?) ON CONFLICT(institute_key,course_key,source) DO UPDATE SET data_json=excluded.data_json",
                         (institute, course, source, json.dumps(state)))
