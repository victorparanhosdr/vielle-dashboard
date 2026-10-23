(() => {
  "use strict";
  const limits={image:10,audio:16,video:16,document:25};
  const accepts={image:".jpg,.jpeg,.png,.webp,.gif",audio:".mp3,.ogg,.opus,.wav,.m4a,.aac,.webm,.mp4",video:".mp4,.mov",document:".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.csv,.zip"};
  const names={image:"Imagem",audio:"Áudio",video:"Vídeo",document:"Arquivo"};
  const size=bytes=>bytes<1048576?`${Math.ceil(bytes/1024)} KB`:`${(bytes/1048576).toFixed(1).replace('.',',')} MB`;
  window.crmMediaMarkup=(media,esc,icon)=>{
    if(!media?.url?.startsWith("/api/crm/media?"))return "";
    const url=esc(media.url),name=esc(media.name);
    const transcript=window.crmAudioTranscriptMarkup?.(media,esc,icon) || "";
    if(media.state==="remote")return `<div class="chatAttachmentPending"><button type="button" data-load-media="${esc(media.id)}">${icon("download")} Carregar ${names[media.kind]?.toLowerCase() || "anexo"}</button><small>${name}${media.size?` · ${size(media.size)}`:""}</small><p data-media-error role="status" hidden></p></div>${transcript}`;
    if(media.kind==="image")return `<a class="chatImage" href="${url}" target="_blank" rel="noopener"><img src="${url}" alt="${name}" loading="lazy"></a>`;
    if(media.kind==="audio")return `<audio class="chatAudio" controls preload="metadata" src="${url}" aria-label="${name}"></audio>${transcript}`;
    if(media.kind==="video")return `<video class="chatVideo" controls playsinline preload="metadata" src="${url}" aria-label="${name}"></video>`;
    return `<a class="chatDocument" href="${url}" download="${name}">${icon("file-down")}<span><strong>${name}</strong><small>${size(media.size)}</small></span></a>`;
  };
  window.createCrmMedia=({esc,icon,icons,onChange,onBusy,onError})=>{
    let session=null;
    const alive=s=>session===s && s.valid;
    function busy(s,value){s.busy=value;if(alive(s))onBusy(value);}
    function release(s){if(s.url){URL.revokeObjectURL(s.url);s.url="";}}
    function draw(s){
      if(!alive(s))return;
      const p=s.preview;p.hidden=!s.file && !s.media && !s.recording;
      if(p.hidden){p.innerHTML="";return;}
      if(s.recording){
        p.innerHTML=`<div class="audioRecording"><span class="recordingDot" aria-hidden="true"></span><strong>Gravando áudio</strong><time data-elapsed>0:00</time><button type="button" data-stop class="icon-button" title="Parar gravação" aria-label="Parar gravação">${icon("square")}</button><button type="button" data-cancel class="icon-button" title="Descartar gravação" aria-label="Descartar gravação">${icon("trash-2")}</button></div>`;
        p.querySelector("[data-stop]").onclick=()=>stop(s,false);p.querySelector("[data-cancel]").onclick=()=>stop(s,true);icons();return;
      }
      const item=s.media || s.file,url=s.url || item.url,kind=s.kind || item.kind;
      const player=kind==="image"?`<img src="${esc(url)}" alt="Prévia do anexo">`:kind==="audio"?`<audio controls preload="metadata" src="${esc(url)}"></audio>`:kind==="video"?`<video controls playsinline preload="metadata" src="${esc(url)}"></video>`:`<span class="attachmentFileIcon">${icon("file-text")}</span>`;
      p.innerHTML=`<div class="attachmentDraft ${kind}"><div class="attachmentVisual">${player}</div><div class="attachmentDescription"><strong>${esc(item.name)}</strong><span>${names[kind]} · ${size(item.size)}</span><span role="status" data-upload-status>${s.busy?"Preparando anexo…":s.media?"Pronto para enviar":"Anexo não carregado"}</span><progress max="100" value="0" ${s.busy?"":"hidden"} aria-label="Progresso do anexo"></progress></div><button type="button" data-remove class="icon-button" title="Remover anexo" aria-label="Remover anexo">${icon("x")}</button></div>`;
      p.querySelector("[data-remove]").onclick=()=>clear();icons();
    }
    function clear(){
      const s=session;if(!s)return;
      s.uploadSeq++;s.xhr?.abort();release(s);s.file=null;s.media=null;s.kind="";busy(s,false);onChange(null);draw(s);
    }
    function uploadFile(s,file,kind){
      if(!alive(s) || s.locked || s.recording)return;
      if(!file.size || file.size>limits[kind]*1048576){onError(`O limite para ${names[kind].toLowerCase()} é ${limits[kind]} MB.`);return;}
      const ext="."+file.name.split(".").at(-1).toLowerCase();
      if(!accepts[kind].split(",").includes(ext)){onError("Formato não aceito. Selecione outro arquivo.");return;}
      clear();s.file=file;s.kind=kind;s.url=URL.createObjectURL(file);busy(s,true);onError("");draw(s);
      const seq=++s.uploadSeq,xhr=new XMLHttpRequest();s.xhr=xhr;
      const query=new URLSearchParams({clinic:"vielle",doctor:s.scope.doctor,channel:s.scope.channel,contact:s.scope.contact,kind});
      xhr.open("POST","/api/crm/media-upload?"+query);xhr.timeout=120000;
      xhr.setRequestHeader("X-DOC4DOCS-Request","1");
      xhr.setRequestHeader("Content-Type",file.type || "application/octet-stream");
      xhr.setRequestHeader("X-File-Name",encodeURIComponent(file.name));xhr.setRequestHeader("X-Upload-Id",crypto.randomUUID());
      xhr.upload.onprogress=e=>{if(alive(s) && seq===s.uploadSeq && e.lengthComputable){const value=Math.round(e.loaded/e.total*100);s.preview.querySelector("progress").value=value;s.preview.querySelector("[data-upload-status]").textContent=value===100?"Validando anexo…":`Carregando ${value}%`;}};
      const fail=message=>{if(alive(s) && seq===s.uploadSeq){busy(s,false);draw(s);onError(message);}};
      xhr.onerror=()=>fail("Não foi possível carregar o anexo. Remova e selecione novamente.");
      xhr.ontimeout=()=>fail("O envio do arquivo demorou demais. Remova e selecione novamente.");
      xhr.onload=()=>{
        if(!alive(s) || seq!==s.uploadSeq)return;
        let data;try{data=JSON.parse(xhr.responseText);}catch{fail("Não foi possível carregar o anexo.");return;}
        if(xhr.status<200 || xhr.status>=300 || !data.ok || !data.media){fail(data.error || "Não foi possível carregar o anexo.");return;}
        s.media=data.media;busy(s,false);onChange(data.media);draw(s);
      };
      xhr.send(file);
    }
    async function record(s){
      if(s.locked || s.busy || s.media || s.file || s.recording)return;
      if(!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder==="undefined"){onError("Gravação indisponível neste navegador. Use Anexar áudio.");return;}
      busy(s,true);onError("");
      let stream;
      try {
        stream=await navigator.mediaDevices.getUserMedia({audio:true});
        if(!alive(s) || s.locked){stream.getTracks().forEach(t=>t.stop());if(alive(s))busy(s,false);return;}
        const mime=["audio/webm;codecs=opus","audio/mp4","audio/ogg;codecs=opus"].find(m=>MediaRecorder.isTypeSupported(m));
        const recorder=new MediaRecorder(stream,mime?{mimeType:mime,audioBitsPerSecond:64000}:{});
        s.stream=stream;s.recorder=recorder;s.recording=true;s.cancelled=false;const chunks=[];let bytes=0;
        recorder.ondataavailable=e=>{if(e.data.size){chunks.push(e.data);bytes+=e.data.size;if(bytes>limits.audio*1048576)stop(s,true);}};
        recorder.onerror=()=>{stop(s,true);if(alive(s))onError("Não foi possível gravar o áudio.");};
        recorder.onstop=()=>{
          stream.getTracks().forEach(t=>t.stop());clearInterval(s.timer);s.recording=false;s.stream=null;
          if(!alive(s))return;
          busy(s,false);
          if(s.cancelled){draw(s);return;}
          const type=(recorder.mimeType || chunks[0]?.type || "audio/webm").split(";")[0];
          const ext=type==="audio/mp4"?"m4a":type==="audio/ogg"?"ogg":"webm";
          const file=new File(chunks,`audio-${new Date().toISOString().replace(/[:.]/g,"-")}.${ext}`,{type});
          uploadFile(s,file,"audio");
        };
        recorder.start(1000);draw(s);let seconds=0;
        s.timer=setInterval(()=>{if(!alive(s))return;seconds++;const t=s.preview.querySelector("[data-elapsed]");if(t)t.textContent=`${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,"0")}`;if(seconds>=300)stop(s,false);},1000);
      }catch{
        stream?.getTracks().forEach(t=>t.stop());if(alive(s)){busy(s,false);onError("Não foi possível acessar o microfone. Confira a permissão do navegador ou anexe um áudio.");}
      }
    }
    function stop(s,cancelled){s.cancelled=cancelled;clearInterval(s.timer);if(s.recorder?.state!=="inactive")s.recorder?.stop();s.stream?.getTracks().forEach(t=>t.stop());}
    function destroy(){const s=session;if(!s)return;s.valid=false;s.uploadSeq++;s.xhr?.abort();stop(s,true);release(s);if(s.outside)document.removeEventListener("click",s.outside);session=null;}
    function mount(tools,preview,scope,initial=null){
      destroy();const s={tools,preview,scope,valid:true,uploadSeq:0,locked:true,busy:false,recording:false,media:initial,file:null,kind:initial?.kind || "",url:""};session=s;
      tools.innerHTML=`<div class="attachmentTools"><button type="button" data-attach class="icon-button" title="Anexar" aria-label="Anexar" aria-expanded="false" disabled>${icon("paperclip")}</button><div class="attachmentMenu" data-menu hidden>${[["image","image","Imagem"],["video","video","Vídeo"],["document","file-text","Arquivo"],["audio","audio-lines","Áudio do dispositivo"]].map(([kind,symbol,label])=>`<button type="button" data-kind="${kind}" title="Até ${limits[kind]} MB">${icon(symbol)}${label}</button>`).join("")}</div><input type="file" data-file hidden><button type="button" data-record class="icon-button" title="Gravar áudio" aria-label="Gravar áudio" disabled>${icon("mic")}</button></div>`;
      const menu=tools.querySelector("[data-menu]"),input=tools.querySelector("[data-file]"),attach=tools.querySelector("[data-attach]");
      attach.onclick=()=>{menu.hidden=!menu.hidden;attach.setAttribute("aria-expanded",String(!menu.hidden));};
      s.outside=e=>{if(!tools.contains(e.target)){menu.hidden=true;attach.setAttribute("aria-expanded","false");}};document.addEventListener("click",s.outside);
      tools.onkeydown=e=>{if(e.key==="Escape"){menu.hidden=true;attach.setAttribute("aria-expanded","false");attach.focus();}};
      tools.querySelectorAll("[data-kind]").forEach(b=>b.onclick=()=>{s.selectedKind=b.dataset.kind;input.accept=accepts[s.selectedKind];input.value="";menu.hidden=true;attach.setAttribute("aria-expanded","false");input.click();});
      input.onchange=()=>{if(input.files[0])uploadFile(s,input.files[0],s.selectedKind);};
      tools.querySelector("[data-record]").onclick=()=>record(s);draw(s);icons();
    }
    function availability(available){const s=session;if(!s)return;s.locked=!available;s.tools.querySelector("[data-attach]").disabled=!available || s.busy;s.tools.querySelector("[data-record]").disabled=!available || s.busy || Boolean(s.file || s.media);if(!available){s.tools.querySelector("[data-menu]").hidden=true;if(s.recording)stop(s,true);}}
    return {mount,destroy,clear,availability,value:()=>session?.media || null,busy:()=>Boolean(session?.busy || session?.recording),hasDraft:()=>Boolean(session?.file || session?.media || session?.recording)};
  };
})();
