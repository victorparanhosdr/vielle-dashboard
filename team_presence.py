"""Clinic-scoped presence. Interactions are timestamps, never captured input content."""

from datetime import date, datetime, time as day_time, timedelta
import math
import re
import time
import uuid
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Sao_Paulo")
IDLE_SECONDS = 300
LEASE_SECONDS = 90


def initialize_schema(c):
    c.execute("""CREATE TABLE IF NOT EXISTS team_clients (
        clinic_key TEXT NOT NULL, user_id INTEGER NOT NULL REFERENCES users(id),
        session_hash TEXT NOT NULL, client_id TEXT NOT NULL, sequence INTEGER NOT NULL,
        started_at REAL NOT NULL, last_seen REAL NOT NULL, last_activity REAL NOT NULL,
        expires_at REAL NOT NULL, closed_at REAL,
        PRIMARY KEY(clinic_key,user_id,session_hash,client_id))""")
    c.execute("CREATE INDEX IF NOT EXISTS team_clients_user ON team_clients(clinic_key,user_id)")
    c.execute("""CREATE TABLE IF NOT EXISTS team_presence (
        clinic_key TEXT NOT NULL, user_id INTEGER NOT NULL REFERENCES users(id),
        status TEXT NOT NULL, since REAL NOT NULL, cursor REAL NOT NULL,
        PRIMARY KEY(clinic_key,user_id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS team_intervals (
        id INTEGER PRIMARY KEY, clinic_key TEXT NOT NULL,
        user_id INTEGER NOT NULL REFERENCES users(id), status TEXT NOT NULL,
        started_at REAL NOT NULL, ended_at REAL NOT NULL)""")
    c.execute("CREATE INDEX IF NOT EXISTS team_intervals_range ON team_intervals(clinic_key,user_id,ended_at)")
    c.execute("""CREATE TABLE IF NOT EXISTS team_help (
        id TEXT PRIMARY KEY, clinic_key TEXT NOT NULL,
        sender_id INTEGER NOT NULL REFERENCES users(id), recipient_id INTEGER NOT NULL REFERENCES users(id),
        message TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
        request_id TEXT NOT NULL, UNIQUE(clinic_key,sender_id,request_id))""")
    c.execute("CREATE INDEX IF NOT EXISTS team_help_recipient ON team_help(clinic_key,recipient_id,created_at)")


def _append(c, clinic, user_id, status, start, end):
    if end <= start:
        return
    last = c.execute("SELECT id,status,ended_at FROM team_intervals WHERE clinic_key=? AND user_id=? ORDER BY id DESC LIMIT 1",
                     (clinic, user_id)).fetchone()
    if last and last["status"] == status and last["ended_at"] == start:
        c.execute("UPDATE team_intervals SET ended_at=? WHERE id=?", (end, last["id"]))
    else:
        c.execute("INSERT INTO team_intervals(clinic_key,user_id,status,started_at,ended_at) VALUES(?,?,?,?,?)",
                  (clinic, user_id, status, start, end))


def settle(c, clinic, user_id, now):
    state = c.execute("SELECT * FROM team_presence WHERE clinic_key=? AND user_id=?", (clinic, user_id)).fetchone()
    if not state:
        c.execute("INSERT INTO team_presence VALUES(?,?,'offline',?,?)", (clinic, user_id, now, now))
        state = {"cursor": now, "since": now, "status": "offline"}
    now = max(now, state["cursor"])
    clients = c.execute("SELECT * FROM team_clients WHERE clinic_key=? AND user_id=?", (clinic, user_id)).fetchall()
    latest_activity = max((row["last_activity"] for row in clients), default=0)
    def end(row):
        return min(row["last_seen"] + LEASE_SECONDS, row["expires_at"], row["closed_at"] if row["closed_at"] is not None else math.inf)
    def status_at(stamp):
        if not any(row["started_at"] <= stamp < end(row) for row in clients):
            return "offline"
        return "active" if stamp < latest_activity + IDLE_SECONDS else "idle"
    edges = {state["cursor"], now}
    edges.update(end(row) for row in clients if state["cursor"] < end(row) < now)
    edges.update(row["started_at"] for row in clients if state["cursor"] < row["started_at"] < now)
    if state["cursor"] < latest_activity + IDLE_SECONDS < now:
        edges.add(latest_activity + IDLE_SECONDS)
    since, previous = state["since"], state["status"]
    ordered = sorted(edges)
    for start, stop in zip(ordered, ordered[1:]):
        status = status_at(start)
        if status != previous:
            since, previous = start, status
        _append(c, clinic, user_id, status, start, stop)
    current = status_at(now)
    if current != previous:
        since = now
    c.execute("UPDATE team_presence SET status=?,since=?,cursor=? WHERE clinic_key=? AND user_id=?",
              (current, since, now, clinic, user_id))
    return {"status": current, "status_since": since}


