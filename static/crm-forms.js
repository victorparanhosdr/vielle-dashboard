"use strict";
window.createCrmForms=({state,api,esc,icon,icons,notice,profile})=>{
  const types={welcome:['door-open','Boas-vindas'],name:['user-round','Nome próprio'],email:['at-sign','E-mail'],short:['type','Resposta curta'],long:['align-left','Texto longo'],phone:['phone','Telefone'],date:['calendar-days','Data'],url:['link','Link'],identity:['contact-round','Documento de identidade'],cpf:['contact-round','CPF'],cnpj:['building-2','CNPJ'],number:['hash','Número'],money:['banknote','Valor monetário'],address:['map-pin','Endereço'],choice:['list-checks','Múltipla escolha'],scale:['star','Escala de satisfação'],images:['images','Seleção de imagem'],dropdown:['list-filter','Seleção de lista'],file:['paperclip','Arquivo anexo'],message:['message-square','Mensagem'],terms:['shield-check','Termos de uso'],thanks:['circle-check','Agradecimento']};
  const staticTypes=['welcome','message','thanks'];
  const tracking=window.CrmAttribution;
  let linkParams={};
  let root=null,doctor='',seq=0,form=null,tab='editor',index=0,experience=null,integrations={},dirty=false,saving=false,saveTimer=0,page=1;
  const find=selector=>root?.querySelector(selector);
  const uid=()=>crypto.randomUUID().replaceAll('-','');
  const makeField=(type,label=types[type][1])=>({id:uid(),type,label,description:'',required:['name','phone'].includes(type),multiple:false,options:['choice','images','dropdown'].includes(type)?[{label:'Opção 1',image:''},{label:'Opção 2',image:''}]:[],scale:10,media:'',button:''});
  function defaultDefinition(){return {title:'Novo formulário',fields:[makeField('welcome','Vamos nos conhecer?'),makeField('name','Qual é o seu nome?'),makeField('phone','Qual é o seu WhatsApp?'),makeField('email','Qual é o seu e-mail?'),makeField('thanks','Obrigado pelo seu tempo.')],theme:{background:'#101614',text:'#f1f4f2',answer:'#ffffff',accent:'#b4d4c1',font:'Inter',radius:6,logo:'',image:''},finish:{mode:'message',title:'Recebemos suas respostas.',description:'Em breve entraremos em contato.',url:''},emails:[...(integrations.default_emails || [])],crm:{enabled:false,pipeline_id:'',stage_id:'',name_field:'',phone_field:'',email_field:''},google_enabled:false};}
  const url=()=>location.origin+'/f/'+form.slug;
  const asset=id=>'/api/crm/forms/file?'+new URLSearchParams({clinic:'vielle',doctor,id});
  const option=(value,label,selected)=>`<option value="${esc(value)}" ${selected?'selected':''}>${esc(label)}</option>`;
  const status={draft:'Rascunho',published:'Publicado',paused:'Pausado',archived:'Arquivado'};
  function active(request){return root?.isConnected && doctor===state.doctor && request===seq;}
  function setError(message=''){const node=find('#formsError');if(node){node.textContent=message;node.hidden=!message;}else if(message)notice(message);}
  async function mount(target){
    if(root===target && doctor===state.doctor)return;
    unmount();root=target;doctor=state.doctor;const request=++seq;
    root.innerHTML='<p class="connectionNote">Carregando formulários…</p>';
    try{const data=await api('forms',undefined,{doctor});if(!active(request))return;integrations=data.integrations;root._accounts=data.accounts || [];root._forms=data.forms;const requested=new URLSearchParams(location.search).get('form');if(requested){await open(requested,new URLSearchParams(location.search).has('responses')?'responses':new URLSearchParams(location.search).has('google')?'settings':'editor');}else library(data.forms);}
    catch(e){if(active(request))root.innerHTML=`<p role="alert">${esc(e.message)}</p>`;}
  }
  async function list(){
    if(dirty && !await save())return;
    const request=++seq;form=null;experience?.destroy();experience=null;
    const current=new URL(location.href);current.searchParams.delete('form');current.searchParams.delete('responses');history.replaceState(null,'',current);
    try{const data=await api('forms',undefined,{doctor});if(active(request)){integrations=data.integrations;root._accounts=data.accounts || [];library(data.forms);}}
    catch(e){if(active(request))setError(e.message);}
  }
  function library(items){
    root._forms=items;
    root.innerHTML=`<div class="formsLibrary"><header><div class="formsStats"><span><b>${items.length}</b> formulários</span><span><b>${items.reduce((n,f)=>n+f.responses,0)}</b> respostas</span></div><button class="primary" data-command="create">${icon('plus')} Novo formulário</button></header><p id="formsError" role="alert" hidden></p><div class="formsLibraryRows">${items.map(f=>`<article class="formLibraryRow"><button data-open-form="${esc(f.id)}">${icon('clipboard-list')}<span><strong>${esc(f.name)}</strong><small>${esc(status[f.status])} · ${f.responses} respostas</small></span></button><span>${new Date(f.updated_at*1000).toLocaleDateString('pt-BR')}</span><button class="icon-button" data-response-form="${esc(f.id)}" title="Ver respostas" aria-label="Ver respostas de ${esc(f.name)}">${icon('table-2')}</button><button class="icon-button" data-open-form="${esc(f.id)}" title="Editar formulário" aria-label="Editar ${esc(f.name)}">${icon('arrow-up-right')}</button></article>`).join('') || `<div class="formsEmpty">${icon('clipboard-list')}<h2>Nenhum formulário</h2></div>`}</div></div>`;
    bind();icons();
  }
  async function open(id,nextTab='editor'){
    if(dirty && !await save())return;
    const request=++seq;
    try{const data=await api('forms/item',undefined,{doctor,id});if(!active(request))return;form=data.form;tab=nextTab;index=0;dirty=false;page=1;draw();const current=new URL(location.href);current.searchParams.set('form',id);history.replaceState(null,'',current);}
    catch(e){if(active(request))setError(e.message);}
  }
  function toolbar(){return `<header class="formsEditorBar"><button data-command="back" class="icon-button" title="Todos os formulários" aria-label="Todos os formulários">${icon('arrow-left')}</button><input id="formTitle" aria-label="Título do formulário" maxlength="120" value="${esc(form.definition.title)}"><span id="formSaveState" role="status">${dirty?'Alterações não salvas':status[form.status]}</span><button data-command="preview" class="icon-button" title="Pré-visualizar formulário" aria-label="Pré-visualizar formulário">${icon('eye')}</button><button data-command="save">${icon('save')} Salvar</button><button data-command="publish" class="primary">${icon('check')}${form.status==='published'?'Publicar alterações':'Publicar'}</button></header><nav class="formsEditorTabs" aria-label="Editor do formulário">${[['editor','Editor'],['appearance','Aparência'],['settings','Configurações'],['share','Compartilhar'],['responses','Respostas']].map(([key,label])=>`<button data-form-tab="${key}" aria-current="${tab===key?'page':'false'}">${label}</button>`).join('')}</nav><p id="formsError" role="alert" hidden></p>`;}
  function draw(){
    if(!root || !form)return;experience?.destroy();experience=null;
    root.innerHTML=`<section class="formsEditor">${toolbar()}<div id="formEditorContent"></div></section>`;
    if(tab==='editor')editor();else if(tab==='appearance')appearance();else if(tab==='settings'){settings();trackingSettings();integrationControls();}else if(tab==='share'){share();trackingLink();}else responses();
    bind();icons();
  }
  function fieldList(){return `<aside class="formFieldList"><div class="formFieldRows">${form.definition.fields.map((f,i)=>`<div class="formFieldRow ${i===index?'selected':''}"><button data-field="${i}"><span>${i+1}</span>${icon(types[f.type][0])}<span><strong>${esc(f.label)}</strong><small>${esc(types[f.type][1])}</small></span></button></div>`).join('')}</div><div class="formAddField"><select aria-label="Tipo do novo campo" id="newFieldType">${Object.entries(types).map(([key,[,label]])=>option(key,label,key==='short')).join('')}</select><button data-command="addField">${icon('plus')} Adicionar campo</button></div></aside>`;}
  function editor(){
    const f=form.definition.fields[index];
    find('#formEditorContent').innerHTML=`<div class="formBuilder">${fieldList()}<div class="formCanvas"><div id="formLivePreview"></div></div><aside class="formInspector"><header><strong>Pergunta ${index+1}</strong><div><button data-command="fieldUp" class="icon-button" aria-label="Mover campo para cima" title="Mover para cima" ${index===0?'disabled':''}>${icon('arrow-up')}</button><button data-command="fieldDown" class="icon-button" aria-label="Mover campo para baixo" title="Mover para baixo" ${index===form.definition.fields.length-1?'disabled':''}>${icon('arrow-down')}</button><button data-command="deleteField" class="icon-button" aria-label="Remover campo" title="Remover campo" ${form.definition.fields.length===1?'disabled':''}>${icon('trash-2')}</button></div></header><label>Tipo<select data-field-prop="type">${Object.entries(types).map(([key,[,label]])=>option(key,label,f.type===key)).join('')}</select></label><label>Título<textarea data-field-prop="label" rows="3" maxlength="500">${esc(f.label)}</textarea></label><label>Descrição<textarea data-field-prop="description" rows="4" maxlength="3000">${esc(f.description)}</textarea></label>${!staticTypes.includes(f.type)?`<label class="check"><input type="checkbox" data-field-prop="required" ${f.required?'checked':''}>Obrigatório</label>`:''}${['choice','images','dropdown'].includes(f.type)?`<label>Opções<textarea data-field-prop="options" rows="5" aria-label="Opções, uma por linha">${esc(f.options.map(o=>o.label).join('\n'))}</textarea></label>${f.type!=='dropdown'?`<label class="check"><input type="checkbox" data-field-prop="multiple" ${f.multiple?'checked':''}>Permitir várias escolhas</label>`:''}${f.type==='images'?f.options.map((o,i)=>`<label class="formUploadControl">${esc(o.label)}<input type="file" data-upload="option" data-option="${i}" accept="image/*"></label>`).join(''):''}`:''}${f.type==='scale'?`<label>Escala máxima<input type="number" data-field-prop="scale" min="2" max="10" value="${f.scale}"></label>`:''}<label>Texto do botão<input data-field-prop="button" maxlength="60" value="${esc(f.button || '')}"></label><label class="formUploadControl">Imagem / vídeo<input type="file" data-upload="field" accept="image/*,video/mp4,video/quicktime"></label>${f.media?`<button data-command="removeMedia">${icon('x')} Remover mídia</button>`:''}</aside></div>`;
    preview();
  }
  function preview(){const host=find('#formLivePreview');if(!host)return;experience?.destroy();experience=window.createFormExperience(host,form.definition,{preview:true,assetUrl:asset});experience.go(index);}
  function appearance(){
    const t=form.definition.theme;
    find('#formEditorContent').innerHTML=`<div class="formAppearance"><div class="formAppearanceControls"><h2>Identidade visual</h2>${[['accent','Botões'],['text','Perguntas'],['answer','Respostas'],['background','Fundo']].map(([key,label])=>`<label class="formColor">${label}<input type="color" data-theme="${key}" value="${esc(t[key])}"><span>${esc(t[key])}</span></label>`).join('')}<label>Fonte<select data-theme="font">${['Inter','Lato','Georgia','Arial','Verdana','Trebuchet MS'].map(font=>option(font,font,t.font===font)).join('')}</select></label><label>Bordas<input type="range" data-theme="radius" min="0" max="24" step="1" value="${t.radius}"></label><label class="formUploadControl">Logotipo<input type="file" data-upload="logo" accept="image/*"></label>${t.logo?'<button data-remove-theme="logo">Remover logotipo</button>':''}<label class="formUploadControl">Imagem de fundo<input type="file" data-upload="background" accept="image/*"></label>${t.image?'<button data-remove-theme="image">Remover fundo</button>':''}</div><div class="formCanvas"><div id="formLivePreview"></div></div></div>`;preview();
  }
  function fieldSelect(key,label,kinds){return `<label>${label}<select data-crm="${key}"><option value="">Selecionar campo</option>${form.definition.fields.filter(f=>kinds.includes(f.type)).map(f=>option(f.id,f.label,form.definition.crm[key]===f.id)).join('')}</select></label>`;}
  function trackingSettings(){
    const settings=form.definition.tracking ||= {enabled:true,capture_click_ids:false,forward_on_redirect:false,defaults:{}};
    find('.formSettings').insertAdjacentHTML('beforeend',`<section class="formTrackingSettings"><h2>Rastreamento de origem · UTM</h2><label class="check"><input type="checkbox" data-tracking="enabled" ${settings.enabled?'checked':''}>Capturar parâmetros de campanha</label><div class="formSettingsGrid">${tracking.keys.map(key=>`<label>${esc(tracking.labels[key])}<input data-utm-default="${key}" maxlength="250" value="${esc(settings.defaults[key] || '')}" placeholder="${key}"></label>`).join('')}</div><label class="check"><input type="checkbox" data-tracking="capture_click_ids" ${settings.capture_click_ids?'checked':''}>Registrar IDs de clique dos anúncios</label><label class="check"><input type="checkbox" data-tracking="forward_on_redirect" ${settings.forward_on_redirect?'checked':''}>Manter UTMs no link de redirecionamento</label></section>`);
  }
  function trackingLink(){
    linkParams={...form.definition.tracking?.defaults};
    find('.formShare').insertAdjacentHTML('beforeend',`<section class="formCampaignLink"><h2>Link de campanha</h2><div class="formSettingsGrid">${tracking.keys.map(key=>`<label>${esc(tracking.labels[key])}<input data-utm-link="${key}" maxlength="250" value="${esc(linkParams[key] || '')}" placeholder="${key}"></label>`).join('')}</div><label>Link com UTMs<div class="formLinkRow"><input id="formCampaignUrl" readonly aria-label="Link com UTMs" value="${esc(tracking.link(url(),linkParams))}"><button data-command="copyTracking" class="icon-button" aria-label="Copiar link com UTMs" title="Copiar link com UTMs">${icon('copy')}</button></div></label></section>`);
  }
  function integrationControls(){
    const host=find('#formEmails').closest('section'),provider=form.definition.email_provider || 'auto';
    const ready=(provider!=='google' && integrations.mail_ready) || (provider!=='smtp' && form.google_mail_ready);
    const status=host.querySelector('.formIntegrationStatus');status.textContent=ready?(form.google_mail_ready && provider!=='smtp'?'Gmail conectado · '+form.google_email:'Servidor de e-mail configurado'):'Remetente não conectado';status.classList.toggle('ready',Boolean(ready));
    if(ready)host.querySelector('.connectionNote')?.remove();
    host.insertAdjacentHTML('beforeend',`<div class="formIntegrationControls"><label>Enviar por<select id="formEmailProvider">${option('auto','Automático',provider==='auto')}${option('google','Gmail conectado',provider==='google')}${option('smtp','Servidor SMTP',provider==='smtp')}</select></label><div class="commands"><button data-command="gmailConnect" ${!integrations.google_ready?'disabled':''}>${icon('mail')} ${form.google_mail_ready?'Reconectar Gmail':'Conectar Gmail'}</button>${state.data.can_manage?`<button data-command="testEmail" ${ready?'':'disabled'}>${icon('send')} Enviar e-mail de teste</button>`:''}</div></div>`);
    const googleHost=find('#formGoogleEnabled').closest('section');
    if(form.google_email)googleHost.querySelector('.formIntegrationStatus').textContent='Conta conectada · '+form.google_email;
    const reusable=(root._accounts || []).filter(f=>f.connected && f.institute_key===form.institute_key);
    if(reusable.length)googleHost.insertAdjacentHTML('beforeend',`<div class="formIntegrationControls"><label>Conta já conectada<select id="reuseGoogleSource">${reusable.map(f=>option(f.id,(f.google_email || 'Conta Google')+(f.id===form.google_account_id?' · Em uso':''),false)).join('')}</select></label><button data-command="reuseGoogle">${icon('link')} Usar esta conta</button></div>`);
  }
  function mailTest(){
    const dialog=document.createElement('dialog');dialog.className='formMailTestDialog';dialog.innerHTML=`<header><h2>Testar notificação</h2><button class="icon-button" data-close aria-label="Fechar teste" title="Fechar">${icon('x')}</button></header><form><label>Destinatário<input type="email" name="to" required value="${esc(form.definition.emails[0] || form.google_email || '')}"></label><button type="submit" class="primary">${icon('send')} Enviar teste</button><p role="status" hidden></p></form>`;document.body.append(dialog);dialog.showModal();dialog.querySelector('[data-close]').onclick=()=>dialog.close();dialog.onclose=()=>dialog.remove();dialog.querySelector('form').onsubmit=async event=>{event.preventDefault();event.submitter.disabled=true;const status=dialog.querySelector('[role=status]');status.hidden=false;status.textContent='Enviando…';try{await api('forms/test-email',{id:form.id,doctor_id:doctor,to:new FormData(event.currentTarget).get('to')});status.textContent='Teste aceito pelo serviço de e-mail. Confira a caixa de entrada e o spam.';}catch(e){status.textContent=e.message;event.submitter.disabled=false;}};icons();
  }
  function settings(){
    const d=form.definition,c=d.crm,p=state.data.pipelines.find(p=>p.id===c.pipeline_id);
    find('#formEditorContent').innerHTML=`<div class="formSettings"><section><h2>Finalização</h2><label>Ao concluir<select data-finish="mode">${option('message','Página de agradecimento',d.finish.mode==='message')}${option('redirect','Redirecionar para um link',d.finish.mode==='redirect')}</select></label>${d.finish.mode==='redirect'?`<label>Link de destino<input type="url" data-finish="url" value="${esc(d.finish.url)}" placeholder="https://"></label>`:`<label>Título<input data-finish="title" maxlength="200" value="${esc(d.finish.title)}"></label><label>Mensagem<textarea data-finish="description" rows="3" maxlength="2000">${esc(d.finish.description)}</textarea></label>`}</section><section><h2>Notificações por e-mail</h2><label>Destinatários<textarea id="formEmails" rows="3" placeholder="nome@clinica.com.br, equipe@clinica.com.br">${esc(d.emails.join(', '))}</textarea></label><span class="formIntegrationStatus ${integrations.mail_ready?'ready':''}">${icon(integrations.mail_ready?'circle-check':'circle-alert')}${integrations.mail_ready?'Servidor de e-mail configurado':'Servidor de e-mail pendente de configuração'}</span>${!integrations.mail_ready && state.data.can_manage?'<p class="connectionNote">Configuração do servidor: CRM_SMTP_HOST, CRM_SMTP_PORT, CRM_SMTP_FROM, CRM_SMTP_USER e CRM_SMTP_PASSWORD.</p>':''}</section><section><h2>Oportunidades no CRM</h2><label class="check"><input type="checkbox" data-crm="enabled" ${c.enabled?'checked':''}>Criar ou vincular oportunidade a cada resposta</label><div class="formSettingsGrid"><label>Funil<select data-crm="pipeline_id"><option value="">Selecionar funil</option>${state.data.pipelines.map(p=>option(p.id,p.name,p.id===c.pipeline_id)).join('')}</select></label><label>Fase de entrada<select data-crm="stage_id"><option value="">Selecionar fase</option>${(p?.stages || []).map(s=>option(s.id,s.name,s.id===c.stage_id)).join('')}</select></label>${fieldSelect('name_field','Nome do contato',['name','short'])}${fieldSelect('phone_field','WhatsApp',['phone'])}${fieldSelect('email_field','E-mail',['email'])}</div></section><section><h2>Google Planilhas</h2><label class="check"><input type="checkbox" id="formGoogleEnabled" ${d.google_enabled?'checked':''}>Enviar respostas para a planilha</label><span class="formIntegrationStatus ${form.google_connected?'ready':''}">${icon(form.google_connected?'circle-check':'circle-alert')}${form.google_connected?'Conta Google conectada':integrations.google_ready?'Conta Google não conectada':'Conexão Google pendente de configuração'}</span><div class="commands"><button data-command="googleConnect" ${!integrations.google_ready?'disabled':''}>${icon('link')} ${form.google_connected?'Reconectar Google':'Conectar conta Google'}</button><button data-command="googleSheet" ${!form.google_connected || form.sheet_id?'disabled':''}>${icon('sheet')} Criar planilha</button>${form.sheet_id?`<a href="https://docs.google.com/spreadsheets/d/${encodeURIComponent(form.sheet_id)}/edit" target="_blank" rel="noopener">Abrir planilha ${icon('arrow-up-right')}</a>`:''}</div>${!integrations.google_ready && state.data.can_manage?`<p class="connectionNote">Configure CRM_GOOGLE_CLIENT_ID e CRM_GOOGLE_CLIENT_SECRET no servidor. URI de retorno:</p><code class="formCallback">${esc(integrations.google_redirect_uri)}</code>`:''}</section></div>`;
  }
  function share(){find('#formEditorContent').innerHTML=`<div class="formShare"><h2>${esc(form.name)}</h2><span class="formStatus">${esc(status[form.status])}</span><label>Link do formulário<div class="formLinkRow"><input readonly value="${esc(url())}" aria-label="Link do formulário"><button data-command="copy" class="icon-button" aria-label="Copiar link" title="Copiar link">${icon('copy')}</button><a href="${esc(url())}" target="_blank" rel="noopener" aria-label="Abrir formulário" title="Abrir formulário">${icon('arrow-up-right')}</a></div></label>${form.status==='published'?'<button data-command="pause">Pausar recebimento</button>':''}${form.status!=='archived'?'<button data-command="archive">Arquivar formulário</button>':''}</div>`;}
  async function responses(){
    const request=seq,id=form.id;find('#formEditorContent').innerHTML='<p class="connectionNote">Carregando respostas…</p>';
    try{const data=await api('forms/responses',undefined,{doctor,id,page});if(!active(request) || form?.id!==id || tab!=='responses')return;
      find('#formEditorContent').innerHTML=`<div class="formResponses"><header><strong>${data.total} respostas</strong><a class="formExport" href="/api/crm/forms/export?${new URLSearchParams({clinic:'vielle',doctor,id})}">${icon('download')} Exportar CSV</a></header>${data.items.map((r,i)=>{const name=r.definition.fields.find(f=>f.type==='name'),phone=r.definition.fields.find(f=>f.type==='phone');return `<article class="formResponseRow"><button data-response="${i}"><strong>${esc(name?r.answers[name.id] || 'Resposta recebida':'Resposta recebida')}</strong><small>${esc(phone?r.answers[phone.id] || '':'')} · ${new Date(r.created_at*1000).toLocaleString('pt-BR')}</small></button><span class="formResponseState">${esc({created:'Nova oportunidade',reused:'Contato reaproveitado',disabled:'Sem automação'}[r.lead_state] || r.lead_state)}</span><button class="icon-button" data-response="${i}" title="Ver resposta" aria-label="Ver resposta">${icon('arrow-up-right')}</button></article>`;}).join('') || '<div class="formsEmpty">Nenhuma resposta recebida.</div>'}<footer class="campaignPagination"><button class="icon-button" data-command="previousResponses" aria-label="Respostas anteriores" ${page===1?'disabled':''}>${icon('chevron-left')}</button><span>${page} de ${Math.max(1,Math.ceil(data.total/30))}</span><button class="icon-button" data-command="nextResponses" aria-label="Próximas respostas" ${!data.has_more?'disabled':''}>${icon('chevron-right')}</button></footer></div>`;
      root._responses=data.items;icons();
    }catch(e){if(active(request))setError(e.message);}
  }
  function responseDialog(r){
    const dialog=document.createElement('dialog');dialog.className='formResponseDialog';
    const render=(field,value)=>field.type==='file'?(value || []).map(id=>{const f=r.files.find(f=>f.id===id);return f?`<a href="${esc(asset(id))}" target="_blank" rel="noopener">${icon('paperclip')}${esc(f.name)}</a>`:'';}).join(''):esc(typeof value==='boolean'?value?'Sim':'Não':Array.isArray(value)?value.join(', '):typeof value==='object'?Object.values(value).filter(Boolean).join(', '):value ?? '—');
    dialog.innerHTML=`<header><h2>Resposta recebida</h2><button class="icon-button" data-close aria-label="Fechar resposta" title="Fechar">${icon('x')}</button></header><p class="connectionNote">${new Date(r.created_at*1000).toLocaleString('pt-BR')}</p><dl>${r.definition.fields.filter(f=>!staticTypes.includes(f.type)).map(f=>`<div><dt>${esc(f.label)}</dt><dd>${render(f,r.answers[f.id])}</dd></div>`).join('')}</dl>${r.deliveries.length?`<h3>Integrações</h3>${r.deliveries.map(d=>`<p class="formDelivery">${icon(d.kind==='google'?'sheet':'mail')}<span>${esc(d.kind==='google'?'Google Planilhas':d.target)}<small>${esc({queued:'Na fila',processing:'Processando',sent:d.kind==='google'?'Planilha atualizada':'Aceito pelo servidor de e-mail',unknown:'Envio sem confirmação',failed:'Falha',blocked:'Aguardando conexão de e-mail'}[d.status])}${d.last_error?' · '+esc(d.last_error):''}</small></span></p>`).join('')}`:''}${r.lead_id?'<button data-open-opportunity>Ver oportunidade</button>':''}${r.deliveries.some(d=>d.kind==='google' && d.status==='failed')?'<button data-retry-sheet>Atualizar planilha novamente</button>':''}`;
    document.body.append(dialog);dialog.showModal();dialog.querySelector('[data-close]').onclick=()=>dialog.close();dialog.onclose=()=>dialog.remove();
    dialog.querySelector('dl').insertAdjacentHTML('afterend',tracking.response(r.attribution,esc));
    dialog.querySelector('[data-open-opportunity]')?.addEventListener('click',()=>{dialog.close();profile(r.lead_id);});
    dialog.querySelector('[data-retry-sheet]')?.addEventListener('click',async e=>{e.target.disabled=true;try{await api('forms/retry',{id:form.id,doctor_id:doctor,response_id:r.id});dialog.close();responses();}catch(err){notice(err.message);e.target.disabled=false;}});icons();
  }
  function changed(){dirty=true;find('#formSaveState').textContent='Alterações não salvas';clearTimeout(saveTimer);}
  async function save(){
    if(!form || saving)return false;saving=true;clearTimeout(saveTimer);const current=form,doc=doctor;const version=JSON.stringify(form.definition);find('#formSaveState').textContent='Salvando…';setError('');
    try{const data=await api('forms/save',{id:current.id,revision:current.revision,doctor_id:doc,definition:current.definition});if(form===current && doctor===doc){form.revision=data.form.revision;form.name=data.form.name;if(JSON.stringify(form.definition)===version){form.definition=data.form.definition;dirty=false;}find('#formSaveState').textContent=dirty?'Alterações não salvas':'Salvo';}return true;}
    catch(e){setError(e.message);return false;}finally{saving=false;}
  }
  async function upload(input){
    const file=input.files[0];if(!file)return;if(dirty && !await save())return;
    input.disabled=true;setError('');
    try{const response=await fetch('/api/crm/forms/upload?'+new URLSearchParams({clinic:'vielle',doctor,id:form.id}),{method:'POST',headers:{'Content-Type':'application/octet-stream','X-File-Name':encodeURIComponent(file.name),'X-DOC4DOCS-Request':'1'},body:file});const result=await response.json();if(!response.ok)throw Error(result.error || 'Falha ao enviar imagem.');const f=form.definition.fields[index];if(input.dataset.upload==='logo')form.definition.theme.logo=result.file.id;else if(input.dataset.upload==='background')form.definition.theme.image=result.file.id;else if(input.dataset.upload==='option')f.options[Number(input.dataset.option)].image=result.file.id;else{f.media=result.file.id;f.media_type=result.file.mime.startsWith('video/')?'video':'image';}changed();draw();}
    catch(e){setError(e.message);}finally{if(input.isConnected)input.disabled=false;}
  }
  function bind(){
    root.onclick=async event=>{
      const b=event.target.closest('button');if(!b)return;
      if(b.dataset.openForm)return open(b.dataset.openForm);
      if(b.dataset.responseForm)return open(b.dataset.responseForm,'responses');
      if(b.dataset.response!==undefined)return responseDialog(root._responses[Number(b.dataset.response)]);
      if(b.dataset.formTab){tab=b.dataset.formTab;draw();return;}
      if(b.dataset.field!==undefined){index=Number(b.dataset.field);draw();return;}
      if(b.dataset.removeTheme){form.definition.theme[b.dataset.removeTheme]='';changed();draw();return;}
      const action=b.dataset.command;
      try{
        if(action==='create'){b.disabled=true;const result=await api('forms/save',{doctor_id:doctor,definition:defaultDefinition()});await open(result.form.id);}
        if(action==='back')await list();
        if(action==='save')await save();
        if(['publish','pause','archive'].includes(action)){if(dirty && !await save())return;b.disabled=true;const data=await api('forms/publish',{doctor_id:doctor,id:form.id,revision:form.revision,status:{publish:'published',pause:'paused',archive:'archived'}[action]});form={...data.form};dirty=false;draw();}
        if(action==='addField'){const type=find('#newFieldType').value;const at=form.definition.fields.findIndex(f=>f.type==='thanks');index=at<0?form.definition.fields.length:at;form.definition.fields.splice(index,0,makeField(type));changed();draw();}
        if(action==='deleteField'){form.definition.fields.splice(index,1);index=Math.min(index,form.definition.fields.length-1);changed();draw();}
        if(action==='fieldUp' || action==='fieldDown'){const other=index+(action==='fieldUp'?-1:1);[form.definition.fields[index],form.definition.fields[other]]=[form.definition.fields[other],form.definition.fields[index]];index=other;changed();draw();}
        if(action==='removeMedia'){form.definition.fields[index].media='';changed();draw();}
        if(action==='copy'){await navigator.clipboard.writeText(url());find('#formSaveState').textContent='Link copiado';}
        if(action==='copyTracking'){await navigator.clipboard.writeText(tracking.link(url(),linkParams));find('#formSaveState').textContent='Link com UTMs copiado';}
        if(action==='preview'){const dialog=document.createElement('dialog');dialog.className='formExperienceDialog';dialog.innerHTML=`<button class="formPreviewClose" aria-label="Fechar prévia" title="Fechar prévia">${icon('x')}</button><div></div>`;document.body.append(dialog);const exp=window.createFormExperience(dialog.querySelector('div'),form.definition,{preview:true,assetUrl:asset});dialog.querySelector('button').onclick=()=>dialog.close();dialog.onclose=()=>{exp.destroy();dialog.remove();};dialog.showModal();icons();}
        if(action==='googleConnect'){if(dirty && !await save())return;const result=await api('forms/google-connect',{id:form.id,doctor_id:doctor});location.assign(result.url);}
        if(action==='gmailConnect'){if(dirty && !await save())return;const result=await api('forms/google-connect',{id:form.id,doctor_id:doctor,mail:true});location.assign(result.url);}
        if(action==='reuseGoogle'){if(dirty && !await save())return;b.disabled=true;const result=await api('forms/google-bind',{id:form.id,doctor_id:doctor,account_id:find('#reuseGoogleSource').value});form=result.form;draw();}
        if(action==='testEmail'){if(dirty && !await save())return;mailTest();}
        if(action==='googleSheet'){if(dirty && !await save())return;b.disabled=true;const result=await api('forms/google-sheet',{id:form.id,doctor_id:doctor});form.sheet_id=result.sheet_id;draw();}
        if(action==='previousResponses' || action==='nextResponses'){page+=action==='nextResponses'?1:-1;responses();}
      }catch(e){setError(e.message);}finally{if(b.isConnected)b.disabled=false;}
    };
    root.oninput=event=>{
      const input=event.target;if(!form || input.type==='file')return;
      if(input.id==='formTitle'){form.definition.title=input.value;changed();return;}
      if(input.dataset.utmLink){linkParams[input.dataset.utmLink]=input.value;find('#formCampaignUrl').value=tracking.link(url(),linkParams);return;}
      if(input.dataset.utmDefault){form.definition.tracking.defaults[input.dataset.utmDefault]=input.value;changed();return;}
      if(input.dataset.tracking){form.definition.tracking[input.dataset.tracking]=input.checked;changed();return;}
      if(input.dataset.fieldProp){const key=input.dataset.fieldProp,f=form.definition.fields[index];if(key==='type')return;f[key]=input.type==='checkbox'?input.checked:key==='scale'?Number(input.value):key==='options'?input.value.split('\n').filter(s=>s.trim()).map((label,i)=>({label:label.trim(),image:f.options[i]?.image || ''})):input.value;changed();preview();const label=find(`.formFieldRow.selected strong`);if(label)label.textContent=f.label;}
      if(input.dataset.theme){form.definition.theme[input.dataset.theme]=input.dataset.theme==='radius'?Number(input.value):input.value;changed();preview();if(input.type==='color')input.nextElementSibling.textContent=input.value;}
      if(input.id==='formEmails'){form.definition.emails=input.value.split(/[,;\n]/).map(s=>s.trim()).filter(Boolean);changed();}
      if(input.dataset.finish){form.definition.finish[input.dataset.finish]=input.value;changed();}
    };
    root.onchange=event=>{
      const input=event.target;if(!form)return;
      if(input.type==='file')return upload(input);
      if(input.dataset.fieldProp==='type'){const f=form.definition.fields[index],next=input.value;f.type=next;if(['choice','images','dropdown'].includes(next) && !f.options.length)f.options=makeField(next).options;changed();draw();}
      if(input.dataset.finish==='mode'){form.definition.finish.mode=input.value;changed();draw();}
      if(input.dataset.crm){const key=input.dataset.crm;form.definition.crm[key]=input.type==='checkbox'?input.checked:input.value;if(key==='pipeline_id'){form.definition.crm.stage_id=state.data.pipelines.find(p=>p.id===input.value)?.stages[0]?.id || '';}if(key==='enabled' && input.checked){for(const [name,type] of [['name_field','name'],['phone_field','phone'],['email_field','email']])form.definition.crm[name] ||= form.definition.fields.find(f=>f.type===type)?.id || '';}changed();if(['enabled','pipeline_id'].includes(key))draw();}
      if(input.id==='formGoogleEnabled'){form.definition.google_enabled=input.checked;changed();}
      if(input.id==='formEmailProvider'){form.definition.email_provider=input.value;changed();draw();}
    };
  }
  function unmount(){seq++;clearTimeout(saveTimer);experience?.destroy();experience=null;if(root){root.onclick=root.oninput=root.onchange=null;}root=null;form=null;dirty=false;}
  window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
  return {mount,unmount,canLeave:()=>!dirty || window.confirm('Há alterações não salvas no formulário. Sair sem salvar?')};
};
