(() => {
  "use strict";
  const fold=value=>String(value).normalize("NFKD").replace(/\p{M}/gu,"").toLowerCase().replace(/\u00df/g,"ss");
  const shortcutAt=(value,caret)=>{const match=value.slice(0,caret).match(/(?:^|\s)(\/([\p{L}\p{N}_-]*))$/u);return match?{start:caret-match[1].length,end:caret,query:match[2]}:null;};
  const fill=(body,context)=>body.replace(/\{(nome|doutor)\}/g,(_,key)=>key==="nome"?String(context.name || "").trim().split(/\s+/)[0]:context.doctor || "");
  const matching=(items,query)=>{const term=fold(query.replace(/^\//,""));return items.filter(m=>fold(m.title).includes(term) || m.shortcut_key.includes(term)).sort((a,b)=>Number(b.shortcut_key===term)-Number(a.shortcut_key===term));};
  window.createCrmMessageTemplates=({api,esc,icon,icons,getContext,onError})=>{
    let session=null,items=[],request=0;
    const alive=s=>session===s && s.valid;
    function hide(s){if(!alive(s))return;s.menu.hidden=true;s.toggle.setAttribute("aria-expanded","false");s.mode="";}
    function status(s,message=""){if(!alive(s))return;s.status.textContent=message;s.status.hidden=!message;}
    async function load(s){
      const seq=++request;status(s,"Carregando modelos\u2026");
      try {
        const data=await api("message-templates",undefined,s.archiveView?{archived:1}:{});
        if(!alive(s) || seq!==request)return;
        if(s.archiveView)s.archived=data.templates;else items=data.templates;status(s);draw(s);
      }catch(e){if(alive(s) && seq===request)status(s,e.message);}
    }
    function draw(s){
      if(!alive(s))return;
      s.matches=matching(s.archiveView?s.archived:items,s.query);s.index=Math.max(0,Math.min(s.index,s.matches.length-1));
      s.rows.innerHTML=s.matches.map((m,i)=>`<li class="messageTemplateRow ${i===s.index?"active":""}"><button type="button" data-template-use="${i}" title="${esc(m.title)}"><span><strong>${esc(m.title)}</strong><code>/${esc(m.shortcut)}</code></span><small>${esc(m.body.slice(0,140))}</small></button><button type="button" data-template-edit="${i}" class="icon-button" title="${s.archiveView?"Restaurar":"Editar"} ${esc(m.title)}" aria-label="${s.archiveView?"Restaurar":"Editar"} ${esc(m.title)}">${icon(s.archiveView?"archive-restore":"pencil")}</button></li>`).join("") || '<li class="messageTemplatesEmpty">Nenhum modelo encontrado</li>';
      s.rows.querySelectorAll("[data-template-use]").forEach(b=>b.onclick=()=>s.archiveView?restore(s,s.matches[Number(b.dataset.templateUse)]):insert(s,s.matches[Number(b.dataset.templateUse)]));
      s.rows.querySelectorAll("[data-template-edit]").forEach(b=>b.onclick=()=>s.archiveView?restore(s,s.matches[Number(b.dataset.templateEdit)]):editor(s,s.matches[Number(b.dataset.templateEdit)]));
      s.tools.querySelector("[data-template-heading]").textContent=s.archiveView?"Modelos arquivados":"Modelos de mensagens";
      s.tools.querySelector("[data-template-archived]").innerHTML=`${icon(s.archiveView?"messages-square":"archive")} ${s.archiveView?"Ativos":"Arquivados"}`;icons();
    }
    function show(s,mode="picker",token=null){
      if(!alive(s) || s.locked)return;
      s.mode=mode;s.token=token;s.archiveView=false;s.index=0;s.query=token?.query || "";s.input.value=s.query;s.menu.hidden=false;s.toggle.setAttribute("aria-expanded","true");
      draw(s);if(mode==="picker")s.input.focus({preventScroll:true});load(s);
    }
    function insert(s,item){
      if(!alive(s) || s.locked || !item)return;
      const body=fill(item.body,getContext()),token=s.mode==="shortcut"?shortcutAt(s.textarea.value,s.textarea.selectionStart):null;
      if(s.mode==="shortcut" && !token){hide(s);return;}
      const start=token?.start ?? s.textarea.selectionStart,end=token?.end ?? s.textarea.selectionEnd;
      const value=s.textarea.value.slice(0,start)+body+s.textarea.value.slice(end);
      if(value.length>s.textarea.maxLength){status(s,`A mensagem completa pode ter at\u00e9 ${s.textarea.maxLength} caracteres.`);return;}
      s.inserting=true;s.textarea.setRangeText(body,start,end,"end");s.textarea.dispatchEvent(new Event("input",{bubbles:true}));s.inserting=false;
      hide(s);s.textarea.focus({preventScroll:true});onError("");
    }
    async function restore(s,item){
      if(s.busy)return;s.busy=true;status(s,"Restaurando modelo\u2026");
      try{await api("message-template-restore",{id:item.id,revision:item.revision});if(alive(s)){s.archiveView=false;await load(s);}}
      catch(e){if(alive(s))status(s,e.message);}finally{s.busy=false;}
    }
    function navigate(s,event){
      if(s.menu.hidden)return;
      if(event.key==="Escape"){event.preventDefault();hide(s);s.textarea.focus({preventScroll:true});return;}
      if(["ArrowDown","ArrowUp"].includes(event.key)){
        event.preventDefault();s.index=(s.index+(event.key==="ArrowDown"?1:-1)+s.matches.length)%Math.max(1,s.matches.length);draw(s);
        s.rows.querySelector(".active")?.scrollIntoView({block:"nearest"});
      }
      if(["Enter","Tab"].includes(event.key) && s.matches.length){event.preventDefault();if(s.archiveView)restore(s,s.matches[s.index]);else insert(s,s.matches[s.index]);}
    }
    function editor(s,item=null){
      hide(s);s.dialog?.remove();const dialog=document.createElement("dialog");s.dialog=dialog;
      dialog.className="messageTemplateDialog";dialog.setAttribute("aria-labelledby","messageTemplateTitle");
      dialog.innerHTML=`<header class="messageTemplateHeading"><h2 id="messageTemplateTitle">${item?"Editar modelo":"Novo modelo"}</h2><button type="button" data-editor-close class="icon-button" title="Fechar" aria-label="Fechar editor de modelo">${icon("x")}</button></header><form><label>Nome do modelo<input name="title" maxlength="80" required value="${esc(item?.title || "")}" autocomplete="off"></label><label>Atalho<span class="templateShortcutInput"><span>/</span><input name="shortcut" maxlength="40" required value="${esc(item?.shortcut || "")}" autocomplete="off" autocapitalize="none" spellcheck="false" placeholder="endere\u00e7o"></span></label><div class="templateMessageField"><label>Mensagem<textarea name="body" rows="8" maxlength="3000" required>${esc(item?.body || "")}</textarea></label><div class="templateVariables"><button type="button" data-variable="nome">{nome}</button><button type="button" data-variable="doutor">{doutor}</button><span data-template-length>${item?.body.length || 0}/3000</span></div></div>${item?`<p class="templateUpdated">${esc(item.updated_by_name)} \u00b7 ${new Date(item.updated_at*1000).toLocaleString("pt-BR")}</p>`:""}<p class="templateEditorError" role="alert" hidden></p><footer>${item?`<button type="button" data-archive>${icon("archive")} Arquivar</button>`:""}<button type="submit" class="primary">${icon("check")} Salvar modelo</button></footer></form>`;
      document.body.append(dialog);const form=dialog.querySelector("form"),error=dialog.querySelector(".templateEditorError");let busy=false,confirmArchive=false;
      const lock=value=>{busy=value;dialog.querySelectorAll("input,textarea,button").forEach(e=>e.disabled=value);};
      const close=()=>{if(busy)return;dialog.close();};
      dialog.querySelector("[data-editor-close]").onclick=close;
      dialog.oncancel=e=>{if(busy)e.preventDefault();};
      dialog.onclose=()=>{dialog.remove();if(s.dialog===dialog)s.dialog=null;if(alive(s))s.toggle.focus({preventScroll:true});};
      const body=form.elements.namedItem("body");body.oninput=()=>dialog.querySelector("[data-template-length]").textContent=`${body.value.length}/3000`;
      dialog.querySelectorAll("[data-variable]").forEach(b=>b.onclick=()=>{body.setRangeText(`{${b.dataset.variable}}`,body.selectionStart,body.selectionEnd,"end");body.oninput();body.focus();});
      form.onsubmit=async event=>{
        event.preventDefault();if(busy || !form.reportValidity())return;
        const payload={title:form.elements.namedItem("title").value,shortcut:form.elements.namedItem("shortcut").value,body:body.value,...(item?{id:item.id,revision:item.revision}:{})};
        lock(true);error.hidden=true;
        try {
          await api("message-template-save",payload);lock(false);dialog.close();if(alive(s))show(s);
        }catch(e){if(alive(s) && dialog.isConnected){error.textContent=e.message;error.hidden=false;lock(false);}}
      };
      const archive=dialog.querySelector("[data-archive]");
      if(archive)archive.onclick=async()=>{
        if(!confirmArchive){confirmArchive=true;archive.textContent="Confirmar arquivamento";return;}
        lock(true);error.hidden=true;
        try{await api("message-template-archive",{id:item.id,revision:item.revision});lock(false);dialog.close();if(alive(s))show(s);}
        catch(e){if(alive(s) && dialog.isConnected){error.textContent=e.message;error.hidden=false;lock(false);}}
      };
      icons();dialog.showModal();form.elements.namedItem("title").focus();
    }
    function destroy(){
      const s=session;if(!s)return;s.valid=false;request++;s.dialog?.remove();s.textarea.removeEventListener("input",s.inputListener);s.textarea.removeEventListener("keydown",s.keyListener);document.removeEventListener("click",s.outside);session=null;
    }
    function mount(tools,textarea){
      destroy();tools.innerHTML=`<button type="button" class="messageTemplatesToggle" aria-label="Modelos de mensagens" aria-expanded="false" title="Modelos de mensagens">${icon("messages-square")} Modelos</button><div class="messageTemplatesMenu" hidden><header><strong data-template-heading>Modelos de mensagens</strong><button type="button" data-template-refresh class="icon-button" title="Atualizar modelos" aria-label="Atualizar modelos">${icon("refresh-cw")}</button></header><input type="search" data-template-search aria-label="Buscar modelos de mensagens" placeholder="Buscar modelos" autocomplete="off"><p data-template-status role="status" hidden></p><ul class="messageTemplateRows"></ul><footer><button type="button" data-template-new>${icon("plus")} Novo modelo</button><button type="button" data-template-archived>${icon("archive")} Arquivados</button></footer></div>`;
      const s={tools,textarea,valid:true,locked:false,mode:"",query:"",index:0,matches:[],inserting:false,archiveView:false,archived:[]};session=s;
      s.toggle=tools.querySelector(".messageTemplatesToggle");s.menu=tools.querySelector(".messageTemplatesMenu");s.input=tools.querySelector("[data-template-search]");s.rows=tools.querySelector(".messageTemplateRows");s.status=tools.querySelector("[data-template-status]");
      s.toggle.onclick=()=>s.menu.hidden?show(s):hide(s);
      s.input.oninput=()=>{s.query=s.input.value;s.index=0;draw(s);};s.input.onkeydown=e=>navigate(s,e);
      tools.querySelector("[data-template-new]").onclick=()=>editor(s);
      tools.querySelector("[data-template-refresh]").onclick=()=>load(s);
      tools.querySelector("[data-template-archived]").onclick=()=>{s.archiveView=!s.archiveView;s.mode="picker";s.index=0;draw(s);load(s);};
      s.inputListener=()=>{if(s.inserting || s.locked)return;const token=shortcutAt(textarea.value,textarea.selectionStart);if(token){if(s.mode!=="shortcut")show(s,"shortcut",token);else{s.query=token.query;s.index=0;s.input.value=s.query;draw(s);}}else if(s.mode==="shortcut")hide(s);};
      s.keyListener=e=>{if(s.mode==="shortcut")navigate(s,e);};
      textarea.addEventListener("input",s.inputListener);textarea.addEventListener("keydown",s.keyListener);
      s.outside=e=>{if(!tools.contains(e.target) && e.target!==textarea && !s.dialog?.contains(e.target))hide(s);};document.addEventListener("click",s.outside);
      icons();load(s);
    }
    function availability(available){const s=session;if(!s)return;s.locked=!available;s.toggle.disabled=!available;if(!available)hide(s);}
    return {mount,destroy,availability};
  };
})();
