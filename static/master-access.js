"use strict";

function allowedModuleKeys(clinic) { return Object.keys(moduleCatalog).filter(key=>key!=="patient_followup"||clinic==="vielle"); }
function choiceOption(value,text) { const option=document.createElement("option");option.value=value;option.textContent=text;return option; }
function permissionGrid(container,values,clinic="vielle") {
  container.replaceChildren();
  allowedModuleKeys(clinic).forEach(key=>{
    const group=document.createElement("fieldset"),legend=document.createElement("legend");legend.textContent=moduleCatalog[key].label;group.append(legend);
    moduleCatalog[key].actions.forEach(action=>{
      const label=document.createElement("label"),input=document.createElement("input"),span=document.createElement("span");label.className="checkboxLabel";
      input.type="checkbox";input.dataset.permission=`${key}.${action}`;input.checked=values.includes(input.dataset.permission);span.textContent=actionLabels[action]||action;label.append(input,span);group.append(label);
      input.addEventListener("change",()=>{
        if(action==="view"&&!input.checked)group.querySelectorAll("input").forEach(el=>{el.checked=false;});
        else if(action!=="view"&&input.checked)group.querySelector(`[data-permission="${key}.view"]`).checked=true;
      });
    });
    container.append(group);
  });
}
function gridValues(container) { return Array.from(container.querySelectorAll("[data-permission]:checked"),input=>input.dataset.permission); }
function setGridValues(container,values) { container.querySelectorAll("[data-permission]").forEach(input=>{input.checked=values.includes(input.dataset.permission);}); }

function renderClinicPermissions(user) {
  $("clinicChoices").replaceChildren();
  clinicCatalog.forEach(clinic=>{
    const section=document.createElement("div");section.className="clinicPermissions";section.dataset.clinic=clinic.key;
    const label=document.createElement("label"),toggle=document.createElement("input"),name=document.createElement("span");label.className="checkboxLabel clinicToggle";toggle.type="checkbox";toggle.name="clinic_key";toggle.value=clinic.key;toggle.checked=Boolean(user?.clinic_keys?.includes(clinic.key));toggle.disabled=Boolean(user?.is_master);name.textContent=clinic.name;label.append(toggle,name);section.append(label);
    const details=document.createElement("details"),summary=document.createElement("summary");summary.textContent="Abas e ações";details.open=toggle.checked;details.hidden=!toggle.checked;details.append(summary);
    const toolbar=document.createElement("div");toolbar.className="permissionTools";
    const profileLabel=document.createElement("label"),profile=document.createElement("select");profile.setAttribute("aria-label",`Perfil em ${clinic.name}`);profile.append(choiceOption("","Personalizado"));accessProfiles.forEach(item=>profile.append(choiceOption(item.id,item.name)));profileLabel.textContent="Aplicar perfil";profileLabel.append(profile);toolbar.append(profileLabel);
    const copyLabel=document.createElement("label"),copy=document.createElement("select");copy.setAttribute("aria-label",`Copiar permissões para ${clinic.name}`);copy.append(choiceOption("","Selecionar clínica"));clinicCatalog.filter(item=>item.key!==clinic.key).forEach(item=>copy.append(choiceOption(item.key,item.name)));copyLabel.textContent="Copiar de";copyLabel.append(copy);toolbar.append(copyLabel);
    const all=document.createElement("button"),none=document.createElement("button");all.type=none.type="button";all.className=none.className="secondary";all.textContent="Selecionar tudo";none.textContent="Remover tudo";toolbar.append(all,none);details.append(toolbar);
    const grid=document.createElement("div");grid.className="permissionGrid";permissionGrid(grid,user?.permissions?.[clinic.key]||[],clinic.key);details.append(grid);section.append(details);$("clinicChoices").append(section);
    toggle.addEventListener("change",()=>{details.hidden=!toggle.checked;details.open=toggle.checked;});
    profile.addEventListener("change",()=>{const selected=accessProfiles.find(item=>String(item.id)===profile.value);if(selected)setGridValues(grid,selected.permissions);});
    copy.addEventListener("change",()=>{const source=document.querySelector(`.clinicPermissions[data-clinic="${copy.value}"] .permissionGrid`);if(source)setGridValues(grid,gridValues(source));profile.value="";copy.value="";});
    all.addEventListener("click",()=>{grid.querySelectorAll("input").forEach(input=>{input.checked=true;});profile.value="";});
    none.addEventListener("click",()=>{setGridValues(grid,[]);profile.value="";});
    grid.addEventListener("change",()=>{profile.value="";});
  });
}
function collectClinicPermissions(keys) { return Object.fromEntries(keys.map(key=>[key,gridValues(document.querySelector(`.clinicPermissions[data-clinic="${key}"] .permissionGrid`))])); }

