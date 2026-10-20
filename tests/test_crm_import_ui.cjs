const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const elements = new Map();
const dialog = {open:true, addEventListener(){}};
let html = "", loaded = 0;
const body = {set innerHTML(value) {
  html = value;
  elements.clear();
  for (const match of value.matchAll(/id="([^"]+)"/g)) elements.set(match[1], {});
}};
const calls = [];
let current = {id:"original",status:"done",message:"Concluida",report:{total:3,created:1,issue_count:2},
  issues:[{source_id:2,name:"<Nome>",reason:"Compartilhado"}],page:1,pages:1};
const context = {window:{},document:{getElementById:id=>id==="crmDialog"?dialog:id==="modalBody"?body:elements.get(id)},setTimeout,clearTimeout};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname,"../static/crm-import.js"),"utf8"),context);
const importer = context.window.createCrmImporter({
  api:async(action,payload)=>{
    calls.push({action,payload});
    if(action==="kommo-import-preview") {
      current = {id:"retry",status:"ready",message:"Previa",report:{total:3,created:1,review:1,issue_count:1},
        issues:[{source_id:3,name:"Paciente",reason:"Sem ficha",imported:true}],page:1,pages:1};
      return {id:"retry"};
    }
    if(action==="kommo-import-commit") current={...current,status:"done"};
    return current;
  },
  modal(){},error(message){throw Error(message);},icons(){},
  esc:value=>String(value).replaceAll("<","&lt;"),load:async()=>{loaded++;}
});
(async()=>{
  await importer.open("doctor","pipeline","REGENCODE");
  assert.ok(html.includes("REGENCODE · Dr. Victor Paranhos de Andrade"));
  assert.ok(!html.includes("Orgânico Paranhos"));
  assert.ok(html.includes("&lt;Nome>"));
  assert.ok(html.includes("Resolver pendências em lote"));
  await elements.get("retryKommo").onclick();
  const request=calls.find(call=>call.action==="kommo-import-preview");
  assert.deepEqual(JSON.parse(JSON.stringify(request.payload)),{doctor_id:"doctor",pipeline_id:"pipeline",retry_from:"original"});
  assert.ok(html.includes("Importar sem ficha"));
  assert.ok(elements.has("commitKommo"));
  await elements.get("commitKommo").onclick();
  assert.ok(html.includes("Importado sem ficha"));
  assert.equal(loaded,1);
  assert.equal(calls.find(call=>call.action==="kommo-import-commit").payload.id,"retry");
  assert.ok(!elements.has("commitKommo"));
  console.log("CRM import: bulk retry scope, preview, review labels, escaping and commit OK");
})().catch(error=>{console.error(error);process.exitCode=1;});
