"use strict";
(async()=>{
  const target=document.getElementById('publicForm'),slug=location.pathname.split('/').filter(Boolean).at(-1),endpoint='/api/public-forms/'+encodeURIComponent(slug);
  try{
    const response=await fetch(endpoint,{credentials:'omit',cache:'no-store'}),data=await response.json();
    if(!response.ok)throw Error(data.error || 'Formulário não disponível.');
    document.title=data.form.title;
    const request=async(url,body,headers={})=>{const response=await fetch(url,{method:'POST',credentials:'omit',headers:{'X-DOC4DOCS-Request':'1',...headers},body});const value=await response.json();if(!response.ok)throw Error(value.error || 'Não foi possível enviar.');return value;};
    window.createFormExperience(target,data.form,{assetUrl:id=>endpoint+'/assets/'+encodeURIComponent(id),
      onUpload:async(field,file)=>(await request(endpoint+'/upload?field='+encodeURIComponent(field),file,{'X-Form-Token':data.token,'X-File-Name':encodeURIComponent(file.name),'Content-Type':'application/octet-stream'})).file,
      onSubmit:answers=>request(endpoint,JSON.stringify({token:data.token,answers,website:''}),{'Content-Type':'application/json'})});
  }catch(e){target.innerHTML='<p class="publicFormStatus" role="alert"></p>';target.firstElementChild.textContent=e.message;}
})();
