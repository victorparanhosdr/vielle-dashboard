"use strict";
(() => {
  const previews = {
    inbox: {number:"01 / ATENDIMENTO", title:"Cada conversa tem uma história.", description:"Visualize quem aguarda resposta, por qual número a pessoa chamou e o histórico comercial, sem sair do atendimento.", alt:"Atendimento DOC4DOCS: conversas, identificação do número e ficha comercial. Dados fictícios."},
    kanban: {number:"02 / FUNIS", title:"O próximo passo fica à vista.", description:"Organize oportunidades por etapa e profissional. Filtre, selecione e acompanhe o caminho de cada contato com mais clareza.", alt:"Funil de oportunidades DOC4DOCS, organizado por etapas. Dados fictícios."},
    dashboard: {number:"03 / DASHBOARD", title:"Decida com uma visão da operação.", description:"Acompanhe oportunidades, clientes sem resposta e indicadores da equipe para entender onde sua atenção faz diferença.", alt:"Dashboard DOC4DOCS com indicadores de atendimento e oportunidades. Dados fictícios."}
  };
  const tabs = [...document.querySelectorAll("[data-preview]")];
  function selectPreview(button) {
    const key = button.dataset.preview, data = previews[key];
    if (!data) return;
    tabs.forEach(tab => {const selected=tab===button;tab.setAttribute("aria-selected",String(selected));tab.tabIndex=selected?0:-1;});
    document.getElementById("productPanel").setAttribute("aria-labelledby",button.id);
    document.getElementById("previewNumber").textContent=data.number;
    document.getElementById("previewTitle").textContent=data.title;
    document.getElementById("previewDescription").textContent=data.description;
    const image=document.getElementById("productPreview");image.src=`/marketing-${key}.png`;image.alt=data.alt;
  }
  tabs.forEach((tab,index)=>{
    tab.addEventListener("click",()=>selectPreview(tab));
    tab.addEventListener("keydown",event=>{
      let target;
      if(event.key==="ArrowRight")target=tabs[(index+1)%tabs.length];
      if(event.key==="ArrowLeft")target=tabs[(index+tabs.length-1)%tabs.length];
      if(event.key==="Home")target=tabs[0];if(event.key==="End")target=tabs.at(-1);
      if(target){event.preventDefault();selectPreview(target);target.focus();}
    });
  });
  const menu=document.querySelector(".navMenu"),nav=document.getElementById("siteNav");
  function closeMenu(){menu?.setAttribute("aria-expanded","false");menu?.setAttribute("aria-label","Abrir menu");nav?.classList.remove("isOpen");}
  menu?.addEventListener("click",()=>{const open=menu.getAttribute("aria-expanded")!=="true";menu.setAttribute("aria-expanded",String(open));menu.setAttribute("aria-label",open?"Fechar menu":"Abrir menu");nav.classList.toggle("isOpen",open);});
  nav?.addEventListener("click",event=>{if(event.target.closest("a"))closeMenu();});
  document.addEventListener("keydown",event=>{if(event.key==="Escape")closeMenu();});

  const attributionKeys=["utm_source","utm_medium","utm_campaign","utm_content","utm_term"];
  let attribution={};
  try {
    const saved=JSON.parse(sessionStorage.getItem("doc4docs:site-origin")||"{}");
    if(saved && typeof saved==="object" && !Array.isArray(saved))for(const key of attributionKeys)if(typeof saved[key]==="string")attribution[key]=saved[key].slice(0,200);
    const query=new URLSearchParams(location.search);
    if(attributionKeys.some(key=>query.has(key))){attribution=Object.fromEntries(attributionKeys.map(key=>[key,(query.get(key)||"").slice(0,200)]));sessionStorage.setItem("doc4docs:site-origin",JSON.stringify(attribution));}
  } catch (_) {}
  const mount=document.getElementById("demoMount");
  if(mount){
    // Static, shared form; visitor values are never interpolated into HTML.
    mount.innerHTML=`<dialog class="demoDialog" id="demoDialog" aria-labelledby="demoTitle">
      <div class="demoDialogHeader"><span class="eyebrow">VAMOS CONVERSAR</span><button class="iconButton" type="button" data-close-demo aria-label="Fechar solicitação"><i data-lucide="x"></i></button></div>
      <h2 id="demoTitle">Seu próximo passo<br>com o DOC4DOCS.</h2><p>Conte sobre sua operação. Nossa equipe entrará em contato para entender o que você precisa.</p>
      <form id="demoForm"><label>Seu nome<input name="name" autocomplete="name" maxlength="100" required></label><div class="demoFormGrid"><label>E-mail de contato<input name="email" type="email" autocomplete="email" maxlength="180" required></label><label>Clínica ou instituto<input name="organization" autocomplete="organization" maxlength="140" required></label></div>
      <label>WhatsApp de contato</label><div id="demoPhone"></div>
      <label>Tenho interesse em<select name="plan"><option value="Demonstração">Conhecer a plataforma</option><option value="Essencial">Plano Essencial</option><option value="Conectado">Plano Conectado</option><option value="Institutos">Plano Institutos</option></select></label>
      <label>Sobre sua operação <span>(opcional)</span><textarea name="message" rows="2" maxlength="1000" placeholder="O que você gostaria de organizar na sua equipe?"></textarea></label>
      <div class="websiteField" aria-hidden="true"><label>Website<input name="website" tabindex="-1" autocomplete="off"></label></div>
      <label class="privacyCheck"><input type="checkbox" name="privacy" required><span>Estou ciente da <a href="/privacidade.html" target="_blank" rel="noopener">Política de Privacidade</a> e solicito contato sobre o DOC4DOCS.</span></label>
      <p class="demoError" id="demoError" role="alert"></p><button class="button primary" id="demoSubmit" type="submit">Solicitar contato <i data-lucide="arrow-up-right"></i></button>
      </form><div class="demoSuccess" id="demoSuccess" tabindex="-1" hidden><i data-lucide="circle-check"></i><h3>Pedido recebido.</h3><p>Nossa equipe entrará em contato pelos dados que você informou.</p><button class="button primary" type="button" data-close-demo>Concluir <i data-lucide="check"></i></button></div></dialog>`;
    const dialog=document.getElementById("demoDialog"),form=document.getElementById("demoForm"),host=document.getElementById("demoPhone"),success=document.getElementById("demoSuccess"),error=document.getElementById("demoError"),submit=document.getElementById("demoSubmit");
    let phone,requestId=crypto.randomUUID().replaceAll("-",""),pending=false,received=false;
    if(window.CrmFormPhone){host.innerHTML=window.CrmFormPhone.markup("WhatsApp de contato",true,s=>s);phone=window.CrmFormPhone.bind(host,"");}
    else {submit.disabled=true;error.textContent="Não foi possível carregar o formulário. Atualize a página.";}
    function close(){if(!pending)dialog.close();}
    document.querySelectorAll("[data-close-demo]").forEach(button=>button.addEventListener("click",close));
    dialog.addEventListener("cancel",event=>{if(pending)event.preventDefault();});
    dialog.addEventListener("click",event=>{const r=dialog.getBoundingClientRect();if(event.target===dialog&&(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom))close();});
    document.querySelectorAll("[data-demo]").forEach(button=>button.addEventListener("click",()=>{
      closeMenu();
      if(received){form.reset();received=false;requestId=crypto.randomUUID().replaceAll("-","");host.querySelector("input").value="";}
      form.hidden=false;success.hidden=true;error.textContent="";form.elements.plan.value=button.dataset.plan||"Demonstração";dialog.showModal();form.elements.name.focus();
    }));
    form.addEventListener("submit",async event=>{
      event.preventDefault();if(pending)return;error.textContent="";
      let number;try{number=phone.read();}catch(problem){error.textContent=problem.message;return;}
      if(!form.reportValidity())return;
      const body={request_id:requestId,name:form.elements.name.value,email:form.elements.email.value,organization:form.elements.organization.value,phone:number,plan:form.elements.plan.value,message:form.elements.message.value,website:form.elements.website.value,privacy:form.elements.privacy.checked,attribution,source_path:location.pathname};
      pending=true;submit.disabled=true;submit.textContent="Registrando pedido...";
      try {
        const response=await fetch("/api/public/demo",{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json","X-DOC4DOCS-Request":"1"},body:JSON.stringify(body)});
        const data=await response.json();if(!response.ok||!data.ok)throw Error(data.error||"Não foi possível registrar. Tente novamente.");
        received=true;form.hidden=true;success.hidden=false;success.focus();
      } catch(problem){error.textContent=problem instanceof TypeError?"Não foi possível conectar. Seus dados continuam aqui para tentar novamente.":problem.message;}
      finally {pending=false;submit.disabled=false;submit.textContent="Solicitar contato";}
    });
  }
  window.lucide?.createIcons();
})();
