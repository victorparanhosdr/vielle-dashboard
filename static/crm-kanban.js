(() => {
  "use strict";
  window.createCrmKanban=({api,esc,icon,icons,leadCard,onLeads,onRefresh})=>{
    let active=null;
    function footer(column) {
      return `<div class="columnFooter"><small data-column-count>${column.loaded} de ${column.total}</small><p data-column-status role="status" hidden></p><button type="button" class="columnLoadMore" ${column.next_cursor?"":"hidden"}>${icon("chevron-down")} Carregar mais</button></div>`;
    }
    function destroy() {
      if(!active)return;
      active.valid=false;active.columns.forEach(c=>c.observer?.disconnect());active=null;
    }
    function mount(board,{doctor,pipeline,filters,columns}) {
      destroy();
      const session={valid:true,columns:[],board};active=session;
      for(const meta of columns){
        const column=board.querySelector(`[data-stage="${CSS.escape(meta.stage_id)}"]`);
        if(!column)continue;
        const lane=column.querySelector(".columnLeads"),cards=lane.querySelector(".columnCards");
        const button=lane.querySelector(".columnLoadMore"),status=lane.querySelector("[data-column-status]"),count=lane.querySelector("[data-column-count]");
        const entry={lane,busy:false,loaded:meta.loaded};session.columns.push(entry);
        let cursor=meta.next_cursor,conflict=false;
        const known=new Set([...cards.querySelectorAll("[data-lead]")].map(n=>n.dataset.lead));
        async function more() {
          if(!session.valid || !cursor || entry.busy)return;
          if(conflict){onRefresh();return;}
          entry.busy=true;button.disabled=true;lane.setAttribute("aria-busy","true");status.hidden=true;
          button.textContent="Carregando…";
          try {
            const data=await api("kanban-page",undefined,{doctor,pipeline,stage:meta.stage_id,filters:JSON.stringify(filters),cursor});
            if(!session.valid || !lane.isConnected)return;
            if(data.pipeline_id!==pipeline || data.stage_id!==meta.stage_id)throw new Error("Não foi possível carregar esta fase.");
            const rows=data.leads.filter(l=>!known.has(l.id));
            onLeads(rows);cards.insertAdjacentHTML("beforeend",rows.map(leadCard).join(""));rows.forEach(l=>known.add(l.id));
            cursor=data.next_cursor;entry.loaded=data.loaded;
            count.textContent=`${data.loaded} de ${data.total}`;button.hidden=!cursor;
            if(!cursor)entry.observer?.disconnect();
            icons();
          }catch(error){
            if(!session.valid || !lane.isConnected)return;
            conflict=error.status===409;status.textContent=error.message;status.hidden=false;
            entry.observer?.disconnect();
          }finally{
            entry.busy=false;
            if(session.valid && lane.isConnected){
              button.disabled=false;button.textContent=conflict?"Atualizar funil":status.hidden?"Carregar mais":"Tentar novamente";
              lane.setAttribute("aria-busy","false");
            }
          }
        }
        button.onclick=more;
        if(cursor && typeof IntersectionObserver!=="undefined"){
          entry.observer=new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting))more();},{root:null,rootMargin:"0px 0px 200px 0px"});
          entry.observer.observe(button);
        }
      }
    }
    return {footer,mount,destroy,interacting:()=>Boolean(active && (active.board.getBoundingClientRect().top<0 || active.columns.some(c=>c.busy || c.loaded>20)))};
  };
})();
