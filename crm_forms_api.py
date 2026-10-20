"""Narrow anonymous submission routes; all responses and answer files require CRM access."""
import csv
import io
import json
from pathlib import Path
import re
import threading
from urllib.parse import parse_qs, unquote, urlsplit, urlencode

from crm_forms import FormStore
from task_api import read_body

STORES, LOCK = {}, threading.Lock()
PUBLIC = re.compile(r"/api/public-forms/([a-z0-9][a-z0-9-]{3,79})(?:/(upload|assets/([a-f0-9]{32})))?\Z")
PAGE = re.compile(r"/f/([a-z0-9][a-z0-9-]{3,79})\Z")


def store_for(crm):
    with LOCK:
        if crm.path not in STORES:
            STORES[crm.path]=FormStore(crm)
        return STORES[crm.path]


def send_file(handler, forms, row, public=False):
    data=(forms.root/row["id"]).read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type",row["mime"])
    handler.send_header("Content-Length",str(len(data)))
    handler.send_header("X-Content-Type-Options","nosniff")
    handler.send_header("Cache-Control","private, no-store" if not public else "public, max-age=3600")
    from urllib.parse import quote
    inline=public and row["mime"].startswith(("image/","video/"))
    handler.send_header("Content-Disposition",("inline" if inline else "attachment")+"; filename*=UTF-8''"+quote(row["name"],safe=""))
    handler.end_headers();handler.wfile.write(data)


def public_request(handler):
    parsed=urlsplit(handler.path)
    match=PUBLIC.fullmatch(parsed.path)
    page=PAGE.fullmatch(parsed.path)
    if not match and not page:
        return False
    if page:
        if handler.command!="GET":
            handler.auth_json({"ok":False},405)
        else:
            data=(Path(__file__).parent/"static"/"crm-form.html").read_bytes()
            handler.send_response(200);handler.send_header("Content-Type","text/html; charset=utf-8")
            handler.send_header("Content-Length",str(len(data)));handler.send_header("Cache-Control","no-store")
            handler.send_header("Referrer-Policy","strict-origin-when-cross-origin")
            handler.end_headers();handler.wfile.write(data)
        return True
    from crm_api import store_for as crm_for
    forms=store_for(crm_for(handler.server.auth_store))
    slug,operation,asset=match.groups()
    try:
        row,info=forms.public(slug)
        if handler.command=="GET" and asset:
            if asset not in forms.assets(info):
                raise LookupError()
            with forms.crm.db() as c:
                uploaded=c.execute("SELECT * FROM form_uploads WHERE id=? AND form_id=? AND purpose='asset'",(asset,row["id"])).fetchone()
            if not uploaded:
                raise LookupError()
            send_file(handler,forms,dict(uploaded),True)
        elif handler.command=="GET" and not operation:
            from crm_attribution import config
            handler.auth_json({"ok":True,"form":{k:info[k] for k in ("title","fields","theme","finish","revision")} | {"tracking":config(info.get("tracking"))},"token":forms.token(row,info)})
        elif handler.command=="POST" and operation in {None,"upload"}:
            if not handler.same_origin_request():
                handler.auth_json({"ok":False,"error":"Origem invalida."},403);return True
            if operation=="upload":
                nonce=forms.verify_token(row,info,handler.headers.get("X-Form-Token",""))
                field=parse_qs(parsed.query).get("field",[""])[0]
                if not any(f["id"]==field and f["type"]=="file" for f in info["fields"]):
                    raise ValueError("Campo de anexo invalido.")
                data=read_body(handler,10*1024*1024)
                result=forms.upload(row["id"],field,unquote(handler.headers.get("X-File-Name","")),data,nonce=nonce,remote=handler.client_address[0])
                handler.auth_json({"ok":True,"file":result})
            else:
                payload=json.loads(read_body(handler,512*1024))
                result=forms.submit(slug,payload,handler.client_address[0])
                from crm_form_integrations import start_worker
                start_worker(forms)
                handler.auth_json({"ok":True,**result})
        else:
            handler.auth_json({"ok":False},405)
    except LookupError:
        handler.auth_json({"ok":False,"error":"Formulario ou arquivo nao disponivel."},404)
    except ValueError as exc:
        handler.auth_json({"ok":False,"error":str(exc)},400)
    except (TypeError,UnicodeError,KeyError):
        handler.auth_json({"ok":False,"error":"Nao foi possivel validar o envio. Confira os campos e atualize a pagina se necessario."},400)
    return True


