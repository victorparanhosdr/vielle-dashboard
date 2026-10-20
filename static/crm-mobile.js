(() => {
  "use strict";
  const primaryModes=["inbox","board","dashboard"];
  const viewportHeight=(viewport,height)=>Math.round(viewport && Math.abs(viewport.scale-1)<0.01?viewport.height:height);
  const viewportTop=viewport=>viewport && Math.abs(viewport.scale-1)<0.01?Math.max(0,Math.round(viewport.offsetTop || 0)):0;
  if(typeof module!=="undefined" && module.exports){module.exports={primaryModes,viewportHeight,viewportTop};return;}
  const menu=document.getElementById("crmMobileMenu"),more=document.getElementById("crmMobileMore");
  if(!menu || !more)return;
  const mobile=matchMedia("(max-width:760px)"),bar=document.querySelector(".appTopbar");
  const tabs=[...document.querySelectorAll(".crmMobileNav [role=tab]")];
  tabs.forEach((tab,index)=>tab.addEventListener("keydown",event=>{
    if(!["ArrowLeft","ArrowRight","Home","End"].includes(event.key))return;
    event.preventDefault();
    const next=event.key==="Home"?0:event.key==="End"?tabs.length-1:(index+(event.key==="ArrowRight"?1:-1)+tabs.length)%tabs.length;
    tabs[next].focus();tabs[next].click();
  }));
  const brand=document.querySelector(".crmRailBrand").cloneNode(true);
  brand.className="crmMobileBrand crmMobileOnly";bar.prepend(brand);
  more.onclick=()=>{if(!menu.open)menu.showModal();};
  menu.querySelector("[data-mobile-menu-close]").onclick=()=>menu.close();
  menu.addEventListener("click",event=>{
    if(event.target===menu){const rect=menu.getBoundingClientRect();if(event.clientY<rect.top)menu.close();}
    const button=event.target.closest("[data-mode]");
    if(button && document.body.dataset.crmMode===button.dataset.mode)menu.close();
  });
  document.getElementById("crmMobileJourney").onclick=()=>{menu.close();document.getElementById("crmJourneyToggle").click();};
  const sync=()=>{
    more.setAttribute("aria-pressed",String(!primaryModes.includes(document.body.dataset.crmMode)));
    document.getElementById("crmMobileSettings").hidden=document.getElementById("crmSettings").hidden;
  };
  new MutationObserver(sync).observe(document.body,{attributes:true,attributeFilter:["data-crm-mode"]});
  new MutationObserver(sync).observe(document.getElementById("crmSettings"),{attributes:true,attributeFilter:["hidden"]});
  const resize=()=>{
    const timeline=document.getElementById("messageTimeline"),pinned=timeline && timeline.scrollHeight-timeline.scrollTop-timeline.clientHeight<60;
    document.body.style.setProperty("--crm-mobile-height",`${viewportHeight(window.visualViewport,innerHeight)}px`);
    document.body.style.setProperty("--crm-mobile-top",`${viewportTop(window.visualViewport)}px`);
    if(mobile.matches && pinned)requestAnimationFrame(()=>{if(timeline.isConnected)timeline.scrollTop=timeline.scrollHeight;});
    if(!mobile.matches && menu.open)menu.close();
  };
  window.visualViewport?.addEventListener("resize",resize);
  window.visualViewport?.addEventListener("scroll",resize);
  window.addEventListener("resize",resize);mobile.addEventListener("change",resize);
  document.addEventListener("input",event=>{
    if(!mobile.matches || event.target.id!=="replyBody")return;
    const field=event.target;field.style.height="44px";field.style.height=`${Math.min(112,field.scrollHeight)}px`;
  });
  document.addEventListener("click",event=>{
    const details=document.getElementById("mobileChatMore");
    if(details?.open && !details.contains(event.target))details.open=false;
  });
  document.addEventListener("keydown",event=>{
    if(event.key!=="Escape" || !mobile.matches || document.querySelector("dialog[open]"))return;
    const actions=document.getElementById("mobileChatMore");
    if(actions?.open){actions.open=false;actions.querySelector("summary").focus();return;}
    const workspace=document.getElementById("inboxWorkspace");
    if(workspace?.classList.contains("showContact"))document.getElementById("toggleContact")?.click();
    else if(document.querySelector(".chatPane.replySettingsOpen"))document.getElementById("mobileReplyOptions")?.click();
  });
  sync();resize();window.lucide?.createIcons();
})();
