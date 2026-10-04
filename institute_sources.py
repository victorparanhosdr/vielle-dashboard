"""Read-only, bounded connectors; credentials and provider errors stay server-side."""
import csv
import io
import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo

BRAZIL = ZoneInfo("America/Sao_Paulo")
MAX_BYTES = 24 * 1024 * 1024


class SourceError(RuntimeError):
    pass


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        host = urllib.parse.urlsplit(newurl).hostname or ""
        old = urllib.parse.urlsplit(req.full_url).hostname or ""
        if old == "docs.google.com" and urllib.parse.urlsplit(newurl).scheme == "https" and (host == "docs.google.com" or host.endswith(".googleusercontent.com")):
            return super().redirect_request(req, fp, code, msg, headers, newurl)
        raise SourceError("A fonte retornou um redirecionamento não permitido.")


def request(url, headers=None, data=None, source="Integração", as_json=True):
    req = urllib.request.Request(url, data=data, headers={"Accept": "application/json" if as_json else "text/csv", **(headers or {})})
    opener = urllib.request.build_opener(SafeRedirect())
    for attempt in range(2):
        try:
            with opener.open(req, timeout=35) as response:
                raw = response.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise SourceError(f"{source}: resposta muito grande. Reduza o período.")
                if not as_json:
                    if "text/html" in response.headers.get("Content-Type", ""):
                        raise SourceError("A planilha exige autenticação. Importe seu CSV na área de integrações; não torne os leads públicos.")
                    return raw.decode("utf-8-sig")
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError()
                return value
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and not attempt:
                time.sleep(2)
                continue
            message = "credenciais/permissões inválidas" if exc.code in {400, 401, 403} else "limite de requisições" if exc.code == 429 else "serviço indisponível"
            raise SourceError(f"{source}: {message} (HTTP {exc.code}).") from None
        except (urllib.error.URLError, TimeoutError, OSError, UnicodeError, ValueError):
            raise SourceError(f"{source}: não foi possível obter uma resposta válida. Os dados anteriores foram preservados.") from None
    raise SourceError(f"{source}: serviço indisponível.")


def day(value):
    if value is None or value == "":
        return ""
    try:
        if isinstance(value, (float, int)):
            return datetime.fromtimestamp(value, BRAZIL).date().isoformat()
        text = str(value).strip()
        if re.match(r"^\d{4}-\d{2}-\d{2}", text):
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return (parsed.astimezone(BRAZIL) if parsed.tzinfo else parsed).date().isoformat()
        for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(text, fmt).date().isoformat()
            except ValueError:
                pass
    except (ValueError, OverflowError, OSError):
        pass
    return ""


def cents(value, already_cents=False):
    if value is None or value == "":
        return None
    try:
        amount = Decimal(str(value)) * (1 if already_cents else 100)
        if not amount.is_finite() or abs(amount) > 10**14:
            raise ValueError()
        return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        raise SourceError("Valor financeiro inválido na origem.") from None


def period(start, end, max_days=366):
    try:
        a, b = date.fromisoformat(start), date.fromisoformat(end)
        if a > b or (b - a).days >= max_days:
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError(f"Escolha um período válido de até {max_days} dias.") from None
    return a, b


def windows(start, end):
    a, b = period(start, end, max_days=3653)
    while a <= b:
        last = min(b, a + timedelta(days=29))
        yield a, last
        a = last + timedelta(days=1)


