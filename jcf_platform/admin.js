/* OIDC Authorization Code + PKCE, in-memory access token; all writes require separate approval. */
'use strict';
const $=s=>document.querySelector(s); let token=null, profile=null, config=null, current='locations';
const views={locations:['Locations','Approved organizational formations and searchable aliases'],
 sections:['Sections / Units','Reusable standardized Section/Unit names'],
 mappings:['Location–Section Mapping','Assign Section/Unit instances to their organizational Location'],
 reporting:['Reporting Relationships','Local and functional authorities can coexist'],
 facilities:['Buildings & Facilities','Separate physical facilities from organizational placement'],
 contacts:['Contact Directory','Entity-scoped contact numbers and extensions'],
 personnel:['Personnel & Postings','Only official verified assignments become HRIS postings'],
 approval:['Change Approvals','Four-eyes validation and independent decision'],
 import:['Bulk Excel / CSV Import','Preview spreadsheet records; changes still require approval']};
const actions={locations:['LOCATION_CREATE','LOCATION_RENAME','LOCATION_RETIRE','LOCATION_PARENT','LOCATION_PARENT_CLOSE','LOCATION_ALIAS'],
 sections:['SECTION_DEFINE','SECTION_RENAME','SECTION_ALIAS','SECTION_RETIRE'],mappings:['SECTION_ATTACH'],reporting:['REPORTING_EDGE'],
 facilities:['BUILDING_CREATE','LOCATION_BUILDING','SECTION_BUILDING'],contacts:['CONTACT_ASSIGN'],
 personnel:['PERSONNEL_CREATE','POSTING_CREATE','LEAVE_REQUEST'],approval:[],import:['BATCH_REGISTER']};
const schema={
 LOCATION_CREATE:[['code','text'],['name','text'],['locationType','text','DIVISION'],['validFrom','date'],['validTo','date'],['visibility','select','STANDARD|RESTRICTED'],['selectable','checkbox']],
 SECTION_DEFINE:[['code','text'],['name','text'],['validFrom','date'],['validTo','date']],
 SECTION_ATTACH:[['locationId','uuid'],['sectionDefinitionId','uuid'],['validFrom','date'],['validTo','date'],['visibility','select','STANDARD|RESTRICTED'],['isDefault','checkbox']],
 LOCATION_RENAME:[['locationId','uuid'],['name','text'],['effectiveFrom','date']],
 LOCATION_RETIRE:[['locationId','uuid'],['effectiveFrom','date']],
 SECTION_RETIRE:[['sectionId','uuid'],['effectiveFrom','date']],
 SECTION_RENAME:[['sectionDefinitionId','uuid'],['name','text'],['effectiveFrom','date']],
 LOCATION_PARENT_CLOSE:[['relationshipId','uuid'],['effectiveFrom','date']],
 LOCATION_PARENT:[['childId','uuid'],['parentId','uuid'],['validFrom','date'],['validTo','date']],
 LOCATION_ALIAS:[['locationId','uuid'],['alias','text']], SECTION_ALIAS:[['definitionId','uuid'],['alias','text']],
 REPORTING_EDGE:[['sourceId','uuid'],['authorityId','uuid'],['relationType','select','LOCAL|FUNCTIONAL|OPERATIONAL|ADMINISTRATIVE'],['scope','text'],['validFrom','date'],['validTo','date']],
 BUILDING_CREATE:[['code','text'],['name','text'],['address','text'],['parish','text'],['latitude','number'],['longitude','number'],['validFrom','date'],['validTo','date']],
 LOCATION_BUILDING:[['locationId','uuid'],['buildingId','uuid'],['occupancyType','select','PERMANENT|TEMPORARY|SHARED'],['validFrom','date'],['validTo','date']],
 SECTION_BUILDING:[['sectionId','uuid'],['locationBuildingId','uuid'],['assignmentType','select','PRIMARY|SECONDARY|TEMPORARY'],['validFrom','date'],['validTo','date']],
 CONTACT_ASSIGN:[['entityKind','select','LOCATION|LOCATION_SECTION|BUILDING|PERSONNEL'],['entityId','uuid'],['kind','select','PHONE|MOBILE|EXTENSION|EMAIL|FAX'],['value','text'],['telephoneSystem','text'],['visibility','select','INTERNAL|PUBLIC|PERSONAL'],['purpose','text'],['validFrom','date'],['validTo','date']],
 PERSONNEL_CREATE:[['computerNumber','text'],['displayName','text']],
 POSTING_CREATE:[['personnelId','uuid'],['locationId','uuid'],['sectionId','uuid'],['validFrom','date'],['validTo','date'],['isPrimary','checkbox']],
 LEAVE_REQUEST:[['personnelId','uuid'],['category','text'],['startsOn','date'],['endsBefore','date']],
 BATCH_REGISTER:[['operations','json']]};
