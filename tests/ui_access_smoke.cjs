// Run only against the disposable QA database, never against production.
const assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.ACCESS_QA_URL || 'http://127.0.0.1:8773';
assert.equal(new URL(base).hostname, '127.0.0.1');

(async () => {
  const browser = await chromium.launch({headless:true, executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
  const errors = [];
  try {
    async function login(user, master=false) {
      const context = await browser.newContext({viewport:{width:1440,height:1000}});
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(base + (master ? '/master' : '/'));
      await page.locator('#login').fill(user);
      await page.locator('#password').fill(master ? 'MasterUiTest2026!' : 'AccessUiTest2026!');
      await page.locator('#loginSubmit').click();
      if (master) await page.getByRole('button',{name:'Editar Comercial Teste',exact:true}).waitFor();
      else await page.waitForFunction(() => typeof allowedClinics !== 'undefined' && allowedClinics !== null);
      return page;
    }
    const master = await login('master',true);
    await master.getByRole('button',{name:'Editar Comercial Teste',exact:true}).click();
    const vielle = master.locator('.clinicPermissions[data-clinic="vielle"]');
    const carla = master.locator('.clinicPermissions[data-clinic="carla"]');
    assert.equal(await carla.locator('[data-permission="patient_followup.view"]').count(),0);
    assert.equal(await vielle.locator('[data-permission="financial.view"]').isChecked(),false);
    await master.screenshot({path:'/tmp/doc4docs-permissions-desktop.png'});
    await master.setViewportSize({width:390,height:844});
    await master.screenshot({path:'/tmp/doc4docs-permissions-mobile.png'});
    await master.locator('[data-close="userDialog"]').first().click();
    await master.locator('[data-master-section="profiles"]').click();
    await master.getByRole('button',{name:'Editar perfil Financeiro',exact:true}).waitFor();
    await master.locator('#newProfile').click();
    const profileName = 'Leitura QA ' + Date.now();
    await master.locator('#profileName').fill(profileName);
    await master.locator('#profilePermissions [data-permission="financial.view"]').check();
    await master.locator('#saveProfile').click();
    await master.getByRole('button',{name:`Editar perfil ${profileName}`,exact:true}).waitFor();
    await master.locator('[data-master-section="users"]').click();
    await master.getByRole('button',{name:'Editar Comercial Teste',exact:true}).click();
    await vielle.getByRole('combobox',{name:'Perfil em Vielle Clinic',exact:true}).selectOption({label:profileName});
    assert.equal(await vielle.locator('[data-permission="financial.view"]').isChecked(),true);
    assert.equal(await vielle.locator('[data-permission="commercial.view"]').isChecked(),false);
    // Cancel the template exercise to keep the restricted user's original rights.
    await master.locator('[data-close="userDialog"]').first().click();
    await master.locator('[data-master-section="audit"]').click();
    await master.locator('#auditAction').selectOption('profile_saved');
    await master.locator('#auditList li').filter({hasText:'Perfil salvo'}).first().waitFor();
    await master.screenshot({path:'/tmp/doc4docs-audit-mobile.png'});

    const limited = await login('limited');
    await limited.locator('[data-clinic-select="vielle"][data-access-mode="dashboard"]').click();
    await limited.locator('#commercialView.active').waitFor();
    assert.equal(await limited.locator('#clinicAccessModal').isVisible(),false);
    assert.equal(await limited.locator('[data-view="financialView"]').isVisible(),false);
    assert.equal(await limited.locator('#exportPdfBtn').isVisible(),false);
    assert.equal(await limited.locator('#syncBtn').isVisible(),false);
    const denied = await limited.request.get(base+'/api/report?clinic=vielle&view=financialView');
    assert.equal(denied.status(),403);
    await limited.setViewportSize({width:390,height:844});
    await limited.locator('#mobileTabsToggle').click();
    assert.equal(await limited.locator('.tabBtn:not([hidden])').count(),1);
    await limited.screenshot({path:'/tmp/doc4docs-restricted-mobile.png'});
    await limited.locator('#mobileTabsToggle').click();
    await limited.locator('#changeClinicBtn').click();
    await limited.locator('[data-clinic-select="carla"]').click();
    await limited.locator('#financialView.active').waitFor();
    assert.equal(await limited.locator('#exportPdfBtn').isVisible(),true);

    const readonly = await login('readonly');
    await readonly.locator('#patientFollowupView.active').waitFor();
    await readonly.evaluate(() => {
      renderPatientFollowup({items:[{patient_uuid:'qa',patient_name:'Paciente de teste',category:'Toxina Botulínica',status:'red',wallet_status:'active'}],totals:{}});
    });
    assert.equal(await readonly.locator('.followupForm:visible').count(),0);
    assert.equal(await readonly.locator('.followupStatusForm:visible').count(),0);
    const noActions = await readonly.request.post(base+'/api/patient-followup-contact?clinic=vielle',{headers:{'X-DOC4DOCS-Request':'1'},data:{}});
    assert.equal(noActions.status(),403);

    const empty = await login('empty');
    await empty.getByText('Nenhuma aba liberada nesta clínica. Entre em contato com o Master.',{exact:true}).waitFor();
    assert.equal(await empty.locator('#dashboardShell').isVisible(),false);

    await master.locator('[data-master-section="users"]').click();
    await master.getByRole('button',{name:'Editar Comercial Teste',exact:true}).click();
    await master.locator('.clinicPermissions[data-clinic="carla"] [data-permission="financial.view"]').uncheck();
    await master.locator('#saveUser').click();
    await master.locator('#userDialog').waitFor({state:'hidden'});
    await limited.reload();
    await limited.getByText('Nenhuma aba liberada nesta clínica. Entre em contato com o Master.',{exact:true}).waitFor();
    assert.deepEqual(errors,[]);
    console.log('PASS: permissions editor, profile creation/application, audit, per-clinic views, mobile menu, readonly actions, session revocation, no legacy code prompt, no JS errors');
  } finally {
    await browser.close();
  }
})().catch(error=>{console.error(error);process.exit(1);});
