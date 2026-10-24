"use strict";
(() => {
  const $=id=>document.getElementById(id);
  const labels={disabled:"Rotina desligada",waiting:"Aguardando próxima execução",running:"Cópia em andamento",uploaded:"Cópia enviada e conferida",failed:"Backup precisa de atenção",review:"Tentativas suspensas para revisão"};
  const date=value=>value?new Date(value*1000).toLocaleString("pt-BR",{timeZone:"America/Sao_Paulo"}):"Ainda não registrado";
  let busy=false;
  async function api(body){
    const response=await fetch("/api/master/backups",body?{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}:{cache:"no-store"});
    const data=await response.json();
    if(!response.ok||!data.ok)throw new Error(data.error||"Não foi possível consultar o backup.");
    return data;
  }
  async function load(){
    if(busy)return;busy=true;$("refreshBackup").disabled=true;
    try{
      const data=await api();
      $("backupState").textContent=labels[data.status]||"Estado não confirmado";
      $("backupBadge").textContent=data.enabled?"No servidor":"Desligado";
      $("backupBadge").className=`statusBadge backup-${data.status}`;
      $("lastUploaded").textContent=date(data.last_uploaded);$("lastAttempt").textContent=date(data.last_attempt);
      $("nextAttempt").textContent=!data.enabled?"Sem execução programada":data.status==="review"?"Aguardando revisão":data.status==="running"?"Em andamento":date(data.next_attempt);
      $("schedule").textContent=data.enabled?`Diariamente, ${String(data.schedule_hour).padStart(2,"0")}:00 · São Paulo`:"Desligado";
      $("restoreVerified").textContent=data.restore_verified?"Confirmada":"Não confirmada";
      $("failureNotice").hidden=!data.needs_attention;
      $("failureNotice").textContent=data.needs_attention?"A proteção externa precisa de revisão. Não considere este estado como confirmação de um backup recente.":"";
      $("backupErrorCode").hidden=!data.error;$("backupErrorCode").textContent=data.error?`Código técnico: ${data.error}`:"";
      const link=data.folder_url;
      const allowed=typeof link==="string"&&/^https:\/\/drive\.google\.com\/drive\/folders\/[A-Za-z0-9_-]{10,200}$/.test(link);
      $("backupFolder").hidden=!allowed;$("noFolder").hidden=allowed;if(allowed)$("backupFolder").href=link;
      $("runBackup").disabled=!data.enabled||data.status==="running";
      $("notice").textContent="";
    }catch(error){$("notice").textContent=error.message;$("notice").className="notice error";$("runBackup").disabled=true;}
    finally{busy=false;$("refreshBackup").disabled=false;}
  }
  $("refreshBackup").onclick=load;
  $("retryBackup").onsubmit=async event=>{
    event.preventDefault();$("runBackup").disabled=true;
    try{await api({confirm:true});$("retryBackup").reset();await load();$("notice").textContent="Solicitação registrada. O servidor verificará a pendência no próximo ciclo.";}
    catch(error){$("notice").textContent=error.message;$("notice").className="notice error";}
  };
  load();setInterval(()=>{if(!document.hidden)load();},30000);
})();
