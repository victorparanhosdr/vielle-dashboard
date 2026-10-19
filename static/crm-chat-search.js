"use strict";
window.createCrmChatSearch = function ({api,esc,icon,icons,onResult,onClose}) {
  let root=null,button=null,scope=null,timer=null,revision=0,shown=false,busy=false;
  let results=[],position=-1,total=0,before=0,query="",direction="all";
  const $=selector=>root?.querySelector(selector);
  function status(value="") {
    if(!root)return;
    $("[data-search-count]").textContent=value || (position>=0?`${position+1} de ${total}`:query?"Nenhum resultado":"");
    $("[data-previous]").disabled=busy || position<=0;
    $("[data-next]").disabled=busy || !results.length || position+1>=results.length && !before;
  }
  function destroy() {
    clearTimeout(timer);revision++;shown=false;root=null;button=null;scope=null;
    results=[];position=-1;total=0;before=0;query="";direction="all";busy=false;
  }
  function close(restore=true) {
    if(!root || !shown)return;
    clearTimeout(timer);revision++;shown=false;root.hidden=true;query="";results=[];position=-1;total=0;before=0;busy=false;
    $("[data-query]").value="";status();button.setAttribute("aria-expanded","false");button.focus();if(restore)onClose();
  }
  function mount(target,toggle,nextScope) {
    destroy();root=target;button=toggle;scope={...nextScope};root.hidden=true;
    root.innerHTML=`<div class="chatSearchFields">${icon("search")}<input type="search" data-query maxlength="160" aria-label="Buscar nesta conversa" placeholder="Buscar nesta conversa" autocomplete="off"><select data-direction aria-label="Mensagens de"><option value="all">Todos</option><option value="in">Paciente</option><option value="out">Equipe</option></select></div><div class="chatSearchNavigation"><span data-search-count role="status" aria-live="polite"></span><button type="button" data-previous class="icon-button" title="Resultado anterior" aria-label="Resultado anterior" disabled>${icon("chevron-up")}</button><button type="button" data-next class="icon-button" title="Pr\u00f3ximo resultado" aria-label="Pr\u00f3ximo resultado" disabled>${icon("chevron-down")}</button><button type="button" data-close class="icon-button" title="Fechar busca" aria-label="Fechar busca">${icon("x")}</button></div>`;
    button.onclick=()=>{if(shown){close();return;}shown=true;root.hidden=false;button.setAttribute("aria-expanded","true");$("[data-query]").focus();};
    $("[data-query]").oninput=()=>{clearTimeout(timer);revision++;busy=true;status("Buscando\u2026");timer=setTimeout(()=>search(),300);};
    $("[data-query]").onkeydown=event=>{
      if(event.key==="Escape"){event.preventDefault();close();}
      if(event.key==="Enter"){event.preventDefault();clearTimeout(timer);search();}
    };
    $("[data-direction]").onchange=()=>{clearTimeout(timer);return search();};
    $("[data-previous]").onclick=()=>move(-1);
    $("[data-next]").onclick=()=>move(1);
    $("[data-close]").onclick=close;
    icons();
  }
  async function search() {
    if(!root || !shown)return;
    const request=++revision;
    query=$("[data-query]").value.trim();direction=$("[data-direction]").value;
    results=[];position=-1;total=0;before=0;busy=!!query;status(query?"Buscando\u2026":"");
    if(!query){onResult(0);return;}
    try {
      const data=await api("conversation-search",undefined,{...scope,search:query,direction});
      if(!root || request!==revision)return;
      results=data.results;total=data.total;before=data.next_before;busy=false;
      if(results.length){position=0;onResult(results[0].seq);}else onResult(0);
      status();
    }catch(e){if(root && request===revision){busy=false;status(e.message);}}
  }
  async function move(step) {
    if(busy || !root)return;
    const next=position+step,request=revision;
    if(next<0)return;
    if(next>=results.length && before){
      busy=true;status("Buscando\u2026");
      try {
        const data=await api("conversation-search",undefined,{...scope,search:query,direction,before});
        if(!root || request!==revision)return;
        const seen=new Set(results.map(r=>r.seq));results.push(...data.results.filter(r=>!seen.has(r.seq)));before=data.next_before;
      }catch(e){if(root && request===revision){busy=false;status(e.message);}return;}
      busy=false;
    }
    if(next<results.length){position=next;onResult(results[position].seq);}
    status();
  }
  function highlight(value) {
    if(!shown || !query)return esc(value);
    const ranges=[],parts=[];let normalized="",offset=0;
    for(const char of value){const folded=char.normalize("NFKD").replace(/\p{M}/gu,"").toLowerCase().replace(/\u00df/g,"ss");for(let i=0;i<folded.length;i++)ranges.push([offset,offset+char.length]);normalized+=folded;offset+=char.length;}
    const term=query.normalize("NFKD").replace(/\p{M}/gu,"").toLowerCase().replace(/\u00df/g,"ss");
    if(!term)return esc(value);
    let cursor=0,found=normalized.indexOf(term);
    while(found>=0){const start=ranges[found][0],end=ranges[found+term.length-1][1];parts.push(esc(value.slice(cursor,start)),`<mark>${esc(value.slice(start,end))}</mark>`);cursor=end;found=normalized.indexOf(term,found+term.length);}
    return parts.join("")+esc(value.slice(cursor));
  }
  return {mount,destroy,close,highlight,target:()=>shown?results[position]?.seq || 0:0,term:()=>shown?query:""};
};