function text(tag,value){let n=document.createElement(tag);n.textContent=String(value??'');return n}
function button(label,fn){let b=text('button',label);b.type='button';b.addEventListener('click',fn);return b}
function alertMsg(msg){$('#alert').textContent=msg}
const rand=()=>Array.from(crypto.getRandomValues(new Uint8Array(32)),b=>b.toString(16).padStart(2,'0')).join('');
function b64url(buffer){return btoa(String.fromCharCode(...new Uint8Array(buffer))).replaceAll('+','-').replaceAll('/','_').replaceAll('=','')}
async function signIn(){config=await (await fetch('/api/v1/admin/oidc-config')).json();
 if(!config.clientId||!config.authorizationEndpoint||!config.tokenEndpoint){alertMsg('OIDC client configuration is required on the server.');return}
 const verifier=b64url(crypto.getRandomValues(new Uint8Array(32)));const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(verifier));
 const state=rand();sessionStorage.setItem('jcf_pkce',JSON.stringify({verifier,state,at:Date.now()}));
 const url=new URL(config.authorizationEndpoint);url.search=new URLSearchParams({response_type:'code',client_id:config.clientId,
  redirect_uri:config.redirectUri||location.origin+'/admin',scope:config.scope,state,code_challenge:b64url(digest),code_challenge_method:'S256'});
 location.assign(url.toString());}
async function authCallback(){const p=new URLSearchParams(location.search);if(!p.has('code'))return;
 config=await (await fetch('/api/v1/admin/oidc-config')).json();let transaction=JSON.parse(sessionStorage.getItem('jcf_pkce')||'null');sessionStorage.removeItem('jcf_pkce');
 history.replaceState({},'',location.pathname);
 if(!transaction||transaction.state!==p.get('state')||Date.now()-transaction.at>300000){alertMsg('OIDC state expired or mismatched.');return}
 const body=new URLSearchParams({grant_type:'authorization_code',code:p.get('code'),client_id:config.clientId,
   redirect_uri:config.redirectUri||location.origin+'/admin',code_verifier:transaction.verifier});
 let res=await fetch(config.tokenEndpoint,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body});
 if(!res.ok){alertMsg('OIDC exchange failed. Check provider CORS and SPA client configuration.');return}
 token=(await res.json()).access_token; if(!token){alertMsg('No access token received.');return}
 try{profile=JSON.parse(atob(token.split('.')[1].replaceAll('-','+').replaceAll('_','/')))}catch{profile={sub:'Authenticated'}}
 $('#identity').textContent=profile.name||profile.preferred_username||profile.sub;
 $('#login').hidden=true;$('#logout').hidden=false;loadView();}
async function api(path,opts={}){if(!token)throw Error('Sign in required');
 const response=await fetch(path,{...opts,headers:{'Authorization':'Bearer '+token,'Content-Type':'application/json',...(opts.headers||{})}});
 let result=await response.json();if(!response.ok)throw Error(result.code+': '+result.detail);return result;}
function makeTable(items,pane=$('#results')){pane.replaceChildren();if(!items||!items.length){pane.append(text('p','No matching records.'));return}
 let table=document.createElement('table'),thead=document.createElement('thead'),tr=document.createElement('tr');
 const columns=Object.keys(items[0]);for(const col of columns)tr.append(text('th',col));thead.append(tr);table.append(thead);
 let body=document.createElement('tbody');for(const item of items){let row=document.createElement('tr');for(const col of columns){let td=text('td',typeof item[col]==='object'?JSON.stringify(item[col]):item[col]);row.append(td)}body.append(row)}table.append(body);pane.append(table);}
