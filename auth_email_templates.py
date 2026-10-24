"""Branded transactional mail with inline styles and a plain-text alternative."""

from html import escape
from urllib.parse import urlsplit

from auth_recovery import TOKEN_PATTERN


def render_auth_email(kind, public_url, *, token=None, logo_cid="doc4docs-logo"):
    origin = public_url.rstrip("/")
    if kind == "recovery":
        if not isinstance(token, str) or not TOKEN_PATTERN.fullmatch(token):
            raise ValueError("Invalid recovery token.")
        subject = "Redefinir sua senha do DOC4DOCS"
        title = "Redefina sua senha."
        intro = "Recebemos seu pedido para recuperar o acesso ao DOC4DOCS. Escolha uma nova senha para continuar."
        action, link = "Redefinir minha senha", origin + "/redefinir-senha#token=" + token
        detail = "Válido por 20 minutos. Uso único."
        notice_title = "Não foi você?"
        notice = "Pode ignorar este e-mail. Sua senha permanece a mesma e nenhuma alteração será feita sem a sua confirmação."
        preheader = "Seu link para recuperar o acesso ao DOC4DOCS. Válido por 20 minutos."
    elif kind == "changed":
        subject = "Sua senha do DOC4DOCS foi alterada"
        title = "Senha atualizada."
        intro = "A senha da sua conta DOC4DOCS foi alterada. Para sua segurança, encerramos as sessões anteriores."
        action, link = "Acessar minha conta", origin + "/login"
        detail = "Entre novamente com a sua nova senha."
        notice_title = "Não reconhece esta alteração?"
        notice = "Avise o responsável da sua empresa e solicite uma nova recuperação de acesso na página de login."
        preheader = "Sua senha foi alterada e as sessões anteriores foram encerradas."
    elif kind == "test":
        subject = "DOC4DOCS - Teste de conexão de e-mail"
        title = "Conexão de e-mail pronta."
        intro = "Este é um teste de envio do DOC4DOCS pela caixa configurada. Seus e-mails de acesso já têm a identidade da nossa marca."
        action, link = "Acessar DOC4DOCS", origin + "/login"
        detail = "Mensagem de teste. Nenhuma ação é necessária."
        notice_title = "Apenas uma conferência de envio."
        notice = "Nenhuma senha de usuário foi alterada. Este e-mail não contém link de recuperação."
        preheader = "Um teste de envio do DOC4DOCS. Nenhuma senha foi alterada."
    else:
        raise ValueError("Unsupported authentication email.")

    plain = (f"{title}\n\n{intro}\n\n{action}:\n{link}\n\n{detail}\n\n"
             f"{notice_title}\n{notice}\n\nNão compartilhe sua senha nem encaminhe este e-mail.\n\nEquipe DOC4DOCS\n")
    domain = urlsplit(origin).hostname or "DOC4DOCS"
    e = escape
    html = f"""<!doctype html>
<html lang="pt-BR">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light"><title>{e(subject)}</title></head>
<body style="margin:0;padding:0;background-color:#eef3f0;color:#172e28;font-family:Arial,Helvetica,sans-serif;letter-spacing:0;-webkit-text-size-adjust:100%;">
<div style="display:none;font-size:1px;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;mso-hide:all;">{e(preheader)}</div>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="#eef3f0" style="width:100%;border-collapse:collapse;">
<tr><td align="center" style="padding:32px 16px;">
<!--[if mso]><table role="presentation" width="600" cellspacing="0" cellpadding="0" border="0"><tr><td><![endif]-->
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;max-width:600px;border-collapse:separate;border-spacing:0;background-color:#ffffff;border:1px solid #dce6e1;border-radius:8px;overflow:hidden;">
<tr><td bgcolor="#123e32" style="padding:28px 32px 24px;background-color:#123e32;border-radius:7px 7px 0 0;">
<img src="cid:{e(logo_cid, quote=True)}" alt="DOC4DOCS" width="180" height="60" style="display:block;width:180px;max-width:100%;height:auto;border:0;color:#ffffff;font-size:22px;font-weight:bold;">
</td></tr>
<tr><td height="3" bgcolor="#c5ad78" style="height:3px;line-height:3px;font-size:1px;background-color:#c5ad78;">&nbsp;</td></tr>
<tr><td style="padding:32px 32px 28px;">
<p style="margin:0 0 14px;color:#587267;font-size:11px;line-height:16px;font-weight:bold;">SEGURANÇA DA SUA CONTA</p>
<h1 style="margin:0 0 18px;color:#172e28;font-size:28px;line-height:35px;font-weight:bold;">{e(title)}</h1>
<p style="margin:0 0 26px;color:#52675f;font-size:16px;line-height:26px;">{e(intro)}</p>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;border-collapse:separate;">
<tr><td align="center" bgcolor="#123e32" style="background-color:#123e32;border-radius:6px;mso-padding-alt:16px 20px;">
<a href="{e(link, quote=True)}" style="display:block;padding:16px 20px;border:1px solid #123e32;border-radius:6px;color:#ffffff;background-color:#123e32;text-decoration:none;text-align:center;font-size:16px;line-height:22px;font-weight:bold;">{e(action)}</a>
</td></tr></table>
<p style="margin:12px 0 28px;text-align:center;color:#6b7f75;font-size:12px;line-height:19px;">{e(detail)}</p>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;border-collapse:collapse;">
<tr><td style="padding:24px 0 0;border-top:1px solid #e3ebe6;">
<h2 style="margin:0 0 8px;color:#294a3b;font-size:14px;line-height:21px;font-weight:bold;">{e(notice_title)}</h2>
<p style="margin:0 0 20px;color:#61756b;font-size:14px;line-height:23px;">{e(notice)}</p>
<p style="margin:0;color:#294a3b;font-size:14px;line-height:22px;font-weight:bold;">Equipe DOC4DOCS</p>
<p style="margin:2px 0 0;color:#7a8c82;font-size:12px;line-height:20px;">Criado por doutores. Para doutores.</p>
</td></tr></table>
</td></tr></table>
<!--[if mso]></td></tr></table><![endif]-->
<p style="margin:20px 0 6px;color:#73857b;font-size:12px;line-height:20px;">Não compartilhe sua senha nem encaminhe este e-mail.</p>
<p style="margin:0;color:#829188;font-size:11px;line-height:18px;">DOC4DOCS &middot; {e(domain)}</p>
</td></tr></table></body></html>"""
    return subject, plain, html
