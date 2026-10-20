"""Google OAuth and queued form delivery. Tokens never leave server-side storage."""
import base64
from datetime import datetime, timezone
from email.message import EmailMessage
import hashlib
import json
import os
import re
import secrets
import smtplib
import ssl
import threading
import time
from urllib.parse import urlencode, quote
from urllib.error import HTTPError, URLError
from urllib.request import Request, HTTPRedirectHandler, build_opener

WORKERS, LOCK = {}, threading.Lock()
DRIVE_SCOPE="https://www.googleapis.com/auth/drive.file"
MAIL_SCOPE="https://www.googleapis.com/auth/gmail.send"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def base_url():
    return os.getenv("DOC4DOCS_PUBLIC_URL", "https://doc4docs.com.br").rstrip("/")


def google_ready():
    return bool(os.getenv("CRM_GOOGLE_CLIENT_ID") and os.getenv("CRM_GOOGLE_CLIENT_SECRET"))


def mail_ready():
    return all(os.getenv(k) for k in ("CRM_SMTP_HOST","CRM_SMTP_FROM","CRM_SMTP_USER","CRM_SMTP_PASSWORD"))


def capabilities():
    from institute_report import email
    defaults=[]
    for item in os.getenv("CRM_FORM_DEFAULT_EMAILS","").split(","):
        try:
            address=email(item.strip())
            if address and address not in defaults:
                defaults.append(address)
        except ValueError:
            continue
    return {"google_ready":google_ready(),"mail_ready":mail_ready(),
            "default_emails":defaults[:10],
            "google_redirect_uri":base_url()+"/api/crm/forms/google-callback?clinic=vielle"}


def form_mail_ready(row, info=None):
    info=info or json.loads(row["draft"])
    provider=info.get("email_provider","auto")
    return provider in {"auto","smtp"} and mail_ready() or provider in {"auto","google"} and bool(row.get("google_token") and row.get("google_email") and MAIL_SCOPE in row.get("google_scopes","").split())


def cipher(forms):
    from cryptography.fernet import Fernet
    return Fernet(forms.key("oauth.key",Fernet.generate_key))


def http_json(url, method="GET", payload=None, token="", form=False):
    headers={"Accept":"application/json"}
    if token:
        headers["Authorization"]="Bearer "+token
    if payload is not None:
        headers["Content-Type"]="application/x-www-form-urlencoded" if form else "application/json"
        payload=urlencode(payload).encode() if form else json.dumps(payload).encode()
    request=Request(url,data=payload,headers=headers,method=method)
    try:
        with build_opener(NoRedirect).open(request,timeout=30) as response:
            return json.loads(response.read(4*1024*1024))
    except HTTPError as exc:
        raise ValueError("A integracao Google recusou a solicitacao (HTTP "+str(exc.code)+"). Confira a conta e as permissoes.") from None
    except (URLError,TimeoutError,OSError):
        raise ValueError("Nao foi possivel confirmar a resposta do Google. Tente novamente mais tarde.") from None


def authorization(forms, ident, doctor, user, *, mail=False, account=False):
    if not google_ready():
        raise ValueError("Configure CRM_GOOGLE_CLIENT_ID e CRM_GOOGLE_CLIENT_SECRET no servidor.")
    state, verifier=secrets.token_urlsafe(32),secrets.token_urlsafe(48)
    with forms.crm.db() as c:
        if account:
            from crm_form_accounts import owned
            owned(forms,c,ident,doctor)
        else:
            forms.owned(c,ident,doctor)
        c.execute("DELETE FROM form_oauth WHERE expires<?",(time.time(),))
        c.execute("INSERT INTO form_oauth VALUES(?,?,?,?,?)",(hashlib.sha256(state.encode()).hexdigest(),str(user),'account:'+ident if account else ident,time.time()+600,verifier))
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return "https://accounts.google.com/o/oauth2/v2/auth?"+urlencode({"client_id":os.environ["CRM_GOOGLE_CLIENT_ID"],
        "redirect_uri":capabilities()["google_redirect_uri"],"response_type":"code","scope":" ".join([DRIVE_SCOPE,"openid","email",*([MAIL_SCOPE] if mail else [])]),
        "include_granted_scopes":"true","access_type":"offline","prompt":"consent","state":state,"code_challenge":challenge,"code_challenge_method":"S256"})


