// Synthetic loopback fixture only; never touches a real CRM or sends messages.
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
(async()=>{
  const url=new URL(process.argv[2]);assert.equal(url.hostname,'127.0.0.1');
  const browser=await chromium.launch({headless:true,channel:'chrome'});
  const page=await browser.newPage({viewport:{width:1440,height:960}}),errors=[];
  const negotiationName='Nova negociação de teste '+Date.now();
  page.on('pageerror',error=>errors.push(error.message));
  try{
    await page.goto(url.href);await page.waitForURL('**/crm.html?**');
    const board=new URL(page.url());board.searchParams.set('mode','board');await page.goto(board.href);
    await page.locator('.leadCard').first().waitFor();
    const selected=await page.locator('#pipeline').inputValue();
    if(await page.locator('#applyClinicJourney').count()){
      await page.locator('#applyClinicJourney').click();
      await page.locator('#clinicMigration').waitFor();
      await page.locator('#clinicMigration input[name=confirm]').check();
      await page.locator('#clinicMigration button[type=submit]').click();
    }
    await page.waitForFunction(()=>document.querySelectorAll('.crmColumn').length===8);
    await page.screenshot({path:'/private/tmp/doc4docs-patient-journey-desktop.png',fullPage:true});
    const cards=await page.locator('.leadCard').count();
    await page.locator('.leadCard').filter({hasText:'Clara Exemplo'}).click();
    await page.getByRole('button',{name:'Ficha e histórico',exact:true}).click();
    await page.getByRole('tab',{name:'Oportunidades',exact:true}).click();
    await page.locator('[data-deal-new]').click();
    await page.locator('#patientOpportunityForm input[name=title]').fill(negotiationName);
    await page.locator('#patientOpportunityForm input[name=value]').fill('450');
    await page.locator('#patientOpportunityForm input[name=confirm]').check();
    await page.locator('#patientOpportunityForm button[type=submit]').click();
    await page.locator('.patientOpportunity').filter({hasText:negotiationName}).waitFor();
    const item=page.locator('.patientOpportunity').filter({hasText:negotiationName});
    await item.locator('[data-deal-edit]').click();
    await page.locator('#patientOpportunityForm select[name=status]').selectOption('lost');
    await page.locator('#patientOpportunityForm select[name=closure_reason]').selectOption('value');
    await page.locator('#patientOpportunityForm textarea[name=closure_note]').fill('Recusa expressa de exemplo');
    await page.locator('#patientOpportunityForm button[type=submit]').click();
    await page.locator('.patientOpportunity').filter({hasText:negotiationName}).locator('.commercialBadge').filter({hasText:'Encerrada'}).waitFor();
    await page.screenshot({path:'/private/tmp/doc4docs-patient-opportunities-desktop.png',fullPage:true});
    await page.locator('#closeDialog').click();
    await page.goto(board.href);
    await page.locator('.leadCard').first().waitFor();
    assert.equal(await page.locator('.leadCard').count(),cards);
    assert.equal(await page.locator('.crmColumn[aria-label="Pacientes"] .leadCard').filter({hasText:'Clara Exemplo'}).count(),1);
    await page.locator('.leadCard').filter({hasText:'Camila Exemplo'}).click();
    await page.getByRole('button',{name:'Ficha e histórico',exact:true}).click();
    await page.locator('#leadEdit select[name=stage_id]').selectOption({label:'Não comercial'});
    await page.locator('#leadEdit select[name=closure_reason]').selectOption('supplier');
    await page.locator('#leadEdit textarea[name=closure_note]').fill('Contato de fornecedor de exemplo');
    await page.locator('#leadEdit button[type=submit]').click();
    await page.locator('#crmDialog').waitFor({state:'hidden'});
    await page.goto(board.href);await page.locator('.leadCard').first().waitFor();
    assert.equal(await page.locator('.leadCard').count(),cards);
    assert.ok((await page.locator('.boardSummary').innerText()).includes('1 não comerciais'));
    await page.locator('.leadCard').filter({hasText:'Clara Exemplo'}).click();
    await page.getByRole('button',{name:'Ficha e histórico',exact:true}).click();
    await page.getByRole('tab',{name:'Oportunidades',exact:true}).click();
    await page.locator('.patientOpportunity').first().waitFor();
    await page.setViewportSize({width:390,height:844});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
    await page.screenshot({path:'/private/tmp/doc4docs-patient-opportunities-mobile.png',fullPage:true});
    await page.locator('#closeDialog').click();
    await page.setViewportSize({width:1440,height:960});
    await page.goto(board.href);await page.locator('.leadCard').first().waitFor();
    const regen=page.locator('#pipeline option').filter({hasText:'RegenCode'});
    if(await regen.count()){
      await page.locator('#pipeline').selectOption(await regen.getAttribute('value'));
      await page.waitForFunction(()=>document.querySelectorAll('.crmColumn').length===6);
      assert.equal(await page.locator('#applyClinicJourney').count(),0);
      assert.equal(await page.locator('.crmColumn[aria-label="Não comercial"]').count(),0);
      await page.locator('#pipeline').selectOption(selected);
    }
  }catch(error){
    console.error('Page errors:',errors);
    console.error('Dialog:',await page.locator('#crmDialog').innerText().catch(()=>''));
    await page.screenshot({path:'/private/tmp/doc4docs-patient-journey-ui-error.png',fullPage:true});
    throw error;
  }finally{await browser.close();}
  assert.deepEqual(errors,[]);console.log('Patient journey UI: migration, nested negotiations and preservation checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
