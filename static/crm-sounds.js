(function (root) {
  "use strict";
  const defaults={enabled:false,messages:true,reminders:true,achievements:true,volume:35};
  function preferences(value={}) {
    const result={...defaults};
    for(const key of ["enabled","messages","reminders","achievements"])if(typeof value[key]==="boolean")result[key]=value[key];
    if(Number.isFinite(value.volume))result.volume=Math.max(0,Math.min(100,value.volume));
    return result;
  }
  function create({getUser}) {
    let context=null,settings=preferences(),user="",lastPlayed=0;
    const key=()=>`doc4docs.crm.sounds.${user}`;
    function reset(){user=String(getUser() || "");try{settings=preferences(JSON.parse(localStorage.getItem(key()) || "{}"));}catch{settings=preferences();}}
    function save(value){settings=preferences({...settings,...value});try{localStorage.setItem(key(),JSON.stringify(settings));}catch{}return {...settings};}
    async function unlock(){
      const Audio=root.AudioContext || root.webkitAudioContext;
      if(!Audio)return false;
      try{context ||= new Audio();await context.resume();return context.state==="running";}catch{return false;}
    }
    async function play(kind,{test=false}={}) {
      const category={message:"messages",reminder:"reminders",win:"achievements",achievement:"achievements",level:"achievements"}[kind];
      if(!test && (!settings.enabled || !settings[category] || Date.now()-lastPlayed<1800))return false;
      if(!settings.volume)return false;
      if(!context || context.state!=="running")return false;
      lastPlayed=Date.now();
      const notes={message:[[660,0,.10],[880,.12,.14]],reminder:[[587,0,.13],[784,.17,.18]],achievement:[[523,0,.10],[659,.11,.10],[784,.22,.18]],win:[[523,0,.13],[659,.14,.13],[784,.28,.13],[1046,.43,.36]],level:[[659,0,.13],[784,.14,.13],[988,.28,.13],[1318,.43,.36]]}[kind] || [[660,0,.15]];
      const volume=settings.volume/100*.12;
      for(const [frequency,offset,duration] of notes){
        const oscillator=context.createOscillator(),gain=context.createGain(),at=context.currentTime+offset;
        oscillator.type="sine";oscillator.frequency.setValueAtTime(frequency,at);
        gain.gain.setValueAtTime(0,at);gain.gain.linearRampToValueAtTime(volume,at+.012);gain.gain.exponentialRampToValueAtTime(.0001,at+duration);
        oscillator.connect(gain);gain.connect(context.destination);oscillator.start(at);oscillator.stop(at+duration+.025);
        oscillator.onended=()=>{oscillator.disconnect();gain.disconnect();};
      }
      return true;
    }
    async function once(id,kind){
      const run=async()=>{
        const storageKey=`${key()}.events`;
        let events=[];try{events=JSON.parse(localStorage.getItem(storageKey) || "[]");if(!Array.isArray(events))events=[];}catch{}
        if(events.includes(id))return false;
        const played=await play(kind);
        if(played)try{localStorage.setItem(storageKey,JSON.stringify([...events.slice(-100),id]));}catch{}
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
  root.CrmSounds={preferences,create};
  if(typeof module!=="undefined")module.exports={preferences,create};
})(typeof window!=="undefined"?window:globalThis);
