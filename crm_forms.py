"""Versioned public forms with private responses and a shared CRM contact identity."""
import base64
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import time
from urllib.parse import urlsplit
import uuid

from crm_access import pipeline_visible
from crm_store import Conflict, phone, text
from institute_report import email

TYPES = {"name", "email", "short", "long", "phone", "date", "url", "identity", "cpf", "cnpj", "number", "money", "address", "choice", "scale", "images", "dropdown", "file", "message", "terms", "welcome", "thanks"}
STATIC = {"message", "welcome", "thanks"}
FONTS = {"Inter", "Lato", "Georgia", "Arial", "Verdana", "Trebuchet MS"}
ID = re.compile(r"[a-zA-Z0-9_-]{1,64}\Z")


def short(value, maximum=200, required=False):
    return text(value, maximum, required)


def safe_url(value):
    value = short(value, 2000)
    parsed = urlsplit(value)
    if value and (parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password):
        raise ValueError("Informe um link http ou https valido.")
    return value


def definition(payload):
    if not isinstance(payload, dict):
        raise ValueError("Formulario invalido.")
    fields = payload.get("fields", [])
    if not isinstance(fields, list) or not 1 <= len(fields) <= 100:
        raise ValueError("Adicione de 1 a 100 campos.")
    cleaned, ids = [], set()
    for raw in fields:
        if not isinstance(raw, dict) or raw.get("type") not in TYPES or not ID.fullmatch(str(raw.get("id", ""))) or raw["id"] in ids:
            raise ValueError("Campo invalido ou repetido.")
        ids.add(raw["id"])
        options = raw.get("options", [])
        if not isinstance(options, list) or len(options) > 100:
            raise ValueError("Limite de 100 opcoes por campo.")
        choices = []
        for option in options:
            if isinstance(option, str):
                option = {"label": option}
            choices.append({"label": short(option.get("label", ""), 200, True), "image": short(option.get("image", ""), 64)})
        if raw["type"] in {"choice", "images", "dropdown"} and not choices:
            raise ValueError("Adicione opcoes para a pergunta.")
        if len({o["label"] for o in choices}) != len(choices):
            raise ValueError("Ha opcoes repetidas na pergunta.")
        scale = raw.get("scale", 10)
        if type(scale) is not int or not 2 <= scale <= 10:
            raise ValueError("A escala deve ter entre 2 e 10 pontos.")
        cleaned.append({"id": raw["id"], "type": raw["type"], "label": short(raw.get("label", ""), 500, True),
                        "description": short(raw.get("description", ""), 3000), "required": raw.get("required") is True,
                        "multiple": raw.get("multiple") is True, "options": choices, "scale": scale,
                        "media": short(raw.get("media", ""), 64), "media_type": "video" if raw.get("media_type")=="video" else "image",
                        "button": short(raw.get("button", ""), 60)})
    theme = payload.get("theme", {})
    if not isinstance(theme,dict):
        raise ValueError("Tema invalido.")
    style = {}
    for key, default in (("background", "#101614"), ("text", "#f1f4f2"), ("answer", "#ffffff"), ("accent", "#b4d4c1")):
        value = theme.get(key, default)
        if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("Cor invalida.")
        style[key] = value
    style["font"] = theme.get("font", "Inter")
    if style["font"] not in FONTS:
        raise ValueError("Fonte invalida.")
    radius = theme.get("radius", 6)
    if type(radius) is not int or not 0 <= radius <= 24:
        raise ValueError("Bordas invalidas.")
    style.update(radius=radius, logo=short(theme.get("logo", ""), 64), image=short(theme.get("image", ""), 64))
    finish = payload.get("finish", {})
    if not isinstance(finish,dict):
        raise ValueError("Finalizacao invalida.")
    if finish.get("mode", "message") not in {"message", "redirect"}:
        raise ValueError("Finalizacao invalida.")
    finish = {"mode": finish.get("mode", "message"), "url": safe_url(finish.get("url", "")),
              "title": short(finish.get("title", "Recebemos suas respostas."), 200, True),
              "description": short(finish.get("description", "Obrigado pelo seu tempo."), 2000)}
    if finish["mode"] == "redirect" and not finish["url"]:
        raise ValueError("Informe o link de redirecionamento.")
    emails = payload.get("emails", [])
    if not isinstance(emails, list) or len(emails) > 20 or any(not isinstance(e, str) or not email(e) for e in emails):
        raise ValueError("Informe ate 20 e-mails validos para notificacoes.")
    crm = payload.get("crm", {})
    if not isinstance(crm,dict):
        raise ValueError("Destino do formulario invalido.")
    mapping = {key: short(crm.get(key, ""), 64) for key in ("pipeline_id", "stage_id", "name_field", "phone_field", "email_field")}
    mapping["enabled"] = crm.get("enabled") is True
    if mapping["enabled"]:
        indexed = {f["id"]: f for f in cleaned}
        for key, allowed in (("name_field", {"name", "short"}), ("phone_field", {"phone"})):
            item = indexed.get(mapping[key])
            if not item or item["type"] not in allowed or not item["required"]:
                raise ValueError("Para criar oportunidades, vincule nome e telefone a campos obrigatorios.")
        if mapping["email_field"] and indexed.get(mapping["email_field"], {}).get("type") != "email":
            raise ValueError("Vincule o e-mail a um campo do tipo e-mail.")
    return {"title": short(payload.get("title", ""), 120, True), "fields": cleaned, "theme": style, "finish": finish,
            "emails": list(dict.fromkeys(email(e) for e in emails)), "crm": mapping,
            "google_enabled": payload.get("google_enabled") is True}