const lookupRoutes={locationId:'/api/v1/registry/locations',childId:'/api/v1/registry/locations',
 parentId:'/api/v1/registry/locations',sectionDefinitionId:'/api/v1/registry/section-definitions',
 definitionId:'/api/v1/registry/section-definitions',sourceId:'/api/v1/registry/org-nodes',
 authorityId:'/api/v1/registry/org-nodes',sectionId:'/api/v1/registry/org-nodes',
 buildingId:'/api/v1/facilities/buildings',locationBuildingId:'/api/v1/facilities/location-building-links'};
function addLookup(label,name){
 const route=lookupRoutes[name];if(!route)return null;
 const search=document.createElement('input');search.type='search';search.placeholder='Search '+name+' by name or code';
 const sel=document.createElement('select');sel.id='f_'+name;sel.required=true;
 let clock;const load=async()=>{if(!token)return;try{
   let url=route+(route.includes('?')?'&':'?')+'search='+encodeURIComponent(search.value)+'&limit=200';
   if(name==='sectionId') {const selected=$('#f_locationId')?.value;
     if(!selected){sel.replaceChildren(new Option('Select Location first',''));return}
     url='/api/v1/registry/locations/'+selected+'/sections';}
   let result=await api(url);const prev=sel.value;sel.replaceChildren(new Option('Select '+name,'',true,true));
   for(const x of result.items){const id=x.locationId||x.sectionId||x.sectionDefinitionId||x.orgNodeId||x.buildingId||x.locationBuildingId;
    const nameText=x.name||x.label||x.code||x.locationBuildingId;
    sel.append(new Option(nameText+' ('+(x.code||x.kind||id.slice(0,8))+')',id));}
   if([...sel.options].some(x=>x.value===prev))sel.value=prev;
 }catch(e){alertMsg(e.message)}};
 search.oninput=()=>{clearTimeout(clock);clock=setTimeout(load,250)};
 sel.reload=load;label.append(search,sel);load();return sel;
}
function fieldForm(action){const container=$('#fields');container.replaceChildren();let grid=document.createElement('div');grid.className='fieldgrid';
 for(const [name,type,options] of schema[action]||[]){let label=text('label',name);label.htmlFor='f_'+name;let inp;
 if(type==='select'){inp=document.createElement('select');for(const opt of options.split('|')){let el=document.createElement('option');el.value=opt;el.textContent=opt;inp.append(el)}}
 else if(type==='json'){inp=document.createElement('textarea');inp.rows=5;inp.placeholder='[{"action":"LOCATION_CREATE","payload":{...}}]'}
 else if(type==='uuid'&&lookupRoutes[name]){inp=addLookup(label,name)}
 else{inp=document.createElement('input');inp.type=type==='uuid'?'text':type;inp.placeholder=type==='uuid'?'UUID':name;
  if(type==='date'&&name==='validFrom')inp.value=new Date().toISOString().slice(0,10);
  if(type==='checkbox')inp.checked=name==='isPrimary';if(type==='number')inp.step='any';}
 if(inp)inp.id='f_'+name;if(inp && !label.contains(inp))label.append(inp);grid.append(label)}container.append(grid);
 const locationField=container.querySelector('#f_locationId'),sectionField=container.querySelector('#f_sectionId');
 if(locationField&&sectionField)locationField.addEventListener('change',()=>{sectionField.value='';sectionField.reload?.()});}
function setupForm(){let sel=$('#action');sel.replaceChildren();for(const action of actions[current]||[]){let opt=document.createElement('option');opt.value=action;opt.textContent=action.replaceAll('_',' ');sel.append(opt)}
 sel.onchange=()=>fieldForm(sel.value);fieldForm(sel.value);$('#changeForm').parentElement.hidden=actions[current].length===0;}
