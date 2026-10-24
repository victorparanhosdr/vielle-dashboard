"""Bounded transactional SMTP. No mailbox reads, token logs or delivery retries."""

from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from datetime import datetime, timezone
import logging
import os
import queue
import smtplib
import ssl
import threading
from urllib.parse import urlsplit

from auth_recovery import normalize_email


@dataclass(frozen=True)
class MailSettings:
    host: str
    username: str
    password: str = field(repr=False)
    public_url: str = "https://doc4docs.com.br"

    @classmethod
    def from_environ(cls, env=None):
        env = os.environ if env is None else env
        if env.get("DOC4DOCS_AUTH_EMAIL_ENABLED") != "1":
            return None
        try:
            host = env.get("DOC4DOCS_SMTP_HOST", "smtp.hostinger.com").strip()
            user = normalize_email(env.get("DOC4DOCS_SMTP_USER"))
            password = env.get("DOC4DOCS_SMTP_PASSWORD", "")
            public_url = env.get("DOC4DOCS_AUTH_PUBLIC_URL", "https://doc4docs.com.br").rstrip("/")
            parsed = urlsplit(public_url)
            if (not host or any(c.isspace() for c in host) or len(host) > 253
                    or not password or len(password) > 1024
                    or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                    or parsed.path or parsed.query or parsed.fragment or parsed.port not in (None, 443)):
                raise ValueError()
            return cls(host, user, password, public_url)
        except (ValueError, TypeError):
            logging.warning("auth_email_configuration_invalid")
            return None


class SmtpMailer:
    def __init__(self, settings):
        self.settings = settings

    def send(self, recipient, *, token=None):
        settings = self.settings
        recipient = normalize_email(recipient)
        message = EmailMessage()
        message["From"] = "DOC4DOCS <" + settings.username + ">"
        message["To"] = recipient
        message["Date"] = format_datetime(datetime.now(timezone.utc))
        message["Message-ID"] = make_msgid(domain=settings.username.rsplit("@", 1)[1])
        if token:
            message["Subject"] = "Redefinir sua senha do DOC4DOCS"
            link = settings.public_url + "/redefinir-senha#token=" + token
            message.set_content("Recebemos uma solicitação para redefinir sua senha do DOC4DOCS.\n\n"
                "Abra o link abaixo para escolher uma nova senha:\n" + link + "\n\n"
                "O link vence em 20 minutos e só pode ser usado uma vez.\n"
                "Se você não fez esta solicitação, ignore este e-mail. Sua senha continua a mesma.\n"
                "Nunca compartilhe este link ou sua senha.\n\nEquipe DOC4DOCS\n")
        else:
            message["Subject"] = "Sua senha do DOC4DOCS foi alterada"
            message.set_content("A senha da sua conta DOC4DOCS foi alterada e as sessões anteriores foram encerradas.\n\n"
                "Se você não reconhece esta alteração, avise o responsável da sua empresa e solicite uma nova recuperação em "
                + settings.public_url + "/recuperar-senha.\n\nEquipe DOC4DOCS\n")
        self._deliver(message, recipient)

    def send_test_to_sender(self):
        message = EmailMessage()
        message["From"] = "DOC4DOCS <" + self.settings.username + ">"
        message["To"] = self.settings.username
        message["Date"] = format_datetime(datetime.now(timezone.utc))
        message["Message-ID"] = make_msgid(domain=self.settings.username.rsplit("@", 1)[1])
        message["Subject"] = "DOC4DOCS - Teste de conexão de e-mail"
        message.set_content("Este é um teste de envio do DOC4DOCS pela caixa configurada.\n\n"
                            "Nenhuma senha de usuário foi alterada. Este e-mail não contém link de recuperação.\n\nEquipe DOC4DOCS\n")
        self._deliver(message, self.settings.username)

    def _deliver(self, message, recipient):
        settings = self.settings
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        with smtplib.SMTP_SSL(settings.host, 465, timeout=15, context=context) as smtp:
            smtp.login(settings.username, settings.password)
            refused = smtp.send_message(message, from_addr=settings.username, to_addrs=[recipient])
            if refused:
                raise smtplib.SMTPRecipientsRefused(refused)


class RecoveryService:
    def __init__(self, store, settings=None, *, mailer=None):
        self.store = store
        self.mailer = mailer or (SmtpMailer(settings) if settings else None)
        self.jobs = queue.Queue(maxsize=64)
        self.stop = threading.Event()

    @property
    def ready(self):
        return self.mailer is not None

    def request(self, email, peer):
        try:
            email = normalize_email(email)
        except ValueError:
            email = "invalid"
        if self.store.recovery_limit(email, peer):
            try:
                self.jobs.put_nowait(("reset", email))
            except queue.Full:
                logging.warning("auth_email_queue_full")

    def notify_changed(self, user_id):
        try:
            self.jobs.put_nowait(("changed", user_id))
        except queue.Full:
            logging.warning("auth_email_queue_full")

    def process_one(self, job):
        kind, value = job
        if not self.ready:
            return
        try:
            if kind == "reset":
                if value == "invalid":
                    return
                with self.store.connection() as conn:
                    eligible = conn.execute("SELECT 1 FROM users WHERE email=? AND status='active'", (value,)).fetchone()
                if not eligible or not self.store.recovery_limit("", "", phase="send"):
                    return
                result = self.store.create_password_recovery(value)
                if result:
                    self.mailer.send(result[0], token=result[1])
            else:
                user = self.store.get_public_user(value)
                if user and user.get("email") and user["status"] == "active" and self.store.recovery_limit("", "", phase="send"):
                    self.mailer.send(user["email"])
        except Exception:
            # Provider messages can contain addresses, credentials or recovery links.
            logging.warning("auth_email_delivery_unconfirmed")

    def run(self):
        while not self.stop.is_set():
            try:
                job = self.jobs.get(timeout=1)
            except queue.Empty:
                continue
            try:
                self.process_one(job)
            finally:
                self.jobs.task_done()

    def start(self):
        if self.ready:
            threading.Thread(target=self.run, name="auth-email", daemon=True).start()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Explicit SMTP test to the configured sender only.")
    parser.add_argument("command", choices=("test-sender",))
    parser.add_argument("--confirm", action="store_true", required=True)
    parser.parse_args()
    settings = MailSettings.from_environ()
    if not settings:
        raise SystemExit("auth_email_not_configured")
    try:
        SmtpMailer(settings).send_test_to_sender()
    except Exception:
        raise SystemExit("auth_email_test_unconfirmed") from None
    print("auth_email_test_accepted")