def document_number(value, kind):
    digits = re.sub(r"\D", "", value)
    size = 11 if kind == "cpf" else 14
    if len(digits) != size or len(set(digits)) == 1:
        raise ValueError("Documento invalido.")
    body = digits[:-2]
    for weights in ((list(range(10, 1, -1)), list(range(11, 1, -1))) if kind == "cpf" else
                    ([5,4,3,2,9,8,7,6,5,4,3,2], [6,5,4,3,2,9,8,7,6,5,4,3,2])):
        remainder = sum(int(n)*w for n,w in zip(body, weights)) % 11
        body += str(0 if remainder < 2 else 11-remainder)
    if body != digits:
        raise ValueError("Documento invalido.")
    return digits


def answers_for(info, supplied):
    if not isinstance(supplied, dict) or set(supplied) - {f["id"] for f in info["fields"]}:
        raise ValueError("Respostas invalidas.")
    result = {}
    for field in info["fields"]:
        key, kind = field["id"], field["type"]
        if kind in STATIC:
            continue
        value = supplied.get(key)
        if value in (None, "", []):
            if field["required"]:
                raise ValueError("Preencha: " + field["label"])
            continue
        if kind == "terms":
            if type(value) is not bool or field["required"] and not value:
                raise ValueError("Confirme os termos: " + field["label"])
        elif kind == "address":
            if not isinstance(value, dict) or set(value) - {"street", "number", "extra", "district", "city", "state", "zip"}:
                raise ValueError("Endereco invalido.")
            value = {k: short(v, 200) for k,v in value.items()}
            if field["required"] and not all(value.get(k) for k in ("street", "city", "state")):
                raise ValueError("Complete o endereco.")
        elif kind in {"choice", "images", "dropdown"}:
            options = {o["label"] for o in field["options"]}
            if field["multiple"]:
                if not isinstance(value, list) or len(value) != len(set(value)) or any(not isinstance(v,str) or v not in options for v in value):
                    raise ValueError("Selecao invalida.")
            elif not isinstance(value,str) or value not in options:
                raise ValueError("Selecao invalida.")
        elif kind == "file":
            if not isinstance(value,list) or not 1 <= len(value) <= 5 or any(not isinstance(v,str) or not re.fullmatch(r"[a-f0-9]{32}",v) for v in value):
                raise ValueError("Anexo invalido.")
        elif kind == "scale":
            if type(value) is not int or not 0 <= value <= field["scale"]:
                raise ValueError("Nota invalida.")
        else:
            value = short(value, 10000 if kind == "long" else 500, True)
            if kind == "email":
                value = email(value)
                if not value:
                    raise ValueError("E-mail invalido.")
            elif kind == "phone":
                number = re.sub(r"\D", "", value)
                value = phone("55" + number if len(number) in {10,11} else number)
            elif kind in {"cpf", "cnpj"}:
                value = document_number(value,kind)
            elif kind == "date":
                value = date.fromisoformat(value).isoformat()
            elif kind == "url":
                value = safe_url(value)
            elif kind in {"number", "money"}:
                try:
                    n = Decimal(value.replace(",", "."))
                    if not n.is_finite() or abs(n) > Decimal("1000000000000"):
                        raise InvalidOperation()
                    value = str(n.quantize(Decimal("0.01"))) if kind == "money" else str(n)
                except InvalidOperation:
                    raise ValueError("Numero invalido.") from None
        result[key] = value
    return result


