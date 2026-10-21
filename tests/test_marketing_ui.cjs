const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const script=fs.readFileSync('static/marketing.js','utf8');
function boot(query='',saved=null){
  const storage=new Map(saved===null?[]:[['doc4docs:site-origin',saved]]);
  function node(dataset={}){return {dataset,attributes:{},events:{},classList:{open:false,toggle(_name,value){this.open=value;},remove(){this.open=false;}},setAttribute(k,v){this.attributes[k]=v;},getAttribute(k){return this.attributes[k];},addEventListener(k,fn){this.events[k]=fn;},focus(){this.focused=true;}};}
  const tabs=['monthly','sales','procedures'].map((key)=>Object.assign(node({preview:key}),{id:'tab-'+key}));
  const aiTabs=['personal','clarity','followup'].map(key=>Object.assign(node({aiExample:key}),{id:'ai-tab-'+key}));
  const elements=Object.fromEntries(['siteNav','productPanel','previewNumber','previewTitle','previewDescription','productPreview','aiExamplePanel','aiQuote','aiReading','aiAction','aiScore','aiCriterion','aiStatus'].map(key=>[key,node()]));
  const next=node(),zoom=node();
  const menu=node(),events={};menu.setAttribute('aria-expanded','false');
  vm.runInNewContext(script,{document:{querySelectorAll:selector=>selector==='[data-ai-example]'?aiTabs:tabs,querySelector:selector=>({'.navMenu':menu,'[data-next-preview]':next,'[data-zoom-preview]':zoom}[selector]||null),getElementById:key=>elements[key]||null,addEventListener:(key,fn)=>{events[key]=fn;}},window:{},location:{search:query},URLSearchParams,sessionStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)}});
  return {tabs,aiTabs,elements,menu,next,zoom,events,storage};
}
let state=boot('?utm_source=google&utm_campaign=clinicas&untrusted=secret');
let origin=JSON.parse(state.storage.get('doc4docs:site-origin'));
assert.equal(origin.utm_source,'google');assert.equal(origin.utm_campaign,'clinicas');assert.equal(origin.untrusted,undefined);
state=boot('',JSON.stringify(origin));assert.deepEqual(JSON.parse(state.storage.get('doc4docs:site-origin')),origin);
state=boot('?utm_source='+ 'x'.repeat(250));assert.equal(JSON.parse(state.storage.get('doc4docs:site-origin')).utm_source.length,200);
assert.doesNotThrow(()=>boot('', '{broken'));
state=boot();state.tabs[1].events.click();
assert.equal(state.elements.productPreview.src,'/marketing-sales-v3.png');
assert.equal(state.elements.productPanel.attributes['aria-labelledby'],'tab-sales');
assert.equal(state.tabs[1].attributes['aria-selected'],'true');assert.equal(state.tabs[0].tabIndex,-1);
state.tabs[1].events.keydown({key:'ArrowRight',preventDefault(){}});
assert.equal(state.elements.productPreview.src,'/marketing-procedures-v3.png');assert.equal(state.tabs[2].focused,true);
state.tabs[2].events.keydown({key:'Home',preventDefault(){}});assert.equal(state.tabs[0].focused,true);
state.next.events.click();assert.equal(state.elements.productPreview.src,'/marketing-sales-v3.png');assert.equal(state.zoom.attributes['aria-label'],'Ampliar Vendas e orçamentos');
state.aiTabs[1].events.click();assert.equal(state.elements.aiExamplePanel.attributes['aria-labelledby'],'ai-tab-clarity');assert.match(state.elements.aiQuote.textContent,/quinta/);assert.equal(state.elements.aiScore.textContent,'70');
state.aiTabs[1].events.keydown({key:'ArrowRight',preventDefault(){}});assert.equal(state.aiTabs[2].focused,true);assert.match(state.elements.aiReading.textContent,/retorno combinado/);
assert.equal(state.elements.aiScore.textContent,'50');assert.match(state.elements.aiCriterion.textContent,/Pontualidade/);
state.aiTabs[2].events.keydown({key:'Home',preventDefault(){}});assert.equal(state.aiTabs[0].focused,true);assert.match(state.elements.aiAction.textContent,/consultar o histórico/);
state.menu.events.click();assert.equal(state.menu.attributes['aria-expanded'],'true');
state.events.keydown({key:'Escape'});assert.equal(state.menu.attributes['aria-expanded'],'false');assert.equal(state.menu.attributes['aria-label'],'Abrir menu');
for(const filename of ['home.html','plans.html']){
  const html=fs.readFileSync('static/'+filename,'utf8');
  assert.match(html,/href="\/login"[^>]*>Já sou usuário/);assert.match(html,/doc4docs-logo-white.png/);
  assert.doesNotMatch(html,/data-app-page=|session\.js|app-shell\.js/);
}
assert.equal((fs.readFileSync('static/plans.html','utf8').match(/Sob consulta/g)||[]).length,3);
const home=fs.readFileSync('static/home.html','utf8');assert.match(home,/marketing-palm-general-v2.png/);assert.match(home,/marketing-hero-owner-v3.png/);assert.match(home,/Avaliações sob demanda/);assert.match(home,/EXEMPLO ILUSTRATIVO/);assert.match(home,/GESTÃO PARA QUEM É DONO DE CLÍNICA/);
assert.match(home,/data-preview="monthly"/);assert.match(home,/NOTA SIMULADA/);assert.match(home,/Bronze, Prata e Ouro/);assert.match(home,/id="implantacao"/);
assert.doesNotMatch(script,/<input name="(?:email|organization)"[^>]*required/);assert.match(script,/Nome|Seu nome/);assert.match(script,/phone\.read\(\)/);assert.match(script,/Solicitar demonstração/);
const css=fs.readFileSync('static/marketing.css','utf8');assert.match(css,/\.palmVisual img\{[^}]*height:auto/);assert.match(css,/\.palmVisual\{[^}]*max-width:420px/);
const commercial=fs.readFileSync('static/master/commercial.js','utf8');
assert.doesNotMatch(commercial,/\.innerHTML\s*=/);assert.match(commercial,/revision:editing.revision/);
console.log('Marketing UI: attribution, tabs, keyboard, menu, brand/login, consultative plans and safe commercial rendering OK');