const auditLabels={login:"Login realizado",login_denied:"Login negado",logout:"Logout",user_created:"Usuário criado",user_updated:"Usuário editado",user_activated:"Usuário ativado",user_deactivated:"Usuário desativado",password_reset:"Senha redefinida",clinics_changed:"Clínicas alteradas",permissions_changed:"Permissões alteradas",profile_saved:"Perfil salvo"};
Object.entries(auditLabels).forEach(([key,label])=>$("auditAction").append(choiceOption(key,label)));
let auditPage=1,auditRequest=0,editingProfile=null;
async function loadAudit() {
  const request=++auditRequest;
  try {
    const result=await api(`/api/master/audit?${new URLSearchParams({page:auditPage,action:$("auditAction").value})}`);if(request!==auditRequest)return;
    $("auditList").replaceChildren();result.events.forEach(item=>{
      const row=document.createElement("li"),title=document.createElement("strong"),meta=document.createElement("p");title.textContent=auditLabels[item.action]||item.action;meta.textContent=`${date(item.created_at,true)} · ${item.actor_name}${item.target_name?` → ${item.target_name}`:""}`;row.append(title,meta);
      if(Object.keys(item.details).length){const details=document.createElement("details"),summary=document.createElement("summary"),content=document.createElement("pre");summary.textContent="Detalhes";content.textContent=JSON.stringify(item.details,null,2);details.append(summary,content);row.append(details);}
      $("auditList").append(row);
    });
    if(!result.events.length){const row=document.createElement("li");row.textContent="Nenhum registro encontrado.";$("auditList").append(row);}
    $("auditPageLabel").textContent=`${result.total} registros · Página ${auditPage}`;$("auditPrev").disabled=auditPage<=1;$("auditNext").disabled=auditPage*25>=result.total;
  }catch(error){notice(error.message,true);}
}
async function loadProfiles() {
  try {const data=await api("/api/master/profiles");accessProfiles=data.profiles;moduleCatalog=data.modules;actionLabels=data.actions;$("profileList").replaceChildren();accessProfiles.forEach(item=>{
    const row=document.createElement("div"),title=document.createElement("strong"),count=document.createElement("span"),button=document.createElement("button");row.className="profileRow";title.textContent=item.name;count.textContent=`${item.permissions.length} permissões`;button.type="button";button.className="secondary";button.textContent="Editar";button.setAttribute("aria-label",`Editar perfil ${item.name}`);button.addEventListener("click",()=>openProfile(item));row.append(title,count,button);$("profileList").append(row);
  });}catch(error){notice(error.message,true);}
}
function openProfile(profile=null) { editingProfile=profile;$("profileName").value=profile?.name||"";$("profileError").textContent="";permissionGrid($("profilePermissions"),profile?.permissions||[]);$("profileDialog").showModal(); }
document.querySelectorAll("[data-master-section]").forEach(button=>button.addEventListener("click",()=>{
  const section=button.dataset.masterSection;document.querySelectorAll("[data-master-section]").forEach(tab=>tab.setAttribute("aria-pressed",String(tab===button)));["users","profiles","audit"].forEach(key=>{$(`${key}Section`).hidden=key!==section;});$("newUser").hidden=section!=="users";notice("");if(section==="audit")loadAudit();if(section==="profiles")loadProfiles();if(section==="users")loadUsers();
}));
$("newProfile").addEventListener("click",()=>openProfile());$("closeProfile").addEventListener("click",()=>$("profileDialog").close());
formSubmit("profileForm","saveProfile","profileError",async()=>{await api(`/api/master/profiles${editingProfile?`/${editingProfile.id}`:""}`,{name:$("profileName").value,permissions:gridValues($("profilePermissions"))});$("profileDialog").close();notice("Perfil salvo. Os acessos já atribuídos aos usuários não foram alterados.");await loadProfiles();});
$("reloadAudit").addEventListener("click",loadAudit);$("auditAction").addEventListener("change",()=>{auditPage=1;loadAudit();});$("auditPrev").addEventListener("click",()=>{auditPage--;loadAudit();});$("auditNext").addEventListener("click",()=>{auditPage++;loadAudit();});
