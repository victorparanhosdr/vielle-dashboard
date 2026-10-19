const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const context={window:{},setTimeout,clearTimeout,console};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/crm-audience.js'),'utf8'),context);
const pipelines=[{id:'p',name:'Orgânico',count:70,stages:[{id:'s',name:'Novo',kind:'open',count:70}]}];
const rows=Array.from({length:70},(_,i)=>({id:`lead-${i}`,name:`Cliente ${i}`,phone:`55000000${i}`,pipeline_id:'p',stage_id:'s',customer_history:{},campaign_reason:''}));
const controls=new Map();
function node(){return {innerHTML:'',hidden:false,options:[],setAttribute(){},querySelector(){return null;},querySelectorAll(){return [];}};}
function root(){
  const elements=new Map();
  return {isConnected:true,innerHTML:'',querySelector(selector){if(!elements.has(selector))elements.set(selector,node());return elements.get(selector);},querySelectorAll(){return [];}};
}
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let lastRequest,failed=false;
const picker=context.window.createCrmAudience({esc,icon:()=>'',icons(){},api:async(action,payload)=>{
  assert.equal(action,'campaign-candidates');lastRequest=payload;
  if(failed)throw new Error('Histórico indisponível');
  const start=(payload.page-1)*50;
  return {selected:70,recipients:70,excluded:0,duplicates:0,reasons:{},pipelines,items:rows.slice(start,start+50),has_more:start+50<70};
}});
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const click=(target,action)=>target.onclick({target:{closest:()=>({dataset:{audience:action},setAttribute(){}})}});
const choose=(target,id,checked)=>target.onchange({target:{dataset:{leadId:id},checked}});
(async()=>{
  const target=root();let campaign,lastStatus;
  const list=picker(target,{doctor:'doctor',pipelines,initial:{pipeline_id:'p'},manual:true,editable:false,onChange:(p,status)=>lastStatus=status,onCampaign:p=>campaign=p});
  await settle();assert.equal(list.count(),0);
  target.onchange({target:{dataset:{audience:'selectPage'},checked:true}});
  assert.equal(list.count(),50);
  click(target,'next');await settle();assert.equal(lastRequest.page,2);assert.equal(list.count(),50);
  choose(target,'lead-65',true);assert.equal(list.count(),51);
  click(target,'selectAll');assert.equal(list.count(),70);
  choose(target,'lead-65',false);assert.equal(list.count(),69);
  click(target,'campaign');assert.deepEqual(Array.from(campaign.exclude_lead_ids),['lead-65']);
  assert.equal(campaign.lead_ids,undefined);
  list.destroy();
  const transfer=root();const copied=picker(transfer,{doctor:'doctor',pipelines,initial:campaign});
  await settle();assert.equal(copied.count(),69);assert.equal(copied.payload().pipeline_ids[0],'p');
  copied.destroy();
  const manual=root();const selected=picker(manual,{doctor:'doctor',pipelines,initial:{pipeline_id:'p',lead_ids:['lead-1','lead-64']}});
  await settle();assert.equal(selected.count(),2);assert.deepEqual(Array.from(selected.payload().lead_ids),['lead-1','lead-64']);
  let prevented=false;
  manual.oninput({target:{name:'search',value:'Cliente 64'}});
  manual.onkeydown({target:{name:'search'},key:'Enter',preventDefault(){prevented=true;}});
  await settle();assert.ok(prevented);assert.equal(lastRequest.filters.search,'Cliente 64');
  selected.destroy();
  failed=true;
  const unavailable=root();let state;
  const bad=picker(unavailable,{doctor:'doctor',pipelines,onChange:(p,s)=>state=s});
  await settle();assert.equal(state.error,true);assert.match(unavailable.querySelector('[data-audience="results"]').innerHTML,/Histórico indisponível/);
  bad.destroy();
  console.log('CRM audience: manual, page and all-filter selection; exclusions; transfer; search; failure states OK');
})().catch(error=>{console.error(error);process.exitCode=1;});
