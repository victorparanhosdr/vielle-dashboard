"use strict";
const $ = id => document.getElementById(id);
let users = [], page = 1, requestNumber = 0, editing = null, passwordUser = null, statusUser = null;
let currentUser = null, clinicCatalog = [], moduleCatalog = {}, actionLabels = {}, accessProfiles = [];

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  Object.entries({viewBox:"0 0 24 24",fill:"none",stroke:"currentColor","stroke-width":"2","stroke-linecap":"round","stroke-linejoin":"round","aria-hidden":"true"}).forEach(([k,v]) => svg.setAttribute(k,v));
  (window.masterIcons[name] || []).forEach(([tag, attrs]) => {
    const element = document.createElementNS(svg.namespaceURI, tag);
    Object.entries(attrs).forEach(([k,v]) => element.setAttribute(k,v));svg.append(element);
  });
  return svg;
}
document.querySelectorAll("[data-icon]").forEach(element => element.replaceWith(icon(element.dataset.icon)));
function notice(message, error = false) { $("notice").textContent=message; $("notice").hidden=!message; $("notice").classList.toggle("error",error); }
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.ok) throw new Error(data.error || "Não foi possível concluir a operação.");
  return data;
}
function date(value, full=false) {
  if (!value) return "Nunca acessou";
  const parsed = new Date(value);if (Number.isNaN(parsed.getTime())) return "—";
  return new Intl.DateTimeFormat("pt-BR",full?{dateStyle:"short",timeStyle:"short"}:{dateStyle:"short"}).format(parsed);
}
function action(label, name, callback) {
  const button=document.createElement("button");button.type="button";button.className="secondary iconButton";button.title=label;button.setAttribute("aria-label",label);button.append(icon(name));button.addEventListener("click",callback);return button;
}
function renderRows() {
  $("userRows").replaceChildren();
  if (!users.length) {
    const row=document.createElement("tr"),cell=document.createElement("td");cell.colSpan=7;cell.className="emptyRow";cell.textContent="Nenhum usuário encontrado.";row.append(cell);$("userRows").append(row);return;
  }
  users.forEach(user=>{
    const row=document.createElement("tr");row.dataset.userId=user.id;
    [user.nome,user.login,user.is_master?"Master":"Comum",user.status==="active"?"Ativo":"Inativo",date(user.created_at),date(user.last_login,true)].forEach((value,index)=>{
      const cell=document.createElement("td");cell.dataset.label=["Nome","Login","Perfil","Status","Criação","Último acesso"][index];
      if(index===3){const badge=document.createElement("span");badge.className=`statusBadge ${user.status}`;badge.textContent=value;cell.append(badge);}else cell.textContent=value;
      if(index===0){const memberships=document.createElement("small");memberships.className="clinicSummary";memberships.textContent=user.is_master?"Todas as clínicas":(user.clinic_keys||[]).map(key=>clinicCatalog.find(clinic=>clinic.key===key)?.name||key).join(" · ")||"Sem clínicas liberadas";cell.append(memberships);}
      row.append(cell);
    });
    const cell=document.createElement("td");cell.dataset.label="Ações";const actions=document.createElement("div");actions.className="rowActions";
    actions.append(action(`Editar ${user.nome}`,"Pencil",()=>openUser(user)),action(`Definir senha de ${user.nome}`,"KeyRound",()=>openPassword(user)),action(`${user.status==="active"?"Desativar":"Ativar"} ${user.nome}`,"Power",()=>openStatus(user)));
    cell.append(actions);row.append(cell);$("userRows").append(row);
  });
}
async function loadUsers() {
  const number=++requestNumber;$("userRows").setAttribute("aria-busy","true");
  try {
    const query=new URLSearchParams({search:$("searchUsers").value,status:$("statusFilter").value,page});
    const data=await api(`/api/master/users?${query}`);if(number!==requestNumber)return;
    const pages=Math.max(1,Math.ceil(data.total/data.page_size));if(page>pages){page=pages;return loadUsers();}
    clinicCatalog=data.clinics||[];moduleCatalog=data.modules||{};actionLabels=data.actions||{};accessProfiles=data.profiles||[];users=data.users;renderRows();$("newUser").disabled=false;$("userCount").textContent=`${data.total} ${data.total===1?"usuário":"usuários"}`;
    $("pageLabel").textContent=`Página ${page} de ${pages}`;$("prevPage").disabled=page<=1;$("nextPage").disabled=page>=pages;
  }catch(error){if(number===requestNumber){notice(error.message,true);if(!users.length){$("userRows").replaceChildren();const row=document.createElement("tr"),cell=document.createElement("td");cell.colSpan=7;cell.className="emptyRow";cell.textContent="Não foi possível carregar a lista.";row.append(cell);$("userRows").append(row);}}}
  finally{if(number===requestNumber)$("userRows").removeAttribute("aria-busy");}
}
function openUser(user=null) {
  editing=user;$("userForm").reset();$("userFormError").textContent="";
  $("userDialogTitle").textContent=user?"Editar usuário":"Novo usuário";$("saveUser").textContent=user?"Salvar alterações":"Criar usuário";
  $("userName").value=user?.nome||"";$("userLogin").value=user?.login||"";
  $("initialPasswordField").hidden=Boolean(user);$("initialStatusField").hidden=Boolean(user);$("initialPassword").required=!user;$("initialPassword").disabled=Boolean(user);
  $("clinicMemberships").hidden=Boolean(user?.is_master);$("masterClinicNotice").hidden=!user?.is_master;
  renderClinicPermissions(user);
  $("userDialog").showModal();$("userName").focus();
}
function openPassword(user) { passwordUser=user;$("passwordForm").reset();$("passwordError").textContent="";$("passwordTarget").textContent=`${user.nome} · ${user.login}`;$("passwordDialog").showModal();$("newPassword").focus(); }
function openStatus(user) { statusUser=user;const active=user.status==="active";$("statusTitle").textContent=active?"Desativar usuário?":"Ativar usuário?";$("statusTarget").textContent=`${user.nome} · ${user.login}`;$("statusMessage").textContent=active?"O acesso será bloqueado e as sessões abertas serão encerradas.":"O usuário poderá entrar novamente com sua senha.";$("saveStatus").textContent=active?"Desativar":"Ativar";$("statusError").textContent="";$("statusDialog").showModal(); }
function formSubmit(formId, buttonId, errorId, run) {
  $(formId).addEventListener("submit",async event=>{event.preventDefault();const button=$(buttonId);if(button.disabled)return;button.disabled=true;$(errorId).textContent="";try{await run();}catch(error){$(errorId).textContent=error.message;}finally{button.disabled=false;}});
}
formSubmit("userForm","saveUser","userFormError",async()=>{
  const body={nome:$("userName").value,login:$("userLogin").value};const user=editing;
  if(!user?.is_master){body.clinic_keys=Array.from($("clinicChoices").querySelectorAll('input[name="clinic_key"]:checked'),input=>input.value);body.permissions=collectClinicPermissions(body.clinic_keys);}
  if(!user){body.password=$("initialPassword").value;body.status=$("initialActive").checked?"active":"inactive";}
  const result=await api(user?`/api/master/users/${user.id}/edit`:"/api/master/users",body);
  $("userDialog").close();notice(user?"Usuário atualizado.":"Usuário criado.");
  if(user?.id===currentUser?.id&&result.user.login!==currentUser.login){window.location.replace("/login?next=/master");return;}
  if(user?.id===currentUser?.id)document.querySelectorAll("[data-session-user]").forEach(el=>el.textContent=result.user.nome);
  await loadUsers();
});
formSubmit("passwordForm","savePassword","passwordError",async()=>{
  if($("newPassword").value!==$("confirmPassword").value)throw new Error("As senhas não conferem.");
  await api(`/api/master/users/${passwordUser.id}/password`,{password:$("newPassword").value});$("passwordDialog").close();notice("Nova senha definida. As sessões anteriores foram encerradas.");
  if(passwordUser.id===currentUser?.id){window.location.replace("/login?next=/master");return;}await loadUsers();
});
formSubmit("statusForm","saveStatus","statusError",async()=>{
  await api(`/api/master/users/${statusUser.id}/status`,{active:statusUser.status!=="active"});$("statusDialog").close();notice("Status atualizado.");if(statusUser.id===currentUser?.id){window.location.replace("/login?next=/master");return;}await loadUsers();
});
document.querySelectorAll("[data-close]").forEach(button=>button.addEventListener("click",()=>$(button.dataset.close).close()));
$("passwordDialog").addEventListener("close",()=>$("passwordForm").reset());$("userDialog").addEventListener("close",()=>{$("initialPassword").value="";});
$("newUser").addEventListener("click",()=>openUser());$("reloadUsers").addEventListener("click",()=>{notice("");loadUsers();});
let searchTimer;$("searchUsers").addEventListener("input",()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{page=1;loadUsers();},250);});
$("statusFilter").addEventListener("change",()=>{page=1;loadUsers();});$("prevPage").addEventListener("click",()=>{page--;loadUsers();});$("nextPage").addEventListener("click",()=>{page++;loadUsers();});
$("newUser").disabled=true;
api("/api/auth/me").then(data=>{currentUser=data.user;return loadUsers();}).catch(error=>notice(error.message,true));