def callback(forms, user, state, code):
    if not google_ready() or not isinstance(state,str) or len(state)>200 or not isinstance(code,str) or not 1<=len(code)<=4096:
        raise ValueError("Autorizacao Google invalida.")
    with forms.crm.db() as c:
        c.execute("BEGIN IMMEDIATE")
        digest=hashlib.sha256(state.encode()).hexdigest()
        row=c.execute("SELECT * FROM form_oauth WHERE state=?",(digest,)).fetchone()
        if not row or row["user_id"]!=str(user) or row["expires"]<time.time():
            raise ValueError("Autorizacao Google expirada ou de outro usuario.")
        row=dict(row)
        is_account=row['form_id'].startswith('account:')
        if is_account:
            from crm_form_accounts import owned
            form=owned(forms,c,row['form_id'].removeprefix('account:'))
        else:
            form=forms.owned(c,row["form_id"])
        c.execute("DELETE FROM form_oauth WHERE state=?",(digest,))
    token=http_json("https://oauth2.googleapis.com/token","POST",{"client_id":os.environ["CRM_GOOGLE_CLIENT_ID"],
        "client_secret":os.environ["CRM_GOOGLE_CLIENT_SECRET"],"code":code,"code_verifier":row["verifier"],
        "redirect_uri":capabilities()["google_redirect_uri"],"grant_type":"authorization_code"},form=True)
    if not token.get("refresh_token") or DRIVE_SCOPE not in token.get("scope","").split():
        raise ValueError("Autorize a criacao e atualizacao da planilha para concluir.")
    account={}
    if "email" in token.get("scope","").split() or "https://www.googleapis.com/auth/userinfo.email" in token.get("scope","").split():
        account=http_json("https://openidconnect.googleapis.com/v1/userinfo",token=token["access_token"])
    from institute_report import email
    address=email(account.get("email","")) if account.get("email_verified") is True else ""
    token["expires_at"]=time.time()+int(token.get("expires_in",3600))
    encoded=cipher(forms).encrypt(json.dumps(token).encode()).decode()
    with forms.crm.db() as c:
        if is_account:
            from crm_form_accounts import owned
            account=owned(forms,c,form['id'])
            if account['google_email'] and address!=account['google_email'] and c.execute("SELECT 1 FROM crm_forms WHERE google_account_id=? AND sheet_id!=''",(form['id'],)).fetchone():
                raise ValueError('A conta escolhida nao corresponde as planilhas vinculadas. Reconecte a conta atual.')
            c.execute('UPDATE form_google_accounts SET google_token=?,google_email=?,google_scopes=?,updated_at=? WHERE id=?',
                      (encoded,address,token.get('scope',''),time.time(),form['id']))
            c.execute("UPDATE form_deliveries SET status='queued',last_error='' WHERE kind='google' AND status='failed' AND response_id IN (SELECT r.id FROM form_responses r JOIN crm_forms f ON f.id=r.form_id WHERE f.google_account_id=?)",(form['id'],))
            return form | {'return_settings':True}
        forms.owned(c,form["id"])
        if form['google_email'] and address!=form['google_email'] and form['sheet_id']:
            raise ValueError('Reconecte a conta atual para preservar a planilha vinculada.')
        c.execute("UPDATE crm_forms SET google_token=?,google_email=?,google_scopes=?,google_account_id='' WHERE id=?",(encoded,address,token.get("scope",""),form["id"]))
        c.execute("UPDATE form_deliveries SET status='queued',last_error='' WHERE response_id IN (SELECT id FROM form_responses WHERE form_id=?) AND kind='google' AND status='failed'",(form["id"],))
    return form


