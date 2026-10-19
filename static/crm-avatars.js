(() => {
  "use strict";
  window.createCrmAvatars=({api,esc,enabled})=>{
    const cache=new Map(),queued=new Set(),waiting=[],observed=new Set();
    let running=0;
    const signature=(doctor,contact,channel)=>`${doctor}:${channel}:${contact}`;
    const markup=(name,contact,doctor,channel="",cls="contactAvatar")=>`<span class="${cls} crmAvatar" data-avatar-contact="${esc(contact)}" data-avatar-doctor="${esc(doctor)}" data-avatar-channel="${esc(channel)}" aria-hidden="true"><span>${esc(String(name).split(/\s+/).slice(0,2).map(n=>n[0]).join("").toUpperCase())}</span></span>`;
    function paint(node,data){
      if(!node.isConnected || !data?.url?.startsWith("/api/crm/avatar?"))return;
      if(node.querySelector("img")?.getAttribute("src")===data.url)return;
      node.querySelector("img")?.remove();
      const image=new Image();image.alt="";image.decoding="async";image.referrerPolicy="no-referrer";
      image.onload=()=>{if(node.isConnected)node.classList.add("hasPhoto");};
      image.onerror=()=>{node.classList.remove("hasPhoto");image.remove();};
      image.src=data.url;node.append(image);
    }
    function pump(){
      while(running<2 && waiting.length){
        const item=waiting.shift();
        if(!item.node.isConnected || !enabled(item.doctor)){queued.delete(item.key);continue;}
        running++;
        api("avatar-refresh",{doctor_id:item.doctor,contact_id:item.contact,channel_id:item.channel}).then(data=>{
          const entry={...data,expires:Date.now()+(data.retry_after?Math.max(15,data.retry_after)*1000:600000)};
          cache.set(item.key,entry);
          if(cache.size>2000)cache.delete(cache.keys().next().value);
          for(const node of observed){if(signature(node.dataset.avatarDoctor,node.dataset.avatarContact,node.dataset.avatarChannel)===item.key)paint(node,data);}
          if(data.state==="loading" && !data.url)setTimeout(()=>{if(item.node.isConnected)enqueue(item.node);},Math.max(15,data.retry_after || 15)*1000);
        }).catch(()=>{cache.set(item.key,{expires:Date.now()+60000});}).finally(()=>{running--;queued.delete(item.key);pump();});
      }
    }
    function enqueue(node){
      const {avatarDoctor:doctor,avatarContact:contact,avatarChannel:channel}=node.dataset;
      if(!contact || !enabled(doctor))return;
      const key=signature(doctor,contact,channel),old=cache.get(key);
      if(old?.expires>Date.now()){paint(node,old);return;}
      if(queued.has(key))return;
      queued.add(key);
      const item={key,doctor,contact,channel,node};
      if(node.closest(".chatHeader"))waiting.unshift(item);else waiting.push(item);
      pump();
    }
    const observer=new IntersectionObserver(entries=>{for(const entry of entries)if(entry.isIntersecting)enqueue(entry.target);},{rootMargin:"40px"});
    const scan=()=>{
      for(const node of observed)if(!node.isConnected){observer.unobserve(node);observed.delete(node);}
      document.querySelectorAll("[data-avatar-contact]").forEach(node=>{if(!observed.has(node)){observed.add(node);observer.observe(node);}});
    };
    new MutationObserver(scan).observe(document.body,{childList:true,subtree:true});
    scan();
    return {markup};
  };
})();
