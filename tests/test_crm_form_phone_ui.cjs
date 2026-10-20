const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const listeners={},input={value:'',selectionStart:0,setCustomValidity(v){this.error=v;},setSelectionRange(a){this.selectionStart=a;},addEventListener(name,fn){listeners[name]=fn;},focus(){},reportValidity(){return !this.error;}};
const select={value:'',options:[],append(v){this.options.push(v);},addEventListener(name,fn){listeners['country-'+name]=fn;}};
const nodes={select,'[name=answer]':input,'.flowCountryFlag':{},'.flowDialCode':{},'.flowCountry':{}};
const context=vm.createContext({window:{},document:{createElement:()=>({})},Intl,String,Error});
vm.runInContext(fs.readFileSync('static/vendor/libphonenumber-max-1.13.15.js','utf8'),context);
context.window.libphonenumber=context.libphonenumber;
vm.runInContext(fs.readFileSync('static/crm-form-phone.js','utf8'),context);
const control=context.window.CrmFormPhone.bind({querySelector:selector=>nodes[selector]},'');
assert.equal(select.value,'BR');assert.ok(select.options.length>200);
function type(value){input.value=value;input.selectionStart=value.length;listeners.input();}
type('11999990000');assert.equal(input.value,'(11) 99999-0000');assert.equal(control.read(),'+5511999990000');
type('+5511999990000');assert.equal(input.value,'(11) 99999-0000');assert.equal(control.read(),'+5511999990000');
type('+12025550123');assert.equal(select.value,'US');assert.equal(control.read(),'+12025550123');
type('123');assert.throws(()=>control.read(),/Confira/);
select.value='BR';type('1133334444');assert.equal(control.read(),'+551133334444');
type('');assert.equal(control.read(),'');
console.log('Form phone: country selector, live mask, paste, E.164 and validation passed');