def access_token(forms, row):
    if not row["google_token"]:
        raise ValueError("Conecte a conta Google deste formulario.")
    token=json.loads(cipher(forms).decrypt(row["google_token"].encode()))
    if token.get("expires_at",0)<time.time()+60:
        fresh=http_json("https://oauth2.googleapis.com/token","POST",{"client_id":os.environ["CRM_GOOGLE_CLIENT_ID"],
            "client_secret":os.environ["CRM_GOOGLE_CLIENT_SECRET"],"refresh_token":token["refresh_token"],"grant_type":"refresh_token"},form=True)
        token.update(fresh,expires_at=time.time()+int(fresh.get("expires_in",3600)))
        with forms.crm.db() as c:
            table='form_google_accounts' if row.get('_account_id') else 'crm_forms'
            c.execute(f"UPDATE {table} SET google_token=? WHERE id=?",(cipher(forms).encrypt(json.dumps(token).encode()).decode(),row.get('_account_id') or row['id']))
    return token["access_token"]


def create_sheet(forms, ident, doctor):
    with forms.crm.db() as c:
        row=forms.owned(c,ident,doctor)
        if row["sheet_id"]:
            return row["sheet_id"]
    token=access_token(forms,row)
    result=http_json("https://sheets.googleapis.com/v4/spreadsheets","POST",{"properties":{"title":row["name"]+" · DOC4DOCS"},
        "sheets":[{"properties":{"title":"Respostas","gridProperties":{"rowCount":10000,"columnCount":502,"frozenRowCount":1}}}]},token)
    ident_sheet=result.get("spreadsheetId","")
    if not re.fullmatch(r"[A-Za-z0-9_-]{10,200}",ident_sheet):
        raise ValueError("Nao foi possivel confirmar a criacao da planilha.")
    with forms.crm.db() as c:
        forms.owned(c,ident,doctor)
        c.execute("UPDATE crm_forms SET sheet_id=? WHERE id=? AND sheet_id=''",(ident_sheet,ident))
    return ident_sheet


def column_name(number):
    result=""
    while number:
        number,remainder=divmod(number-1,26);result=chr(65+remainder)+result
    return result


def sheet_delivery(forms, job, form, response):
    from crm_attribution import EXPORT_HEADERS,export_cells
    if form["sheet_id"]!=job["target"] or not re.fullmatch(r"[A-Za-z0-9_-]{10,200}",job["target"]):
        raise ValueError("Vinculo da planilha alterado. Revise a integracao.")
    token=access_token(forms,form)
    columns=json.loads(form["sheet_columns"])
    answers=json.loads(response["answers"])
    render=lambda value: json.dumps(value,ensure_ascii=False) if isinstance(value,(list,dict)) else str(value) if value is not None else ""
    values=[response["id"],datetime.fromtimestamp(response["created_at"],timezone.utc).isoformat(),*export_cells(response.get("attribution","{}")),*[render(answers.get(f["id"],"")) for f in columns]]
    root="https://sheets.googleapis.com/v4/spreadsheets/"+job["target"]+"/values/"
    for row,content in ((1,["Resposta ID","Enviado em",*EXPORT_HEADERS,*[f["label"]+" ["+f["id"]+"]" for f in columns]]),(response["row_number"],values)):
        address=f"Respostas!A{row}:{column_name(len(content))}{row}"
        http_json(root+quote(address,safe="")+"?valueInputOption=RAW","PUT",{"values":[content]},token)


