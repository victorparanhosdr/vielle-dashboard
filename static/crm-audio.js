"use strict";
window.crmAudioTranscriptMarkup=(media,esc,icon)=>{
  if(media?.kind!=="audio")return "";
  const transcript=media.transcript;
  if(transcript?.status==="done")return `<details class="audioTranscript" data-audio-transcript="${esc(media.id)}"><summary>${icon("text")} Transcrição</summary><p>${esc(transcript.text)}</p><footer><small>Automática · pode conter erros</small><button type="button" class="icon-button" data-copy-transcript="${esc(media.id)}" title="Copiar transcrição" aria-label="Copiar transcrição">${icon("copy")}</button></footer></details>`;
  const labels={queued:"Transcrição na fila",running:"Transcrevendo…",limited:"Transcrição aguardando limite diário",failed:"Áudio não transcrito · revisão do gestor",cancelled:"Transcrição cancelada"};
  return labels[transcript?.status]?`<small class="audioTranscriptState">${icon("audio-lines")}${labels[transcript.status]}</small>`:"";
};
window.createCrmAudio=({api,esc,icon,icons,modal,error,state})=>{
  let sequence=0;
  async function open(doctor){
    if(!state.data?.can_manage)return;
    const token=++sequence;
    modal("Transcrição de áudios",'<p class="inboxEmpty">Carregando…</p>');
    try{
      const data=await api("audio",undefined,{doctor});
      if(token!==sequence || state.doctor!==doctor || !state.data?.can_manage || !document.getElementById('crmDialog').open)return;
      const s=data.settings,used=Math.ceil(data.used_seconds/60),queued=(data.counts.queued||0)+(data.counts.running||0)+(data.counts.limited||0);
      modal("Transcrição de áudios",`<div class="audioSettings"><header><span class="audioMode ${s.enabled?'on':''}">${icon(s.enabled?'audio-lines':'pause')}${s.enabled?'Automática ligada':'Automática desligada'}</span><span>${esc(data.day)} · São Paulo</span></header><dl class="audioUsage"><div><dt>Minutos reservados hoje</dt><dd>${used}<small> / ${s.daily_minutes}</small></dd><progress max="${s.daily_minutes}" value="${used}" aria-label="Uso diário de transcrição"></progress></div><div><dt>Áudios pendentes</dt><dd>${queued}</dd></div></dl><form id="audioSettingsForm"><label class="check"><input type="checkbox" name="enabled" ${s.enabled?'checked':''}>Transcrever novos áudios recebidos e enviados</label><label>Limite diário por profissional (minutos)<input type="number" name="daily_minutes" min="1" max="1440" value="${s.daily_minutes}" required></label><p class="inlineWarning">Os áudios serão processados pela OpenAI, com consumo da API. A fala pode conter dados pessoais. Alterações cancelam pendências, sem apagar transcrições prontas. O limite inclui reservas de chamadas sem confirmação e não abrange análises manuais da monitoria.</p><label class="check"><input type="checkbox" name="confirm">Autorizo o processamento automático pela OpenAI e o consumo da API</label>${!data.provider_ready?'<p class="inlineWarning">Configure a API OpenAI da clínica antes de ativar.</p>':''}<div class="formActions"><button type="submit" class="primary">${icon('check')} Salvar configuração</button></div></form><section class="audioHistory"><h3>${icon('history')} Áudios do histórico</h3><form id="audioBatchForm"><div class="audioBatchRange"><label>De<input type="date" name="from" required></label><label>Até<input type="date" name="to" required></label><label>Máximo de áudios<input type="number" name="limit" min="1" max="100" value="50" required></label></div><button type="submit" ${s.enabled?'':'disabled'}>${icon('list-filter')} Preparar lote</button></form><div id="audioBatchPreview"></div></section>${data.failed.length?`<section class="audioFailures"><h3>${icon('circle-alert')} Transcrições que precisam de revisão</h3>${data.failed.map(job=>`<div class="audioFailure" data-audio-job="${esc(job.id)}"><time>${esc(new Date(job.created_at*1000).toLocaleString('pt-BR'))}</time><p>${esc(job.error)}</p><label class="check"><input type="checkbox">Autorizo nova tentativa pela OpenAI, com novo consumo</label><button type="button" data-audio-retry ${s.enabled?'':'disabled'}>${icon('rotate-cw')} Tentar novamente</button></div>`).join('')}</section>`:''}</div>`);
      const form=document.getElementById('audioSettingsForm'),host=form.closest('.audioSettings');
      const alive=()=>token===sequence && host.isConnected && state.doctor===doctor && state.data?.can_manage;
      form.onsubmit=async event=>{
        event.preventDefault();const values=new FormData(form),button=form.querySelector('[type="submit"]');
        if(values.has('enabled') && !values.has('confirm')){error('Confirme o processamento dos áudios pela OpenAI.');return;}
        button.disabled=true;
        try{await api('audio/settings',{doctor_id:doctor,enabled:values.has('enabled'),daily_minutes:Number(values.get('daily_minutes')),revision:s.revision,confirm:values.has('confirm')},{doctor});if(alive())open(doctor);}
        catch(e){if(alive()){error(e.message);button.disabled=false;}}
      };
      document.getElementById('audioBatchForm').onsubmit=async event=>{
        event.preventDefault();const form=event.currentTarget,button=form.querySelector('button');button.disabled=true;
        const values=Object.fromEntries(new FormData(form));
        try{
          const result=await api('audio/batch-preview',{doctor_id:doctor,...values,limit:Number(values.limit)},{doctor});if(!alive())return;
          const batch=result.batch,target=document.getElementById('audioBatchPreview');
          target.innerHTML=`<p><strong>${batch.count} áudios no lote</strong>${batch.has_more?' · há outros no período':''}</p><p>Até ${batch.maximum_minutes} minutos de áudio. A duração real é verificada antes de cada chamada. Limite diário: ${batch.daily_minutes} minutos.</p><label class="check"><input type="checkbox" data-batch-confirm>Autorizo o envio deste lote à OpenAI e o consumo da API</label><button type="button" data-batch-apply ${batch.count?'':'disabled'}>${icon('list-plus')} Colocar lote na fila</button>`;
          target.querySelector('[data-batch-apply]').onclick=async event=>{
            if(!target.querySelector('[data-batch-confirm]').checked){error('Confirme o processamento e o consumo do lote.');return;}
            const b=event.currentTarget;b.disabled=true;
            try{await api('audio/batch-apply',{doctor_id:doctor,id:batch.id,confirm:true},{doctor});if(alive())open(doctor);}
            catch(e){if(alive()){error(e.message);b.disabled=false;}}
          };icons();
        }catch(e){if(alive())error(e.message);}
        finally{if(alive())button.disabled=false;}
      };
      host.querySelectorAll('[data-audio-retry]').forEach(button=>button.onclick=async()=>{
        const row=button.closest('[data-audio-job]');
        if(!row.querySelector('input').checked){error('Confirme o novo consumo da API.');return;}
        button.disabled=true;
        try{await api('audio/retry',{doctor_id:doctor,id:row.dataset.audioJob,confirm:true},{doctor});if(alive())open(doctor);}
        catch(e){if(alive()){error(e.message);button.disabled=false;}}
      });icons();
    }catch(e){if(token===sequence)error(e.message);}
  }
  return {open};
};
