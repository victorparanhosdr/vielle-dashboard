"use strict";
window.createCrmContactPhone=({api,esc,icon,icons,modal,error})=>({
  async open(lead,{remove=false,active=()=>true,onDone=()=>{}}={}){
    modal(remove?"Remover telefone":"Editar telefone",'<p class="connectionNote">Carregando a ficha…</p>');
    const loading=document.getElementById("modalBody").firstElementChild;
    try{
      const data=await api("lead",undefined,{doctor:lead.doctor_id,id:lead.id});
      if(!active() || !loading.isConnected || !document.getElementById("crmDialog").open)return;
      lead=data.lead;
      if(!lead.can_edit_phone){error("Contato compartilhado com funis fora do seu acesso. Solicite a alteração ao Master.");return;}
      modal(remove?"Remover telefone da ficha":"Editar telefone do contato",`<form id="contactPhoneForm" class="contactPhoneForm"><p class="connectionNote">${esc(lead.name)}</p>${remove?'<p>O lead e o histórico serão preservados. Novas mensagens e ligações ficarão bloqueadas até cadastrar outro telefone. O número anterior permanece apenas nos registros históricos e de auditoria.</p>':`<label>WhatsApp / telefone</label><div id="contactPhoneInput">${window.CrmFormPhone.markup("Novo telefone do contato",true,esc)}</div><p class="connectionNote">A alteração vale para este contato nos seus funis. Mensagens antigas manterão o destino original. Envios pendentes e robôs serão interrompidos.</p>`}<label class="check"><input name="confirm" type="checkbox" required>${remove?"Confirmo a remoção do telefone, sem excluir o lead.":"Conferi o país, o DDD e o número do destinatário."}</label><div class="formActions"><button type="button" data-phone-cancel>Cancelar</button><button type="submit" class="primary">${icon(remove?"phone-off":"check")}${remove?"Remover telefone":"Salvar telefone"}</button></div></form>`);
      const form=document.getElementById("contactPhoneForm"),field=remove?null:window.CrmFormPhone.bind(document.getElementById("contactPhoneInput"),lead.phone?"+"+lead.phone:"");
      form.querySelector('[data-phone-cancel]').onclick=()=>document.getElementById("crmDialog").close();
      form.onsubmit=async e=>{
        e.preventDefault();if(!active())return;
        const button=e.currentTarget.querySelector('[type=submit]');if(button.disabled)return;
        try{
          const phone=remove?"":field.read();if(!remove && !phone)throw Error("Informe o telefone completo.");
          button.disabled=true;
          const result=await api("phone-update",{id:lead.id,doctor_id:lead.doctor_id,phone,remove,phone_revision:lead.phone_revision,confirm:form.elements.confirm.checked});
          if(active() && form.isConnected && document.getElementById("crmDialog").open){document.getElementById("crmDialog").close();await onDone(result.lead);}
        }catch(err){if(form.isConnected)error(err.message);}
        finally{if(button.isConnected)button.disabled=false;}
      };icons();
    }catch(err){if(active() && loading.isConnected)error(err.message);}
  }
});
