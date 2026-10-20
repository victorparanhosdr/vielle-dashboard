(() => {
  "use strict";
  const doctor=document.getElementById("doctor"),search=document.querySelector(".crmSearchInput"),bar=document.querySelector(".appTopbar");
  if(!bar || !doctor || !search)return;
  const doctorField=doctor.closest("label");doctorField.classList.add("crmHeaderDoctor");
  const brand=bar.querySelector(".appBrand");brand.textContent="DOC4DOCS";
  const actions=bar.querySelector(".appHeaderActions");
  bar.insertBefore(doctorField,actions);bar.insertBefore(search,actions);search.classList.add("crmHeaderSearch");
  search.querySelector("input").placeholder="Buscar clientes...";
  const heading=document.querySelector(".crmHeading"),viewActions=document.getElementById("viewActions");
  heading.insertBefore(document.getElementById("pipelineField"),heading.querySelector(".commands"));heading.append(viewActions);
  const journey=document.querySelector(".journey"),toggle=document.getElementById("crmJourneyToggle");
  toggle.onclick=()=>{const open=!document.body.classList.contains("crmJourneyOpen");document.body.classList.toggle("crmJourneyOpen",open);journey.hidden=!open;toggle.setAttribute("aria-expanded",String(open));};
  document.addEventListener("keydown",event=>{if(event.key==="Escape"){document.body.classList.remove("crmJourneyOpen");toggle.setAttribute("aria-expanded","false");}});
  const size=()=>document.body.style.setProperty("--crm-header-height",`${Math.ceil(document.querySelector(".appChrome").getBoundingClientRect().height)}px`);
  new ResizeObserver(size).observe(bar);size();
  const access=data=>document.getElementById("crmSettings").hidden=!data?.user?.is_master;
  window.addEventListener("doc4docs-session",event=>access(event.detail));if(window.doc4docsSession)access(window.doc4docsSession);
  const tabs=[...document.querySelectorAll(".crmRailModules [role=tab]")];
  tabs.forEach((tab,index)=>tab.addEventListener("keydown",event=>{
    if(!["ArrowUp","ArrowDown","Home","End"].includes(event.key))return;
    event.preventDefault();
    const next=event.key==="Home"?0:event.key==="End"?tabs.length-1:(index+(event.key==="ArrowDown"?1:-1)+tabs.length)%tabs.length;
    tabs[next].focus();tabs[next].click();
  }));
  const tooltip=document.createElement("span");tooltip.className="crmRailTooltip";tooltip.hidden=true;tooltip.setAttribute("role","tooltip");document.body.append(tooltip);
  document.querySelectorAll(".crmRail [data-tooltip]").forEach(button=>{
    const show=()=>{const rect=button.getBoundingClientRect();tooltip.textContent=button.dataset.tooltip;tooltip.style.top=`${Math.max(8,rect.top+rect.height/2)}px`;tooltip.hidden=false;};
    button.addEventListener("mouseenter",show);button.addEventListener("focus",show);button.addEventListener("mouseleave",()=>tooltip.hidden=true);button.addEventListener("blur",()=>tooltip.hidden=true);button.addEventListener("click",()=>tooltip.hidden=true);
  });
  window.lucide?.createIcons();
})();