def handle(handler, crm, action, query, user):
    forms=store_for(crm)
    doctor=query.get("doctor",[""])[0]
    ident=query.get("id",[""])[0]
    if handler.command=="GET":
        if action=='forms/google-settings':
            if not user.get('is_master'):
                raise PermissionError('Configuracao exclusiva do Master.')
            from crm_form_accounts import listing
            from crm_form_integrations import capabilities
            from crm_access import pipeline_visible
            with crm.db() as c:
                if doctor:
                    crm.row(c,'doctors',doctor)
                doctors=[dict(r) for r in c.execute('SELECT * FROM doctors ORDER BY name')]
                pipelines=[dict(r) for r in c.execute('SELECT * FROM pipelines ORDER BY name')
                           if (not doctor or r['doctor_id']==doctor) and pipeline_visible(dict(r))]
            return handler.auth_json({'ok':True,'accounts':listing(forms,doctor or None),'doctors':doctors,
                                      'pipelines':pipelines,'integrations':capabilities()})
        if action=="forms":
            from crm_form_integrations import capabilities,start_worker
            from crm_form_accounts import listing
            start_worker(forms)
            accounts=listing(forms,doctor)
            config=capabilities();config['default_emails']=next((a['default_emails'] for a in accounts if not a['institute_key']),[])
            return handler.auth_json({"ok":True,"forms":forms.listing(doctor),"accounts":accounts,"integrations":config})
        if action=="forms/item":
            return handler.auth_json({"ok":True,"form":forms.detail(ident,doctor)})
        if action=="forms/responses":
            return handler.auth_json({"ok":True,**forms.responses(ident,doctor,query.get("page",[1])[0])})
        if action=="forms/file":
            with crm.db() as c:
                row=c.execute("SELECT * FROM form_uploads WHERE id=?",(ident,)).fetchone()
                if not row:
                    raise LookupError()
                forms.owned(c,row["form_id"],doctor)
            return send_file(handler,forms,dict(row))
        if action=="forms/export":
            with crm.db() as c:
                form=forms.owned(c,ident,doctor)
                rows=[dict(r) for r in c.execute("SELECT * FROM form_responses WHERE form_id=? ORDER BY row_number",(ident,))]
            columns=[]
            for row in rows:
                for field in json.loads(row["definition"])["fields"]:
                    if field["type"] not in {"welcome","thanks","message"} and not any(f["id"]==field["id"] for f in columns):
                        columns.append(field)
            buffer=io.StringIO();writer=csv.writer(buffer)
            from crm_attribution import EXPORT_HEADERS,export_cells
            writer.writerow(["Resposta ID","Enviado em",*EXPORT_HEADERS,*[f["label"] for f in columns]])
            def cell(value):
                value=json.dumps(value,ensure_ascii=False) if isinstance(value,(dict,list)) else str(value)
                return "'"+value if value.lstrip().startswith(("=","+","-","@")) else value
            from datetime import datetime,timezone
            for row in rows:
                answers=json.loads(row["answers"])
                writer.writerow([row["id"],datetime.fromtimestamp(row["created_at"],timezone.utc).isoformat(),*[cell(v) for v in export_cells(row["attribution"])],*[cell(answers.get(f["id"],"")) for f in columns]])
            data=b"\xef\xbb\xbf"+buffer.getvalue().encode()
            handler.send_response(200);handler.send_header("Content-Type","text/csv; charset=utf-8")
            handler.send_header("Content-Disposition",'attachment; filename="respostas.csv"')
            handler.send_header("Content-Length",str(len(data)));handler.send_header("Cache-Control","private, no-store")
            handler.end_headers();handler.wfile.write(data);return
        if action=="forms/google-callback":
            from crm_form_integrations import callback
            handler.requestline=handler.command+" "+urlsplit(handler.path).path+" "+handler.request_version
            if query.get("error"):
                raise ValueError("Conexao Google cancelada. Nenhuma planilha foi criada.")
            form=callback(forms,user["id"],query.get("state",[""])[0],query.get("code",[""])[0])
            destination="/crm.html?"+urlencode({"clinic":"vielle","mode":"forms","doctor":form["doctor_id"],"form":form["id"],"google":"connected"})
            if form.get('return_settings'):
                destination='/settings.html?'+urlencode({'clinic':'vielle','google':'connected','doctor':form['doctor_id']})+'#googleFormsIntegration'
            handler.send_response(303);handler.send_header("Location",destination);handler.send_header("Content-Length","0");handler.send_header("Referrer-Policy","no-referrer");handler.end_headers();return
        raise LookupError()
    if handler.command!="POST":
        return handler.auth_json({"ok":False},405)
    if not handler.same_origin_request():
        return handler.auth_json({"ok":False},403)
    if action=="forms/upload":
        with crm.db() as c:
            forms.owned(c,ident,doctor)
        result=forms.upload(ident,"",unquote(handler.headers.get("X-File-Name","")),read_body(handler,10*1024*1024),"asset")
        return handler.auth_json({"ok":True,"file":result})
    payload=json.loads(read_body(handler,512*1024))
    doctor=payload.get("doctor_id",doctor);ident=payload.get("id",ident)
    if action=='forms/google-account':
        if not user.get('is_master'):
            raise PermissionError('Configuracao exclusiva do Master.')
        from crm_form_accounts import prepare,metadata
        from crm_form_integrations import authorization
        account=prepare(forms,doctor,payload.get('pipeline_id',''),payload.get('default_emails',[]))
        handler.server.auth_store.audit_event('crm_google_account_settings',user['id'],details={'doctor_id':doctor,'id':account['id']})
        if payload.get('connect'):
            return handler.auth_json({'ok':True,'url':authorization(forms,account['id'],doctor,user['id'],mail=True,account=True)})
        return handler.auth_json({'ok':True,'account':metadata(account)})
    if action=="forms/save":
        return handler.auth_json({"ok":True,"form":forms.save(payload,user["id"])})
    with crm.db() as c:
        forms.owned(c,ident,doctor)
    if action=='forms/google-bind':
        from crm_form_accounts import attach
        form=attach(forms,ident,doctor,payload.get('account_id',''))
        handler.server.auth_store.audit_event('crm_form_google_bind',user['id'],details={'doctor_id':doctor,'id':ident,'account_id':form['google_account_id']})
        return handler.auth_json({'ok':True,'form':form})
    if action=="forms/publish":
        forms.publish(ident,doctor,payload.get("revision"),payload.get("status","published"))
        return handler.auth_json({"ok":True,"form":forms.detail(ident,doctor)})
    if action=="forms/google-connect":
        from crm_form_integrations import authorization
        return handler.auth_json({"ok":True,"url":authorization(forms,ident,doctor,user["id"],mail=payload.get("mail") is True)})
    if action=="forms/google-reuse":
        with crm.db() as c:
            c.execute("BEGIN IMMEDIATE")
            target=forms.owned(c,ident,doctor);source=forms.owned(c,payload.get("source_id"),doctor)
            if not source["google_token"] or target["institute_key"]!=source["institute_key"]:
                raise ValueError("Conta nao disponivel nesta atividade.")
            if target["sheet_id"] and target["google_email"]!=source["google_email"]:
                raise ValueError("Reconecte a conta atual para preservar a planilha vinculada.")
            c.execute("UPDATE crm_forms SET google_token=?,google_email=?,google_scopes=?,google_account_id='' WHERE id=?",(source["google_token"],source["google_email"],source["google_scopes"],ident))
        return handler.auth_json({"ok":True,"form":forms.detail(ident,doctor)})
    if action=="forms/test-email":
        if not user.get("is_master"):
            raise PermissionError("Teste de integracao exclusivo do Master.")
        from crm_form_integrations import email_delivery,form_mail_ready
        from institute_report import email
        import uuid
        with crm.db() as c:
            row=forms.owned(c,ident,doctor)
        address=email(payload.get("to",""))
        if not address or not form_mail_ready(row):
            raise ValueError("Conecte o remetente e informe um destinatario valido.")
        email_delivery(forms,{"target":address},row,{"id":"test-"+uuid.uuid4().hex})
        return handler.auth_json({"ok":True,"accepted":True})
    if action=="forms/google-sheet":
        from crm_form_integrations import create_sheet
        return handler.auth_json({"ok":True,"sheet_id":create_sheet(forms,ident,doctor)})
    if action=="forms/retry":
        response=payload.get("response_id")
        with crm.db() as c:
            if not c.execute("SELECT 1 FROM form_responses WHERE id=? AND form_id=?",(response,ident)).fetchone():
                raise LookupError()
            # Repeating a Google row update is idempotent; unconfirmed email sends are never retried here.
            c.execute("UPDATE form_deliveries SET status='queued',last_error='' WHERE response_id=? AND kind='google' AND status='failed'",(response,))
        return handler.auth_json({"ok":True})
    raise LookupError()
