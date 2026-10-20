"""Private Google connections scoped to one CRM owner and activity."""
import json
import time
import uuid

from crm_access import activity_visible
from institute_report import email


def schema(c):
    c.execute("""CREATE TABLE IF NOT EXISTS form_google_accounts(
        id TEXT PRIMARY KEY,doctor_id TEXT NOT NULL REFERENCES doctors(id),institute_key TEXT NOT NULL DEFAULT '',
        google_token TEXT NOT NULL DEFAULT '',google_email TEXT NOT NULL DEFAULT '',google_scopes TEXT NOT NULL DEFAULT '',
        default_emails TEXT NOT NULL DEFAULT '[]',updated_at REAL NOT NULL,
        UNIQUE(doctor_id,institute_key))""")


def owned(forms,c,ident,doctor=None):
    row=c.execute("SELECT * FROM form_google_accounts WHERE id=?",(ident,)).fetchone()
    if not row or doctor is not None and row['doctor_id']!=doctor:
        raise LookupError('Conexao Google nao encontrada.')
    row=dict(row)
    if not activity_visible(row):
        raise PermissionError('Conexao indisponivel para esta atividade.')
    return row


def metadata(row):
    from crm_form_integrations import MAIL_SCOPE
    return {k:row[k] for k in ('id','doctor_id','institute_key','google_email','updated_at')} | {
        'connected':bool(row['google_token']),
        'mail_ready':bool(row['google_token'] and row['google_email'] and MAIL_SCOPE in row['google_scopes'].split()),
        'default_emails':json.loads(row['default_emails'])}


def listing(forms,doctor=None):
    with forms.crm.db() as c:
        if doctor is not None:
            forms.crm.row(c,'doctors',doctor)
        return [metadata(dict(row)) for row in c.execute('SELECT * FROM form_google_accounts ORDER BY updated_at DESC')
                if (doctor is None or row['doctor_id']==doctor) and activity_visible(dict(row))]


def prepare(forms,doctor,pipeline,recipients):
    if not isinstance(recipients,list) or len(recipients)>20 or any(not isinstance(e,str) or not email(e) for e in recipients):
        raise ValueError('Informe ate 20 e-mails validos.')
    with forms.crm.db() as c:
        c.execute('BEGIN IMMEDIATE')
        forms.crm.row(c,'doctors',doctor)
        activity=forms.crm.owned(c,'pipelines',pipeline,doctor)['institute_key'] if pipeline else ''
        row=c.execute('SELECT id FROM form_google_accounts WHERE doctor_id=? AND institute_key=?',(doctor,activity)).fetchone()
        ident=row['id'] if row else uuid.uuid4().hex
        if not row:
            c.execute('INSERT INTO form_google_accounts(id,doctor_id,institute_key,updated_at) VALUES(?,?,?,?)',(ident,doctor,activity,time.time()))
        c.execute('UPDATE form_google_accounts SET default_emails=?,updated_at=? WHERE id=?',
                  (json.dumps(list(dict.fromkeys(email(e) for e in recipients))),time.time(),ident))
        return owned(forms,c,ident,doctor)


def resolve(forms,c,row):
    if not row.get('google_account_id'):
        return row
    account=owned(forms,c,row['google_account_id'],row['doctor_id'])
    if account['institute_key']!=row['institute_key']:
        raise PermissionError('A conta Google pertence a outra atividade.')
    return row | {k:account[k] for k in ('google_token','google_email','google_scopes')} | {'_account_id':account['id']}


def attach(forms,ident,doctor,account_id):
    with forms.crm.db() as c:
        c.execute('BEGIN IMMEDIATE')
        form=forms.owned(c,ident,doctor);account=owned(forms,c,account_id,doctor)
        if not account['google_token'] or account['institute_key']!=form['institute_key']:
            raise ValueError('Selecione uma conta conectada da mesma atividade.')
        if form['sheet_id'] and form['google_email']!=account['google_email']:
            raise ValueError('A planilha vinculada pertence a outra conta. Mantenha a conexao atual.')
        c.execute('UPDATE crm_forms SET google_account_id=? WHERE id=?',(account_id,ident))
    return forms.detail(ident,doctor)
