"""Isolated local UI fixture. No production databases, external API calls or keys."""
import argparse
import copy
import os
from pathlib import Path
import secrets
import sqlite3
import sys
import tempfile
import time
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--history-state', choices=('ok', 'partial', 'unavailable'), default='ok')
    parser.add_argument('--inventory-state', choices=('complete', 'partial'), default='complete')
    parser.add_argument('--comparison-state', choices=('complete', 'legacy', 'undated'), default='complete')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='doc4docs-brain-preview-') as directory:
        os.environ['DATA_DIR'] = directory
        import app
        from auth_store import AuthStore
        import system_brain as brain
        app.DATA_DIR = Path(directory)
        app.DB_PATH = app.DATA_DIR / 'preview.sqlite3'
        app.CONFIG_DEFAULTS = {}
        now = int(time.time())
        for clinic in ('vielle', 'inspire', 'carla'):
            with sqlite3.connect(brain.clinic_path(vars(app), clinic)) as conn:
                conn.executescript('''
                    CREATE TABLE sync_log(id INTEGER PRIMARY KEY,started_at INTEGER,finished_at INTEGER,ok INTEGER);
                    CREATE TABLE clinica_sync_log(id INTEGER PRIMARY KEY,started_at INTEGER,finished_at INTEGER,ok INTEGER);
                    CREATE TABLE clinica_sales(uuid TEXT,sale_date TEXT,total REAL);
                    CREATE TABLE clinica_bookings(uuid TEXT,starts_at TEXT,registered_at TEXT);
                ''')
                conn.execute('INSERT INTO sync_log VALUES(1,?,?,1)', (now-90, now-60))
                conn.execute('INSERT INTO clinica_sync_log VALUES(1,?,?,0)', (now-40, now-20))
                conn.execute("INSERT INTO clinica_sales VALUES('synthetic','2026-09-01',0)")
                conn.execute("INSERT INTO clinica_bookings VALUES('synthetic','2026-09-02',NULL)")
        store = AuthStore(app.DATA_DIR/'auth.sqlite3')
        store.initialize()
        password = secrets.token_urlsafe(24)
        user_id = store.create_user('Master - prévia local', 'preview', password, is_master=True)
        session = store.login('preview', password)
        model_config = lambda *_: ('preview-no-external-key', 'Demonstração local', 'Dados sintéticos')
        with patch.object(brain, 'openai_config', side_effect=model_config):
            snap = brain.snapshot(vars(app), 'vielle')
        analysis = {'clinic':'vielle', 'model':'Demonstração local', 'focus':'general',
            'summary':'Prévia com dados sintéticos. A última tentativa do Clínica Experts falhou e há um agendamento sem data de criação.',
            'findings':[{'title':'Verificar a última sincronização', 'kind':'observacao', 'priority':'media',
                'detail':'O registro local indica uma tentativa sem sucesso; não é um teste da API em tempo real.',
                'recommendation':'Conferir o histórico antes de uma nova tentativa.', 'evidence_ids':['experts_sync']}],
            'evidence':snap['evidence'], 'created_at':now, 'observed_at':now, 'fingerprint':snap['inventory']['fingerprint'],
            'inventory_status':snap['inventory']['status'],
            'diagnostic':{'database':'readable','datasets':snap['health']['datasets']}}
        previous = copy.deepcopy(analysis)
        previous.update(created_at=now-86400, observed_at=now-86400, fingerprint='previous-code')
        previous['evidence'] = [{'id':'experts_sync','state':'ok','message':'Última sincronização concluída','last_success':now-86500},
                                {'id':'booking_dates','state':'warning','message':'4 agendamentos sem data','count':4}]
        previous['diagnostic']['datasets'][0]['records'] = 9
        if args.comparison_state == 'legacy':
            previous.pop('inventory_status')
        elif args.comparison_state == 'undated':
            previous.pop('observed_at')
        brain.save_analysis(vars(app), 'vielle', user_id, previous)
        brain.save_analysis(vars(app), 'vielle', user_id, analysis)
        if args.history_state == 'partial':
            brain.save_analysis(vars(app), 'vielle', user_id, {'summary':'Synthetic invalid record','findings':{}})
        elif args.history_state == 'unavailable':
            with sqlite3.connect(brain.analysis_path(vars(app))) as conn:
                conn.execute('ALTER TABLE analyses RENAME TO preview_unreadable_analyses')

        class PreviewHandler(app.Handler):
            def do_GET(self):
                if urlparse(self.path).path == '/__preview__':
                    self.send_response(303)
                    self.send_header('Set-Cookie', f'doc4docs_session={session}; HttpOnly; SameSite=Lax; Path=/')
                    self.send_header('Location', '/master/brain.html')
                    self.end_headers()
                    return
                return super().do_GET()

            def do_POST(self):
                if urlparse(self.path).path != '/api/master/brain/analyze':
                    self.send_error(403)
                    return
                return super().do_POST()

        def fake_analysis(_app, current, focus):
            result = copy.deepcopy(analysis)
            result.update(clinic=current['clinic'], focus=focus, created_at=int(time.time()), observed_at=current['generated_at'],
                          fingerprint=current['inventory']['fingerprint'], inventory_status=current['inventory']['status'])
            return result

        read_inventory = brain.inventory
        def preview_inventory(base):
            result = copy.deepcopy(read_inventory(base))
            if args.inventory_state == 'partial':
                result['status'] = 'partial'
                result['files'].append({'name':'preview_unreadable.py', 'analysis_status':'unavailable',
                                        'functions':None, 'dependencies':[], 'hash':None})
            return result

        server = ThreadingHTTPServer(('127.0.0.1', args.port), PreviewHandler)
        server.auth_store = store
        print(f'Local synthetic preview: http://127.0.0.1:{server.server_port}/__preview__', flush=True)
        try:
            with patch.object(brain, 'inventory', side_effect=preview_inventory), patch.object(brain, 'openai_config', side_effect=model_config), patch.object(brain, 'ai_analysis', side_effect=fake_analysis), patch('urllib.request.urlopen', side_effect=RuntimeError('External requests disabled in preview')):
                server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == '__main__':
    main()