def email_delivery(forms, job, form, response):
    info=json.loads(form["draft"])
    provider=info.get("email_provider","auto")
    use_google=provider=="google" or provider=="auto" and not mail_ready()
    if not form_mail_ready(form,info):
        raise ValueError("Servidor de e-mail nao configurado.")
    message=EmailMessage()
    message["Subject"]="Nova resposta · "+form["name"]
    message["From"]=form["google_email"] if use_google else os.environ["CRM_SMTP_FROM"]
    message["To"]=job["target"]
    message["Message-ID"]="<form-"+response["id"]+"-"+hashlib.sha256(job["target"].encode()).hexdigest()[:12]+"@doc4docs.com.br>"
    message.set_content("Uma nova resposta foi registrada no formulario "+form["name"]+".\n\nConsulte com seu acesso ao CRM:\n"+
        base_url()+"/crm.html?clinic=vielle&mode=forms&doctor="+form["doctor_id"]+"&form="+form["id"]+"&responses=1\n\nAs respostas e anexos permanecem privados no CRM.")
    if use_google:
        result=http_json("https://gmail.googleapis.com/gmail/v1/users/me/messages/send","POST",{"raw":base64.urlsafe_b64encode(message.as_bytes()).decode()},access_token(forms,form))
        if not result.get("id"):
            raise ValueError("Envio sem confirmacao do Gmail.")
        return
    port=int(os.getenv("CRM_SMTP_PORT","587"))
    factory=smtplib.SMTP_SSL if port==465 else smtplib.SMTP
    with factory(os.environ["CRM_SMTP_HOST"],port,timeout=25) as client:
        if port!=465:
            client.starttls(context=ssl.create_default_context())
        client.login(os.environ["CRM_SMTP_USER"],os.environ["CRM_SMTP_PASSWORD"])
        client.send_message(message)


def deliver_once(forms):
    with forms.crm.db() as c:
        c.execute("BEGIN IMMEDIATE")
        c.execute("UPDATE form_deliveries SET status='unknown',last_error='Execucao interrompida; confira antes de reenviar' WHERE status='processing' AND updated_at<?",(time.time()-180,))
        job=None
        for row in c.execute("SELECT * FROM form_deliveries WHERE status IN ('queued','blocked') ORDER BY CASE WHEN status='queued' THEN 0 ELSE 1 END,updated_at,id LIMIT 100").fetchall():
            candidate=dict(row)
            response=dict(c.execute("SELECT * FROM form_responses WHERE id=?",(candidate["response_id"],)).fetchone())
            form=forms.owned(c,response['form_id'])
            if candidate["kind"]=="email" and not form_mail_ready(form):
                c.execute("UPDATE form_deliveries SET status='blocked',last_error='Conecte o Gmail ou configure o servidor de e-mail',updated_at=? WHERE id=?",(time.time(),candidate["id"]))
                continue
            job=candidate;break
        if not job:
            return False
        c.execute("UPDATE form_deliveries SET status='processing',attempts=attempts+1,updated_at=? WHERE id=?",(time.time(),job["id"]))
    try:
        (email_delivery if job["kind"]=="email" else sheet_delivery)(forms,job,form,response)
        status,error="sent",""
    except Exception:
        status="unknown" if job["kind"]=="email" else "failed"
        error="Envio sem confirmacao. Confira o e-mail antes de reenviar." if job["kind"]=="email" else "Falha ao atualizar planilha. Confira a conexao Google."
    with forms.crm.db() as c:
        c.execute("UPDATE form_deliveries SET status=?,last_error=?,updated_at=? WHERE id=?",(status,error,time.time(),job["id"]))
    return True


def start_worker(forms):
    key=str(forms.crm.path)
    with LOCK:
        if key in WORKERS:
            return
        WORKERS[key]=True
    def run():
        cleaned=0
        while True:
            try:
                if time.time()-cleaned>3600:
                    forms.prune_uploads();cleaned=time.time()
                worked=deliver_once(forms)
            except Exception:
                worked=False
            time.sleep(.2 if worked else 5)
    threading.Thread(target=run,daemon=True,name="crm-form-deliveries").start()
