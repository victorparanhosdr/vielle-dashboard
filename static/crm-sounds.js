(function (root) {
  "use strict";
  const defaults={enabled:false,messages:true,reminders:true,achievements:true,volume:35,style:"vibrant"};
  const scores={
    message:[[84,0,.16,"pop"],[88,.10,.18,"bell"],[91,.20,.24,"bell",.7]],
    reminder:[[79,0,.22,"bell"],[86,.18,.3,"bell"],[79,.43,.2,"bell"],[86,.60,.38,"bell"]],
    stage:[[76,0,.14,"chip"],[81,.09,.16,"chip"],[83,.18,.24,"bell"]],
    achievement:[[72,0,.16,"chip"],[76,.12,.16,"chip"],[79,.24,.18,"chip"],[84,.36,.34,"bell"],[60,.02,.35,"bass",.5]],
    win:[[60,0,.28,"bass",.8],[72,0,.18,"chip"],[76,.14,.18,"chip"],[79,.28,.18,"chip"],
      [84,.43,.24,"bell"],[88,.60,.24,"bell"],[91,.77,.27,"bell"],[96,.95,.45,"bell",.7],
      [60,.95,.6,"bass",.65],[72,1.02,.5,"chip",.45],[76,1.02,.5,"chip",.4],[79,1.02,.5,"chip",.4]],
    level:[[64,0,.2,"bass",.8],[76,0,.15,"chip"],[79,.12,.15,"chip"],[83,.24,.15,"chip"],
      [86,.36,.18,"chip"],[91,.50,.2,"bell"],[95,.65,.24,"bell"],[96,.82,.3,"bell"],
      [72,1,.58,"bell",.6],[76,1,.58,"bell",.5],[79,1,.58,"bell",.5],[48,1,.5,"bass",.7]]
  };
  const softScores={message:[[79,0,.18,"bell"],[84,.13,.23,"bell",.65]],reminder:[[74,0,.25,"bell"],[79,.23,.3,"bell"]],
    stage:[[72,0,.16,"bell"],[76,.12,.22,"bell"]],achievement:[[72,0,.18,"bell"],[76,.13,.18,"bell"],[79,.26,.3,"bell"]],
    win:[[72,0,.18,"bell"],[76,.16,.18,"bell"],[79,.32,.18,"bell"],[84,.48,.45,"bell"]],
    level:[[76,0,.18,"bell"],[79,.14,.18,"bell"],[83,.28,.18,"bell"],[88,.42,.45,"bell"]]};
  const category=kind=>({message:"messages",reminder:"reminders",stage:"achievements",win:"achievements",achievement:"achievements",level:"achievements"}[kind]);
  function renderCue(kind,style="vibrant",sampleRate=44100){
    const notes=(style==="soft"?softScores:scores)[kind] || scores.message;
    const duration=Math.max(...notes.map(n=>n[1]+n[2]))+.06,samples=new Float32Array(Math.ceil(duration*sampleRate));
    for(const [midi,start,length,voice,strength=1] of notes){
      const frequency=440*2**((midi-69)/12),first=Math.round(start*sampleRate),count=Math.ceil(length*sampleRate);
      for(let i=0;i<count && first+i<samples.length;i++){
        const t=i/sampleRate,phase=2*Math.PI*frequency*t;
        const attack=Math.min(1,t/.007),release=Math.min(1,(length-t)/.055),decay=Math.exp(-t/(voice==="bass"?.22:.29));
        let wave;
        if(voice==="chip")wave=(Math.sin(phase)+.24*Math.sin(phase*3)+.08*Math.sin(phase*5))*.76;
        else if(voice==="bell")wave=Math.sin(phase)+.32*Math.sin(phase*2)*Math.exp(-t*12)+.12*Math.sin(phase*4)*Math.exp(-t*18);
        else if(voice==="pop")wave=Math.sin(phase+5*(1-Math.exp(-t*60)))+.15*Math.sin(phase*2);
        else wave=Math.sin(phase)+.16*Math.sin(phase*2);
        samples[first+i]+=wave*attack*Math.max(0,release)*decay*strength;
      }
    }
    let peak=0;for(let i=0;i<samples.length;i++){samples[i]=Math.tanh(samples[i]*.8);peak=Math.max(peak,Math.abs(samples[i]));}
    const headroom=(style==="soft"?.56:.84)/(peak || 1);for(let i=0;i<samples.length;i++)samples[i]*=headroom;
    return {samples,sampleRate,duration};
  }
  function preferences(value={}) {
    if(!value || typeof value!=="object")value={};
    const result={...defaults};
    for(const key of ["enabled","messages","reminders","achievements"])if(typeof value[key]==="boolean")result[key]=value[key];
    if(Number.isFinite(value.volume))result.volume=Math.max(0,Math.min(100,value.volume));
    if(["vibrant","soft"].includes(value.style))result.style=value.style;
    return result;
  }
  function create({getUser}) {
    let context=null,settings=preferences(),user="",lastPlayed=0,active=null;
    const buffers=new Map();
    const key=()=>`doc4docs.crm.sounds.${user}`;
    function reset(){user=String(getUser() || "");try{settings=preferences(JSON.parse(localStorage.getItem(key()) || "{}"));}catch{settings=preferences();}}
    function save(value){settings=preferences({...settings,...value});try{localStorage.setItem(key(),JSON.stringify(settings));}catch{}if(active){if(!settings.enabled || !settings[category(active.kind)]){try{active.source.stop();}catch{}}else active.gain.gain.setValueAtTime(settings.volume/100,context.currentTime);}return {...settings};}
    async function unlock(){
      const Audio=root.AudioContext || root.webkitAudioContext;
      if(!Audio)return false;
      try{context ||= new Audio();await context.resume();return context.state==="running";}catch{return false;}
    }
    async function play(kind,{test=false}={}) {
      if(!category(kind))return false;
      const celebration=["win","level","achievement","stage"].includes(kind);
      if(!test && (!settings.enabled || !settings[category(kind)] || !celebration && Date.now()-lastPlayed<1800))return false;
      if(!settings.volume)return false;
      if(!context || context.state!=="running")return false;
      lastPlayed=Date.now();
      const bufferKey=`${settings.style}:${kind}:${context.sampleRate}`;
      if(!buffers.has(bufferKey)){
        const cue=renderCue(kind,settings.style,context.sampleRate),buffer=context.createBuffer(1,cue.samples.length,cue.sampleRate);
        buffer.getChannelData(0).set(cue.samples);buffers.set(bufferKey,buffer);
      }
      if(active && (test || celebration)){try{active.source.stop();}catch{}}
      const source=context.createBufferSource(),gain=context.createGain();source.buffer=buffers.get(bufferKey);
      gain.gain.setValueAtTime(settings.volume/100,context.currentTime);source.connect(gain);gain.connect(context.destination);
      active={source,gain,kind};source.onended=()=>{source.disconnect();gain.disconnect();if(active?.source===source)active=null;};source.start();
      return true;
    }
    async function once(id,kind,{relatedIds=[]}={}){
      const run=async()=>{
        const storageKey=`${key()}.events`;
        let events=[];try{events=JSON.parse(localStorage.getItem(storageKey) || "[]");if(!Array.isArray(events))events=[];}catch{}
        if(events.includes(id))return false;
        const played=await play(kind);
        if(played)try{localStorage.setItem(storageKey,JSON.stringify([...new Set([...events,id,...relatedIds])].slice(-150)));}catch{}
        return played;
      };
      return root.navigator?.locks?root.navigator.locks.request(`crm-sound-${user}`,run):run();
    }
    function gesture(){if(settings.enabled)unlock();}
    root.document?.addEventListener("pointerdown",gesture,{passive:true});
    root.document?.addEventListener("keydown",gesture);
    root.addEventListener?.("storage",event=>{if(event.key===key())reset();});
    return {reset,save,unlock,play,once,settings:()=>({...settings}),ready:()=>context?.state==="running"};
  }
  root.CrmSounds={preferences,create,renderCue};
  if(typeof module!=="undefined")module.exports={preferences,create,renderCue};
})(typeof window!=="undefined"?window:globalThis);
