"use strict";
window.crmUploadFollowupMedia=({file,kind,doctor,pipeline,progress})=>new Promise((resolve,reject)=>{
  const limits={image:10,audio:16,video:16,document:25};
  if(!file || !limits[kind] || file.size>limits[kind]*1024*1024){reject(Error("Arquivo inválido ou acima do limite permitido."));return;}
  const xhr=new XMLHttpRequest();
  xhr.open("POST","/api/crm/followups/media?"+new URLSearchParams({clinic:"vielle",doctor,pipeline,kind}));
  xhr.withCredentials=true;xhr.timeout=120000;
  xhr.setRequestHeader("X-DOC4DOCS-Request","1");xhr.setRequestHeader("X-File-Name",encodeURIComponent(file.name));xhr.setRequestHeader("X-Upload-Id",crypto.randomUUID());
  xhr.upload.onprogress=event=>{if(event.lengthComputable)progress(Math.round(event.loaded/event.total*100));};
  xhr.onerror=()=>reject(Error("Falha ao carregar o anexo. Confira a conexão."));
  xhr.ontimeout=()=>reject(Error("O upload demorou demais. Tente novamente."));
  xhr.onload=()=>{try{const result=JSON.parse(xhr.responseText);if(xhr.status<200 || xhr.status>=300 || !result.ok)throw Error(result.error || "Não foi possível carregar o anexo.");resolve(result.media);}catch(err){reject(err);}};
  xhr.send(file);
});
