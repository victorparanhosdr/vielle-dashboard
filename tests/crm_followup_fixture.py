"""Synthetic follow-up assets and progress, without starting a worker."""
from io import BytesIO
import json
import time
import uuid
import wave

from PIL import Image,ImageDraw
from reportlab.pdfgen import canvas


def prepare(store,doc,pipeline,actor):
    import crm_followup_assets as assets
    import crm_followup_settings as settings
    from crm_bots import BotStore
    image=BytesIO();art=Image.new("RGB",(640,360),"#164c3c")
    ImageDraw.Draw(art).text((36,160),"DOC4DOCS / CUIDADO PERSONALIZADO",fill="white");art.save(image,format="PNG")
    document=BytesIO();pdf=canvas.Canvas(document);pdf.drawString(50,750,"Documento sintetico de demonstracao DOC4DOCS");pdf.save()
    audio=BytesIO()
    with wave.open(audio,"wb") as wav:
        wav.setparams((1,2,8000,0,"NONE","not compressed"));wav.writeframes(b"\0\0"*8000)
    media=[]
    for kind,name,content in (("image","apresentacao.png",image.getvalue()),("document","apresentacao.pdf",document.getvalue()),("audio","recado.wav",audio.getvalue())):
        media.append(assets.upload(store,doc,pipeline["id"],actor,uuid.uuid4().hex,name,kind,content))
        (store.path.parent/name).write_bytes(content)
    bots=BotStore(store)
    bot=bots.save({"doctor_id":doc,"pipeline_id":pipeline["id"],"name":"Ligação da equipe","definition":{"trigger":{"kind":"manual"},"stop_on_reply":True,"nodes":[
        {"id":"start","kind":"start","next":{"next":"task"}},{"id":"task","kind":"task","data":{"title":"Ligar para acompanhar interesse","minutes":60},"next":{"next":"end"}},{"id":"end","kind":"end"}]}},actor["id"])
    bots.publish(bot["id"],doc,1,True,actor["id"])
    config=settings.snapshot(store,doc,pipeline["id"])["config"]
    config["steps"][1].update(media_id=media[0]["id"])
    config["steps"][2].update(media_id=media[1]["id"])
    config["steps"][3].update(action="bot",bot_id=bot["id"],bot_revision=2,body="")
    saved=settings.save(store,doc,{"pipeline_id":pipeline["id"],"revision":0,"config":config},actor)
    with store.db() as c:
        channel=c.execute("SELECT * FROM channels WHERE doctor_id=? LIMIT 1",(doc,)).fetchone()
        leads=c.execute("SELECT * FROM leads WHERE pipeline_id=? AND deleted_at=0 LIMIT 4",(pipeline["id"],)).fetchall()
        now=time.time()
        for index,lead in enumerate(leads):
            cycle=uuid.uuid4().hex
            inbound=c.execute("SELECT coalesce(max(rowid),0) FROM messages WHERE contact_id=? AND direction='in'",(lead["contact_id"],)).fetchone()[0]
            c.execute("""INSERT INTO crm_followup_cycles(id,lead_id,contact_id,doctor_id,pipeline_id,stage_id,channel_id,phone,
                phone_revision,channel_revision,policy_revision,actor_id,activator_id,anchor_id,inbound_seq,attempt,config,due_at,status,reason,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,1,1,?,'1','1','synthetic',?,?,?,?,?,?,?,?)""",(cycle,lead["id"],lead["contact_id"],doc,pipeline["id"],lead["stage_id"],channel["id"],"",saved["revision"],inbound,index,json.dumps(config),now+7200,"paused","Régua desligada na demonstração",now,now))
            for step in range(index):
                c.execute("""INSERT INTO messages(id,channel_id,contact_id,body,direction,origin,status,created_at,sent_at,followup_cycle_id,followup_attempt)
                    VALUES(?,?,?,'Mensagem sintética','out','followup','sent',?,?,?,?)""",(uuid.uuid4().hex,channel["id"],lead["contact_id"],now-100,now-100,cycle,step+1))
    print("Synthetic attachment directory: "+str(store.path.parent),flush=True)
