"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm");
const source=fs.readFileSync("static/master-access.js","utf8");
class Element {
  constructor(tag){this.tagName=tag;this.children=[];this.dataset={};this.value="";this.hidden=false;}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  setAttribute(key,value){this[key]=value;}
  querySelectorAll(){return this.children.flatMap(node=>[...(node.dataset.teamProfile?[node]:[]),...node.querySelectorAll()]);}
}
const choices=new Element("div"),field=new Element("fieldset");
const context=vm.createContext({$:id=>id==="teamProfileChoices"?choices:field,
  document:{createElement:tag=>new Element(tag)},clinicCatalog:[{key:"vielle",name:"Vielle Clinic"},{key:"inspire",name:"Inspire"}]});
vm.runInContext(source.slice(source.indexOf("function choiceOption"),source.indexOf("function permissionGrid")),context);
vm.runInContext(source.slice(source.indexOf("function collectTeamProfiles"),source.indexOf("function renderClinicPermissions")),context);
const values=()=>JSON.parse(JSON.stringify(context.collectTeamProfiles()));
context.renderTeamProfiles(null,["vielle"]);
assert.deepEqual(values(),{vielle:"attendant"});
choices.querySelectorAll()[0].value="management";
context.renderTeamProfiles(null,["vielle","inspire"]);
assert.deepEqual(values(),{vielle:"management",inspire:"attendant"});
context.renderTeamProfiles(null,["inspire"]);
assert.deepEqual(values(),{inspire:"attendant"});
context.renderTeamProfiles(null,[]);assert.equal(field.hidden,true);
choices.replaceChildren();context.renderTeamProfiles({is_master:1},["vielle","inspire"]);
assert.deepEqual(values(),{vielle:"management",inspire:"management"});
choices.replaceChildren();context.renderTeamProfiles({is_master:1,team_profiles:{vielle:"attendant"}},["vielle"]);
assert.deepEqual(values(),{vielle:"attendant"});
assert.equal(choices.querySelectorAll()[0]["aria-label"],"Perfil na equipe em Vielle Clinic");
const master=fs.readFileSync("static/master.js","utf8"),html=fs.readFileSync("static/master.html","utf8");
assert.match(master,/body\.team_profiles=collectTeamProfiles\(\)/);
assert.match(master,/\$\("teamProfileChoices"\)\.replaceChildren\(\);renderClinicPermissions/);
assert.match(html,/id="teamProfileField"/);
assert.match(source,/team_profiles_changed/);
console.log("Master team profiles: clinic scope, defaults, Master choice, preserved draft values and payload OK");
