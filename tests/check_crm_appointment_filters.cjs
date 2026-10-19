// Synthetic loopback fixture only. No provider calls or message sending.
const assert=require("node:assert/strict");
const path=require("node:path");
const {chromium}=require("playwright");

(async()=>{
  const target=new URL(process.argv[2]);assert.equal(target.hostname,"127.0.0.1");
  const browser=await chromium.launch({headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1100},locale:"pt-BR",timezoneId:"America/Sao_Paulo"}),errors=[];
  page.on("pageerror",e=>errors.push(e.message));
  const response=()=>page.waitForResponse(r=>r.url().includes("/api/crm?") && r.status()===200);
  async function open(){if(await page.locator("#crmFilterPanel").isHidden())await page.locator("#toggleCrmFilters").click();}
  async function apply(){const pending=response();await page.getByRole("button",{name:"Aplicar filtros",exact:true}).click();await pending;await page.waitForFunction(()=>document.querySelector("#crmView").getAttribute("aria-busy")==="false");}
  async function names(expected,inbox=false){
    await page.waitForFunction(({expected,inbox})=>{
      const selector=inbox?".conversationRow strong":".leadCard .leadPerson>strong";
      const actual=[...document.querySelectorAll(selector)].map(n=>n.textContent).sort();
      return JSON.stringify(actual)===JSON.stringify([...expected].sort());
    },{expected,inbox});
  }
  try{
    const initial=response();await page.goto(target.href);const data=await (await initial).json();await page.locator(".leadCard").first().waitFor();
    const doctor=data.doctor_id, anchor=new Date(data.today+"T12:00:00Z");
    const year=anchor.getUTCFullYear(),month=anchor.getUTCMonth(),iso=d=>d.toISOString().slice(0,10);
    const first=iso(new Date(Date.UTC(year,month-1,1))),last=iso(new Date(Date.UTC(year,month,0)));
    const unread=async()=>{const r=await page.request.get(new URL(`/api/crm/inbox?clinic=vielle&doctor=${doctor}`,target).href);assert.equal(r.status(),200);return (await r.json()).counts.unread;};
    assert.equal(await unread(),3);
    await open();const shortcut=response();await page.locator("#previousMonthReturns").click();await shortcut;
    await names(["Marina Exemplo"]);
    assert.equal(await page.locator(".filterChip").count(),4);
    assert.match(await page.locator(".leadCard .appointmentInline").textContent(),/Realizado/);
    assert.ok((await page.locator(".leadCard .appointmentInline").getAttribute("title")).includes(first.slice(0,7).split("-").reverse().join("/")));
    await open();assert.equal(await page.locator("#filterAppointmentFrom").inputValue(),first);assert.equal(await page.locator("#filterAppointmentTo").inputValue(),last);
    assert.equal(await page.locator("#appointmentRange").inputValue(),"last_month");
    await page.locator("#filterAppointmentNoUpcoming").uncheck();await apply();await names(["Ana Exemplo","Marina Exemplo"]);
    await open();await page.locator("#filterMinSpent").fill("5000");await apply();await names(["Ana Exemplo"]);
    await open();await page.locator("#filterMinSpent").fill("");await page.locator("#filterAppointmentStatus").selectOption("scheduled");await page.locator("#appointmentRange").selectOption("next_30");
    assert.equal(await page.locator("#filterAppointmentFrom").inputValue(),data.today);
    await apply();await names(["Ana Exemplo","Renata Exemplo"]);
    await open();await page.locator("#appointmentRange").selectOption("last_month");
    await page.locator("#filterAppointmentFrom").fill(first.slice(0,8)+"02");await page.locator("#filterAppointmentFrom").blur();
    assert.equal(await page.locator("#appointmentRange").inputValue(),"");
    await page.locator("#filterAppointmentFrom").fill(last);await page.locator("#filterAppointmentTo").fill(first);
    await page.getByRole("button",{name:"Aplicar filtros",exact:true}).click();assert.ok(await page.locator("#crmFilterError").isVisible());
    const again=response();await page.locator("#previousMonthReturns").click();await again;await names(["Marina Exemplo"]);
    assert.ok(await page.locator("#crmFilterError").isHidden());
    await page.getByRole("tab",{name:"Ponto de atendimento",exact:true}).click();await names(["Marina Exemplo"],true);
    await page.locator(".conversationRow").click();await page.waitForFunction(()=>document.querySelector("#chatName")?.textContent==="Marina Exemplo");
    assert.equal(await unread(),3);
    await page.locator('[data-remove-filter="appointment_no_upcoming"]').click();await names(["Ana Exemplo","Marina Exemplo"],true);
    assert.equal(await unread(),3);
    await page.getByRole("tab",{name:"Funis",exact:true}).click();await names(["Ana Exemplo","Marina Exemplo"]);
    for(const width of [1440,768,390,320]){
      await page.setViewportSize({width,height:1100});await open();
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`page overflow at ${width}`);
      assert.ok(await page.locator("#crmFilterPanel").evaluate(n=>n.scrollWidth<=n.clientWidth),`filter overflow at ${width}`);
      const bounds=await page.locator(".appointmentFilterGrid").evaluate(n=>[...n.querySelectorAll("input,select")].every(e=>{const b=e.getBoundingClientRect(),p=n.getBoundingClientRect();return b.left>=p.left-1 && b.right<=p.right+1;}));
      assert.ok(bounds,`appointment controls overflow at ${width}`);
      if(width===1440 || width===390){await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:path.resolve(__dirname,`../../doc4docs-crm-appointment-filters-${width}.png`),fullPage:true});}
      await page.locator("#toggleCrmFilters").click();
    }
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({ok:true,previousMonthReturns:true,completedNotJustPast:true,combinedPurchaseFilters:true,sharedInboxFilters:true,unreadPreserved:true,viewports:[320,390,768,1440],pageErrors:errors}));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
