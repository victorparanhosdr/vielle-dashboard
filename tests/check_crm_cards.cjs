// Read-only visual check against the synthetic loopback CRM preview.
const assert=require("node:assert/strict");
const path=require("node:path");
const {chromium}=require("playwright");

(async()=>{
  const target=new URL(process.argv[2]);assert.equal(target.hostname,"127.0.0.1");
  const browser=await chromium.launch({headless:true});
  const page=await browser.newPage();const errors=[];
  page.on("pageerror",e=>errors.push(e.message));
  try {
    await page.goto(target.href);await page.locator(".appointmentInline.next").first().waitFor();
    for(const width of [1440,390,320]){
      await page.setViewportSize({width,height:900});
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      assert.ok(await page.locator(".leadCard .appointmentInline").evaluateAll(items=>items.every(item=>item.getBoundingClientRect().height<=19 && item.scrollWidth<=item.clientWidth)));
      assert.equal(await page.locator(".leadCard .appointmentProcedure,.leadCard .appointmentStatus,.leadCard .leadAppointment").count(),0);
      assert.equal(await page.locator(".leadCard .customerTier").count(),await page.locator(".leadCard").count());
      assert.ok(await page.locator(".leadCard .customerTier").evaluateAll(items=>items.every(n=>n.getBoundingClientRect().height<=17 && n.scrollWidth<=n.clientWidth)));
      assert.ok(await page.locator(".leadCard").evaluateAll(items=>items.every(n=>n.scrollWidth<=n.clientWidth)));
      const card=page.locator(".leadCard").filter({has:page.getByText("Ana Exemplo",{exact:true})});
      assert.ok((await card.boundingBox()).height<210);
      assert.ok((await card.locator(".appointmentInline").getAttribute("title")).includes("Clínica Experts"));
      if(width!==320)await page.screenshot({path:path.resolve(__dirname,`../../doc4docs-crm-compact-${width}.png`),fullPage:true});
    }
    await page.setViewportSize({width:1440,height:1000});
    await page.locator(".leadCard").filter({has:page.getByText("Ana Exemplo",{exact:true})}).click();
    await page.locator("#chatName").filter({hasText:"Ana Exemplo"}).waitFor();
    await page.getByRole("button",{name:"Agendamentos",exact:true}).click();
    await page.locator("#profileAppointments .appointmentItem").first().waitFor();
    assert.ok(await page.locator(".appointmentHighlight .appointmentProcedure").isVisible());
    assert.ok(await page.locator(".appointmentHighlight .appointmentStatus").isVisible());
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({ok:true,viewports:[1440,390,320],compactLine:true,fullDetailsPreserved:true,pageErrors:errors}));
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
