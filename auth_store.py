"""Central users, sessions and clinic memberships."""

import contextlib
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import sqlite3
import time
import unicodedata
from datetime import datetime, timezone
from clinic_catalog import SUPPORTED_CLINICS, validate_clinic_keys
from access_store import AccessStoreMixin, initialize_access_schema
from access_policy import permission_map


DATABASE_NAME = "doc4docs_auth.sqlite3"
SCHEMA_VERSION = 4
PASSWORD_ITERATIONS = 600_000
BUSY_TIMEOUT_MS = 60_000
SESSION_TTL_SECONDS = 12 * 60 * 60
USER_PUBLIC_COLUMNS = "id, nome, login, status, is_master, created_at, updated_at, last_login"


def auth_database_path(base_dir, environ=None):
    env = os.environ if environ is None else environ
    data_dir = env.get("DATA_DIR") or env.get("RAILWAY_VOLUME_MOUNT_PATH")
    if not data_dir and env.get("RAILWAY_PROJECT_ID"):
        raise RuntimeError("Configure DATA_DIR or a Railway volume for user storage.")
    return Path(data_dir or base_dir).expanduser().resolve() / DATABASE_NAME


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def normalize_login(login):
    if not isinstance(login, str):
        raise ValueError("Login must be text.")
    login = unicodedata.normalize("NFKC", login).strip().casefold()
    if not login or len(login) > 254 or any(char.isspace() or unicodedata.category(char).startswith("C") for char in login):
        raise ValueError("Login must contain 1-254 characters without spaces or controls.")
    return login


def password_bytes(password, new=False):
    if not isinstance(password, str):
        raise ValueError("Password must be text.")
    raw = password.encode("utf-8")
    if not raw or len(raw) > 1024 or (new and len(password) < 12):
        raise ValueError("New passwords require at least 12 characters and at most 1024 UTF-8 bytes.")
    return raw


def hash_password(password):
    raw = password_bytes(password, new=True)
    salt = secrets.token_bytes(32)
    digest = hashlib.pbkdf2_hmac("sha256", raw, salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password, stored_hash):
    try:
        raw = password_bytes(password)
        algorithm, count, salt_hex, digest_hex = stored_hash.split("$")
        iterations = int(count)
        salt, expected = bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
        if algorithm != "pbkdf2_sha256" or not 100_000 <= iterations <= 2_000_000:
            return False
        if len(salt) != 32 or len(expected) != 32:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", raw, salt, iterations)
        return hmac.compare_digest(actual, expected)
    except (AttributeError, TypeError, ValueError, UnicodeError):
        return False


