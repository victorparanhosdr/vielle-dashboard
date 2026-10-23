"""Follow-up owns robot executions, cancellation and the business-hour guard."""
import json
import time

SAFE_KINDS={"start","message","delay","condition","note","task","tag","end"}


def schema(c):
    c.execute("""CREATE TABLE IF NOT EXISTS crm_followup_bot_steps(
        cycle_id TEXT NOT NULL,attempt INTEGER NOT NULL,run_id TEXT NOT NULL UNIQUE,
        bot_revision INTEGER NOT NULL,PRIMARY KEY(cycle_id,attempt))""")


def eligible(bot):
    try:
        info=json.loads(bot["published"])
        return bot["status"]=="active" and info["stop_on_reply"] and all(n["kind"] in SAFE_KINDS for n in info["nodes"])
    except (ValueError,KeyError,TypeError):return False


def selected(c,pipeline,key,revision=None):
    if not isinstance(key,str) or not key:raise ValueError("Selecione um robô deste funil.")
    if revision is not None and type(revision) is not int:raise ValueError("Versão do robô inválida.")
    bot=c.execute("SELECT * FROM crm_bots WHERE id=? AND pipeline_id=?",(key,pipeline)).fetchone()
    if not bot or not eligible(bot):raise ValueError("Escolha um robô ativo deste funil, com parada na resposta e sem alterar a fase do paciente.")
    if revision is not None and revision!=bot["revision"]:raise ValueError("O robô mudou. Selecione a versão atual antes de salvar a régua.")
    return dict(bot)


def launch(c,store,cycle,step,now):
    from crm_bots import BotStore
    bot=selected(c,cycle["pipeline_id"],step["bot_id"],step["bot_revision"])
    info=json.loads(bot["published"])
    if any(n["data"].get("channel_id") and n["data"]["channel_id"]!=cycle["channel_id"] for n in info["nodes"]):
        raise ValueError("O número do robô é diferente do atendimento acompanhado.")
    lead=dict(c.execute("SELECT * FROM leads WHERE id=?",(cycle["lead_id"],)).fetchone())
    run=BotStore(store).enqueue(c,bot,lead,f"followup:{cycle['id']}:{cycle['attempt']}",now,cycle["channel_id"],cycle["id"],0)
    if not run:raise ValueError("Não foi possível reservar o robô; confira outras execuções deste contato.")
    c.execute("INSERT INTO crm_followup_bot_steps VALUES(?,?,?,?)",(cycle["id"],cycle["attempt"],run,bot["revision"]))
    return run


def gate(c,store,run_id,now=None):
    from crm_followup_runtime import guard,stop
    from crm_followup_settings import next_window
    now=time.time() if now is None else now
    linked=c.execute("SELECT * FROM crm_followup_bot_steps WHERE run_id=?",(run_id,)).fetchone()
    if not linked:return True
    cycle=c.execute("SELECT * FROM crm_followup_cycles WHERE id=?",(linked["cycle_id"],)).fetchone()
    if not cycle or cycle["status"]!="waiting" or cycle["bot_run_id"]!=run_id:return False
    allowed=getattr(store,"followup_permissions",None)
    reason=None
    if not callable(allowed) or not allowed(dict(cycle)):reason=("paused","Responsável sem acesso atual")
    else:reason=guard(c,store,cycle,now)
    bot=c.execute("SELECT b.* FROM crm_bots b JOIN bot_runs r ON r.bot_id=b.id WHERE r.id=?",(run_id,)).fetchone()
    if not reason and (not bot or bot["revision"]!=linked["bot_revision"]):reason=("paused","Versão do robô alterada; não retomar")
    if reason:
        if reason[0]!="defer":stop(c,cycle,reason[1],now,reason[0])
        return False
    return next_window(json.loads(cycle["config"]),now)<=now
