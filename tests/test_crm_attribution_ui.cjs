const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const ctx={window:{},URL};vm.createContext(ctx);vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/crm-attribution.js'),'utf8'),ctx);
const api=ctx.window.CrmAttribution;
const data=api.collect({capture_click_ids:true},'https://doc4docs.com.br/f/test?utm_source=instagram&utm_campaign=Regen%20Code&gclid=click&email=private','https://example.test/path?token=private#top');
assert.equal(data.utm.utm_source,'instagram');assert.equal(data.utm.utm_campaign,'Regen Code');assert.equal(data.click_ids.gclid,'click');assert.equal(data.referrer,'https://example.test/path');assert.equal(data.landing_url,'https://doc4docs.com.br/f/test');assert.ok(!JSON.stringify(data).includes('private'));
assert.equal(api.collect({},'https://doc4docs.com.br/f/test?gclid=click','').click_ids.gclid,undefined);
assert.equal(Object.keys(api.collect({enabled:false},'https://doc4docs.com.br/f/test?utm_source=x','')).length,0);
const url=api.link('https://doc4docs.com.br/f/test',{utm_campaign:'Curso & Aula',utm_source:'Instagram'});assert.equal(new URL(url).searchParams.get('utm_campaign'),'Curso & Aula');
console.log('UTM capture and campaign link tests passed');
