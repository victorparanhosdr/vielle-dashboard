const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/crm-inbox.js'),'utf8');
new vm.Script(source);
const openCode=source.slice(source.indexOf('  async function openLead('),source.indexOf('  function select('));
const rowCode=source.slice(source.indexOf('  function drawList()'),source.indexOf('  async function openLead('));
const nodes={inboxChannel:{value:'live'},chatPane:{innerHTML:''},inboxWorkspace:{classList:{add:()=>{}}},conversationRows:{innerHTML:''}};
let selected,cleared=0;
const requests=[];
const ctx={root:{},openSeq:0,channel:'live',owner:'a',selected:null,state:{search:'Ana'},
  $:id=>nodes[id],doctors:()=>['a','b'],refreshList:async()=>{},clearChat:()=>{cleared++;},select:item=>{selected=item;},
  catalog:{channels:[{id:'removed',doctor_id:'a',archived_at:1,state:'open'},{id:'live',doctor_id:'a',archived_at:0,state:'open'},{id:'other',doctor_id:'b',state:'open'}]},
  api:async(action,payload,query)=>{requests.push({action,payload,query});return {lead:{id:'lead',contact_id:'canonical-contact'},preferred_channel_id:'removed',messages:[{channel_id:'removed'}]};},
  leadResults:[{id:'lead',doctor_id:'a',contact_id:'contact',name:'Ana <Exemplo>',phone:'5511987654321',pipeline_name:'Organico',stage_name:'Novo',has_conversation:false}],leadResultsTotal:1,list:[],
  esc:value=>String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'),
  avatars:{markup:()=>''},icon:()=>'',icons:()=>{},updateTimers:()=>{},
};
vm.createContext(ctx);vm.runInContext(`${openCode}\n${rowCode}\nopen=openLead;draw=drawList;`,ctx);
(async()=>{
  ctx.draw();
  assert.match(nodes.conversationRows.innerHTML,/Clientes nos funis/);
  assert.match(nodes.conversationRows.innerHTML,/Criar conversa com Ana &lt;Exemplo&gt;/);
  assert.match(nodes.conversationRows.innerHTML,/Organico · Novo/);
  await ctx.open({id:'lead',doctor_id:'a',contact_id:'outdated-contact'},{channelId:'live',start:true,keepFilter:true});
  assert.equal(selected.channel_id,'live');assert.equal(selected.contact_id,'canonical-contact');
  assert.equal(ctx.channel,'live','Starting from search preserves the selected number filter');
  await ctx.open({id:'lead',doctor_id:'a'},{channelId:'other',start:true});
  assert.equal(cleared,1,'Never silently use a different sender when the selected one is unavailable');
  assert.equal(selected.channel_id,'live','Never open another doctor\'s sender or an archived number');
  ctx.catalog.channels=ctx.catalog.channels.filter(c=>c.id!=='live');
  await ctx.open({id:'lead',doctor_id:'a'},{start:true});
  assert.equal(cleared,2);assert.match(nodes.chatPane.innerHTML,/Nenhum número disponível/);
  await ctx.open({id:'lead',doctor_id:'a'},{channelId:'removed',keepFilter:true});
  assert.equal(selected.channel_id,'removed','An existing archived conversation remains readable');
  assert.ok(requests.every(r=>r.action==='lead' && r.payload===undefined),'Opening a search result must not create leads or send messages');
  ctx.leadResults[0].has_conversation=true;ctx.draw();assert.match(nodes.conversationRows.innerHTML,/Abrir conversa/);
  assert.match(source,/data-start-lead/);assert.match(source,/leadResults=data.lead_results \|\| \[\]/);
  console.log('Inbox discovery: escaped lead results, existing/new chat actions, canonical contact, scoped active sender and read-only opening OK');
})().catch(error=>{console.error(error);process.exitCode=1;});
