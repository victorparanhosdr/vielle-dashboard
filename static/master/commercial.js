"use strict";
(() => {
  const $=id=>document.getElementById(id);
  let page=1,request=0,leads=[],stages={},editing=null;
  const notice=(text,error=false)=>{$("salesNotice").textContent=text;$("salesNotice").hidden=!text;$("salesNotice").classList.toggle("error",error);};
  function el(tag,text,cls){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;return node;}
  const date=value=>new Intl.DateTimeFormat("pt-BR",{dateStyle:"short",timeStyle:"short"}).format(new Date(value));
  async function api(path,body){const response=await fetch(path,body===undefined?{}:{method:"POST",headers:{"Content-Type":"application/json","X-DOC4DOCS-Request":"1"},body:JSON.stringify(body)});const data=await response.json();if(!response.ok||!data.ok)throw Error(data.error||"Não foi possível carregar os pedidos.");return data;}
  async function load(){
    const sequence=++request;$("salesRows").setAttribute("aria-busy","true");
    try{
      const data=await api(`/api/master/marketing-leads?${new URLSearchParams({page,search:$("salesSearch").value,status:$("salesStatus").value})}`);
      if(sequence!==request)return;
      if($("salesNotice").classList.contains("error"))notice("");
      const pages=Math.max(1,Math.ceil(data.total/data.page_size));if(page>pages){page=pages;return load();}
      stages=data.stages;leads=data.leads;
      if($("salesStatus").options.length===1)for(const [key,label] of Object.entries(stages)){$("salesStatus").append(new Option(label,key));$("salesLeadStatus").append(new Option(label,key));}
      $("salesStats").replaceChildren();for(const [key,label] of Object.entries(stages)){const item=el("div"),name=el("span",label),count=el("strong",String(data.counts[key]||0));item.dataset.stage=key;item.append(name,count);$("salesStats").append(item);}
      $("salesRows").replaceChildren();
      for(const lead of leads){
        const row=el("tr");row.dataset.leadId=lead.id;
        const identity=el("td"),open=el("button",lead.name,"salesName"),contact=el("small",lead.email);identity.dataset.label="Interessado";open.type="button";open.addEventListener("click",()=>show(lead));identity.append(open,contact);row.append(identity);
        for(const [label,text] of [["Organização",lead.organization],["Interesse",lead.plan],["Etapa",stages[lead.status]],["Origem",lead.attribution.utm_source||"Site / direto"],["Recebido",date(lead.created_at)]]){const cell=el("td",text);cell.dataset.label=label;if(label==="Etapa")cell.dataset.stage=lead.status;row.append(cell);}
        const actions=el("td"),button=el("button",undefined,"secondary iconButton");button.type="button";button.title="Abrir pedido";button.setAttribute("aria-label",`Abrir pedido de ${lead.name}`);const icon=el("i");icon.dataset.lucide="arrow-up-right";button.append(icon);button.addEventListener("click",()=>show(lead));actions.append(button);row.append(actions);$("salesRows").append(row);
      }
      if(!leads.length){const row=el("tr"),cell=el("td","Nenhum pedido encontrado.","emptyRow");cell.colSpan=7;row.append(cell);$("salesRows").append(row);}
      $("salesTotal").textContent=`${data.total} ${data.total===1?"pedido":"pedidos"}`;$("salesPageLabel").textContent=`Página ${page} de ${pages}`;$("salesPrev").disabled=page<=1;$("salesNext").disabled=page>=pages;window.lucide?.createIcons();
    }catch(error){if(sequence===request){notice(error.message,true);leads=[];const row=el("tr"),cell=el("td","Não foi possível carregar. Tente buscar novamente.","emptyRow");cell.colSpan=7;row.append(cell);$("salesRows").replaceChildren(row);$("salesPrev").disabled=true;$("salesNext").disabled=true;$("salesPageLabel").textContent="Carregamento indisponível";$("salesTotal").textContent="";}}finally{if(sequence===request)$("salesRows").removeAttribute("aria-busy");}
  }
  function show(lead){
    editing=lead;$("salesLeadTitle").textContent=lead.name;$("salesLeadOrganization").textContent=lead.organization;$("salesError").textContent="";$("salesLeadStatus").value=lead.status;$("salesNotes").value=lead.notes;$("salesMessage").textContent=lead.message||"Sem mensagem adicional.";$("salesDetails").replaceChildren();
    const details={"Interesse":lead.plan,"Recebido":date(lead.created_at),"Atualizado":date(lead.updated_at),"Página":lead.source_path,"Ciência de privacidade":lead.privacy_version,...Object.fromEntries(Object.entries(lead.attribution).filter(([,value])=>value))};
    for(const [key,value] of Object.entries(details)){$("salesDetails").append(el("dt",key),el("dd",value));}
    $("salesContactLinks").replaceChildren();for(const [href,label,kind] of [[`mailto:${encodeURIComponent(lead.email)}`,lead.email,"mail"],[`tel:${lead.phone}`,lead.phone,"phone"],[`https://wa.me/${lead.phone.replace(/\D/g,"")}`,"Conversar no WhatsApp","message-circle"]]){const link=el("a",undefined,"buttonLink secondary"),icon=el("i");link.href=href;icon.dataset.lucide=kind;link.append(icon,el("span",label));if(href.startsWith("https")){link.target="_blank";link.rel="noopener noreferrer";}$("salesContactLinks").append(link);}
    window.lucide?.createIcons();$("salesDialog").showModal();
  }
  function close(){if(!$("salesSave").disabled)$("salesDialog").close();}
  $("salesClose").addEventListener("click",close);$("salesCancel").addEventListener("click",close);
  $("salesDialog").addEventListener("cancel",event=>{if($("salesSave").disabled)event.preventDefault();});
  $("salesLeadForm").addEventListener("submit",async event=>{event.preventDefault();if(!editing||$("salesSave").disabled)return;$("salesSave").disabled=true;$("salesError").textContent="";try{await api(`/api/master/marketing-leads/${editing.id}`,{status:$("salesLeadStatus").value,notes:$("salesNotes").value,revision:editing.revision});$("salesDialog").close();notice("Acompanhamento atualizado.");await load();}catch(error){$("salesError").textContent=error.message;}finally{$("salesSave").disabled=false;}});
  $("salesFilters").addEventListener("submit",event=>{event.preventDefault();page=1;load();});$("salesStatus").addEventListener("change",()=>{page=1;load();});let timer;$("salesSearch").addEventListener("input",()=>{clearTimeout(timer);timer=setTimeout(()=>{page=1;load();},250);});$("salesPrev").addEventListener("click",()=>{page--;load();});$("salesNext").addEventListener("click",()=>{page++;load();});
  load();
})();
