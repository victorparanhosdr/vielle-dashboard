// Synthetic loopback fixture only; no pairing, provider calls or real messages.
const assert=require("node:assert/strict");
const path=require("node:path");
const {chromium}=require("playwright");

(async()=>{
  const target=new URL(process.argv[2]);assert.equal(target.hostname,"127.0.0.1");
  const browser=await chromium.launch({headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1000}}), errors=[];
  page.on("pageerror",e=>errors.push(e.message));
  const out=name=>path.resolve(__dirname,`../../doc4docs-crm-${name}.png`);
  const client=name=>page.locator(".leadCard").filter({has:page.getByText(name,{exact:true})});
  try {
    await page.goto(target.href);await client("Ana Exemplo").waitFor();
    for(const [name,tier] of [["Ana Exemplo","gold"],["Renata Exemplo","silver"],["Marina Exemplo","bronze"]]){
      assert.equal(await client(name).getAttribute("data-tier"),tier);
      assert.equal(await client(name).locator(".customerTier").count(),1);
      assert.ok((await client(name).boundingBox()).height<180);
    }
    assert.ok((await client("Ana Exemplo").getAttribute("title")).includes("18.100,50"));
    const fills=await Promise.all(["Ana Exemplo","Renata Exemplo","Marina Exemplo"].map(name=>client(name).evaluate(n=>getComputedStyle(n).backgroundImage)));
    assert.equal(new Set(fills).size,3);
    for(const [tier,name] of [["gold","Ana Exemplo"],["silver","Renata Exemplo"],["bronze","Marina Exemplo"]]){
      await page.getByRole("button",{name:/^Filtros/}).click();
      await page.locator("#customerTier").selectOption(tier);
      await page.getByRole("button",{name:"Aplicar filtros",exact:true}).click();
      await page.waitForFunction(t=>document.querySelectorAll(".leadCard").length===1 && document.querySelector(".leadCard")?.dataset.tier===t,tier);
      assert.equal(await page.locator(".leadCard").count(),1);
      assert.ok(await client(name).isVisible());
    }
    await page.getByRole("button",{name:/^Filtros/}).click();
    await page.locator("#customerTier").selectOption("unclassified");
    await page.getByRole("button",{name:"Aplicar filtros",exact:true}).click();
    await page.waitForFunction(()=>document.querySelectorAll(".leadCard").length===3);
    assert.equal(await page.locator(".leadCard").count(),3);
    await page.getByRole("button",{name:/^Filtros/}).click();
    await page.getByRole("button",{name:"Limpar",exact:true}).click();
    await page.waitForFunction(()=>document.querySelectorAll(".leadCard").length===6);
    await page.getByRole("button",{name:"Configurar classificação de clientes",exact:true}).click();
    await page.screenshot({path:out("tier-settings")});
    await page.locator('[name="silver"]').fill("1000");
    await page.locator('[name="gold"]').fill("500");
    await page.getByRole("button",{name:"Salvar classificação",exact:true}).click();
    await page.locator("#formError:not([hidden])").waitFor();
    await page.locator('[name="gold"]').fill("2500");
    await page.getByRole("button",{name:"Salvar classificação",exact:true}).click();
    await page.locator("#crmDialog").waitFor({state:"hidden"});
    await page.waitForFunction(()=>document.querySelectorAll('.leadCard[data-tier="gold"]').length===3);
    await page.getByRole("button",{name:"Configurar classificação de clientes",exact:true}).click();
    await page.locator('[name="silver"]').fill("5000");await page.locator('[name="gold"]').fill("15000");
    await page.getByRole("button",{name:"Salvar classificação",exact:true}).click();
    await page.locator("#crmDialog").waitFor({state:"hidden"});
    await page.waitForFunction(()=>document.querySelectorAll('.leadCard[data-tier="gold"]').length===1);
    await page.getByRole("button",{name:/^Filtros/}).click();
    for(const width of [1440,1024,768,390,320]){
      await page.setViewportSize({width,height:1000});
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`page overflow at ${width}`);
      assert.ok(await page.locator(".leadCard").evaluateAll(items=>items.every(n=>n.scrollWidth<=n.clientWidth)),`card overflow at ${width}`);
      assert.ok(await page.locator(".customerTier").evaluateAll(items=>items.every(n=>n.scrollWidth<=n.clientWidth)),`tier overflow at ${width}`);
      const height=await client("Ana Exemplo").evaluate(n=>n.getBoundingClientRect().height);
      assert.ok(height<210,`card too tall at ${width}: ${height}`);
      if(width<=760)assert.ok(await page.locator("#search").evaluate(n=>parseFloat(getComputedStyle(n).fontSize)>=16));
      if([1440,390].includes(width))await page.screenshot({path:out(`premium-${width}`),fullPage:true});
    }
    await page.setViewportSize({width:1440,height:1000});
    await client("Ana Exemplo").click();
    await page.waitForFunction(()=>document.querySelector("#chatName")?.textContent==="Ana Exemplo");
    await page.locator("#chatPane .customerTier.gold").waitFor();
    assert.equal(await page.locator(".chatCustomerTier").count(),1);
    assert.ok((await page.locator("#chatName + span").textContent()).includes("Cliente +"));
    await page.locator("#reload").click();
    await page.waitForResponse(r=>r.url().includes("/api/crm/conversation?") && r.status()===200);
    assert.equal(await page.locator(".chatCustomerTier").count(),1);
    assert.ok((await page.locator(".contactSummary .customerValueSummary").textContent()).includes("18.100,50"));
    await page.screenshot({path:out("premium-inbox"),fullPage:true});
    await page.getByRole("button",{name:"Ficha e histórico",exact:true}).click();
    await page.locator("#modalBody .customerValueSummary.gold").waitFor();
    assert.ok((await page.locator("#modalBody .customerValueSummary").textContent()).includes("18.100,50"));
    await page.screenshot({path:out("premium-profile")});
    await page.getByRole("button",{name:"Fechar",exact:true}).click();
    await page.setViewportSize({width:390,height:900});
    assert.ok(await page.locator("#chatPane").evaluate(n=>n.scrollWidth<=n.clientWidth));
    await page.screenshot({path:out("premium-inbox-mobile"),fullPage:true});
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({ok:true,tiers:["bronze","silver","gold"],editableThresholds:true,compactCards:true,viewports:[320,390,768,1024,1440],pageErrors:errors}));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
