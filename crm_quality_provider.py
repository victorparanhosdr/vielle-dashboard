"""Bounded OpenAI requests and sanitized, actionable diagnostics for monitoria."""
import json
import re
import socket
import urllib.error
import urllib.request
import uuid

ERRORS={
    "api_key":"A chave da API OpenAI está ausente, inválida ou sem permissão. Confira a configuração da clínica.",
    "api_quota":"A OpenAI informou saldo ou cota de uso insuficiente. Confira o faturamento e os limites da API.",
    "api_rate":"A OpenAI limitou temporariamente as requisições. Tente novamente mais tarde, com nova confirmação.",
    "api_model":"O modelo configurado não está disponível para esta conta OpenAI. Confira o modelo e seu acesso.",
    "api_request":"A OpenAI recusou o formato da solicitação. Confira o modelo e a configuração da integração.",
    "api_unavailable":"A OpenAI está indisponível no momento. Não houve nova tentativa automática.",
    "api_timeout":"A chamada à OpenAI ficou sem confirmação. Não foi repetida automaticamente; pode ter havido consumo.",
    "api_network":"Não foi possível comunicar com a OpenAI. Não houve nova tentativa automática.",
    "analysis_invalid":"A resposta da IA não passou na validação de critérios, autoria ou evidências. Nenhuma nota foi atribuída.",
    "analysis_failed":"A análise falhou sem diagnóstico específico disponível. Nenhuma nota foi atribuída.",
    "analysis_interrupted":"A análise foi interrompida antes de concluir. Não foi repetida automaticamente; pode ter havido consumo da API.",
    "audio_missing":"Um áudio do recorte está indisponível no WhatsApp ou no armazenamento. Nenhuma nota foi atribuída.",
    "audio_changed":"Um áudio mudou ou não corresponde mais ao recorte autorizado. Abra uma nova análise da conversa.",
    "audio_decode":"Não foi possível decodificar um áudio. O arquivo pode estar incompleto ou em formato inválido.",
    "audio_limit":"O recorte excede o limite de 8 áudios ou contém áudio com mais de 10 minutos. Avalie um recorte menor.",
    "audio_empty":"A transcrição não retornou fala utilizável. Confira o áudio antes de avaliar.",
    "audio_pending":"Uma transcrição anterior ficou sem conclusão. Não foi repetida para evitar consumo duplicado.",
    "audio_previous":"Uma transcrição falhou anteriormente. Use Tentar novamente e autorize novo consumo para repeti-la.",
    "audio_daily_limit":"Limite diário de transcrição atingido. Aguardando o próximo dia, sem consumir a API.",
    "audio_budget_size":"Este áudio é maior que o limite diário configurado. O gestor precisa revisar o limite antes de autorizar nova tentativa.",
    "scope_changed":"A autorização ou o escopo mudou durante a análise. O resultado não foi utilizado.",
}


class QualityError(ValueError):
    def __init__(self,code):
        self.code=code if code in ERRORS else "analysis_failed"
        super().__init__(ERRORS[self.code])


def http_error(status,body):
    try:code=json.loads(body).get("error",{}).get("code","")
    except (ValueError,AttributeError,TypeError):code=""
    if status in {401,403}:return QualityError("api_key")
    if code in {"insufficient_quota","billing_hard_limit_reached","usage_limit_reached"}:return QualityError("api_quota")
    if status==429:return QualityError("api_rate")
    if status==404 or code=="model_not_found":return QualityError("api_model")
    return QualityError("api_unavailable" if status>=500 else "api_request")


def classify(exc):
    if isinstance(exc,QualityError):return exc
    if isinstance(exc,(TimeoutError,socket.timeout)):return QualityError("api_timeout")
    if isinstance(exc,urllib.error.URLError):return QualityError("api_network")
    # The legacy app helper wraps HTTP errors; only extract status and error code.
    match=re.match(r"OpenAI retornou (\d{3}): (.*)",str(exc),re.S)
    if match:return http_error(int(match[1]),match[2])
    if str(exc).startswith("OPENAI_API_KEY"):return QualityError("api_key")
    return QualityError("analysis_invalid" if isinstance(exc,(ValueError,TypeError,KeyError)) else "analysis_failed")


def result_schema(messages):
    data=json.loads(messages[1]["content"])
    item={"type":"object","additionalProperties":False,"properties":{
        "id":{"type":"string","enum":[r["id"] for r in data["rubric"]]},
        "outcome":{"type":"string","enum":["pass","fail","na"]},"reason":{"type":"string"},
        "evidence":{"type":"array","items":{"type":"string","enum":[m["id"] for m in data["messages"]]}}},
        "required":["id","outcome","reason","evidence"]}
    assessment={"type":"object","additionalProperties":False,"properties":{
        "actor":{"type":"string","enum":data["actors"]},"summary":{"type":"string"},"feedback":{"type":"string"},
        "items":{"type":"array","items":item}},"required":["actor","summary","feedback","items"]}
    return {"type":"object","additionalProperties":False,"properties":{"assessments":{"type":"array","items":assessment}},"required":["assessments"]}


def chat(messages):
    from app import openai_chat_json
    try:return openai_chat_json(messages,schema=result_schema(messages))
    except Exception as exc:raise classify(exc) from None


def audio_model():
    from app import config_value
    return str(config_value("OPENAI_TRANSCRIBE_MODEL","gpt-transcribe") or "gpt-transcribe").strip()


def transcribe(wav, *, model=None):
    from app import config_value,configured
    key=str(config_value("OPENAI_API_KEY","") or "").strip()
    if not configured(key):raise QualityError("api_key")
    model=model or audio_model()
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}",model):raise QualityError("api_model")
    boundary="doc4docs-"+uuid.uuid4().hex
    fields=(f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\n{model}\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\nContent-Type: audio/wav\r\n\r\n').encode()
    request=urllib.request.Request("https://api.openai.com/v1/audio/transcriptions",data=fields+wav+f"\r\n--{boundary}--\r\n".encode(),
        headers={"Authorization":"Bearer "+key,"Content-Type":"multipart/form-data; boundary="+boundary},method="POST")
    try:
        with urllib.request.urlopen(request,timeout=60) as response:
            raw=response.read(512*1024+1)
        if len(raw)>512*1024:raise QualityError("audio_empty")
        result=json.loads(raw);text=result.get("text")
        if not isinstance(text,str) or not text.strip() or len(text)>30000:raise QualityError("audio_empty")
        return text.strip()
    except urllib.error.HTTPError as exc:raise http_error(exc.code,exc.read(8192)) from None
    except QualityError:raise
    except Exception as exc:raise classify(exc) from None
