"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs");
const {preferences,create,renderCue}=require("../static/crm-sounds.js");
assert.equal(preferences().enabled,false);
assert.equal(preferences({enabled:true,volume:900}).volume,100);
assert.equal(preferences({volume:-2}).volume,0);
assert.equal(preferences({enabled:"true"}).enabled,false);
assert.equal(preferences({volume:NaN}).volume,35);
assert.equal(preferences({}).style,"vibrant");
assert.equal(preferences({style:"soft"}).style,"soft");
assert.equal(preferences({style:"invalid"}).style,"vibrant");
assert.equal(preferences(null).style,"vibrant");
for(const kind of ["message","reminder","stage","achievement","win","level"]){
  const cue=renderCue(kind),soft=renderCue(kind,"soft");
  assert.ok(cue.duration>0 && cue.duration<2);
  let peak=0,energy=0;for(const value of cue.samples){assert.ok(Number.isFinite(value));peak=Math.max(peak,Math.abs(value));energy+=value*value;}
  assert.ok(peak<=.841 && peak>=.83);assert.ok(Math.sqrt(energy/cue.samples.length)>.07);
  assert.equal(cue.samples[0],0);assert.equal(cue.samples.at(-1),0);
  assert.notDeepEqual(cue.samples,soft.samples);
}
assert.ok(renderCue("win").duration>renderCue("message").duration*3);
assert.notDeepEqual(renderCue("win").samples,renderCue("level").samples);
const html=fs.readFileSync("static/crm.html","utf8"),reminders=fs.readFileSync("static/crm-reminders.js","utf8"),engagement=fs.readFileSync("static/crm-engagement.js","utf8"),sounds=fs.readFileSync("static/crm-sounds.js","utf8"),inbox=fs.readFileSync("static/crm-inbox.js","utf8");
assert.match(html,/data-mode="tasks"/);
assert.match(reminders,/type="datetime-local"/);
assert.match(reminders,/request_id:requestId/);
assert.match(reminders,/America\/Sao_Paulo/);
assert.match(inbox,/data-mobile-chat="createChatReminder"/);
assert.match(engagement,/role="switch"/);
assert.match(engagement,/cursors\?\{watch:"1"/);
assert.match(sounds,/navigator\.locks\.request/);
assert.doesNotMatch(engagement,/Notification\.requestPermission|new Notification/);
assert.ok(html.indexOf("crm-reminders.js")<html.indexOf("crm.js?v="));
console.log("CRM reminders, opt-in audio and notification integration checks passed");
async function audioTests(){
  const saved=new Map();global.localStorage={getItem:key=>saved.get(key),setItem:(key,value)=>saved.set(key,value)};
  let played=0,stopped=0,buffers=0;
  global.AudioContext=class{constructor(){this.state="suspended";this.currentTime=1;this.sampleRate=44100;this.destination={};}async resume(){this.state="running";}createBuffer(_,length){buffers++;return{getChannelData:()=>new Float32Array(length)};}createBufferSource(){return{connect(){},start(){played++;},stop(){stopped++;this.onended?.();},disconnect(){}};}createGain(){return{gain:{setValueAtTime(){}},connect(){},disconnect(){}};}};
  let user=1;const audio=create({getUser:()=>user});audio.reset();
  assert.equal(await audio.play("message"),false);
  await audio.unlock();audio.save({enabled:true});
  assert.equal(await audio.once("message:1","message"),true);assert.equal(played,1);
  assert.equal(await audio.once("message:1","message"),false);assert.equal(played,1);
  assert.equal(await audio.once("stage:1","stage",{relatedIds:["xp:1"]}),true);assert.equal(played,2);
  assert.equal(await audio.once("xp:1","achievement"),false);assert.equal(played,2);
  assert.equal(await audio.play("stage"),true);assert.equal(played,3);assert.equal(buffers,2);
  assert.ok(stopped>=1);
  audio.save({enabled:false});assert.equal(await audio.play("win"),false);
  assert.equal(await audio.play("win",{test:true}),true);assert.equal(played,4);
  audio.save({volume:0});assert.equal(await audio.play("win",{test:true}),false);audio.save({volume:35});
  user=2;audio.reset();assert.equal(audio.settings().enabled,false);
  user=1;audio.reset();assert.equal(audio.settings().enabled,false);assert.equal(audio.settings().volume,35);
  console.log("Vibrant PCM cues, safe peaks, independent melodies, instant stage audio, mute, cache and event deduplication passed");
}
audioTests().catch(error=>{console.error(error);process.exitCode=1;});
