"""Server-only email identities, recovery tokens and bounded request counters."""

import hashlib
import hmac
import re
import secrets
import sqlite3
import time
import unicodedata


EMAIL_UNCHANGED = object()
RECOVERY_TTL = 20 * 60
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")


def normalize_email(value):
    if not isinstance(value, str):
        raise ValueError("Informe um e-mail valido.")
    value = unicodedata.normalize("NFKC", value).strip().casefold()
    if len(value) > 254 or value.count("@") != 1:
        raise ValueError("Informe um e-mail valido.")
    local, domain = value.rsplit("@", 1)
    try:
        domain = domain.encode("idna").decode("ascii")
    except UnicodeError:
        raise ValueError("Informe um e-mail valido.") from None
    if (not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}", local)
            or local.startswith(".") or local.endswith(".") or ".." in local
            or "." not in domain or len(domain) > 253
            or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part)
                   for part in domain.split("."))):
        raise ValueError("Informe um e-mail valido.")
    result = local + "@" + domain
    if len(result) > 254:
        raise ValueError("Informe um e-mail valido.")
    return result


def optional_email(value):
    return None if value in (None, "") else normalize_email(value)


def initialize_recovery_schema(conn):
    if "email" not in {r[1] for r in conn.execute("PRAGMA table_info(users)")}:
        conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_email_unique ON users(email) WHERE email IS NOT NULL")
    conn.execute("""CREATE TABLE IF NOT EXISTS password_recovery (
        token_hash TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        email TEXT NOT NULL,
        password_version TEXT NOT NULL,
        expires_at INTEGER NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS password_recovery_user ON password_recovery(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS password_recovery_expiry ON password_recovery(expires_at)")
    conn.execute("""CREATE TABLE IF NOT EXISTS recovery_limits (
        key_hash TEXT PRIMARY KEY, window INTEGER NOT NULL,
        count INTEGER NOT NULL, expires_at INTEGER NOT NULL
    )""")


class AuthRecoveryMixin:
    def _check_identity_available(self, conn, login, email, user_id=0):
        if conn.execute("""SELECT 1 FROM users WHERE id != ? AND
            (login IN (?, ?) OR email IN (?, ?)) LIMIT 1""",
            (user_id, login, email, login, email)).fetchone():
            raise sqlite3.IntegrityError("Identity already exists.")
        try:
            login_email = normalize_email(login)
        except ValueError:
            login_email = None
        aliases = {a for a in (login_email, email) if a}
        if aliases:
            for row in conn.execute("SELECT login FROM users WHERE id != ?", (user_id,)):
                try:
                    if normalize_email(row["login"]) in aliases:
                        raise sqlite3.IntegrityError("Identity already exists.")
                except ValueError:
                    pass

    @staticmethod
    def _revoke_recovery(conn, user_id):
        conn.execute("DELETE FROM password_recovery WHERE user_id = ?", (user_id,))

    def recovery_limit(self, identity, peer, *, phase="request", now=None):
        now = int(time.time() if now is None else now)
        if phase == "send":
            rules = (("send-minute", "all", 60, 4), ("send-hour", "all", 3600, 60))
        elif phase == "consume":
            rules = (("consume", str(peer)[:200], 600, 30),)
        else:
            rules = (("email", str(identity)[:254], 3600, 3),
                     ("peer", str(peer)[:200], 600, 20), ("requests", "all", 600, 300))
        buckets = [(hashlib.sha256((kind + ":" + key).encode()).hexdigest(), now // span, limit, span)
                   for kind, key, span, limit in rules]
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM recovery_limits WHERE expires_at <= ?", (now,))
            for key, window, limit, _ in buckets:
                row = conn.execute("SELECT window,count FROM recovery_limits WHERE key_hash = ?", (key,)).fetchone()
                if row and row["window"] == window and row["count"] >= limit:
                    return False
            for key, window, _, span in buckets:
                conn.execute("""INSERT INTO recovery_limits VALUES (?, ?, 1, ?)
                    ON CONFLICT(key_hash) DO UPDATE SET window=excluded.window,
                    count=CASE WHEN recovery_limits.window=excluded.window THEN recovery_limits.count+1 ELSE 1 END,
                    expires_at=excluded.expires_at""", (key, window, (window + 1) * span))
        return True

    def create_password_recovery(self, email, *, now=None):
        email = normalize_email(email)
        now = int(time.time() if now is None else now)
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM password_recovery WHERE expires_at <= ?", (now,))
            user = conn.execute("SELECT id,email,password_hash FROM users WHERE email=? AND status='active'", (email,)).fetchone()
            if not user:
                return None
            token = secrets.token_urlsafe(32)
            self._revoke_recovery(conn, user["id"])
            version = hashlib.sha256(user["password_hash"].encode()).hexdigest()
            conn.execute("INSERT INTO password_recovery VALUES (?, ?, ?, ?, ?)",
                         (self.token_hash(token), user["id"], email, version, now + RECOVERY_TTL))
            self._audit(conn, "password_recovery_requested", target_id=user["id"], details={"channel": "email"})
        return email, token

    def consume_password_recovery(self, token, password, *, now=None):
        from auth_store import hash_password, utc_now
        from team_presence import close_sessions
        if not isinstance(token, str) or not TOKEN_PATTERN.fullmatch(token):
            return None
        real_clock = now is None
        now = int(time.time() if real_clock else now)
        token_hash = self.token_hash(token)
        with self.connection() as conn:
            if not conn.execute("SELECT 1 FROM password_recovery WHERE token_hash=? AND expires_at>?", (token_hash, now)).fetchone():
                return None
        encoded = hash_password(password)
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            now = int(time.time()) if real_clock else now
            row = conn.execute("""SELECT r.user_id,r.email,r.password_version,u.password_hash
                FROM password_recovery r JOIN users u ON u.id=r.user_id
                WHERE r.token_hash=? AND r.expires_at>? AND u.status='active' AND u.email=r.email""",
                (token_hash, now)).fetchone()
            if not row or not hmac.compare_digest(row["password_version"], hashlib.sha256(row["password_hash"].encode()).hexdigest()):
                return None
            conn.execute("UPDATE users SET password_hash=?,updated_at=? WHERE id=?", (encoded, utc_now(), row["user_id"]))
            close_sessions(conn, time.time(), user_id=row["user_id"])
            conn.execute("DELETE FROM sessions WHERE user_id=?", (row["user_id"],))
            self._revoke_recovery(conn, row["user_id"])
            self._audit(conn, "password_reset", target_id=row["user_id"], details={"channel": "email_link", "sessions_revoked": True})
            return row["user_id"]