class Kiwify:
    def __init__(self, settings, http=request, pause=time.sleep):
        self.settings, self.http, self.pause = settings, http, pause
        self.token = None

    def get(self, path, params=None):
        if not self.token:
            if not all(self.settings.get(k) for k in ("kiwify_client_id", "kiwify_client_secret", "kiwify_account_id")):
                raise SourceError("Configure as credenciais Kiwify do instituto.")
            response = self.http("https://public-api.kiwify.com/v1/oauth/token",
                {"Content-Type": "application/x-www-form-urlencoded"},
                urllib.parse.urlencode({"client_id": self.settings["kiwify_client_id"], "client_secret": self.settings["kiwify_client_secret"]}).encode(), source="Kiwify")
            self.token = response.get("access_token")
            if not self.token:
                raise SourceError("Kiwify: autenticação não concluída.")
        self.pause(.65)
        return self.http("https://public-api.kiwify.com/v1/" + path + "?" + urllib.parse.urlencode(params or {}),
            {"Authorization": "Bearer " + self.token, "x-kiwify-account-id": self.settings["kiwify_account_id"]}, source="Kiwify")

    def listing(self, path, params):
        rows, seen = [], set()
        for page in range(1, 1001):
            payload = self.get(path, {**params, "page_size": 100, "page_number": page})
            batch = payload.get("data")
            if not isinstance(batch, list):
                raise SourceError("Kiwify: formato da listagem inesperado.")
            if any(not isinstance(r, dict) or not r.get("id") for r in batch):
                raise SourceError("Kiwify: registro sem identificador.")
            fresh = [r for r in batch if str(r["id"]) not in seen]
            if batch and not fresh:
                raise SourceError("Kiwify: paginação repetida. Importação interrompida sem substituir os dados.")
            seen.update(str(r["id"]) for r in fresh)
            rows.extend(fresh)
            if len(batch) < 100:
                return rows
        raise SourceError("Kiwify: limite de páginas atingido. Reduza o período.")

    def products(self):
        return [{"id": str(r["id"]), "name": str(r.get("name", "Produto"))} for r in self.listing("products", {})]

    def sales(self, course, start, end):
        if not course["product_ids"]:
            raise SourceError("Selecione o produto Kiwify correspondente ao curso antes de importar vendas.")
        rows = {}
        for a, b in windows(start, end):
            for product in course["product_ids"]:
                params = {"product_id": product, "start_date": (a - timedelta(days=1)).isoformat(),
                          "end_date": (b + timedelta(days=1)).isoformat(), "view_full_sale_details": "true"}
                for raw in self.listing("sales", params):
                    if str((raw.get("product") or {}).get("id")) not in course["product_ids"]:
                        continue
                    payment, customer = raw.get("payment") or {}, raw.get("customer") or {}
                    rows[str(raw["id"])] = {"id": str(raw["id"]), "status": raw.get("status", "unknown"),
                        "product_id": str(raw["product"]["id"]), "created_day": day(raw.get("created_at")),
                        "approved_day": day(raw.get("approved_date")), "refunded_day": day(raw.get("refunded_at")),
                        "gross": cents(payment.get("charge_amount"), True), "net": cents(payment.get("net_amount", raw.get("net_amount")), True),
                        "currency": payment.get("charge_currency", raw.get("currency", "")),
                        "net_currency": payment.get("settlement_currency", raw.get("currency", "")),
                        "name": str(customer.get("name", "")), "email": str(customer.get("email", "")),
                        "phone": str(customer.get("mobile", "")), "payment_method": str(raw.get("payment_method", "")),
                        "campaign": str((raw.get("tracking") or {}).get("utm_campaign") or ""),
                        "source": str((raw.get("tracking") or {}).get("utm_source") or "")}
        return list(rows.values())


def normalize(value):
    return "".join(c for c in unicodedata.normalize("NFKD", str(value)).lower() if not unicodedata.combining(c)).strip()


def sheet_rows(content):
    content = content.lstrip("\ufeff")
    if content.lstrip().startswith("<"):
        raise SourceError("A planilha não retornou CSV. Use a importação de arquivo na área de integrações.")
    dialect = csv.Sniffer().sniff(content[:8192], delimiters=",;\t") if content else csv.excel
    reader = csv.DictReader(io.StringIO(content), dialect=dialect)
    headers = {normalize(v): v for v in reader.fieldnames or []}
    if not {"seu nome", "data", "id"}.issubset(headers):
        raise SourceError("O CSV precisa conter as colunas Seu nome, Data e ID do formulário.")
    rows = {}
    for count, raw in enumerate(reader, 1):
        if count > 100000:
            raise SourceError("Planilha excede 100 mil respostas. Divida o arquivo.")
        get = lambda k: (raw.get(headers.get(k, "")) or "").strip()
        ident = get("id")
        if not ident:
            if any(raw.values()):
                raise SourceError("Há uma resposta sem ID na planilha. Corrija antes de importar.")
            continue
        rows[ident] = {"id": ident, "name": get("seu nome"), "email": get("seu melhor e-mail"), "phone": get("seu whatsapp"),
            "created_day": day(get("data")), "score": get("pontuacao"), "campaign": get("utm_campaign"),
            "source": get("utm_source"), "medium": get("utm_medium"), "content": get("utm_content")}
    return list(rows.values())


def sheets(course):
    if not course["sheet_id"]:
        raise SourceError("Configure a planilha de leads deste curso.")
    query = urllib.parse.urlencode({"format": "csv", "gid": course["sheet_gid"]})
    text = request(f"https://docs.google.com/spreadsheets/d/{course['sheet_id']}/export?{query}", source="Google Sheets", as_json=False)
    return sheet_rows(text)