class AuthStore(AccessStoreMixin):
    def __init__(self, path):
        self.path = Path(path)

    @contextlib.contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_MS / 1000)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA synchronous = FULL")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self):
        # Fail at the configured location instead of falling back to ephemeral storage.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2, 3, SCHEMA_VERSION):
                raise RuntimeError("Unsupported authentication database schema version.")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nome TEXT NOT NULL CHECK(length(trim(nome)) > 0),
                    login TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active'
                        CHECK(status IN ('active', 'inactive')),
                    is_master INTEGER NOT NULL DEFAULT 0 CHECK(is_master IN (0, 1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_login TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_clinics (
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    clinic_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, clinic_key)
                )
            """)
            initialize_access_schema(conn, version)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def create_user(self, nome, login, password, *, status="active", is_master=False, clinic_keys=None, permissions=None, actor_id=None):
        if not isinstance(nome, str) or not nome.strip() or len(nome.strip()) > 200:
            raise ValueError("Name must contain 1-200 characters.")
        if status not in ("active", "inactive") or not isinstance(is_master, bool):
            raise ValueError("Invalid user status or master flag.")
        login = normalize_login(login)
        keys = validate_clinic_keys([] if clinic_keys is None else clinic_keys)
        permissions = permission_map(keys, permissions)
        encoded = hash_password(password)
        now = utc_now()
        with self.connection() as conn:
            cursor = conn.execute("""
                INSERT INTO users
                    (nome, login, password_hash, status, is_master, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (nome.strip(), login, encoded, status, int(is_master), now, now))
            user_id = cursor.lastrowid
            conn.executemany("INSERT INTO user_clinics (user_id, clinic_key, created_at) VALUES (?, ?, ?)",
                             [(user_id, key, now) for key in keys])
            self._save_permissions(conn, user_id, keys, permissions)
            self._audit(conn, "user_created", actor_id, user_id, {"clinics": keys, "permissions": permissions})
            return user_id

    def get_user_by_login(self, login):
        login = normalize_login(login)
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE login = ?", (login,)).fetchone()
            return dict(row) if row else None

    def create_first_master(self, nome, login, password):
        if not isinstance(nome, str) or not nome.strip() or len(nome.strip()) > 200:
            raise ValueError("Name must contain 1-200 characters.")
        login = normalize_login(login)
        encoded = hash_password(password)
        now = utc_now()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM users WHERE is_master = 1 LIMIT 1").fetchone():
                raise ValueError("A Master already exists. Bootstrap cannot be repeated.")
            cursor = conn.execute("""
                INSERT INTO users
                    (nome, login, password_hash, status, is_master, created_at, updated_at)
                VALUES (?, ?, ?, 'active', 1, ?, ?)
            """, (nome.strip(), login, encoded, now, now))
            self._audit(conn, "user_created", target_id=cursor.lastrowid, details={"master": True})
            return cursor.lastrowid

    def list_users(self, search="", status="", page=1, page_size=25):
        if status not in ("", "active", "inactive") or not 1 <= page or not 1 <= page_size <= 100:
            raise ValueError("Invalid user filter.")
        search = str(search).strip()[:254]
        pattern = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where = "(nome LIKE ? ESCAPE '\\' OR login LIKE ? ESCAPE '\\') AND (? = '' OR status = ?)"
        values = (pattern, pattern, status, status)
        with self.connection() as conn:
            total = conn.execute(f"SELECT count(*) FROM users WHERE {where}", values).fetchone()[0]
            rows = conn.execute(f"SELECT {USER_PUBLIC_COLUMNS} FROM users WHERE {where} ORDER BY nome COLLATE NOCASE, id LIMIT ? OFFSET ?",
                                (*values, page_size, (page - 1) * page_size)).fetchall()
            users = [self._public_memberships(conn, row) for row in rows]
            return {"users": users, "total": total, "page": page, "page_size": page_size}

    def _public_memberships(self, conn, row):
        user = dict(row)
        keys = [item[0] for item in conn.execute("SELECT clinic_key FROM user_clinics WHERE user_id = ?", (user["id"],))]
        user["clinic_keys"] = list(SUPPORTED_CLINICS) if user["is_master"] else [key for key in SUPPORTED_CLINICS if key in keys]
        from access_policy import clinic_permissions
        user["permissions"] = {clinic: clinic_permissions(clinic) if user["is_master"] else [item[0] for item in conn.execute("SELECT permission_key FROM user_clinic_permissions WHERE user_id = ? AND clinic_key = ? ORDER BY permission_key", (user["id"], clinic))] for clinic in user["clinic_keys"]}
        return user

    def allowed_clinics(self, user_id):
        with self.connection() as conn:
            user = conn.execute("SELECT id, is_master FROM users WHERE id = ? AND status = 'active'", (user_id,)).fetchone()
            return self._public_memberships(conn, user)["clinic_keys"] if user else []

    def user_can_access_clinic(self, user_id, clinic_key):
        return clinic_key in SUPPORTED_CLINICS and clinic_key in self.allowed_clinics(user_id)

    def get_public_user(self, user_id):
        with self.connection() as conn:
            row = conn.execute(f"SELECT {USER_PUBLIC_COLUMNS} FROM users WHERE id = ?", (user_id,)).fetchone()
            return self._public_memberships(conn, row) if row else None

    def update_user(self, user_id, nome, login, *, clinic_keys=None, permissions=None, actor_id=None):
        if not isinstance(nome, str) or not nome.strip() or len(nome.strip()) > 200:
            raise ValueError("Name must contain 1-200 characters.")
        login = normalize_login(login)
        keys = None if clinic_keys is None else validate_clinic_keys(clinic_keys)
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute("SELECT login, is_master FROM users WHERE id = ?", (user_id,)).fetchone()
            if not old:
                return False
            old_keys = [row[0] for row in conn.execute("SELECT clinic_key FROM user_clinics WHERE user_id = ?", (user_id,))]
            old_permissions = {clinic: [row[0] for row in conn.execute("SELECT permission_key FROM user_clinic_permissions WHERE user_id = ? AND clinic_key = ? ORDER BY permission_key", (user_id, clinic))] for clinic in old_keys}
            if permissions is not None and keys is None:
                keys = old_keys
            if keys is not None:
                if old["is_master"]:
                    raise ValueError("O Master possui acesso a todas as clínicas.")
                next_permissions = permission_map(keys, permissions if permissions is not None else {clinic: old_permissions.get(clinic, []) for clinic in keys})
                conn.execute("DELETE FROM user_clinics WHERE user_id = ?", (user_id,))
                conn.executemany("INSERT INTO user_clinics (user_id, clinic_key, created_at) VALUES (?, ?, ?)",
                                 [(user_id, key, utc_now()) for key in keys])
                self._save_permissions(conn, user_id, keys, next_permissions)
                if set(old_keys) != set(keys):
                    self._audit(conn, "clinics_changed", actor_id, user_id, {"before": old_keys, "after": keys})
                if old_permissions != next_permissions:
                    self._audit(conn, "permissions_changed", actor_id, user_id, {"before": old_permissions, "after": next_permissions})
            conn.execute("UPDATE users SET nome = ?, login = ?, updated_at = ? WHERE id = ?", (nome.strip(), login, utc_now(), user_id))
            if old["login"] != login:
                conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            self._audit(conn, "user_updated", actor_id, user_id)
            return True

    def validate_password(self, login, password):
        try:
            user = self.get_user_by_login(login)
        except ValueError:
            return False
        return bool(user and user["status"] == "active" and verify_password(password, user["password_hash"]))

    def update_last_login(self, user_id):
        now = utc_now()
        with self.connection() as conn:
            result = conn.execute("""
                UPDATE users SET last_login = ?, updated_at = ?
                WHERE id = ? AND status = 'active'
            """, (now, now, user_id))
            return result.rowcount == 1

    def set_user_active(self, user_id, active, *, actor_id=None):
        if not isinstance(active, bool):
            raise ValueError("Active must be a boolean.")
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            target = conn.execute("SELECT is_master, status FROM users WHERE id = ?", (user_id,)).fetchone()
            if target and target["is_master"] and target["status"] == "active" and not active:
                others = conn.execute("SELECT count(*) FROM users WHERE is_master = 1 AND status = 'active' AND id != ?", (user_id,)).fetchone()[0]
                if not others:
                    raise ValueError("Não é possível desativar o último Master ativo.")
            result = conn.execute("UPDATE users SET status = ?, updated_at = ? WHERE id = ?",
                                  ("active" if active else "inactive", utc_now(), user_id))
            if not active:
                conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            if result.rowcount:
                self._audit(conn, "user_activated" if active else "user_deactivated", actor_id, user_id)
            return result.rowcount == 1

    def set_password(self, user_id, password, *, actor_id=None):
        encoded = hash_password(password)
        with self.connection() as conn:
            result = conn.execute("UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                                  (encoded, utc_now(), user_id))
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            if result.rowcount:
                self._audit(conn, "password_reset", actor_id, user_id)
            return result.rowcount == 1

    def login(self, login, password, previous_token=None):
        try:
            user = self.get_user_by_login(login)
        except ValueError:
            user = None
        # Unknown users still perform the password derivation, without creating an account.
        dummy_hash = f"pbkdf2_sha256${PASSWORD_ITERATIONS}${'00' * 32}${'00' * 32}"
        valid = verify_password(password, user["password_hash"] if user else dummy_hash)
        if not valid or not user or user["status"] != "active":
            self.audit_event("login_denied", target_id=user["id"] if user else None)
            return None
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute("SELECT id FROM users WHERE id = ? AND status = 'active' AND password_hash = ?",
                                   (user["id"], user["password_hash"])).fetchone()
            if not current:
                self._audit(conn, "login_denied", target_id=user["id"])
                return None
            conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            if previous_token:
                conn.execute("DELETE FROM sessions WHERE token_hash = ?", (self.token_hash(previous_token),))
            conn.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)",
                         (self.token_hash(token), user["id"], now, now + SESSION_TTL_SECONDS))
            stamp = utc_now()
            conn.execute("UPDATE users SET last_login = ?, updated_at = ? WHERE id = ?", (stamp, stamp, user["id"]))
            self._audit(conn, "login", user["id"], user["id"])
        return token

    @staticmethod
    def token_hash(token):
        return hashlib.sha256(str(token).encode("utf-8")).hexdigest()

    def session_user(self, token):
        if not isinstance(token, str) or len(token) != 43:
            return None
        with self.connection() as conn:
            row = conn.execute("""
                SELECT u.id, u.nome, u.login, u.status, u.is_master, u.last_login
                FROM sessions s JOIN users u ON u.id = s.user_id
                WHERE s.token_hash = ? AND s.expires_at > ? AND u.status = 'active'
            """, (self.token_hash(token), int(time.time()))).fetchone()
            return dict(row) if row else None

    def logout(self, token):
        with self.connection() as conn:
            row = conn.execute("SELECT user_id FROM sessions WHERE token_hash = ?", (self.token_hash(token),)).fetchone()
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (self.token_hash(token),))
            if row:
                self._audit(conn, "logout", row[0], row[0])
