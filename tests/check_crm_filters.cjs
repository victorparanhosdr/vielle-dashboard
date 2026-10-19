// Synthetic loopback fixture only. No remote writes or message sending.
const assert=require("node:assert/strict");
const path=require("node:path");
const {chromium}=require("playwright");

(async()=>{
  const target=new URL(process.argv[2]);assert.equal(target.hostname,"127.0.0.1");
  const browser=await chromium.launch({headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on("pageerror",e=>errors.push(e.message));
  const fields={identity:"#filterIdentity",tier:"#customerTier",sales:"#filterSales",quotes:"#filterQuotes",min:"#filterMinSpent",max:"#filterMaxSpent",count:"#filterMinSales"};
  const rootResponse=()=>page.waitForResponse(r=>r.url().includes("/api/crm?") && r.status()===200);
  async function apply(values={}){
    if(await page.locator("#crmFilterPanel").isHidden())await page.locator("#toggleCrmFilters").click();
    for(const [key,id] of Object.entries(fields)){
      if(["identity","tier","sales","quotes"].includes(key))await page.locator(id).selectOption(values[key] || "");
      else await page.locator(id).fill(values[key] || "");
    }
    const response=rootResponse();await page.getByRole("button",{name:"Aplicar filtros",exact:true}).click();await response;
    await page.waitForFunction(()=>document.querySelector("#crmView").getAttribute("aria-busy")==="false");
  }
  async function names(expected){
    await page.waitForFunction(names=>{
      const actual=[...document.querySelectorAll(".leadCard .leadPerson>strong")].map(n=>n.textContent).sort();
      return JSON.stringify(actual)===JSON.stringify([...names].sort());
    },expected);
  }
  try {
    await page.goto(target.href);await page.locator(".leadCard").first().waitFor();
    assert.equal(await page.locator(".leadCard .customerTier").count(),await page.locator(".leadCard").count());
    await apply({sales:"yes",min:"5000",max:"10000",count:"2"});await names(["Renata Exemplo"]);
    assert.equal(await page.locator(".filterChip").count(),4);
    await apply({sales:"yes",min:"5000",max:"10000",count:"2",quotes:"active"});await names([]);
    await apply({min:"5000",max:"10000",quotes:"pending"});await names(["Renata Exemplo"]);
    await apply({quotes:"active"});await names(["Marina Exemplo"]);
    await apply({quotes:"none"});await names(["Ana Exemplo"]);
    await apply({sales:"no"});await names([]);
    assert.ok(await page.locator("#crmFilterCoverage").isVisible());
    await apply({identity:"new"});await names(["Camila Exemplo","Juliana Exemplo","Clara Exemplo"]);
    await apply({identity:"matched"});await names(["Ana Exemplo","Marina Exemplo","Renata Exemplo"]);
    await apply();
    await page.locator("#search").fill("@example.test");await names(["Ana Exemplo","Marina Exemplo","Renata Exemplo"]);
    await page.locator("#search").fill("individualizado");await names(["Ana Exemplo","Marina Exemplo","Renata Exemplo"]);
    await page.locator("#search").fill("");
    await names(["Ana Exemplo","Marina Exemplo","Renata Exemplo","Camila Exemplo","Juliana Exemplo","Clara Exemplo"]);
    const sorted=rootResponse();await page.locator("#crmSort").selectOption("spent");
    assert.equal((await (await sorted).json()).leads[0].name,"Ana Exemplo");
    await page.getByRole("tab",{name:"Ponto de atendimento",exact:true}).click();
    await page.locator(".conversationRow").filter({hasText:"Marina Exemplo"}).click();
    await page.waitForFunction(()=>document.querySelector("#chatName")?.textContent==="Marina Exemplo");
    await apply({quotes:"active"});
    await page.waitForFunction(()=>document.querySelectorAll(".conversationRow").length===1);
    assert.ok((await page.locator(".conversationRow").textContent()).includes("Marina Exemplo"));
    await apply({tier:"gold"});
    await page.waitForFunction(()=>document.querySelectorAll(".conversationRow").length===1 && document.querySelector(".conversationRow strong")?.textContent==="Ana Exemplo");
    assert.equal(await page.locator("#chatName").textContent(),"Marina Exemplo");
    assert.ok((await page.locator(".contactSummary").textContent()).includes("Marina Exemplo"));
    await apply();await page.getByRole("tab",{name:"Funis",exact:true}).click();await page.locator(".leadCard").first().waitFor();
    for(const width of [1440,768,390,320]){
      await page.setViewportSize({width,height:1000});
      if(await page.locator("#crmFilterPanel").isHidden())await page.locator("#toggleCrmFilters").click();
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`page overflow at ${width}`);
      assert.ok(await page.locator("#crmFilterPanel").evaluate(n=>n.scrollWidth<=n.clientWidth),`filter overflow at ${width}`);
      assert.ok(await page.locator(".crmSearchBar").evaluate(n=>n.scrollWidth<=n.clientWidth),`search overflow at ${width}`);
      if(width===1440 || width===390){await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:path.resolve(__dirname,`../../doc4docs-crm-search-filters-${width}.png`),fullPage:true});}
      await page.locator("#toggleCrmFilters").click();
    }
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({ok:true,coloredCardsWithCompactBadges:true,combinedExpertsFilters:true,sharedInboxFilters:true,privateProfileSearch:true,viewports:[320,390,768,1440],pageErrors:errors}));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
