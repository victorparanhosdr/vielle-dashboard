"use strict";
window.createCrmInboxScope = function ({esc,icon,icons,onChange}) {
  let host=null,signature="",doctors=[],selected=[];
  function hide(){if(host){host.hidden=true;host.querySelector("details").open=false;}}
  function mount(anchor,options,ids){
    if(!host){host=document.createElement("div");host.className="crmHeaderDoctor crmInboxDoctors";anchor.before(host);}
    host.hidden=false;doctors=options;selected=ids;
    const next=JSON.stringify([options,ids]);if(next===signature)return;signature=next;
    const names=doctors.filter(d=>ids.includes(d.id)).map(d=>d.name);
    host.innerHTML=`<details><summary aria-label="Doutores do atendimento" title="${esc(names.join(" · "))}">${icon("users-round")}<span>${esc(names.length===1?names[0]:`${names.length} doutores selecionados`)}</span>${icon("chevron-down")}</summary><div class="inboxDoctorMenu" role="group" aria-label="Selecionar doutores"><header><strong>Doutores do atendimento</strong><span data-selected-count></span></header><input type="search" aria-label="Buscar doutor" placeholder="Buscar doutor…" autocomplete="off"><div class="inboxDoctorChoices">${doctors.map(d=>`<label><input type="checkbox" value="${esc(d.id)}" ${ids.includes(d.id)?"checked":""}><span>${esc(d.name)}</span></label>`).join("")}</div><p data-empty hidden>Nenhum doutor encontrado</p><footer><button type="button" data-all>Todos</button><button type="button" data-apply class="primary">Aplicar</button></footer></div></details>`;
    const details=host.querySelector("details"),inputs=[...host.querySelectorAll('[type="checkbox"]')],apply=host.querySelector("[data-apply]"),search=host.querySelector('[type="search"]');
    const update=()=>{const count=inputs.filter(i=>i.checked).length;host.querySelector("[data-selected-count]").textContent=`${count} selecionado${count===1?"":"s"}`;apply.disabled=!count;};
    const reset=()=>{inputs.forEach(i=>i.checked=selected.includes(i.value));search.value="";inputs.forEach(i=>i.closest("label").hidden=false);host.querySelector("[data-empty]").hidden=true;update();};
    details.ontoggle=()=>{if(details.open){reset();search.focus();}};
    host.onchange=update;
    host.querySelector("[data-all]").onclick=()=>{inputs.forEach(i=>i.checked=true);update();};
    apply.onclick=()=>{const ids=inputs.filter(i=>i.checked).map(i=>i.value);if(!ids.length)return;details.open=false;host.querySelector("summary").focus();if(JSON.stringify([...ids].sort())!==JSON.stringify([...selected].sort()))onChange(ids);};
    const fold=s=>s.normalize("NFD").replace(/[\u0300-\u036f]/g,"").toLowerCase();
    search.oninput=()=>{const term=fold(search.value);inputs.forEach(i=>i.closest("label").hidden=!fold(i.nextElementSibling.textContent).includes(term));host.querySelector("[data-empty]").hidden=inputs.some(i=>!i.closest("label").hidden);};
    update();icons();
  }
  document.addEventListener("click",e=>{if(host && !host.contains(e.target))host.querySelector("details").open=false;});
  document.addEventListener("keydown",e=>{if(e.key==="Escape" && host?.querySelector("details").open){host.querySelector("details").open=false;host.querySelector("summary").focus();}});
  return {mount,hide};
};
