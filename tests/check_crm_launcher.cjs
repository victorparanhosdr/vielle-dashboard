// Synthetic loopback preview only; no live messages or external provider calls.
const assert=require("node:assert/strict");
const path=require("node:path");
const {chromium}=require("playwright");
(async()=>{
  const target=new URL(process.argv[2]);assert.equal(target.hostname,"127.0.0.1");
  const browser=await chromium.launch({headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1000},locale:"pt-BR"}),errors=[];
  page.on("pageerror",e=>errors.push(e.message));
  try{
    await page.goto(target.href);await page.locator(".leadCard").first().waitFor();
    const launcher=page.getByRole("link",{name:"Abrir ponto de atendimento da Vielle Clinic",exact:true});
    await launcher.waitFor();await launcher.click();
    await page.getByRole("tab",{name:"Ponto de atendimento",exact:true}).waitFor();
    await page.locator(".conversationRow").filter({hasText:"Marina Exemplo"}).click();
    await page.waitForFunction(()=>document.querySelector("#chatName")?.textContent==="Marina Exemplo");
    const draft=page.locator("#replyBody");await draft.fill("Rascunho de teste local");await launcher.click();
    assert.equal(await draft.inputValue(),"Rascunho de teste local");
    assert.equal(await page.locator("#chatName").textContent(),"Marina Exemplo");
    await page.goto(new URL("/tasks.html?clinic=vielle",target).href);await launcher.waitFor();
    await launcher.click();await page.waitForURL(/\/crm.html\?clinic=vielle&mode=inbox/);
    await page.waitForFunction(()=>document.querySelector('[data-mode="inbox"]')?.getAttribute("aria-selected")==="true");
    await page.locator(".conversationRow").first().waitFor();
    for(const width of [1440,390,320]){
      await page.setViewportSize({width,height:1000});
      assert.ok(await launcher.evaluate(n=>{const b=n.getBoundingClientRect();return b.left>=0 && b.right<=innerWidth && b.top>=0 && b.bottom<=innerHeight;}),`launcher outside viewport at ${width}`);
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`overflow at ${width}`);
      if(width!==320)await page.screenshot({path:path.resolve(__dirname,`../../doc4docs-crm-launcher-${width}.png`),fullPage:false});
    }
    await page.evaluate(()=>window.dispatchEvent(new CustomEvent("doc4docs-session",{detail:{...window.doc4docsSession,user:{...window.doc4docsSession.user,is_master:false}}})));
    assert.ok(await launcher.isVisible());
    await page.evaluate(()=>window.dispatchEvent(new CustomEvent("doc4docs-session",{detail:{...window.doc4docsSession,user:{...window.doc4docsSession.user,is_master:false},permissions:{...window.doc4docsSession.permissions,vielle:[]}}})));
    assert.ok(await launcher.isHidden());
    await page.goto(new URL("/precificacao",target).href);assert.equal(await page.locator(".appAttendanceLauncher").count(),0);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({ok:true,sharedLauncher:true,directInbox:true,preservedDraft:true,permissionScoped:true,publicPageExcluded:true,viewports:[320,390,1440],pageErrors:errors}));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