def close_sessions(c, now, *, user_id=None, session_hash=None):
    """Called inside authentication mutations, before invalidating the session."""
    column, value = ("user_id", user_id) if user_id is not None else ("session_hash", session_hash)
    targets = c.execute(f"SELECT DISTINCT clinic_key,user_id FROM team_clients WHERE {column}=? AND closed_at IS NULL", (value,)).fetchall()
    for target in targets:
        clinic, uid = target["clinic_key"], target["user_id"]
        settle(c, clinic, uid, now)
        c.execute(f"UPDATE team_clients SET closed_at=? WHERE clinic_key=? AND user_id=? AND {column}=? AND closed_at IS NULL",
                  (now, clinic, uid, value))
        settle(c, clinic, uid, now)


def _uuid(value):
    if not isinstance(value, str):
        raise ValueError("Identificador inválido.")
    try:
        return uuid.UUID(value).hex
    except (ValueError, AttributeError):
        raise ValueError("Identificador inválido.") from None


class TeamPresence:
    def __init__(self, auth):
        self.auth = auth

    @staticmethod
    def roster(c, clinic, *, attendants_only=False):
        return [dict(row) for row in c.execute("""SELECT u.id,u.nome AS name FROM users u
            LEFT JOIN user_team_profiles profile ON profile.user_id=u.id AND profile.clinic_key=?
            WHERE u.status='active' AND (u.is_master=1 OR EXISTS(
                SELECT 1 FROM user_clinics m WHERE m.user_id=u.id AND m.clinic_key=?))
            AND (?=0 OR coalesce(profile.profile,CASE WHEN u.is_master=1 THEN 'management' ELSE 'attendant' END)='attendant')
            ORDER BY u.nome COLLATE NOCASE,u.id""", (clinic, clinic, attendants_only))]

    def heartbeat(self, clinic, user_id, token, payload, *, leave=False, now=None):
        now = time.time() if now is None else now
        client_id = _uuid(payload.get("client_id"))
        sequence = payload.get("sequence")
        age = payload.get("activity_age", IDLE_SECONDS)
        if isinstance(sequence, bool) or not isinstance(sequence, int) or not 0 <= sequence <= 2**53 - 1:
            raise ValueError("Sequência inválida.")
        if isinstance(age, bool) or not isinstance(age, (float, int)) or not math.isfinite(age) or not 0 <= age <= 86400:
            raise ValueError("Tempo de interação inválido.")
        session_hash = self.auth.token_hash(token)
        with self.auth.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            session = c.execute("""SELECT s.expires_at FROM sessions s JOIN users u ON u.id=s.user_id
                WHERE s.token_hash=? AND s.user_id=? AND s.expires_at>? AND u.status='active'
                AND (u.is_master=1 OR EXISTS(SELECT 1 FROM user_clinics m WHERE m.user_id=u.id AND m.clinic_key=?))""",
                                (session_hash, user_id, now, clinic)).fetchone()
            if not session:
                raise PermissionError("Sessão expirada. Entre novamente.")
            old = c.execute("SELECT * FROM team_clients WHERE clinic_key=? AND user_id=? AND session_hash=? AND client_id=?",
                            (clinic, user_id, session_hash, client_id)).fetchone()
            if old and old["sequence"] >= sequence:
                return settle(c, clinic, user_id, now)
            live = old and old["closed_at"] is None and old["last_seen"] + LEASE_SECONDS > now
            event_time = max(old["last_activity"], now - age) if old else now - age
            if live and event_time > old["last_activity"]:
                cursor = c.execute("SELECT cursor FROM team_presence WHERE clinic_key=? AND user_id=?", (clinic, user_id)).fetchone()[0]
                event_time = max(cursor, event_time)
                settle(c, clinic, user_id, min(now, event_time))
                c.execute("UPDATE team_clients SET last_activity=? WHERE clinic_key=? AND user_id=? AND session_hash=? AND client_id=?",
                          (event_time, clinic, user_id, session_hash, client_id))
            settle(c, clinic, user_id, now)
            if leave:
                if old:
                    c.execute("UPDATE team_clients SET closed_at=?,sequence=? WHERE clinic_key=? AND user_id=? AND session_hash=? AND client_id=?",
                              (now, sequence, clinic, user_id, session_hash, client_id))
                else:
                    c.execute("INSERT INTO team_clients VALUES(?,?,?,?,?,?,?,?,?,?)",
                              (clinic, user_id, session_hash, client_id, sequence, now, now, event_time, session["expires_at"], now))
            else:
                if not old:
                    count = c.execute("SELECT count(*) FROM team_clients WHERE clinic_key=? AND user_id=? AND last_seen>? AND closed_at IS NULL",
                                      (clinic, user_id, now - LEASE_SECONDS)).fetchone()[0]
                    if count >= 40:
                        raise ValueError("Muitas abas de presença abertas. Feche algumas e tente novamente.")
                c.execute("""INSERT INTO team_clients VALUES(?,?,?,?,?,?,?,?,?,NULL)
                    ON CONFLICT(clinic_key,user_id,session_hash,client_id) DO UPDATE SET
                    sequence=excluded.sequence,started_at=excluded.started_at,last_seen=excluded.last_seen,
                    last_activity=excluded.last_activity,expires_at=excluded.expires_at,closed_at=NULL""",
                          (clinic, user_id, session_hash, client_id, sequence, old["started_at"] if live else now,
                           now, event_time, session["expires_at"]))
            result = settle(c, clinic, user_id, now)
            # Settled intervals retain history; expired browser rows need not grow forever.
            c.execute("DELETE FROM team_clients WHERE clinic_key=? AND user_id=? AND last_seen<? AND (closed_at IS NOT NULL OR expires_at<?)",
                      (clinic, user_id, now - 2 * 86400, now))
            return result

    @staticmethod
    def _help(c, clinic, user_id, now):
        c.execute("UPDATE team_help SET status='expired',updated_at=? WHERE clinic_key=? AND status IN ('pending','accepted') AND created_at<?",
                  (now, clinic, now - 86400))
        return [dict(row) for row in c.execute("""SELECT h.id,h.sender_id,h.recipient_id,h.message,h.status,h.created_at,h.updated_at,
            s.nome AS sender_name,r.nome AS recipient_name FROM team_help h
            JOIN users s ON s.id=h.sender_id JOIN users r ON r.id=h.recipient_id
            WHERE h.clinic_key=? AND (h.sender_id=? OR h.recipient_id=?) AND h.created_at>? AND s.status='active' AND r.status='active'
            ORDER BY h.created_at DESC LIMIT 30""", (clinic, user_id, user_id, now - 86400))]

    def presence(self, clinic, user_id, *, now=None):
        now = time.time() if now is None else now
        with self.auth.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            members = self.roster(c, clinic, attendants_only=True)
            for member in members:
                previous = c.execute("SELECT 1 FROM team_presence WHERE clinic_key=? AND user_id=?", (clinic, member["id"])).fetchone()
                member.update(settle(c, clinic, member["id"], now) if previous else {"status": "offline", "status_since": None})
            ids = {member["id"] for member in self.roster(c, clinic)}
            requests = [item for item in self._help(c, clinic, user_id, now) if item["sender_id"] in ids and item["recipient_id"] in ids]
        return {"members": members, "help_requests": requests, "server_time": now, "idle_after": IDLE_SECONDS, "lease_seconds": LEASE_SECONDS}

    def help(self, clinic, user_id, payload, *, now=None):
        now = time.time() if now is None else now
        request_id = _uuid(payload.get("request_id"))
        recipient = payload.get("recipient_id")
        message = payload.get("message")
        if isinstance(recipient, bool) or not isinstance(recipient, int) or recipient == user_id:
            raise ValueError("Selecione um colega da equipe.")
        if not isinstance(message, str) or not 1 <= len(message.strip()) <= 500 or any(ord(c) < 32 and c not in '\n\t' for c in message):
            raise ValueError("Escreva um pedido de até 500 caracteres.")
        message = message.strip()
        with self.auth.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            ids = {member["id"] for member in self.roster(c, clinic)}
            if not {user_id, recipient} <= ids:
                raise PermissionError("Colega indisponível nesta clínica.")
            previous = c.execute("SELECT id,recipient_id,message FROM team_help WHERE clinic_key=? AND sender_id=? AND request_id=?",
                                 (clinic, user_id, request_id)).fetchone()
            if previous:
                if previous["recipient_id"] != recipient or previous["message"] != message:
                    raise ValueError("Este pedido já foi utilizado.")
                return previous["id"]
            if recipient not in {member["id"] for member in self.roster(c, clinic, attendants_only=True)}:
                raise PermissionError("Selecione um atendente desta clinica.")
            if settle(c, clinic, recipient, now)["status"] == "offline":
                raise ValueError("Este colega está offline. Escolha alguém online.")
            if c.execute("SELECT count(*) FROM team_help WHERE clinic_key=? AND sender_id=? AND created_at>?", (clinic, user_id, now - 60)).fetchone()[0] >= 5:
                raise ValueError("Aguarde um minuto antes de enviar mais pedidos.")
            id_ = uuid.uuid4().hex
            c.execute("INSERT INTO team_help VALUES(?,?,?,?,?,'pending',?,?,?)", (id_, clinic, user_id, recipient, message, now, now, request_id))
            return id_

    def help_action(self, clinic, user_id, payload, *, now=None):
        now = time.time() if now is None else now
        id_, action = _uuid(payload.get("id")), payload.get("action")
        if action not in {"accept", "dismiss", "complete"}:
            raise ValueError("Ação inválida.")
        with self.auth.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            item = c.execute("SELECT * FROM team_help WHERE id=? AND clinic_key=?", (id_, clinic)).fetchone()
            ids = {member["id"] for member in self.roster(c, clinic)}
            if not item or user_id not in (item["sender_id"], item["recipient_id"]) or not {item["sender_id"], item["recipient_id"]} <= ids:
                raise LookupError("Pedido não encontrado.")
            if action in {"accept", "dismiss"} and user_id != item["recipient_id"]:
                raise PermissionError("Somente o destinatário pode responder ao pedido.")
            if item["status"] not in {"pending", "accepted"} or item["created_at"] <= now - 86400:
                raise ValueError("Este pedido já foi encerrado.")
            status = {"accept": "accepted", "dismiss": "dismissed", "complete": "completed"}[action]
            c.execute("UPDATE team_help SET status=?,updated_at=? WHERE id=?", (status, now, id_))

    def report(self, clinic, query, *, now=None):
        now = time.time() if now is None else now
        today = datetime.fromtimestamp(now, TZ).date()
        try:
            date_text = query.get("date", [today.isoformat()])[0]
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_text):
                raise ValueError()
            last_day = date.fromisoformat(date_text)
            days = int(query.get("days", ["1"])[0])
            if days not in (1, 7, 30) or last_day > today:
                raise ValueError()
            selected = query.get("user", [""])[0]
            uid = int(selected) if selected else None
        except (ValueError, TypeError):
            raise ValueError("Selecione uma data válida e um período de 1, 7 ou 30 dias.") from None
        start = datetime.combine(last_day - timedelta(days=days-1), day_time(), TZ).timestamp()
        stop = min(now, datetime.combine(last_day + timedelta(days=1), day_time(), TZ).timestamp())
        with self.auth.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            members = self.roster(c, clinic)
            current_ids = {member["id"] for member in members}
            historical = c.execute("""SELECT u.id,u.nome AS name FROM users u WHERE EXISTS(
                SELECT 1 FROM team_intervals i WHERE i.user_id=u.id AND i.clinic_key=?
                AND i.status!='offline' AND i.ended_at>? AND i.started_at<?) ORDER BY u.nome COLLATE NOCASE,u.id""",
                                   (clinic, start, stop)).fetchall()
            members.extend(dict(member) for member in historical if member["id"] not in current_ids)
            if uid is not None and uid not in {member["id"] for member in members}:
                raise LookupError("Colaborador não encontrado nesta clínica.")
            result = []
            for member in members:
                if uid is not None and member["id"] != uid:
                    continue
                previous = c.execute("SELECT 1 FROM team_presence WHERE clinic_key=? AND user_id=?", (clinic, member["id"])).fetchone()
                member.update(settle(c, clinic, member["id"], now) if previous else {"status": "offline", "status_since": None})
                rows = c.execute("""SELECT status,started_at,ended_at FROM team_intervals WHERE clinic_key=? AND user_id=?
                    AND ended_at>? AND started_at<? ORDER BY started_at""", (clinic, member["id"], start, stop)).fetchall()
                intervals = [{"status": row["status"], "start": max(start, row["started_at"]), "end": min(stop, row["ended_at"])} for row in rows]
                active = sum(i["end"]-i["start"] for i in intervals if i["status"] == "active")
                idle = sum(i["end"]-i["start"] for i in intervals if i["status"] == "idle")
                sessions = []
                connected = bool(rows and rows[0]["started_at"] < start and rows[0]["status"] != "offline")
                for interval in rows:
                    online = interval["status"] != "offline"
                    if online != connected:
                        if interval["started_at"] >= start:
                            sessions.append({"time": interval["started_at"], "kind": "login" if online else "logout"})
                    connected = online
                result.append({**member, "active_seconds": active, "idle_seconds": idle, "logged_seconds": active+idle,
                               "intervals": intervals, "sessions": sessions})
            first_record = c.execute("SELECT min(started_at) FROM team_intervals WHERE clinic_key=? AND status!='offline'", (clinic,)).fetchone()[0]
        return {"members": result, "start": start, "end": stop, "date": last_day.isoformat(), "days": days,
                "server_time": now, "first_record_at": first_record, "idle_after": IDLE_SECONDS,
                "totals": {key: sum(p[key] for p in result) for key in ("active_seconds", "idle_seconds", "logged_seconds")}}
