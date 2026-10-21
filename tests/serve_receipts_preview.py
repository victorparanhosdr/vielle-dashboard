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
    parser.add_argument('--marketing-fixture', action='store_true')
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
            if args.marketing_fixture:
                procedures = [('Toxina botulínica', 180000), ('Bioestimulador', 340000),
                              ('Preenchimento facial', 260000), ('Laser CO2', 220000)]
                for i in range(44):
                    name, amount = procedures[i % len(procedures)]
                    day = 1 + (i * 5) % date.today().day
                    sold_at = date.today().replace(day=day).isoformat()
                    patient = {'uuid': f'demo-patient-{i % 19}', 'name': f'Cliente demonstrativo {i % 19 + 1:02}'}
                    quote = i % 6 == 0
                    app.save_clinica_sale(conn, {
                        'uuid': f'demo-sale-{i}', 'type': 'sale_quote' if quote else 'sale',
                        'status': 'budget' if quote else 'active', 'sale_date': sold_at,
                        'final_amount': amount, 'nominal_amount': amount,
                        'seller': {'uuid': 'a' if i % 3 else 'b'}, 'buyer': patient,
                        'procedures': [{'name': name, 'quantity': 1, 'final_amount': amount}],
                    }, 100)
                    if not quote:
                        app.save_clinica_bill(conn, {
                            'uuid': f'demo-bill-{i}', 'type': 'Venda', 'emission_date': sold_at,
                            'person': patient, 'final_amount': amount,
                            'payment_methods': [{'parcels': [{'uuid': f'demo-parcel-{i}', 'status': 'received',
                                'compensation_date': sold_at, 'net_amount': round(amount * .97),
                                'final_amount': amount, 'fees_amount': round(amount * .03)}]}],
                        }, 100)
                for i, (category, amount) in enumerate([('Equipe', 1900000), ('Estrutura', 950000), ('Insumos', 630000)]):
                    app.save_clinica_bill(conn, {'uuid': f'demo-expense-{i}', 'type': 'Conta',
                        'emission_date': today, 'final_amount': amount, 'net_amount': amount,
                        'description': 'Despesa demonstrativa', 'seller': {'uuid': 'a'},
                        'category': {'name': category}, 'payment_methods': [{'parcels': [{
                            'uuid': f'demo-expense-parcel-{i}', 'status': 'paid',
                            'compensation_date': today, 'final_amount': amount, 'net_amount': amount}]}]}, 100)
        if args.marketing_fixture:
            app.save_monthly_goal(date.today().strftime('%Y-%m'), 180000)
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
