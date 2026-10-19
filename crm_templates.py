"""Shared clinic message drafts. Templates never enqueue or send messages."""
import re
import time
import uuid

from crm_filters import fold
from crm_store import Conflict, integer, text


def listing(store, archived=False):
    with store.db() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM message_templates WHERE " + ("archived_at IS NOT NULL" if archived else "archived_at IS NULL"))]
    return sorted(rows, key=lambda r: (fold(r["title"]), r["id"]))


def save(store, p, user, actor):
    title = text(p.get("title"), 80)
    shortcut = text(p.get("shortcut"), 41).removeprefix("/")
    shortcut_key = fold(shortcut)
    body = text(p.get("body"), 3000)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", shortcut_key):
        raise ValueError("Use ate 40 letras, numeros, hifen ou sublinhado no atalho, sem espacos.")
    key = text(p.get("id", ""), 32, False)
    with store.db() as c:
        c.execute("BEGIN IMMEDIATE")
        if key:
            old = store.row(c, "message_templates", key)
            if old["archived_at"] is not None or old["revision"] != integer(p.get("revision"), 1, 2**31-1):
                raise Conflict("Este modelo foi alterado por outra pessoa. Reabra o modelo antes de editar.")
        elif c.execute("SELECT count(*) FROM message_templates WHERE archived_at IS NULL").fetchone()[0] >= 500:
            raise ValueError("Limite de 500 modelos ativos atingido. Arquive os modelos sem uso.")
        duplicate = c.execute("SELECT id FROM message_templates WHERE shortcut_key=? AND archived_at IS NULL AND id<>?", (shortcut_key, key)).fetchone()
        if duplicate:
            raise ValueError("Ja existe outro modelo com esse atalho.")
        now = time.time()
        if key:
            c.execute("""UPDATE message_templates SET title=?,shortcut=?,shortcut_key=?,body=?,revision=revision+1,
                updated_at=?,updated_by=?,updated_by_name=? WHERE id=?""", (title, shortcut, shortcut_key, body, now, str(user), actor, key))
        else:
            key = uuid.uuid4().hex
            c.execute("""INSERT INTO message_templates(id,title,shortcut,shortcut_key,body,created_at,updated_at,updated_by,updated_by_name)
                VALUES(?,?,?,?,?,?,?,?,?)""", (key, title, shortcut, shortcut_key, body, now, now, str(user), actor))
        return store.row(c, "message_templates", key)


def archive(store, p, user, actor):
    with store.db() as c:
        c.execute("BEGIN IMMEDIATE")
        old = store.row(c, "message_templates", text(p.get("id"), 32))
        if old["archived_at"] is not None or old["revision"] != integer(p.get("revision"), 1, 2**31-1):
            raise Conflict("Este modelo foi alterado por outra pessoa. Atualize a lista antes de arquivar.")
        now = time.time()
        c.execute("""UPDATE message_templates SET archived_at=?,updated_at=?,updated_by=?,updated_by_name=?,revision=revision+1 WHERE id=?""",
                  (now, now, str(user), actor, old["id"]))


def restore(store, p, user, actor):
    with store.db() as c:
        c.execute("BEGIN IMMEDIATE")
        old = store.row(c, "message_templates", text(p.get("id"), 32))
        if old["archived_at"] is None or old["revision"] != integer(p.get("revision"), 1, 2**31-1):
            raise Conflict("Este modelo foi alterado por outra pessoa. Atualize a lista antes de restaurar.")
        if c.execute("SELECT 1 FROM message_templates WHERE shortcut_key=? AND archived_at IS NULL", (old["shortcut_key"],)).fetchone():
            raise ValueError("Ja existe outro modelo ativo com esse atalho. Altere o atalho do modelo ativo antes de restaurar.")
        if c.execute("SELECT count(*) FROM message_templates WHERE archived_at IS NULL").fetchone()[0] >= 500:
            raise ValueError("Limite de 500 modelos ativos atingido.")
        c.execute("""UPDATE message_templates SET archived_at=NULL,updated_at=?,updated_by=?,updated_by_name=?,revision=revision+1 WHERE id=?""",
                  (time.time(), str(user), actor, old["id"]))
