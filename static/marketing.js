"use strict";
(() => {
  const previews = {
    monthly: {number:"01 / RESUMO MENSAL", title:"O mês está no ritmo da sua meta?", description:"Veja o total vendido, o valor recebido, a meta e a projeção. Compare a evolução diária e o ticket médio por profissional e período.", alt:"Resumo mensal DOC4DOCS com vendas, recebimentos, meta e projeção. Dados fictícios.",label:"Resumo mensal",src:"/marketing-monthly-v3.png"},
    sales: {number:"02 / VENDAS E ORÇAMENTOS", title:"O que virou venda? O que ainda é oportunidade?", description:"Compare o valor vendido, a quantidade de vendas e o valor orçado ao longo dos dias. Separe resultado realizado de potencial comercial.",alt:"Gráfico real de vendas e orçamentos DOC4DOCS com dados fictícios.",label:"Vendas e orçamentos",src:"/marketing-sales-v3.png"},
    procedures: {number:"03 / PROCEDIMENTOS MAIS VENDIDOS",title:"Quais procedimentos movem o seu faturamento?",description:"Consulte o ranking por valor vendido, a quantidade e as categorias. Entenda a composição das vendas antes de decidir onde concentrar sua atenção.",alt:"Ranking real de procedimentos DOC4DOCS por valor e quantidade. Dados fictícios.",label:"Procedimentos",src:"/marketing-procedures-v3.png"}
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
    const image=document.getElementById("productPreview");image.src=data.src;image.alt=data.alt;
    document.querySelector("[data-zoom-preview]")?.setAttribute("aria-label",`Ampliar ${data.label}`);
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
  document.querySelector("[data-next-preview]")?.addEventListener("click",()=>{
    const current=tabs.findIndex(tab=>tab.getAttribute("aria-selected")==="true");
    const next=tabs[(current+1)%tabs.length];if(next)selectPreview(next);
  });
  const screenDialog=document.getElementById("screenDialog");
  document.querySelector("[data-zoom-preview]")?.addEventListener("click",()=>{
    const selected=tabs.find(tab=>tab.getAttribute("aria-selected")==="true")||tabs[0];
    const data=previews[selected?.dataset.preview];if(!data||!screenDialog)return;
    document.getElementById("screenTitle").textContent=`${data.label} DOC4DOCS`;
    const image=document.getElementById("screenImage");image.src=data.src;image.alt=data.alt;
    screenDialog.showModal();
  });
  document.querySelector("[data-close-screen]")?.addEventListener("click",()=>screenDialog?.close());
  const aiExamples = {
    personal:{score:"70",criterion:"Personalização do Atendimento",status:"Atenção à abordagem genérica",quote:"“Olá. Segue nossa tabela. Qualquer coisa, estamos à disposição.”",reading:"A cliente perguntou sobre sua necessidade, mas recebeu uma resposta genérica, sem retomar o contexto da conversa.",action:"Oriente a equipe a consultar o histórico, usar o nome e fazer uma pergunta ligada ao interesse da cliente."},
    clarity:{score:"70",criterion:"Coerência e Clareza",status:"O combinado precisa ficar claro",quote:"“Pode vir quinta, naquele horário que falei. Depois vemos o restante.”",reading:"A mensagem deixa horário e próximos passos indefinidos. Na simulação, a falha de clareza desconta 30 pontos da nota inicial de 100.",action:"Peça ao colaborador que confirme data, horário, local e próximo passo em uma mensagem objetiva."},
    followup:{score:"50",criterion:"Pontualidade no Follow-Up",status:"O retorno combinado não aconteceu",quote:"Cliente: “Você ficou de me retornar ontem sobre o orçamento. Ainda estou aguardando.”",reading:"O trecho indica uma quebra do retorno combinado. Na simulação, a falha de pontualidade no follow-up desconta 50 pontos.",action:"Revise o combinado com o colaborador e organize uma tarefa com data e responsável para manter a continuidade do contato."}
  };
  const aiTabs=[...document.querySelectorAll("[data-ai-example]")];
  function selectAi(button){
    const example=aiExamples[button.dataset.aiExample];if(!example)return;
    aiTabs.forEach(tab=>{const selected=tab===button;tab.setAttribute("aria-selected",String(selected));tab.tabIndex=selected?0:-1;});
    document.getElementById("aiExamplePanel").setAttribute("aria-labelledby",button.id);
    document.getElementById("aiQuote").textContent=example.quote;
    document.getElementById("aiReading").textContent=example.reading;
    document.getElementById("aiAction").textContent=example.action;
    document.getElementById("aiScore").textContent=example.score;
    document.getElementById("aiCriterion").textContent=example.criterion;
    document.getElementById("aiStatus").textContent=example.status;
  }
  aiTabs.forEach((tab,index)=>{
    tab.addEventListener("click",()=>selectAi(tab));
    tab.addEventListener("keydown",event=>{
      let target;if(event.key==="ArrowRight")target=aiTabs[(index+1)%aiTabs.length];if(event.key==="ArrowLeft")target=aiTabs[(index+aiTabs.length-1)%aiTabs.length];if(event.key==="Home")target=aiTabs[0];if(event.key==="End")target=aiTabs.at(-1);
      if(target){event.preventDefault();selectAi(target);target.focus();}
    });
  });
  const menu=document.querySelector(".navMenu"),nav=document.getElementById("siteNav");
  function closeMenu(){menu?.setAttribute("aria-expanded","false");menu?.setAttribute("aria-label","Abrir menu");nav?.classList.remove("isOpen");}
  menu?.addEventListener("click",()=>{const open=menu.getAttribute("aria-expanded")!=="true";menu.setAttribute("aria-expanded",String(open));menu.setAttribute("aria-label",open?"Fechar menu":"Abrir menu");nav.classList.toggle("isOpen",open);});
  nav?.addEventListener("click",event=>{if(event.target.closest("a"))closeMenu();});
  document.addEventListener("keydown",event=>{if(event.key==="Escape")closeMenu();});
  const conversion=document.querySelector("[data-mobile-conversion]"),hero=document.querySelector(".hero");
  if(conversion&&hero&&window.IntersectionObserver){
    conversion.hidden=true;
    new window.IntersectionObserver(entries=>{conversion.hidden=entries[0].isIntersecting;},{threshold:0}).observe(hero);
  }

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
      <div class="demoDialogHeader"><span class="eyebrow">PARA DONOS E GESTORES DE CLÍNICAS</span><button class="iconButton" type="button" data-close-demo aria-label="Fechar solicitação"><i data-lucide="x"></i></button></div>
      <h2 id="demoTitle">Veja a gestão da clínica<br>com outros olhos.</h2><p>Informe seu contato. A equipe fala com você pelo WhatsApp para entender sua operação e combinar a demonstração. Este pedido não confirma um horário nem gera cobrança.</p>
      <form id="demoForm"><label>Seu nome<input name="name" autocomplete="name" maxlength="100" required></label>
      <label>WhatsApp de contato</label><div id="demoPhone"></div>
      <details class="optionalFields"><summary>Adicionar informações da clínica <span>opcional</span><i data-lucide="plus"></i></summary><div class="demoFormGrid"><label>E-mail de contato<input name="email" type="email" autocomplete="email" maxlength="180"></label><label>Clínica ou instituto<input name="organization" autocomplete="organization" maxlength="140"></label></div>
      <label>Tenho interesse em<select name="plan"><option value="Demonstração">Demonstração para minha operação</option><option value="Essencial">Plano Essencial</option><option value="Conectado">Plano Conectado</option><option value="Institutos">Plano Institutos</option></select></label>
      <label>Sobre sua operação <span>(opcional)</span><textarea name="message" rows="2" maxlength="1000" placeholder="O que você gostaria de organizar na sua equipe?"></textarea></label>
      </details><div class="websiteField" aria-hidden="true"><label>Website<input name="website" tabindex="-1" autocomplete="off"></label></div>
      <label class="privacyCheck"><input type="checkbox" name="privacy" required><span>Estou ciente da <a href="/privacidade.html" target="_blank" rel="noopener">Política de Privacidade</a> e solicito contato sobre o DOC4DOCS.</span></label>
      <p class="demoError" id="demoError" role="alert"></p><button class="button primary" id="demoSubmit" type="submit">Solicitar demonstração <i data-lucide="arrow-up-right"></i></button>
      </form><div class="demoSuccess" id="demoSuccess" tabindex="-1" hidden><i data-lucide="circle-check"></i><h3>Pedido de demonstração recebido.</h3><p>A equipe entrará em contato pelo WhatsApp informado para entender sua operação e combinar a apresentação. Seu horário ainda será confirmado.</p><button class="button primary" type="button" data-close-demo>Concluir <i data-lucide="check"></i></button></div></dialog>`;
    const dialog=document.getElementById("demoDialog"),form=document.getElementById("demoForm"),host=document.getElementById("demoPhone"),success=document.getElementById("demoSuccess"),error=document.getElementById("demoError"),submit=document.getElementById("demoSubmit");
    let phone,requestId=crypto.randomUUID().replaceAll("-",""),pending=false,received=false;
    if(window.CrmFormPhone){host.innerHTML=window.CrmFormPhone.markup("WhatsApp de contato",true,s=>s);phone=window.CrmFormPhone.bind(host,"");}
    else {submit.disabled=true;error.textContent="Não foi possível carregar o formulário. Atualize a página.";}
    function close(){if(!pending)dialog.close();}
    document.querySelectorAll("[data-close-demo]").forEach(button=>button.addEventListener("click",close));
    form.addEventListener("invalid",event=>{const optional=form.querySelector(".optionalFields");if(optional.contains(event.target))optional.open=true;},true);
    dialog.addEventListener("cancel",event=>{if(pending)event.preventDefault();});
    dialog.addEventListener("click",event=>{const r=dialog.getBoundingClientRect();if(event.target===dialog&&(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom))close();});
    document.querySelectorAll("[data-demo]").forEach(button=>button.addEventListener("click",()=>{
      closeMenu();
      if(received){form.reset();received=false;requestId=crypto.randomUUID().replaceAll("-","");host.querySelector("input").value="";}
      form.hidden=false;success.hidden=true;error.textContent="";form.elements.plan.value=button.dataset.plan||"Demonstração";form.querySelector(".optionalFields").open=Boolean(button.dataset.plan);dialog.showModal();form.elements.name.focus();
    }));
    form.addEventListener("submit",async event=>{
      event.preventDefault();if(pending)return;error.textContent="";
      let number;try{number=phone.read();}catch(problem){error.textContent=problem.message;return;}
      const optional=form.querySelector(".optionalFields");
      if(!form.elements.email.validity.valid)optional.open=true;
      if(!form.reportValidity())return;
      const body={request_id:requestId,name:form.elements.name.value,email:form.elements.email.value,organization:form.elements.organization.value,phone:number,plan:form.elements.plan.value,message:form.elements.message.value,website:form.elements.website.value,privacy:form.elements.privacy.checked,attribution,source_path:location.pathname};
      pending=true;submit.disabled=true;submit.textContent="Registrando pedido...";
      try {
        const response=await fetch("/api/public/demo",{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json","X-DOC4DOCS-Request":"1"},body:JSON.stringify(body)});
        const data=await response.json();if(!response.ok||!data.ok)throw Error(data.error||"Não foi possível registrar. Tente novamente.");
        received=true;form.hidden=true;success.hidden=false;success.focus();
      } catch(problem){error.textContent=problem instanceof TypeError?"Não foi possível conectar. Seus dados continuam aqui para tentar novamente.":problem.message;}
      finally {pending=false;submit.disabled=false;submit.textContent="Solicitar demonstração";}
    });
  }
  window.lucide?.createIcons();
})();
