"use strict";
window.createFormExperience=function(target,info,{onSubmit,onUpload,assetUrl,preview=false}={}){
  const esc=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const icon=name=>`<i data-lucide="${name}" aria-hidden="true"></i>`;
  const fields=info.fields.filter(f=>f.type!=="thanks"),answers={},files=new Map();
  let index=0,busy=false,finished=false,phoneControl=null;
  const source=id=>id?assetUrl(id):"";
  const icons=()=>window.lucide?.createIcons();
  target.innerHTML='<section class="flowForm"><div class="flowBackground"></div><header class="flowHeader"></header><div class="flowProgress"><span></span></div><div class="flowViewport"></div><footer class="flowFooter"></footer></section>';
  const root=target.querySelector('.flowForm'),viewport=target.querySelector('.flowViewport');
  for(const [key,value] of Object.entries(info.theme || {}))if(["background","text","answer","accent"].includes(key))root.style.setProperty(`--flow-${key}`,value);
  root.style.setProperty('--flow-radius',`${info.theme?.radius ?? 6}px`);root.style.fontFamily=`"${info.theme?.font || 'Inter'}",sans-serif`;
  if(info.theme?.image)target.querySelector('.flowBackground').style.backgroundImage=`url("${source(info.theme.image)}")`;
  if(info.theme?.logo)target.querySelector('.flowHeader').innerHTML=`<img src="${esc(source(info.theme.logo))}" alt="Logotipo">`;
  function error(message){const node=target.querySelector('.flowError');if(node){node.textContent=message;node.hidden=!message;}}
  function control(f){
    const value=answers[f.id],required=f.required?'required':'';
    const input=(type,attrs='')=>`<input class="flowInput" name="answer" aria-label="${esc(f.label)}" type="${type}" value="${esc(value || '')}" ${required} ${attrs}>`;
    if(['welcome','message'].includes(f.type))return '';
    if(f.type==='phone')return window.CrmFormPhone.markup(f.label,f.required,esc);
    if(f.type==='long')return `<textarea class="flowInput" name="answer" aria-label="${esc(f.label)}" rows="4" maxlength="10000" ${required}>${esc(value || '')}</textarea>`;
    if(f.type==='terms')return `<label class="flowTerm"><input name="answer" type="checkbox" ${value?'checked':''} ${required}><span>Li e aceito</span></label>`;
    if(f.type==='file')return `<div class="flowUpload"><label class="flowUploadButton">${icon('upload')}<span>Selecionar arquivos</span><input type="file" name="files" multiple accept=".jpg,.jpeg,.png,.webp,.gif,.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.csv,.zip,.mp3,.wav,.m4a,.ogg,.mp4,.mov"></label><div class="flowFiles">${(files.get(f.id)||[]).map(file=>`<div>${icon('file')}<span>${esc(file.name)}</span></div>`).join('')}</div></div>`;
    if(f.type==='scale')return `<div class="flowScale" role="radiogroup" aria-label="${esc(f.label)}">${Array.from({length:(f.scale || 10)+1},(_,i)=>`<label><input type="radio" name="answer" value="${i}" ${value===i?'checked':''}><span>${i}</span></label>`).join('')}</div>`;
    if(f.type==='dropdown')return `<select class="flowInput" name="answer" aria-label="${esc(f.label)}" ${required}><option value="">Selecionar</option>${f.options.map(o=>`<option value="${esc(o.label)}" ${value===o.label?'selected':''}>${esc(o.label)}</option>`).join('')}</select>`;
    if(['choice','images'].includes(f.type))return `<div class="flowChoices ${f.type==='images'?'withImages':''}">${f.options.map((o,i)=>`<label><input type="${f.multiple?'checkbox':'radio'}" name="answer" value="${esc(o.label)}" ${(Array.isArray(value)?value.includes(o.label):value===o.label)?'checked':''}>${o.image?`<img src="${esc(source(o.image))}" alt="">`:''}<span class="flowChoiceLetter">${String.fromCharCode(65+i)}</span><span>${esc(o.label)}</span>${icon('check')}</label>`).join('')}</div>`;
    if(f.type==='address')return `<div class="flowAddress">${[['zip','CEP'],['street','Rua / avenida'],['number','Número'],['extra','Complemento'],['district','Bairro'],['city','Cidade'],['state','Estado']].map(([key,label])=>`<label>${label}<input class="flowInput" name="${key}" value="${esc(value?.[key] || '')}" ${f.required && ['street','city','state'].includes(key)?'required':''} maxlength="200"></label>`).join('')}</div>`;
    const type={email:'email',phone:'tel',date:'date',url:'url',number:'number',money:'number'}[f.type] || 'text';
    return input(type,`maxlength="500" ${['number','money'].includes(f.type)?'step="any"':f.type==='phone'?'autocomplete="tel"':f.type==='email'?'autocomplete="email"':f.type==='name'?'autocomplete="name"':''} ${['cpf','cnpj'].includes(f.type)?'inputmode="numeric"':''}`);
  }
  function read(){
    const f=fields[index],form=target.querySelector('.flowQuestion');if(!form || !f)return true;
    if(!form.reportValidity())return false;
    const data=new FormData(form);let value=data.get('answer');
    if(f.type==='phone'){
      try{value=phoneControl.read();}catch(e){error(e.message);return false;}
    }
    if(f.type==='file')value=(files.get(f.id)||[]).map(file=>file.id);
    if(f.type==='address')value=Object.fromEntries(data.entries());
    if(f.type==='terms')value=data.has('answer');
    if(['choice','images'].includes(f.type) && f.multiple)value=data.getAll('answer');
    if(f.type==='scale')value=value==null?null:Number(value);
    const empty=value==null || value==='' || Array.isArray(value) && !value.length || f.type==='terms' && !value;
    if(f.required && !['welcome','message'].includes(f.type) && empty){error('Preencha este campo para continuar.');return false;}
    if(!['welcome','message'].includes(f.type))answers[f.id]=value;
    return true;
  }
  function draw(){
    const f=fields[index];if(!f)return;
    const questions=fields.filter(f=>!['welcome','message'].includes(f.type)),current=fields.slice(0,index+1).filter(f=>!['welcome','message'].includes(f.type)).length;
    target.querySelector('.flowProgress span').style.width=`${100*index/Math.max(1,fields.length)}%`;
    viewport.innerHTML=`<form class="flowQuestion" novalidate><div class="flowQuestionContent">${!['welcome','message'].includes(f.type)?`<span class="flowQuestionNumber">${current} ${icon('arrow-right')}</span>`:''}${f.media?f.media_type==='video'?`<video class="flowMedia" src="${esc(source(f.media))}" controls preload="metadata"></video>`:`<img class="flowMedia" src="${esc(source(f.media))}" alt="">`:''}<${f.type==='welcome'?'h1':'h2'}>${esc(f.label)}${f.required && !['welcome','message'].includes(f.type)?'<sup>*</sup>':''}</${f.type==='welcome'?'h1':'h2'}>${f.description?`<p class="flowDescription">${esc(f.description)}</p>`:''}${control(f)}<p class="flowError" role="alert" hidden></p><button class="flowNext" type="submit">${esc(f.button || (f.type==='welcome'?'Começar':index===fields.length-1?'Enviar':'Continuar'))}${icon(index===fields.length-1?'check':'arrow-right')}</button></div></form>`;
    target.querySelector('.flowFooter').innerHTML=`<span>${questions.length?`${current} / ${questions.length}`:''}</span><a href="/privacidade.html" target="_blank" rel="noopener">Privacidade</a><div><button type="button" data-flow="previous" aria-label="Pergunta anterior" title="Pergunta anterior" ${!index?'disabled':''}>${icon('arrow-up')}</button><button type="button" data-flow="next" aria-label="Próxima pergunta" title="Próxima pergunta">${icon('arrow-down')}</button></div>`;
    phoneControl=f.type==='phone'?window.CrmFormPhone.bind(target.querySelector('.flowPhone'),answers[f.id]):null;
    target.querySelector('.flowQuestion').onsubmit=event=>{event.preventDefault();next();};
    target.querySelector('[data-flow="previous"]').onclick=()=>{if(busy || !index)return;read();index--;draw();};
    target.querySelector('[data-flow="next"]').onclick=next;
    const upload=target.querySelector('[name="files"]');
    if(upload)upload.onchange=async()=>{
      const selected=[...upload.files];if(selected.length+(files.get(f.id)||[]).length>5){error('Escolha no máximo 5 arquivos.');return;}
      busy=true;target.querySelector('.flowNext').disabled=true;error('');
      try{const added=[];for(const file of selected){if(file.size>10*1024*1024)throw Error('O limite por arquivo é 10 MB.');added.push(preview?{id:crypto.randomUUID().replaceAll('-',''),name:file.name}:await onUpload(f.id,file));}files.set(f.id,[...(files.get(f.id)||[]),...added]);draw();}
      catch(e){error(e.message);}finally{busy=false;if(target.querySelector('.flowNext'))target.querySelector('.flowNext').disabled=false;}
    };
    viewport.scrollTop=0;icons();
  }
  async function next(){
    if(busy || finished || !read())return;
    if(index<fields.length-1){index++;draw();return;}
    busy=true;target.querySelector('.flowNext').disabled=true;error('');
    try{
      const result=preview?{finish:info.finish}:await onSubmit(answers);
      const finish=result.finish || info.finish || {};showFinish(finish);
      if(!preview && finish.mode==='redirect')location.assign(finish.url);
    }catch(e){error(e.message);target.querySelector('.flowNext').disabled=false;}finally{busy=false;}
  }
  function showFinish(finish){
    finished=true;target.querySelector('.flowProgress span').style.width='100%';
    const thanks=info.fields.find(f=>f.type==='thanks');
    viewport.innerHTML=`<div class="flowQuestionContent flowDone">${icon('circle-check')}<h1>${esc(thanks?.label || finish.title || 'Recebemos suas respostas.')}</h1><p class="flowDescription">${esc(thanks?.description || finish.description || '')}</p>${preview && finish.mode==='redirect'?`<p class="flowDescription">${esc(finish.url)}</p>`:''}</div>`;
    target.querySelector('.flowFooter').innerHTML='';icons();
  }
  const keydown=event=>{if(event.key==='Enter' && !event.shiftKey && !event.isComposing && !['TEXTAREA','BUTTON','SELECT'].includes(event.target.tagName)){event.preventDefault();next();}};
  root.addEventListener('keydown',keydown);draw();
  return {go(i){if(preview && info.fields[i]?.type==='thanks'){showFinish(info.finish || {});return;}index=Math.max(0,Math.min(fields.length-1,i));finished=false;draw();},destroy(){root.removeEventListener('keydown',keydown);target.innerHTML='';}};
};