async function loadView(){let [title,desc]=views[current];$('#view-title').textContent=title;$('#view-description').textContent=desc;setupForm();
 if(!token)return;alertMsg('');try{
  if(current==='locations')makeTable((await api('/api/v1/registry/locations?limit=200')).items);
  else if(current==='approval'){
   let pane=$('#results');pane.replaceChildren();let rows=(await api('/api/v1/governance/requests')).items;
   for(const item of rows){let wrap=document.createElement('article');wrap.className='pane';wrap.append(text('strong',item.action+' — '+item.status));
    wrap.append(text('p',item.reference+' | Requested by '+item.requestedBy));wrap.append(text('code',JSON.stringify(item.payload)));
    if(item.status==='SUBMITTED'){wrap.append(button('Approve',()=>decide(item.id,'APPROVED')));wrap.append(button('Reject',()=>decide(item.id,'REJECTED')))}
    if(item.status==='APPROVED')wrap.append(button('Apply approved change',()=>apply(item.id)));pane.append(wrap)}
  } else if(current==='mappings'){
   const locs=(await api('/api/v1/registry/locations?limit=200')).items;let pane=$('#results');pane.replaceChildren();
   const select=document.createElement('select');select.append(new Option('Select a Location',''));
   for(const l of locs)select.append(new Option(l.name+' ('+l.code+')',l.locationId));pane.append(select);
   select.onchange=async()=>{if(!select.value)return;const r=await api('/api/v1/registry/locations/'+select.value+'/sections');
    [...pane.querySelectorAll('table,p')].forEach(x=>x.remove());let div=document.createElement('div');pane.append(div);makeTable(r.items,div)};
  } else if(current==='import'){
   const pane=$('#results');pane.replaceChildren();let file=document.createElement('input');file.type='file';file.accept='.csv,.xlsx';pane.append(file);
   pane.append(button('Preview import',async()=>{if(!file.files[0])return;let form=new FormData();form.append('file',file.files[0]);
    let r=await fetch('/api/v1/import/preview',{method:'POST',headers:{Authorization:'Bearer '+token},body:form});let data=await r.json();
    if(!r.ok){alertMsg(data.detail);return}let inp=$('#f_operations');if(inp)inp.value=JSON.stringify(data.operations,null,2);
    alertMsg(data.operations.length+' valid operations and '+data.errors.length+' validation errors; review before submitting for approval.');}));
  } else $('#results').replaceChildren(text('p','Use the change form to submit '+title.toLowerCase()+' changes for independent approval.'));
 }catch(e){alertMsg(e.message)}}
async function decide(id,decision){try{await api('/api/v1/governance/requests/'+id+'/decision',{method:'POST',body:JSON.stringify({decision})});loadView()}catch(e){alertMsg(e.message)}}
async function apply(id){try{await api('/api/v1/governance/requests/'+id+'/apply',{method:'POST'});loadView()}catch(e){alertMsg(e.message)}}
$('#changeForm').onsubmit=async e=>{e.preventDefault();try{const action=$('#action').value;let payload={};
 for(const [name,type] of schema[action]||[]){let el=$('#f_'+name);if(type==='checkbox')payload[name]=el.checked;
  else if(type==='json')payload[name]=JSON.parse(el.value);
  else if(el.value!=='')payload[name]=type==='number'?Number(el.value):el.value;}
 let r=await api('/api/v1/governance/requests',{method:'POST',body:JSON.stringify({action,payload,reference:$('#reference').value})});
 alertMsg('Submitted for independent approval: '+r.changeRequestId);$('#reference').value='';}catch(err){alertMsg(err.message)}};
for(const btn of document.querySelectorAll('nav [data-view]'))btn.onclick=()=>{current=btn.dataset.view;
 document.querySelectorAll('nav [data-view]').forEach(x=>x.classList.toggle('active',x===btn));loadView()};
$('#refresh').onclick=loadView;$('#login').onclick=signIn;$('#logout').onclick=()=>{token=null;profile=null;$('#identity').textContent='Not signed in';
 $('#login').hidden=false;$('#logout').hidden=true;loadView()};
(async()=>{try{await authCallback()}catch(e){alertMsg(e.message)}setupForm()})();
