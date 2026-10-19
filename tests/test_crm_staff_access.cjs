const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");
const path=require("node:path");
const read=name=>fs.readFileSync(path.join(__dirname,"../static/",name),"utf8");
const crm=read("crm.js"), app=read("app.js"), shell=read("app-shell.js");
const permissionCheck=crm.slice(crm.indexOf("  function sessionAllowed("),crm.indexOf("  function session(data)"));
const context={};vm.createContext(context);vm.runInContext(permissionCheck+";check=sessionAllowed;",context);
const staff={user:{is_master:false},clinics:[{key:"vielle"}],permissions:{vielle:["crm.view"]}};
assert.equal(context.check(staff),true);
assert.equal(context.check({...staff,permissions:{vielle:[]}}),false);
assert.equal(context.check({...staff,clinics:[{key:"inspire"}]}),false);
assert.equal(context.check(null),false);
const views=app.slice(app.indexOf("function permittedViews("),app.indexOf("function applyActionPermissions("));
const viewContext={sessionMaster:false,state:{selectedClinic:"vielle"},accessModules:{crm:{view:"crmView"},financial:{view:"financialView"}},canAccess:p=>p==="crm.view"};
vm.createContext(viewContext);vm.runInContext(views+";views=permittedViews();",viewContext);assert.deepEqual(Array.from(viewContext.views),["crmView"]);
const launcher=shell.slice(shell.indexOf("  function updateAttendance("),shell.indexOf("  const originalHeader"));
const attendance={hidden:true}, launcherContext={attendance,shellSession:staff,page:"crm",institutePage:false,
  location:{search:"?clinic=vielle"},URLSearchParams,$:()=>null,document:{body:{classList:{toggle:()=>{}}}},window:{}};
vm.createContext(launcherContext);vm.runInContext(launcher+";updateAttendance();",launcherContext);assert.equal(attendance.hidden,false);
launcherContext.shellSession={...staff,permissions:{vielle:[]}};vm.runInContext("updateAttendance();",launcherContext);assert.equal(attendance.hidden,true);
assert.ok(!/key === "crm" && !data.user\?\.is_master/.test(shell));
const tab=read("index.html").match(/<button[^>]*data-view="crmView"[^>]*>/)?.[0];assert.ok(tab);assert.ok(!tab.includes("data-master-only"));
assert.match(crm,/if\(!state.data.can_manage\)/);
for(const module of ["tasks","birthdays"]){
  const source=read(module+".js");
  const begin=source.indexOf("  function applySession(data)");
  const end=source.indexOf(module==="tasks"?"  function listParams(":"  function params(",begin);
  assert.ok(begin>=0 && end>begin);
  const nodes=new Map();const $=id=>{if(!nodes.has(id))nodes.set(id,{hidden:false,innerHTML:"",textContent:""});return nodes.get(id);};
  const session={...staff,clinics:[{key:"vielle",name:"Vielle Clinic"}],permissions:{vielle:["tasks.view","tasks.create","birthdays.view","crm.view"]},
    modules:{tasks:{view:"tasksView",label:"Tarefas"},birthdays:{view:"birthdaysView",label:"Aniversários"},crm:{view:"crmView",label:"CRM"},financial:{view:"financialView",label:"Financeiro"}}};
  const ctx={state:{clinic:"vielle",session:null,started:true},$,encodeURIComponent,esc:v=>String(v),icons:()=>{},
    clinicRefresh:{setClinic:()=>{}},refreshController:{setClinic:()=>{}},deny:()=>assert.fail("Authorized operator denied"),
    can:action=>ctx.state.session.permissions.vielle.includes(`${module}.${action}`)};
  vm.createContext(ctx);vm.runInContext(source.slice(begin,end)+";apply=applySession;",ctx);ctx.apply(session);
  assert.match($("viewTabs").innerHTML,/data-target="\/crm.html\?clinic=vielle"/);
  assert.ok(!$("viewTabs").innerHTML.includes("financialView"));
  ctx.apply({...session,permissions:{vielle:session.permissions.vielle.filter(p=>p!=="crm.view")}});
  assert.ok(!$("viewTabs").innerHTML.includes("crm.html"));
}
console.log("CRM staff: granted module in navigation, launcher and page; denied/revoked permissions stay hidden; administrative controls excluded OK");
