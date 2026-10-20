"use strict";
window.setupGoogleFormIntegration=async()=>{
  const host=document.getElementById('googleFormsIntegration');
  if(!host || selectedClinic!=='vielle')return;
  const esc=value=>String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let data,doctor=settingsParams.get('doctor') || '',activity='',sequence=0;
  const status=message=>{host.querySelector('[role=status]').textContent=message;};
  const option=(value,label,selected)=>`<option value="${esc(value)}" ${selected?'selected':''}>${esc(label)}</option>`;
  async function load(){
    const request=++sequence;
    host.querySelectorAll('input,textarea,select,button').forEach(el=>el.disabled=true);
    const fresh=await api('/api/crm/forms/google-settings'+(doctor?'?doctor='+encodeURIComponent(doctor):''));
    if(request!==sequence)return;
    data=fresh;
    doctor ||= (settingsParams.get('google')==='connected'?data.accounts.find(a=>a.connected)?.doctor_id:'') || data.doctors[0]?.id || '';
    host.hidden=false;
    const activities=[{key:'',id:'',name:'Clínica'}];
    for(const p of data.pipelines.filter(p=>p.doctor_id===doctor && p.institute_key))if(!activities.some(a=>a.key===p.institute_key))activities.push({key:p.institute_key,id:p.id,name:'Instituto · '+p.name});
    if(!activities.some(a=>a.key===activity))activity='';
    const account=data.accounts.find(a=>a.doctor_id===doctor && a.institute_key===activity);
    host.innerHTML=`<div class="panelHead"><h2>Google · Formulários</h2><span class="metaText">${account?.connected?'Conectado':'Não conectado'}</span></div><div class="settingsForm"><label>Doutor<select id="googleOwner">${data.doctors.map(d=>option(d.id,d.name,d.id===doctor)).join('')}</select></label><label>Atividade<select id="googleActivity">${activities.map(a=>option(a.key,a.name,a.key===activity)).join('')}</select></label><p>${account?.google_email?esc(account.google_email):'Nenhuma conta Google conectada a esta atividade.'}</p><label>E-mails padrão para novos formulários<textarea id="googleDefaultEmails" rows="2" placeholder="equipe@clinica.com.br">${esc((account?.default_emails || []).join(', '))}</textarea></label><p class="connectionNote"><a href="/privacidade.html#google" target="_blank" rel="noopener">Privacidade e uso dos dados Google</a></p><div class="settingsActions"><button id="connectFormGoogle" ${data.integrations.google_ready?'':'disabled'}><i data-lucide="link"></i> ${account?.connected?'Reconectar conta Google':'Conectar conta Google'}</button><button id="saveFormGoogleDefaults" class="secondary">Salvar destinatários</button></div><p role="status">${data.integrations.google_ready?'': 'Integração do Google pendente de configuração no servidor.'}</p></div>`;
    host.querySelector('#googleOwner').onchange=async e=>{doctor=e.target.value;activity='';try{await load();}catch(error){status(error.message);}};
    host.querySelector('#googleActivity').onchange=async e=>{activity=e.target.value;try{await load();}catch(error){status(error.message);}};
    async function save(connect){
      const buttons=host.querySelectorAll('button');buttons.forEach(b=>b.disabled=true);status(connect?'Abrindo autorização do Google…':'Salvando…');
      try{const result=await api('/api/crm/forms/google-account',{method:'POST',headers:{'X-DOC4DOCS-Request':'1'},body:JSON.stringify({doctor_id:doctor,pipeline_id:activities.find(a=>a.key===activity)?.id || '',default_emails:host.querySelector('#googleDefaultEmails').value.split(/[,;\n]/).map(s=>s.trim()).filter(Boolean),connect})});if(connect)location.assign(result.url);else{await load();status('Destinatários salvos.');}}
      catch(error){status(error.message);buttons.forEach(b=>b.disabled=false);}
    }
    host.querySelector('#connectFormGoogle').onclick=()=>save(true);
    host.querySelector('#saveFormGoogleDefaults').onclick=()=>save(false);
    window.lucide?.createIcons();
  }
  try{await load();if(settingsParams.get('google')==='connected'){status('Conta Google conectada.');host.scrollIntoView({block:'start'});}}
  catch(error){host.hidden=false;host.textContent=error.message;}
};
