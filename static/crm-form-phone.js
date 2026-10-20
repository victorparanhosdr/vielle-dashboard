"use strict";
window.CrmFormPhone={
  markup(label,required,esc){
    return `<div class="flowPhone"><div class="flowCountry"><span class="flowCountryFlag" aria-hidden="true"></span><span class="flowDialCode" aria-hidden="true">+55</span><i data-lucide="chevron-down" aria-hidden="true"></i><select aria-label="País do telefone" autocomplete="country"></select></div><input class="flowInput" name="answer" type="tel" inputmode="tel" autocomplete="tel-national" maxlength="32" aria-label="${esc(label)}" placeholder="(00) 00000-0000" ${required?'required':''}></div>`;
  },
  bind(host,value){
    const lib=window.libphonenumber,input=host.querySelector('[name=answer]'),select=host.querySelector('select');
    if(!lib)return {read(){throw Error('Não foi possível carregar a validação do telefone. Atualize a página.');}};
    const names=new Intl.DisplayNames(['pt-BR'],{type:'region'});
    const flag=code=>String.fromCodePoint(...[...code].map(c=>127397+c.charCodeAt(0)));
    const countries=lib.getCountries().sort((a,b)=>a==='BR'?-1:b==='BR'?1:names.of(a).localeCompare(names.of(b),'pt-BR'));
    for(const code of countries){const option=document.createElement('option');option.value=code;option.textContent=`${flag(code)} ${names.of(code)} (+${lib.getCountryCallingCode(code)})`;select.append(option);}
    select.value='BR';
    const parsed=lib.parsePhoneNumberFromString(String(value || ''));
    if(parsed?.country && countries.includes(parsed.country))select.value=parsed.country;
    function country(){
      host.querySelector('.flowCountryFlag').textContent=flag(select.value);
      host.querySelector('.flowDialCode').textContent='+'+lib.getCountryCallingCode(select.value);
      host.querySelector('.flowCountry').title=names.of(select.value);
      input.placeholder=select.value==='BR'?'(00) 00000-0000':select.value==='US'?'(000) 000-0000':'Número com código de área';
    }
    function format(){
      input.setCustomValidity('');
      const original=input.value,caret=input.selectionStart ?? original.length,atEnd=caret===original.length;
      let digits=original.replace(/\D/g,'');
      let international=original.trim().startsWith('+') || select.value==='BR' && digits.startsWith('55') && [12,13].includes(digits.length);
      if(international){
        const number=lib.parsePhoneNumberFromString('+'+digits);
        if(number?.country && countries.includes(number.country)){
          select.value=number.country;digits=number.nationalNumber;international=false;country();
        }
      }
      input.value=new lib.AsYouType(select.value).input(international?'+'+digits:digits);
      const count=original.slice(0,caret).replace(/\D/g,'').length;
      let position=input.value.length;
      if(!atEnd){let seen=0;for(let i=0;i<input.value.length;i++)if(/\d/.test(input.value[i]) && ++seen===count){position=i+1;break;}}
      input.setSelectionRange(position,position);
    }
    input.value=parsed?.nationalNumber || String(value || '');country();format();
    input.addEventListener('input',format);
    select.addEventListener('change',()=>{country();format();input.focus();});
    return {read(){
      if(!input.value.trim())return '';
      const number=lib.parsePhoneNumberFromString(input.value,{defaultCountry:select.value,extract:false});
      if(!number?.isValid() || number.ext){input.setCustomValidity('Confira o país, o DDD e o telefone informado.');input.reportValidity();throw Error('Confira o país, o DDD e o telefone informado.');}
      return number.number;
    }};
  }
};
