const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const vm=require("node:vm");
const source=fs.readFileSync(path.join(__dirname,"../static/crm.js"),"utf8");
const code=source.slice(source.indexOf("  function pendingQuotesSummary("),source.indexOf("  function customerValueDescription("));
assert.ok(code.includes("pendingQuotesValue"));
const ctx={
  esc:value=>String(value).replaceAll('"',"&quot;"),
  icon:name=>`<i>${name}</i>`,
  exactMoney:cents=>(cents/100).toLocaleString("pt-BR",{style:"currency",currency:"BRL"}),
  doctor:()=>({name:"Doutor A"})
};
vm.createContext(ctx);vm.runInContext(code+"; result={pendingQuotesSummary,pendingQuotesValue};",ctx);
const {pendingQuotesSummary:summary,pendingQuotesValue:value}=ctx.result;
const lead=(id,amount,count=1,state="ready")=>({contact_id:id,value_cents:99999999,
  customer_history:{quotes_state:state,pending_quotes_cents:amount,pending_quotes_count:count,open_quotes_count:count-1,expired_quotes_count:1}});
const full=lead("a",202357,2);
assert.match(value(full,true),/Orç\. pendentes/);
assert.ok(value(full,true).includes(ctx.exactMoney(202357)));
assert.match(value(full,true),/1 aberto\(s\) e 1 vencido\(s\)/);
assert.ok(!value(full,true).includes(ctx.exactMoney(full.value_cents)));
assert.equal(value(lead("zero",0,0),true),"");
assert.match(value(lead("zero",0,0)),/Sem orçamentos pendentes/);
assert.equal(value(lead("new",0,0,"unlinked"),true),"");
assert.match(value(lead("partial",12345,1,"partial"),true),/a conferir/);
assert.ok(!value(lead("partial",12345,1,"partial"),true).includes(ctx.exactMoney(12345)));
const sum=summary([full,full,lead("b",10001),lead("bad",50000,1,"partial"),lead("new",0,0,"unlinked")]);
assert.equal(sum.cents,212358);assert.equal(sum.pending,1);
const large=value(lead("large",123456789),true);
assert.ok(large.includes(ctx.exactMoney(123456789)));
console.log("CRM pending quotes: exact cents, expired inclusion, no manual fallback, deduplication, empty and partial states OK");