class FormStore:
    def __init__(self, crm):
        self.crm = crm
        self.root = crm.path.parent / "forms"
        self.root.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.secret = self.key("signing.key", lambda: secrets.token_bytes(32))
        with crm.db() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS crm_forms(id TEXT PRIMARY KEY,doctor_id TEXT NOT NULL REFERENCES doctors(id),
                    name TEXT NOT NULL,slug TEXT UNIQUE NOT NULL,draft TEXT NOT NULL,published TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'draft',revision INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,updated_at REAL NOT NULL,created_by TEXT NOT NULL,
                    institute_key TEXT NOT NULL DEFAULT '',course_key TEXT NOT NULL DEFAULT '',
                    google_token TEXT NOT NULL DEFAULT '',sheet_id TEXT NOT NULL DEFAULT '',sheet_columns TEXT NOT NULL DEFAULT '[]');
                CREATE TABLE IF NOT EXISTS form_responses(id TEXT PRIMARY KEY,form_id TEXT NOT NULL REFERENCES crm_forms(id),
                    nonce TEXT NOT NULL,created_at REAL NOT NULL,definition TEXT NOT NULL,answers TEXT NOT NULL,
                    lead_id TEXT NOT NULL DEFAULT '',lead_state TEXT NOT NULL DEFAULT '',row_number INTEGER NOT NULL,
                    UNIQUE(form_id,nonce));
                CREATE TABLE IF NOT EXISTS form_uploads(id TEXT PRIMARY KEY,form_id TEXT NOT NULL REFERENCES crm_forms(id),
                    field_id TEXT NOT NULL,nonce TEXT NOT NULL,purpose TEXT NOT NULL,name TEXT NOT NULL,mime TEXT NOT NULL,
                    size INTEGER NOT NULL,created_at REAL NOT NULL,response_id TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS form_deliveries(id INTEGER PRIMARY KEY,response_id TEXT NOT NULL REFERENCES form_responses(id),
                    kind TEXT NOT NULL,target TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'queued',attempts INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL,last_error TEXT NOT NULL DEFAULT '',UNIQUE(response_id,kind,target));
                CREATE TABLE IF NOT EXISTS form_rates(bucket TEXT PRIMARY KEY,total INTEGER NOT NULL,expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS form_oauth(state TEXT PRIMARY KEY,user_id TEXT NOT NULL,form_id TEXT NOT NULL,
                    expires REAL NOT NULL,verifier TEXT NOT NULL);
            """)

    def key(self, name, generate):
        path = self.root / name
        try:
            with path.open("xb") as target:
                os.chmod(path, 0o600)
                target.write(generate())
        except FileExistsError:
            pass
        return path.read_bytes()

    def owned(self, c, ident, doctor=None):
        row = c.execute("SELECT * FROM crm_forms WHERE id=?", (ident,)).fetchone()
        if not row or doctor is not None and row["doctor_id"] != doctor:
            raise LookupError("Formulario nao encontrado.")
        row = dict(row)
        if not pipeline_visible(row):
            raise PermissionError("Seu usuario nao possui acesso ao instituto deste formulario.")
        return row

    def public(self, slug):
        with self.crm.db() as c:
            row = c.execute("SELECT * FROM crm_forms WHERE slug=? AND status='published'", (slug,)).fetchone()
        if not row:
            raise LookupError("Este formulario nao esta recebendo respostas.")
        return dict(row), json.loads(row["published"])

    @staticmethod
    def metadata(row):
        return {k:row[k] for k in ("id","doctor_id","name","slug","status","revision","updated_at","created_at","institute_key","course_key","sheet_id")} | {"google_connected": bool(row["google_token"])}

    def listing(self, doctor):
        with self.crm.db() as c:
            self.crm.row(c,"doctors",doctor)
            rows = [dict(r) for r in c.execute("SELECT f.*,(SELECT count(*) FROM form_responses WHERE form_id=f.id) responses FROM crm_forms f WHERE doctor_id=? ORDER BY updated_at DESC", (doctor,)) if pipeline_visible(dict(r))]
            return [{**self.metadata(r),"responses":r["responses"]} for r in rows]

    def detail(self, ident, doctor):
        with self.crm.db() as c:
            row = self.owned(c,ident,doctor)
        return {**self.metadata(row), "definition": json.loads(row["draft"]), "has_unpublished_changes": bool(row["published"] and row["published"] != row["draft"])}

    def save(self, payload, actor):
        info = definition(payload.get("definition"))
        ident, now = payload.get("id") or uuid.uuid4().hex, time.time()
        with self.crm.db() as c:
            c.execute("BEGIN IMMEDIATE")
            doctor = payload.get("doctor_id")
            self.crm.row(c,"doctors",doctor)
            binding = {"institute_key":"","course_key":""}
            if info["crm"]["pipeline_id"]:
                binding = self.crm.owned(c,"pipelines",info["crm"]["pipeline_id"],doctor)
                stage = self.crm.row(c,"stages",info["crm"]["stage_id"])
                if stage["pipeline_id"] != binding["id"]:
                    raise ValueError("A fase nao pertence ao funil selecionado.")
            if payload.get("id"):
                row = self.owned(c,ident,doctor)
                if row["revision"] != payload.get("revision"):
                    raise Conflict("O formulario mudou. Atualize antes de salvar.")
                if row["published"] and (row["institute_key"],row["course_key"]) != (binding["institute_key"],binding["course_key"]):
                    raise ValueError("Formularios publicados nao podem mudar entre clinica e instituto. Crie outro formulario.")
                revision = row["revision"] + 1
            else:
                revision = 1
            info["revision"] = revision
            assets = self.assets(info)
            for asset in assets:
                if not c.execute("SELECT 1 FROM form_uploads WHERE id=? AND form_id=? AND purpose='asset'", (asset,ident)).fetchone():
                    raise ValueError("Imagem nao pertence a este formulario.")
            encoded = json.dumps(info,ensure_ascii=False)
            if payload.get("id"):
                c.execute("UPDATE crm_forms SET name=?,draft=?,revision=?,updated_at=?,institute_key=?,course_key=? WHERE id=?",
                          (info["title"],encoded,revision,now,binding["institute_key"],binding["course_key"],ident))
            else:
                slug = re.sub(r"[^a-z0-9]+","-",info["title"].lower()).strip("-")[:40] or "formulario"
                slug += "-" + secrets.token_hex(4)
                c.execute("INSERT INTO crm_forms(id,doctor_id,name,slug,draft,created_at,updated_at,created_by,institute_key,course_key) VALUES(?,?,?,?,?,?,?,?,?,?)",
                          (ident,doctor,info["title"],slug,encoded,now,now,str(actor),binding["institute_key"],binding["course_key"]))
        return self.detail(ident,doctor)

    @staticmethod
    def assets(info):
        return set(filter(None,[info["theme"].get("logo"),info["theme"].get("image"),*[f.get("media") for f in info["fields"]],*[o.get("image") for f in info["fields"] for o in f["options"]]]))

    def publish(self, ident, doctor, revision, status):
        if status not in {"published","paused","archived"}:
            raise ValueError("Situacao invalida.")
        with self.crm.db() as c:
            c.execute("BEGIN IMMEDIATE")
            row = self.owned(c,ident,doctor)
            if row["revision"] != revision:
                raise Conflict("O formulario mudou. Atualize antes de publicar.")
            info = json.loads(row["draft"])
            if status == "published":
                if not any(f["type"] not in STATIC for f in info["fields"]):
                    raise ValueError("Adicione pelo menos uma pergunta.")
                if info["emails"]:
                    from crm_form_integrations import mail_ready
                    if not mail_ready():
                        raise ValueError("Configure o servidor de e-mail antes de ativar notificacoes.")
                if info["google_enabled"] and not row["sheet_id"]:
                    raise ValueError("Conecte o Google e crie a planilha antes de publicar.")
                columns = json.loads(row["sheet_columns"])
                for f in info["fields"]:
                    if f["type"] not in STATIC and not any(o["id"]==f["id"] for o in columns):
                        columns.append({"id":f["id"],"label":f["label"]})
                c.execute("UPDATE crm_forms SET published=draft,sheet_columns=? WHERE id=?", (json.dumps(columns),ident))
            c.execute("UPDATE crm_forms SET status=?,updated_at=? WHERE id=?", (status,time.time(),ident))

    def token(self, row, info):
        data = base64.urlsafe_b64encode(json.dumps({"id":row["id"],"rev":info["revision"],"at":int(time.time()),"nonce":secrets.token_hex(16)}).encode()).decode().rstrip("=")
        return data + "." + hmac.new(self.secret,data.encode(),hashlib.sha256).hexdigest()

    def verify_token(self, row, info, token):
        try:
            data, signature = token.split(".")
            if not hmac.compare_digest(signature,hmac.new(self.secret,data.encode(),hashlib.sha256).hexdigest()):
                raise ValueError()
            state = json.loads(base64.urlsafe_b64decode(data+"="*(-len(data)%4)))
            if state["id"] != row["id"] or state["rev"] != info["revision"] or not 0 <= time.time()-state["at"] <= 14400 or not re.fullmatch(r"[a-f0-9]{32}",state["nonce"]):
                raise ValueError()
            return state["nonce"]
        except (ValueError,KeyError,TypeError,UnicodeError):
            raise ValueError("Sessao do formulario expirada. Atualize a pagina.") from None

    def rate(self, c, ident, remote, operation, amount=1):
        now=time.time();window=int(now//600)
        digest=hmac.new(self.secret,str(remote).encode(),hashlib.sha256).hexdigest()
        buckets=[(f"{ident}:{operation}:{digest}:{window}", 30 if operation=="submit" else 100),
                 (f"{ident}:{operation}:day:{int(now//86400)}", 2000 if operation=="submit" else 2000)]
        c.execute("DELETE FROM form_rates WHERE expires<?",(now,))
        for key,limit in buckets:
            current=c.execute("SELECT total FROM form_rates WHERE bucket=?",(key,)).fetchone()
            if current and current[0]+amount>limit:
                raise ValueError("Muitas solicitacoes. Tente novamente mais tarde.")
            c.execute("INSERT INTO form_rates VALUES(?,?,?) ON CONFLICT(bucket) DO UPDATE SET total=total+excluded.total",(key,amount,now+86400))

    def submit(self, slug, payload, remote):
        if not isinstance(payload,dict):
            raise ValueError("Envio invalido.")
        row, info = self.public(slug)
        nonce = self.verify_token(row,info,payload.get("token", ""))
        if payload.get("website"):
            raise ValueError("Nao foi possivel enviar o formulario.")
        answers = answers_for(info,payload.get("answers"))
        ident, now = uuid.uuid4().hex, time.time()
        with self.crm.db() as c:
            c.execute("BEGIN IMMEDIATE")
            current=c.execute("SELECT status,published FROM crm_forms WHERE id=?",(row["id"],)).fetchone()
            if current["status"]!="published" or current["published"]!=row["published"]:
                raise Conflict("O formulario foi atualizado. Atualize a pagina.")
            old=c.execute("SELECT id FROM form_responses WHERE form_id=? AND nonce=?",(row["id"],nonce)).fetchone()
            if old:
                return {"id":old[0],"finish":info["finish"]}
            self.rate(c,row["id"],remote,"submit")
            for field in info["fields"]:
                if field["type"]=="file":
                    for asset in answers.get(field["id"],[]):
                        uploaded=c.execute("SELECT * FROM form_uploads WHERE id=? AND form_id=? AND field_id=? AND nonce=? AND purpose='answer' AND response_id=''",(asset,row["id"],field["id"],nonce)).fetchone()
                        if not uploaded:
                            raise ValueError("Anexo nao pertence a esta resposta.")
                        c.execute("UPDATE form_uploads SET response_id=? WHERE id=?",(ident,asset))
            lead, lead_state = "", "disabled"
            config = info["crm"]
            if config["enabled"]:
                pipeline=self.crm.owned(c,"pipelines",config["pipeline_id"],row["doctor_id"])
                stage=self.crm.row(c,"stages",config["stage_id"])
                if stage["pipeline_id"]!=pipeline["id"]:
                    raise ValueError("O funil do formulario precisa ser revisado.")
                name=answers[config["name_field"]];number=answers[config["phone_field"]]
                contact=self.crm.contact(c,number,name,clinical=not pipeline["course_key"])
                existing=c.execute("SELECT id FROM leads WHERE pipeline_id=? AND contact_id=?",(pipeline["id"],contact["id"])).fetchone()
                lead=existing[0] if existing else uuid.uuid4().hex
                lead_state="reused" if existing else "created"
                if not existing:
                    c.execute("INSERT INTO leads(id,doctor_id,pipeline_id,stage_id,contact_id,title,created_at,updated_at,email,source_name) VALUES(?,?,?,?,?,?,?,?,?,?)",(lead,row["doctor_id"],pipeline["id"],stage["id"],contact["id"],info["title"],now,now,answers.get(config["email_field"],""),name if pipeline["course_key"] else ""))
                    from crm_bots import emit
                    emit(c,lead,"created",now)
                self.crm.event(c,lead,"Formulario","form_response",f"Nova resposta: {info['title']} · resposta {ident}",now)
            number=c.execute("SELECT coalesce(max(row_number),1)+1 FROM form_responses WHERE form_id=?",(row["id"],)).fetchone()[0]
            c.execute("INSERT INTO form_responses VALUES(?,?,?,?,?,?,?,?,?)",(ident,row["id"],nonce,now,row["published"],json.dumps(answers,ensure_ascii=False),lead,lead_state,number))
            for address in info["emails"]:
                c.execute("INSERT INTO form_deliveries(response_id,kind,target,updated_at) VALUES(?,'email',?,?)",(ident,address,now))
            if info["google_enabled"]:
                c.execute("INSERT INTO form_deliveries(response_id,kind,target,updated_at) VALUES(?,'google',?,?)",(ident,row["sheet_id"],now))
        return {"id":ident,"finish":info["finish"]}

    def responses(self, ident, doctor, page=1):
        page=int(page)
        if not 1 <= page <= 100000:
            raise ValueError("Pagina invalida.")
        with self.crm.db() as c:
            self.owned(c,ident,doctor)
            total=c.execute("SELECT count(*) FROM form_responses WHERE form_id=?",(ident,)).fetchone()[0]
            rows=[dict(r) for r in c.execute("SELECT * FROM form_responses WHERE form_id=? ORDER BY created_at DESC LIMIT 30 OFFSET ?",(ident,(page-1)*30))]
            for row in rows:
                row["answers"]=json.loads(row["answers"]);row["definition"]=json.loads(row["definition"])
                row.pop("nonce",None)
                row["deliveries"]=[dict(r) for r in c.execute("SELECT kind,target,status,last_error FROM form_deliveries WHERE response_id=?",(row["id"],))]
                row["files"]=[dict(r) for r in c.execute("SELECT id,name,mime,size FROM form_uploads WHERE response_id=?",(row["id"],))]
        return {"items":rows,"total":total,"page":page,"has_more":page*30<total}

    def prune_uploads(self):
        with self.crm.db() as c:
            rows=c.execute("SELECT id FROM form_uploads WHERE purpose='answer' AND response_id='' AND created_at<?",(time.time()-86400,)).fetchall()
            for row in rows:
                (self.root/row[0]).unlink(missing_ok=True)
                c.execute("DELETE FROM form_uploads WHERE id=?",(row[0],))

    def upload(self, form, field, name, content, purpose="answer", nonce="", remote=""):
        from crm_media import validate, TYPES as MEDIA_TYPES
        if len(content)>10*1024*1024 or purpose not in {"asset","answer"}:
            raise ValueError("Limite de 10 MB por arquivo.")
        kind=MEDIA_TYPES.get(Path(name).suffix.lower(),("",))[0]
        if purpose=="asset" and kind not in {"image","video"}:
            raise ValueError("Use uma imagem ou video neste campo.")
        name,mime=validate(name,kind,content)
        ident=uuid.uuid4().hex
        path=self.root/ident
        try:
            with self.crm.db() as c:
                c.execute("BEGIN IMMEDIATE")
                if purpose=="answer":
                    self.rate(c,form,remote,"upload")
                    total=c.execute("SELECT coalesce(sum(size),0),count(*) FROM form_uploads WHERE form_id=? AND nonce=?",(form,nonce)).fetchone()
                    if total[0]+len(content)>50*1024*1024 or total[1]>=20:
                        raise ValueError("Limite de anexos desta resposta atingido.")
                total=c.execute("SELECT coalesce(sum(size),0) FROM form_uploads WHERE created_at>?",(time.time()-86400,)).fetchone()[0]
                if total+len(content)>500*1024*1024:
                    raise ValueError("Limite diario de anexos atingido.")
                with path.open("xb") as target:
                    os.chmod(path,0o600);target.write(content)
                c.execute("INSERT INTO form_uploads VALUES(?,?,?,?,?,?,?,?,?,?)",(ident,form,field,nonce,purpose,name,mime,len(content),time.time(),""))
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return {"id":ident,"name":name,"mime":mime,"size":len(content)}