def kommo(course, call):
    if not course["pipeline_name"]:
        raise SourceError("Configure o funil Kommo do curso.")
    pipelines = call("/api/v4/leads/pipelines").get("_embedded", {}).get("pipelines", [])
    selected = [r for r in pipelines if normalize(r.get("name")) == normalize(course["pipeline_name"])]
    if len(selected) != 1:
        raise SourceError("O funil do curso não foi encontrado de forma única na conta Kommo da Vielle.")
    pipeline = selected[0]
    statuses = {str(s["id"]): {"name": s["name"], "sort": s.get("sort", 0)} for s in pipeline.get("_embedded", {}).get("statuses", [])}
    rows, seen = [], set()
    for page in range(1, 1001):
        query = urllib.parse.urlencode({"filter[pipeline_id][0]": pipeline["id"], "limit": 250, "page": page})
        batch = call("/api/v4/leads?" + query).get("_embedded", {}).get("leads", [])
        if not isinstance(batch, list):
            raise SourceError("Kommo: formato de resposta inválido.")
        for raw in batch:
            if str(raw.get("pipeline_id")) != str(pipeline["id"]):
                raise SourceError("Kommo retornou um lead de outro funil. Importação interrompida.")
            if str(raw["id"]) in seen:
                raise SourceError("Kommo retornou uma página repetida.")
            seen.add(str(raw["id"]))
            status = statuses.get(str(raw.get("status_id")), {"name": "Sem etapa", "sort": 999})
            rows.append({"id": str(raw["id"]), "name": raw.get("name", ""), "created_day": day(raw.get("created_at")),
                         "stage": status["name"], "stage_sort": status["sort"], "seller_id": str(raw.get("responsible_user_id", "")),
                         "pipeline_id": str(pipeline["id"]), "pipeline_name": pipeline["name"]})
        if len(batch) < 250:
            return rows
    raise SourceError("Kommo: limite de páginas atingido.")


class Meta:
    def __init__(self, settings, http=request):
        self.settings, self.http = settings, http
        self.account = "act_" + settings.get("meta_account_id", "").removeprefix("act_")

    def get(self, path, params=None):
        if not self.settings.get("meta_access_token") or self.account == "act_":
            raise SourceError("Conecte a conta de anúncios Meta do instituto.")
        return self.http("https://graph.facebook.com/" + self.settings.get("meta_version", "v22.0") + "/" + path + "?" + urllib.parse.urlencode(params or {}),
                         {"Authorization": "Bearer " + self.settings["meta_access_token"]}, source="Meta Ads")

    def listing(self, path, params):
        result, seen = [], set()
        for _ in range(1000):
            payload = self.get(path, params)
            batch = payload.get("data")
            if not isinstance(batch, list):
                raise SourceError("Meta: formato de resposta inesperado.")
            result.extend(batch)
            paging = payload.get("paging") or {}
            if not paging.get("next"):
                return result
            cursor = (paging.get("cursors") or {}).get("after")
            if not cursor or cursor in seen:
                raise SourceError("Meta: paginação incompleta. Os dados anteriores foram preservados.")
            seen.add(cursor)
            params = {**params, "after": cursor}
        raise SourceError("Meta: limite de páginas atingido.")

    def campaigns(self):
        account = self.get(self.account, {"fields": "name,currency"})
        if account.get("currency") != "BRL":
            raise SourceError("Esta versão exige uma conta Meta em BRL para não misturar moedas.")
        rows = self.listing(self.account + "/campaigns", {"fields": "id,name,status", "limit": 100})
        return {"account": {"name": account.get("name", ""), "currency": "BRL"}, "campaigns": rows}

    def insights(self, campaigns, start, end):
        if not campaigns:
            raise SourceError("Selecione as campanhas Meta exclusivas deste curso.")
        rows = {}
        ids = {r["id"] for r in campaigns}
        for a, b in windows(start, end):
            params = {"fields": "date_start,campaign_id,campaign_name,spend,impressions,clicks,account_currency",
                "level": "campaign", "time_increment": 1, "limit": 500,
                "time_range": json.dumps({"since": a.isoformat(), "until": b.isoformat()}),
                "filtering": json.dumps([{"field": "campaign.id", "operator": "IN", "value": sorted(ids)}])}
            for raw in self.listing(self.account + "/insights", params):
                if str(raw.get("campaign_id")) not in ids or raw.get("account_currency") != "BRL":
                    raise SourceError("Meta retornou campanha não selecionada ou moeda diferente. Dados não importados.")
                d = day(raw.get("date_start"))
                if not d or not start <= d <= end:
                    raise SourceError("Meta retornou uma data fora do período.")
                ident = str(raw["campaign_id"]) + ":" + d
                rows[ident] = {"id": ident, "campaign_id": str(raw["campaign_id"]), "campaign_name": raw.get("campaign_name", ""),
                    "day": d, "spend": cents(raw.get("spend")), "clicks": int(raw.get("clicks", 0)), "impressions": int(raw.get("impressions", 0))}
        return list(rows.values())
