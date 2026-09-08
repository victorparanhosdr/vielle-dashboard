"""Central permission, profile and administrative audit persistence."""

import json
from datetime import datetime, timezone

from access_policy import clinic_permissions, permission_map, profile_defaults, validate_permissions


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def initialize_access_schema(conn, old_version):
    conn.execute("""CREATE TABLE IF NOT EXISTS user_clinic_permissions (
        user_id INTEGER NOT NULL, clinic_key TEXT NOT NULL, permission_key TEXT NOT NULL,
        PRIMARY KEY(user_id, clinic_key, permission_key),
        FOREIGN KEY(user_id, clinic_key) REFERENCES user_clinics(user_id, clinic_key) ON DELETE CASCADE
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS access_profiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
        permissions_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
        actor_id INTEGER, actor_name TEXT NOT NULL, target_id INTEGER,
        target_name TEXT NOT NULL, action TEXT NOT NULL, details_json TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS audit_log_created ON audit_log(created_at)")
    if old_version < 4:
        # Phase 5 users already had every module in each assigned clinic.
        for row in conn.execute("SELECT user_id, clinic_key FROM user_clinics").fetchall():
            conn.executemany("INSERT OR IGNORE INTO user_clinic_permissions VALUES (?, ?, ?)",
                             [(row[0], row[1], key) for key in clinic_permissions(row[1])])
        now = timestamp()
        for name, permissions in profile_defaults().items():
            conn.execute("INSERT OR IGNORE INTO access_profiles(name, permissions_json, created_at, updated_at) VALUES (?, ?, ?, ?)",
                         (name, json.dumps(permissions), now, now))


class AccessStoreMixin:
    def _audit(self, conn, action, actor_id=None, target_id=None, details=None):
        actor = conn.execute("SELECT nome FROM users WHERE id = ?", (actor_id,)).fetchone()
        target = conn.execute("SELECT nome FROM users WHERE id = ?", (target_id,)).fetchone()
        conn.execute("INSERT INTO audit_log(created_at, actor_id, actor_name, target_id, target_name, action, details_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (timestamp(), actor_id, actor[0] if actor else "Sistema", target_id,
                      target[0] if target else "", action, json.dumps(details or {}, ensure_ascii=False)))

    def audit_event(self, action, actor_id=None, target_id=None, details=None):
        with self.connection() as conn:
            self._audit(conn, action, actor_id, target_id, details)

    def _save_permissions(self, conn, user_id, clinics, permissions):
        values = permission_map(clinics, permissions)
        conn.execute("DELETE FROM user_clinic_permissions WHERE user_id = ?", (user_id,))
        conn.executemany("INSERT INTO user_clinic_permissions VALUES (?, ?, ?)",
                         [(user_id, clinic, key) for clinic, keys in values.items() for key in keys])

    def user_permissions(self, user_id, clinic):
        if not self.user_can_access_clinic(user_id, clinic):
            return []
        with self.connection() as conn:
            user = conn.execute("SELECT is_master FROM users WHERE id = ?", (user_id,)).fetchone()
            if user and user[0]:
                return clinic_permissions(clinic)
            return [row[0] for row in conn.execute("SELECT permission_key FROM user_clinic_permissions WHERE user_id = ? AND clinic_key = ? ORDER BY permission_key", (user_id, clinic)) if row[0] in clinic_permissions(clinic)]

    def has_permission(self, user_id, clinic, permission):
        return permission in self.user_permissions(user_id, clinic)

    def list_profiles(self):
        with self.connection() as conn:
            return [{"id": row["id"], "name": row["name"], "permissions": json.loads(row["permissions_json"])}
                    for row in conn.execute("SELECT * FROM access_profiles ORDER BY name COLLATE NOCASE")]

    def save_profile(self, name, permissions, profile_id=None, actor_id=None):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            raise ValueError("Informe um nome de perfil com até 100 caracteres.")
        values = validate_permissions("vielle", permissions)
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if profile_id is None:
                cursor = conn.execute("INSERT INTO access_profiles(name, permissions_json, created_at, updated_at) VALUES (?, ?, ?, ?)",
                                      (name.strip(), json.dumps(values), timestamp(), timestamp()))
                profile_id = cursor.lastrowid
            else:
                result = conn.execute("UPDATE access_profiles SET name = ?, permissions_json = ?, updated_at = ? WHERE id = ?", (name.strip(), json.dumps(values), timestamp(), profile_id))
                if not result.rowcount:
                    raise ValueError("Perfil não encontrado.")
            self._audit(conn, "profile_saved", actor_id, details={"profile_id": profile_id, "name": name.strip(), "permissions": values})
            return profile_id

    def list_audit(self, page=1, action=""):
        if not isinstance(page, int) or not 1 <= page <= 1_000_000 or not isinstance(action, str) or len(action) > 80:
            raise ValueError("Filtro inválido.")
        with self.connection() as conn:
            total = conn.execute("SELECT count(*) FROM audit_log WHERE (? = '' OR action = ?)", (action, action)).fetchone()[0]
            rows = conn.execute("SELECT * FROM audit_log WHERE (? = '' OR action = ?) ORDER BY id DESC LIMIT 25 OFFSET ?", (action, action, (page-1)*25)).fetchall()
            return {"events": [{**dict(row), "details": json.loads(row["details_json"])} for row in rows], "total": total, "page": page, "page_size": 25}
