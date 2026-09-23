"""Local synthetic receipt preview; no real database or external API access."""
import argparse
from datetime import date
from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import secrets
import sys
import tempfile
from urllib.parse import urlsplit
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='doc4docs-receipts-preview-') as directory:
        os.environ['DATA_DIR'] = directory
        import app
        from auth_store import AuthStore
        from auth_http import LoginLimiter
        app.clinic_db_path = lambda clinic_id=None: Path(directory) / 'preview.sqlite3'
        app.CONFIG_DEFAULTS = {}
        app.clinic_doctor_professionals = lambda *_: {'Profissional A (teste)': 'a', 'Profissional B (teste)': 'b'}
        app.clinic_pipeline_doctor_map = lambda *_: {}
        app.forced_professional_uuids = lambda *_: []
        app.init_db()
        today = date.today().isoformat()
        with app.db() as conn:
            for key, gross, net in (('a', 1000000, 970000), ('b', 500000, 482000)):
                app.save_clinica_sale(conn, {
                    'uuid': 'sale-' + key, 'type': 'sale', 'status': 'active',
                    'sale_date': today, 'final_amount': gross,
                    'seller': {'uuid': key}, 'buyer': {'uuid': 'patient-' + key, 'name': 'Paciente ficticio ' + key},
                }, 100)
                app.save_clinica_bill(conn, {
                    'uuid': 'bill-' + key, 'type': 'Venda', 'emission_date': today,
                    'person': {'uuid': 'patient-' + key}, 'final_amount': gross,
                    'payment_methods': [{'parcels': [{'uuid': 'parcel-' + key, 'status': 'received',
                        'compensation_date': today, 'net_amount': net, 'final_amount': gross,
                        'fees_amount': gross - net}]}],
                }, 100)
        store = AuthStore(Path(directory) / 'auth.sqlite3')
        store.initialize()
        password = secrets.token_urlsafe(24)
        store.create_user('Previa local', 'preview', password, is_master=True)
        session = store.login('preview', password)

        class PreviewHandler(app.Handler):
            def do_GET(self):
                path = urlsplit(self.path).path
                if path == '/__preview__':
                    self.send_response(303)
                    self.send_header('Set-Cookie', f'doc4docs_session={session}; HttpOnly; SameSite=Lax; Path=/')
                    self.send_header('Location', '/?clinic=vielle')
                    self.end_headers()
                    return
                if path.startswith('/api/') and path not in {'/api/auth/me', '/api/report', '/api/export-chart'}:
                    return self.auth_json({'error': 'Previa somente leitura'}, 403)
                if path.startswith(('/auth/', '/webhooks/')):
                    return self.auth_json({'error': 'Previa somente leitura'}, 403)
                return super().do_GET()

            def do_POST(self):
                return self.auth_json({'error': 'Previa somente leitura'}, 403)

        server = ThreadingHTTPServer(('127.0.0.1', args.port), PreviewHandler)
        server.auth_store = store
        server.login_limiter = LoginLimiter()
        print(f'http://127.0.0.1:{server.server_port}/__preview__', flush=True)
        with patch('urllib.request.urlopen', side_effect=AssertionError('No external calls in preview')):
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()


if __name__ == '__main__':
    main()
