"use strict";
const test=require("node:test"),assert=require("node:assert/strict");
const {ActivityClock,duration,elapsed,escape,initials}=require("../static/team-presence.js");
test("idle age only changes after trusted visible interaction",()=>{
  const clock=new ActivityClock(1000);
  assert.equal(clock.age(301000),300);
  clock.touch(301000,false,true);assert.equal(clock.age(302000),301);
  clock.touch(302000,true,false);assert.equal(clock.age(303000),302);
  clock.touch(303000,true,true);assert.equal(clock.age(304000),1);
  assert.equal(clock.age(0),0);assert.equal(clock.age(999999999),86400);
  assert.equal(new ActivityClock(1000,false).age(1000),300);
});
test("durations handle missing presence without fabricating a login",()=>{
  assert.equal(duration(60),"1 min");assert.equal(duration(3601),"1h00");
  assert.equal(elapsed(null,500),"Ainda não entrou");
  assert.equal(escape('<img src="x">'),"&lt;img src=&quot;x&quot;&gt;");
  assert.equal(initials("Ana Martins"),"AM");
});
