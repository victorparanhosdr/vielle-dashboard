(() => {
  "use strict";
  const railScrollState=(list,height)=>{
    const overflow=height>0 && list.scrollHeight>height+2;
    return {overflow,up:overflow && list.scrollTop>2,down:overflow && list.scrollTop+list.clientHeight<list.scrollHeight-2};
  };
  if(typeof module!=="undefined" && module.exports){module.exports={railScrollState};return;}
  const doctor=document.getElementById("doctor"),search=document.querySelector(".crmSearchInput"),bar=document.querySelector(".appTopbar");
  if(!bar || !doctor || !search)return;
  const doctorField=doctor.closest("label");doctorField.classList.add("crmHeaderDoctor");
  const actions=bar.querySelector(".appHeaderActions");
  bar.insertBefore(doctorField,actions);bar.insertBefore(search,actions);search.classList.add("crmHeaderSearch");
  search.querySelector("input").placeholder="Buscar clientes...";
  const heading=document.querySelector(".crmHeading"),viewActions=document.getElementById("viewActions");
  heading.insertBefore(document.getElementById("pipelineField"),heading.querySelector(".commands"));heading.append(viewActions);
  const journey=document.querySelector(".journey"),toggle=document.getElementById("crmJourneyToggle");
  toggle.setAttribute("aria-haspopup","dialog");
  toggle.onclick=()=>document.dispatchEvent(new Event("crm-open-journey"));
  document.addEventListener("keydown",event=>{if(event.key==="Escape"){document.body.classList.remove("crmJourneyOpen");toggle.setAttribute("aria-expanded","false");}});
  const size=()=>document.body.style.setProperty("--crm-header-height",`${Math.ceil(document.querySelector(".appChrome").getBoundingClientRect().height)}px`);
  new ResizeObserver(size).observe(bar);size();
  const access=data=>document.getElementById("crmSettings").hidden=!data?.user?.is_master;
  window.addEventListener("doc4docs-session",event=>access(event.detail));if(window.doc4docsSession)access(window.doc4docsSession);
  const tabs=[...document.querySelectorAll(".crmRailModules [role=tab]")];
  tabs.forEach(tab=>tab.addEventListener("keydown",event=>{
    if(!["ArrowUp","ArrowDown","Home","End"].includes(event.key))return;
    event.preventDefault();
    const visible=tabs.filter(item=>!item.hidden && item.getClientRects().length),index=visible.indexOf(tab);
    if(index<0)return;
    const next=event.key==="Home"?0:event.key==="End"?visible.length-1:(index+(event.key==="ArrowDown"?1:-1)+visible.length)%visible.length;
    visible[next].focus();visible[next].click();
  }));
  const moduleArea=document.querySelector('.crmRailModuleArea'),modules=document.querySelector('.crmRailModules');
  const up=document.getElementById('crmRailUp'),down=document.getElementById('crmRailDown');
  if(moduleArea && modules && up && down){
    let frame=0;
    const syncRail=()=>{
      frame=0;const value=railScrollState(modules,moduleArea.clientHeight);
      moduleArea.dataset.overflow=String(value.overflow);
      for(const [button,visible] of [[up,value.up],[down,value.down]]){
        if(!visible && document.activeElement===button){
          const items=[...modules.querySelectorAll('button:not([hidden]),a:not([hidden])')];
          (button===up?items[0]:items.at(-1))?.focus({preventScroll:true});
        }
        button.hidden=!visible;
      }
    };
    const schedule=()=>{if(!frame)frame=requestAnimationFrame(syncRail);};
    const scroll=direction=>modules.scrollBy({top:direction*Math.max(84,modules.clientHeight*.75),behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
    up.onclick=()=>scroll(-1);down.onclick=()=>scroll(1);
    modules.addEventListener('scroll',schedule,{passive:true});
    new ResizeObserver(schedule).observe(moduleArea);
    new MutationObserver(schedule).observe(modules,{childList:true,subtree:true,attributes:true,attributeFilter:['hidden']});
    window.addEventListener('resize',schedule);schedule();
  }
  const tooltip=document.createElement("span");tooltip.className="crmRailTooltip";tooltip.hidden=true;tooltip.setAttribute("role","tooltip");document.body.append(tooltip);
  document.querySelectorAll(".crmRail [data-tooltip]").forEach(button=>{
    const show=()=>{const rect=button.getBoundingClientRect();tooltip.textContent=button.dataset.tooltip;tooltip.style.top=`${Math.max(8,rect.top+rect.height/2)}px`;tooltip.hidden=false;};
    button.addEventListener("mouseenter",show);button.addEventListener("focus",show);button.addEventListener("mouseleave",()=>tooltip.hidden=true);button.addEventListener("blur",()=>tooltip.hidden=true);button.addEventListener("click",()=>tooltip.hidden=true);
  });
  window.lucide?.createIcons();
})();
