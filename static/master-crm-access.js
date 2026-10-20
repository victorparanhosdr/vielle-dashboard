"use strict";

function renderCrmAccess(user) {
  const section=document.createElement("section");section.id="crmUserAccess";section.className="crmUserAccess";
  const heading=document.createElement("h3");heading.textContent="Acesso ao CRM";section.append(heading);
  const modeLabel=document.createElement("label"),mode=document.createElement("select");mode.id="crmAccessMode";
  modeLabel.textContent="Profissionais e funis";
  mode.append(choiceOption("all","Todos os profissionais e funis"),choiceOption("selected","Selecionar acessos"));
  mode.value=user?.crm_scope==null?"all":"selected";modeLabel.append(mode);section.append(modeLabel);
  const list=document.createElement("div");list.id="crmAccessDoctors";section.append(list);
  crmCatalog.doctors.forEach(doctor=>{
    const group=document.createElement("details");group.dataset.crmDoctor=doctor.id;
    const selected=user?.crm_scope!=null && Object.hasOwn(user.crm_scope,doctor.id);
    const summary=document.createElement("summary"),label=document.createElement("label"),toggle=document.createElement("input"),name=document.createElement("span");
    label.className="checkboxLabel";toggle.type="checkbox";toggle.dataset.crmDoctorToggle=doctor.id;toggle.checked=selected;name.textContent=doctor.name;
    label.append(toggle,name);summary.append(label);group.append(summary);group.open=selected;
    const options=document.createElement("div");options.className="crmDoctorPipelines";
    const allLabel=document.createElement("label"),all=document.createElement("input"),allText=document.createElement("span");
    allLabel.className="checkboxLabel";all.type="checkbox";all.dataset.crmAllPipelines=doctor.id;all.checked=!selected || user.crm_scope[doctor.id]===null;
    allText.textContent="Todos os funis deste profissional";allLabel.append(all,allText);options.append(allLabel);
    const pipelines=document.createElement("div");pipelines.className="crmPipelineChoices";
    crmCatalog.pipelines.filter(p=>p.doctor_id===doctor.id).forEach(pipeline=>{
      const item=document.createElement("label"),input=document.createElement("input"),text=document.createElement("span");
      item.className="checkboxLabel";input.type="checkbox";input.dataset.crmPipeline=pipeline.id;
      input.checked=Boolean(selected && user.crm_scope[doctor.id]?.includes(pipeline.id));text.textContent=pipeline.name;
      item.append(input,text);pipelines.append(item);
    });
    if(!pipelines.childElementCount){const empty=document.createElement("p");empty.textContent="Nenhum funil cadastrado.";pipelines.append(empty);}
    options.append(pipelines);group.append(options);list.append(group);
    function sync(){options.hidden=!toggle.checked;all.disabled=!toggle.checked;pipelines.hidden=all.checked;pipelines.querySelectorAll("input").forEach(input=>{input.disabled=!toggle.checked || all.checked;});}
    toggle.addEventListener("change",()=>{group.open=toggle.checked;sync();});
    all.addEventListener("change",sync);sync();
  });
  const syncMode=()=>{list.hidden=mode.value==="all";};mode.addEventListener("change",syncMode);syncMode();
  return section;
}

function collectCrmScope() {
  if(!$("crmAccessMode") || $("crmAccessMode").value==="all")return null;
  const scope={};
  $("crmAccessDoctors").querySelectorAll("[data-crm-doctor]").forEach(group=>{
    if(!group.querySelector("[data-crm-doctor-toggle]").checked)return;
    const id=group.dataset.crmDoctor;
    scope[id]=group.querySelector("[data-crm-all-pipelines]").checked?null:Array.from(group.querySelectorAll("[data-crm-pipeline]:checked"),input=>input.dataset.crmPipeline);
    if(scope[id]?.length===0)throw new Error("Selecione ao menos um funil para cada profissional marcado.");
  });
  return scope;
}
