/* OIDC Authorization Code + PKCE, in-memory access token; all writes require separate approval. */
'use strict';
const $=s=>document.querySelector(s); let token=null, profile=null, config=null, current='overview';
const views={overview:['Registry Overview','Explore verified Locations, Sections, Buildings and reporting links'],
 locations:['Location Hierarchy','Explore parent–child formations; select a Location to view Sections/Units, Buildings and reporting lines'],
 sections:['Sections / Units','Standard definitions, with their current Location assignments'],
 mappings:['Location–Section Matrix','View where each Section/Unit is actually assigned'],
 reporting:['Reporting Lines','View local, functional and operational reporting links'],
 facilities:['Buildings & Locations','View Buildings and the organizational Locations occupying each facility'],
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
 if(config.localTestEnabled){$('#local-login-form').hidden=false;$('#local-username').focus();return}
 if(!config.clientId||!config.authorizationEndpoint||!config.tokenEndpoint){alertMsg('OIDC client configuration is required on the server.');return}
 const verifier=b64url(crypto.getRandomValues(new Uint8Array(32)));const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(verifier));
 const state=rand();sessionStorage.setItem('jcf_pkce',JSON.stringify({verifier,state,at:Date.now()}));
 const url=new URL(config.authorizationEndpoint);url.search=new URLSearchParams({response_type:'code',client_id:config.clientId,
  redirect_uri:config.redirectUri||location.origin+'/admin',scope:config.scope,state,code_challenge:b64url(digest),code_challenge_method:'S256'});
 location.assign(url.toString());}
async function localSignIn(event){
 event.preventDefault();alertMsg('');
 const submit=$('#local-login-form button[type="submit"]');
 submit.disabled=true;
 try{
  const username=$('#local-username').value,password=$('#local-password').value;
  const response=await fetch('/api/v1/admin/local-test-login',{
   method:'POST',headers:{'Content-Type':'application/json'},
   cache:'no-store',body:JSON.stringify({username,password})
  });
  const data=await response.json();
  if(!response.ok)throw Error(data.detail||'Test login failed');
  if(!data.access_token)throw Error('No access token received');
  token=data.access_token;
  profile={preferred_username:username,name:'Development Test Administrator'};
  previewMode=false;
  directoryCache=null;
  $('#identity').textContent='Test admin: '+username;
  $('#preview').textContent='Preview sample';
  $('#local-login-form').reset();
  $('#local-login-form').hidden=true;
  $('#login').hidden=true;
  $('#logout').hidden=false;
  await loadView();
 }catch(error){alertMsg(error.message)}
 finally{submit.disabled=false}
}
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
 previewMode=false;$('#preview').textContent='Preview sample';
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

/* Directory views are READ-ONLY; preview is synthetic in-browser data, NEVER database data. */
let previewMode=false, directoryCache=null, viewGeneration=0;
function ui(tag,className,label){
 const el=document.createElement(tag);
 if(className)el.className=className;
 if(label!==undefined)el.textContent=String(label);
 return el;
}
function sampleSnapshot(){
 const locRows=[
  ['HQ','JCF Headquarters','HQ'],['SO','Strategic Operations','PORTFOLIO'],
  ['A1','Area One (illustrative)','AREA'],['A2','Area Two (illustrative)','AREA'],
  ['TECH','Technology Branch','BRANCH'],['SAC','Saint Andrew Central','DIVISION'],
  ['SAS','Saint Andrew South','DIVISION'],['J','J Division (illustrative)','DIVISION'],
  ['STATION','Example Police Station (fictional)','STATION'],
  ['CIBHQ','CIB Headquarters (illustrative)','BRANCH'],
  ['NIBHQ','NIB Headquarters (illustrative)','BRANCH'],
  ['EXT','External Attachment (illustrative)','EXTERNAL_ATTACHMENT']];
 const defRows=[['CIB','CIB'],['DIU','DIU'],['PIU','PIU'],['GENOFF','General Office'],
  ['ARMOURY','Armoury'],['OPSOFF','Operations Office'],['ADMIN','Administrative Office'],
  ['ICT','ICT Support'],['OPS','Operations']];
 const linkRows=[['HQ','ADMIN'],['SO','OPS'],['A1','OPS'],['A2','OPS'],
  ['TECH','ICT'],['SAC','ADMIN'],['SAC','CIB'],['SAC','DIU'],
  ['SAS','ADMIN'],['SAS','CIB'],['SAS','DIU'],['J','ADMIN'],
  ['STATION','ADMIN'],['CIBHQ','CIB'],['NIBHQ','DIU'],['EXT','ADMIN']];
 const locations=locRows.map(([code,name,type])=>({locationId:'sample-loc-'+code,
  code:'DEMO-'+code,name:'[TEST] '+name,type}));
 const sections=defRows.map(([code,name])=>({sectionDefinitionId:'sample-def-'+code,
  code:'DEMO-'+code,name:'[TEST] '+name}));
 const locByCode=Object.fromEntries(locations.map(x=>[x.code.slice(5),x]));
 const defByCode=Object.fromEntries(sections.map(x=>[x.code.slice(5),x]));
 const mappings=linkRows.map(([a,b])=>({locationId:locByCode[a].locationId,
  locationCode:locByCode[a].code,locationName:locByCode[a].name,locationType:locByCode[a].type,
  sectionId:'sample-section-'+a+'-'+b,sectionDefinitionId:defByCode[b].sectionDefinitionId,
  code:defByCode[b].code,name:defByCode[b].name,isDefault:false}));
 const buildings=[
  {buildingId:'sample-building-NORTH',code:'DEMO-BLD-NORTH',name:'[TEST] Example Building North (fictional)'},
  {buildingId:'sample-building-SOUTH',code:'DEMO-BLD-SOUTH',name:'[TEST] Example Building South (fictional)'}];
 const occupancies=[['SAC','NORTH'],['SAC','SOUTH'],['SAS','SOUTH']].map(([l,b])=>{
  const loc=locByCode[l],building=buildings.find(x=>x.code==='DEMO-BLD-'+b);
  return {locationBuildingId:'sample-occupancy-'+l+'-'+b,locationId:loc.locationId,
   locationName:loc.name,locationCode:loc.code,buildingId:building.buildingId,
   buildingName:building.name,buildingCode:building.code,address:'Fictional testing address',
   parish:'Kingston',occupancyType:'SHARED'};
 });
 const reportPairs=[
  ['SAC-CIB','SAC','LOCAL'],['SAC-CIB','CIBHQ','FUNCTIONAL'],
  ['SAC-DIU','SAC','LOCAL'],['SAC-DIU','NIBHQ','FUNCTIONAL'],
  ['SAS-CIB','SAS','LOCAL'],['SAS-CIB','CIBHQ','FUNCTIONAL'],
  ['SAS-DIU','SAS','LOCAL'],['SAS-DIU','NIBHQ','FUNCTIONAL'],
  ['SAC','SO','OPERATIONAL'],['SAS','SO','OPERATIONAL']];
 const reporting=reportPairs.map(([s,t,type])=>{
  const a=s.includes('-')?mappings.find(x=>x.sectionId==='sample-section-'+s):locByCode[s];
  return {sourceOrgNodeId:a.sectionId||a.locationId,
   sourceName:a.sectionId?a.name+' — '+a.locationName:a.name,
   authorityOrgNodeId:locByCode[t].locationId,authorityName:locByCode[t].name,type,scope:''};
 });
 const parentTokens={SO:'HQ',A1:'SO',A2:'SO',TECH:'HQ',SAC:'A1',SAS:'A1',J:'A2',
  STATION:'SAC',CIBHQ:'SO',NIBHQ:'SO'};
 const hierarchy=locations.map(loc=>{
  const token=loc.code.slice(5);
  return {...loc,parentLocationId:parentTokens[token]?locByCode[parentTokens[token]].locationId:null,
   selectable:true};
 });
 return {locations,hierarchy,sections,mappings,buildings,occupancies,reporting,unavailable:[],preview:true};
}
async function loadPaged(path){
 let items=[],cursor=null;
 for(let page=0;page<50;page++){
  const url=path+(path.includes('?')?'&':'?')+'limit=200'+(cursor?'&cursor='+encodeURIComponent(cursor):'');
  const result=await api(url);
  items.push(...(result.items||[]));
  if(!result.nextCursor)return items;
  if(result.nextCursor===cursor)throw Error('Directory pagination did not advance');
  cursor=result.nextCursor;
 }
 throw Error('Directory exceeds the 10,000-item display limit. Apply a filter or contact an administrator.');
}
async function liveSnapshot(){
 const requested=[
  ['locations','/api/v1/registry/locations'],
  ['hierarchy','/api/v1/registry/location-hierarchy'],
  ['sections','/api/v1/registry/section-definitions'],
  ['mappings','/api/v1/registry/location-sections'],
  ['buildings','/api/v1/facilities/buildings'],
  ['occupancies','/api/v1/facilities/building-occupancies'],
  ['reporting','/api/v1/reporting/relationships']];
 const results=await Promise.allSettled(requested.map(x=>loadPaged(x[1])));
 if(results[0].status==='rejected')throw Error('Unable to load Locations: '+results[0].reason.message);
 const out={preview:false,unavailable:[]};
 results.forEach((r,i)=>{
  if(r.status==='fulfilled')out[requested[i][0]]=r.value;
  else{out[requested[i][0]]=[];out.unavailable.push(requested[i][0]);}
 });
 return out;
}
function sectionTitle(pane,name,detail){
 const h=ui('div','section-title');h.append(ui('h2','',name));
 if(detail)h.append(ui('p','',detail));pane.append(h);
}
function metrics(pane,entries){
 const group=ui('div','metric-grid');
 for(const [name,count,note] of entries){
  const card=ui('div','metric-card');card.append(ui('span','metric-label',name));
  card.append(ui('strong','metric-number',count));
  if(note)card.append(ui('small','metric-note',note));
  group.append(card);
 }
 pane.append(group);
}
function pill(label){return ui('span','pill',label)}
function empty(pane,message){pane.append(ui('p','empty-state',message))}
function dataTable(pane,columns,items){
 if(!items.length){empty(pane,'No records match the current filters.');return}
 const wrap=ui('div','table-scroll'),table=document.createElement('table');
 const thead=document.createElement('thead'),head=document.createElement('tr');
 for(const column of columns)head.append(ui('th','',column[0]));
 thead.append(head);table.append(thead);
 const body=document.createElement('tbody');
 for(const item of items){
  const row=document.createElement('tr');
  for(const [,getter] of columns){
   const cell=document.createElement('td'),value=getter(item);
   if(value instanceof Node)cell.append(value);else cell.textContent=String(value??'—');
   row.append(cell);
  }
  body.append(row);
 }
 table.append(body);wrap.append(table);pane.append(wrap);
}
function searchField(pane,placeholder,onChange){
 const holder=ui('div','directory-controls');
 const label=ui('label','search-box');label.append(ui('span','sr-only','Search'));
 const input=document.createElement('input');input.type='search';input.placeholder=placeholder;
 label.append(input);holder.append(label);pane.append(holder);
 input.addEventListener('input',()=>onChange(input.value));return {holder,input};
}
function selectFilter(holder,label,options,onChange){
 const select=document.createElement('select');
 select.setAttribute('aria-label',label);
 select.append(new Option('All '+label.toLowerCase(),''));
 options.forEach(x=>select.append(new Option(x,x)));
 select.addEventListener('change',()=>onChange(select.value));holder.append(select);
 return select;
}
function overviewView(pane,d){
 metrics(pane,[['Locations',d.locations.length,'Active, visible formations'],
  ['Sections / Units',d.sections.length,'Standard definitions'],
  ['Assigned Sections',d.mappings.length,'Location-specific assignments'],
  ['Buildings',d.buildings.length,'Active facilities'],
  ['Building Occupancies',d.occupancies.length,'Building ↔ Location links'],
  ['Reporting Lines',d.reporting.length,'Active authority relationships']]);
 const layout=ui('div','overview-panels');
 const first=ui('section','subpanel');sectionTitle(first,'Locations and their Sections','An assignment belongs to one specific Location.');
 const counts=d.locations.map(l=>({location:l.name,code:l.code,
  count:d.mappings.filter(s=>s.locationId===l.locationId).length})).sort((a,b)=>b.count-a.count).slice(0,8);
 dataTable(first,[['Location',x=>x.location],['Code',x=>x.code],['Assigned Sections',x=>x.count]],counts);
 const second=ui('section','subpanel');sectionTitle(second,'Facilities at a glance','Locations may occupy multiple Buildings.');
 dataTable(second,[['Building',x=>x.name],['Locations',x=>x.count]],d.buildings.map(b=>({
  name:b.name,count:d.occupancies.filter(x=>x.buildingId===b.buildingId).length})));
 layout.append(first,second);pane.append(layout);
 const note=ui('div','integration-note');
 note.append(ui('strong','','AD self-service integration contract'));
 note.append(ui('p','','The onboarding app selects a Location first, then reads only the Section/Unit instances assigned to that Location. All reads require an authorized bearer token; demo preview values are never API data.'));
 const paths=['GET /api/v1/registry/locations',
  'GET /api/v1/registry/locations/{locationId}/sections',
  'POST /api/v1/registry/posting-selections/validate'];
 paths.forEach(x=>note.append(ui('code','endpoint',x)));pane.append(note);
}
function locationView(pane,d){
 sectionTitle(pane,'Organizational Hierarchy','Expand formations to view their subordinate Locations. Select any Location to view its assigned Sections/Units, Buildings and reporting lines.');
 const hierarchy=d.hierarchy||[];
 const hasHierarchy=hierarchy.length>0;
 const nodes=hasHierarchy?hierarchy:d.locations.map(x=>({...x,parentLocationId:null}));
 const byId=new Map(nodes.map(n=>[n.locationId,n]));
 const children=new Map(nodes.map(n=>[n.locationId,[]]));
 const roots=[];
 for(const node of nodes){
  if(node.parentLocationId!==null && children.has(node.parentLocationId) && node.parentLocationId!==node.locationId)
   children.get(node.parentLocationId).push(node);
  else roots.push(node);
 }
 const rank={HQ:0,PORTFOLIO:1,AREA:2,BRANCH:3,DIVISION:4,STATION:5,EXTERNAL_ATTACHMENT:6};
 const order=(a,b)=>(rank[a.type]??50)-(rank[b.type]??50)||a.name.localeCompare(b.name);
 roots.sort(order);children.forEach(list=>list.sort(order));
 let selected=roots[0]?.locationId||'',query='',type='';
 let viewMode=hasHierarchy?'tree':'list';
 const expanded=new Set(nodes.length<=100?nodes.filter(x=>(children.get(x.locationId)||[]).length)
  .map(x=>x.locationId):roots.map(x=>x.locationId));
 const {holder}=searchField(pane,'Search Location name or code',v=>{query=v.trim().toLowerCase();draw()});
 selectFilter(holder,'Types',[...new Set(nodes.map(x=>x.type))].sort(),v=>{type=v;draw()});
 const viewActions=ui('div','view-mode-controls');
 const treeButton=button('Tree view',()=>{viewMode='tree';draw()});
 const listButton=button('Flat list',()=>{viewMode='list';draw()});
 treeButton.disabled=!hasHierarchy;
 if(!hasHierarchy)treeButton.title='Hierarchy records unavailable for this account';
 const expandButton=button('Expand all',()=>{nodes.forEach(x=>expanded.add(x.locationId));draw()});
 const collapseButton=button('Collapse all',()=>{expanded.clear();draw()});
 viewActions.append(treeButton,listButton,expandButton,collapseButton);pane.append(viewActions);
 const layout=ui('div','directory-split'),list=ui('div','directory-list'),detail=ui('div','directory-detail');
 layout.append(list,detail);pane.append(layout);
 function matches(node){
  return (!type||node.type===type)&&(!query||(node.name+' '+node.code).toLowerCase().includes(query));
 }
 function visibleBranch(node,seen=new Set()){
  if(seen.has(node.locationId))return false;
  const next=new Set(seen);next.add(node.locationId);
  return matches(node)||(children.get(node.locationId)||[]).some(child=>visibleBranch(child,next));
 }
 function draw(){
  treeButton.classList.toggle('selected',viewMode==='tree');
  listButton.classList.toggle('selected',viewMode==='list');
  expandButton.hidden=viewMode!=='tree';collapseButton.hidden=viewMode!=='tree';
  list.replaceChildren();
  const candidates=nodes.filter(matches);
  if(!candidates.length){selected='';empty(list,'No Locations match this search or type filter.')}
  else{
   if(!candidates.some(x=>x.locationId===selected))selected=candidates[0].locationId;
   if(viewMode==='tree'){
    const rootList=ui('ul','hierarchy-tree');rootList.setAttribute('aria-label','Location hierarchy');
    let rendered=new Set();
    function branch(node,depth){
     if(rendered.has(node.locationId))return null;rendered.add(node.locationId);
     const li=ui('li','hierarchy-item'),row=ui('div','hierarchy-row');
     const kids=(children.get(node.locationId)||[]).filter(x=>visibleBranch(x));
     const isOpen=expanded.has(node.locationId)||!!query||!!type;
     if(kids.length){
      const toggle=button(isOpen?'▾':'▸',()=>{if(expanded.has(node.locationId))expanded.delete(node.locationId);else expanded.add(node.locationId);draw()});
      toggle.className='hierarchy-toggle';toggle.setAttribute('aria-expanded',String(isOpen));
      toggle.setAttribute('aria-label',(isOpen?'Collapse ':'Expand ')+node.name);
      row.append(toggle);
     }else row.append(ui('span','hierarchy-toggle-spacer',''));
     const pick=button('',()=>{selected=node.locationId;draw()});
     pick.className='hierarchy-pick'+(selected===node.locationId?' selected':'');
     pick.append(ui('strong','',node.name),ui('small','',node.code+' · '+node.type));
     row.append(pick);li.append(row);
     if(kids.length&&isOpen&&depth<25){
      const sub=ui('ul','hierarchy-children');for(const child of kids){const next=branch(child,depth+1);if(next)sub.append(next)}
      li.append(sub);
     }
     return li;
    }
    for(const root of roots.filter(visibleBranch)){const item=branch(root,0);if(item)rootList.append(item)}
    // Ensure collapsed descendants are not accidentally rendered as roots.
    // Only genuinely disconnected/cyclic records are given a fallback root.
    const reachable=new Set();
    function markReachable(node){
      if(reachable.has(node.locationId))return;
      reachable.add(node.locationId);
      (children.get(node.locationId)||[]).forEach(markReachable);
    }
    roots.forEach(markReachable);
    for(const node of candidates)if(!reachable.has(node.locationId)&&!rendered.has(node.locationId)){
      const item=branch(node,0);if(item)rootList.append(item);
    }
    list.append(rootList);
   }else{
    for(const loc of candidates.sort(order)){
     const b=ui('button','directory-row'+(loc.locationId===selected?' selected':''));b.type='button';
     b.append(ui('strong','',loc.name),ui('small','',loc.code+' · '+loc.type));
     b.onclick=()=>{selected=loc.locationId;draw()};list.append(b);
    }
   }
  }
  detail.replaceChildren();
  const loc=byId.get(selected);
  if(!loc){empty(detail,'Select a Location to view details.');return}
  const title=ui('div','detail-heading');
  title.append(ui('span','eyebrow',loc.type+' · '+loc.code),ui('h2','',loc.name));
  detail.append(title);
  if(hasHierarchy){
   const path=[loc.name],seen=new Set([loc.locationId]);let parent=byId.get(loc.parentLocationId);
   while(parent&&!seen.has(parent.locationId)){
    path.unshift(parent.name);seen.add(parent.locationId);parent=byId.get(parent.parentLocationId);
   }
   detail.append(ui('p','location-breadcrumb',path.join(' / ')));
  }
  const sections=d.mappings.filter(s=>s.locationId===loc.locationId);
  sectionTitle(detail,'Assigned Sections / Units',sections.length+' location-specific instances');
  if(sections.length){const tags=ui('div','pill-list');sections.forEach(s=>tags.append(pill(s.name)));detail.append(tags)}
  else empty(detail,'No Section/Unit is assigned to this Location.');
  const buildings=d.occupancies.filter(o=>o.locationId===loc.locationId);
  sectionTitle(detail,'Buildings',buildings.length+' active occupancies');
  dataTable(detail,[['Building',x=>x.buildingName],['Occupancy',x=>x.occupancyType]],buildings);
  sectionTitle(detail,'Reporting Authorities','Local, functional and operational links');
  dataTable(detail,[['Reports to',x=>x.authorityName],['Type',x=>pill(x.type)]],
   d.reporting.filter(x=>x.sourceOrgNodeId===loc.locationId));
  detail.append(ui('div','detail-footer','Location ID: '+loc.locationId));
 }
 draw();
}
function sectionsView(pane,d){
 metrics(pane,[['Section Definitions',d.sections.length,'Reusable vocabulary'],
  ['Assigned Instances',d.mappings.length,'Active Location attachments']]);
 let q='';searchField(pane,'Find CIB, DIU, General Office, etc.',v=>{q=v.toLowerCase();draw()});
 const body=ui('div','');pane.append(body);
 function draw(){
  const items=d.sections.filter(s=>(s.name+' '+s.code).toLowerCase().includes(q))
   .map(s=>({...s,assigned:d.mappings.filter(a=>a.sectionDefinitionId===s.sectionDefinitionId)}));
  body.replaceChildren();dataTable(body,[['Section / Unit',x=>x.name],['Code',x=>x.code],
   ['Locations',x=>x.assigned.length],['Assigned at',x=>x.assigned.map(a=>a.locationName).join(' • ')||'Not assigned']],
   items);
 }draw();
}
function mappingsView(pane,d){
 metrics(pane,[['Locations',d.locations.length],['Location–Section Assignments',d.mappings.length]]);
 let q='';searchField(pane,'Search Location or Section/Unit',v=>{q=v.toLowerCase();draw()});
 const body=ui('div','');pane.append(body);
 function draw(){
  body.replaceChildren();
  const filtered=d.mappings.filter(x=>(x.locationName+' '+x.locationCode+' '+x.name+' '+x.code)
   .toLowerCase().includes(q));
  dataTable(body,[['Location',x=>x.locationName],['Location Type',x=>x.locationType],
   ['Section / Unit',x=>x.name],['Section Code',x=>x.code],
   ['Default',x=>x.isDefault?'Yes':'No']],filtered);
 }draw();
}
function buildingView(pane,d){
 metrics(pane,[['Buildings',d.buildings.length],['Location Occupancies',d.occupancies.length]]);
 let q='';searchField(pane,'Search Building, Location or parish',v=>{q=v.toLowerCase();draw()});
 const list=ui('div','building-grid');pane.append(list);
 function draw(){
  list.replaceChildren();
  const buildings=d.buildings.filter(b=>{
   const occ=d.occupancies.filter(x=>x.buildingId===b.buildingId);
   return (b.name+' '+b.code+' '+occ.map(x=>x.locationName+' '+x.parish).join(' ')).toLowerCase().includes(q);
  });
  if(!buildings.length){empty(list,'No matching Buildings.');return}
  buildings.forEach(b=>{
   const card=ui('article','building-card');
   card.append(ui('span','eyebrow',b.code),ui('h2','',b.name));
   const occ=d.occupancies.filter(o=>o.buildingId===b.buildingId);
   card.append(ui('p','',occ[0]?[occ[0].address,occ[0].parish].filter(Boolean).join(', '):'No active Location occupancy'));
   const group=ui('div','occupancy-list');
   if(occ.length)for(const o of occ){
    const line=ui('div','occupancy-row');
    line.append(ui('strong','',o.locationName),pill(o.occupancyType));
    group.append(line);
   }
   else empty(group,'No assigned Location');
   card.append(group);list.append(card);
  });
 }draw();
}
function reportingView(pane,d){
 metrics(pane,[['Reporting Relationships',d.reporting.length],['Functional',d.reporting.filter(x=>x.type==='FUNCTIONAL').length],
  ['Local',d.reporting.filter(x=>x.type==='LOCAL').length]]);
 let q='',type='';const {holder}=searchField(pane,'Search source or reporting authority',v=>{q=v.toLowerCase();draw()});
 selectFilter(holder,'Relationship types',[...new Set(d.reporting.map(x=>x.type))].sort(),v=>{type=v;draw()});
 const body=ui('div','');pane.append(body);
 function draw(){body.replaceChildren();dataTable(body,
  [['Formation / Section',x=>x.sourceName],['Reports to',x=>x.authorityName],
   ['Relationship',x=>pill(x.type)],['Scope',x=>x.scope||'—']],
  d.reporting.filter(x=>(!type||x.type===type)&&
   (x.sourceName+' '+x.authorityName).toLowerCase().includes(q)));
 }draw();
}
async function readDirectory(){
 if(previewMode)return sampleSnapshot();
 if(!directoryCache)directoryCache=liveSnapshot().catch(err=>{directoryCache=null;throw err});
 return directoryCache;
}
async function loadView(){
 const generation=++viewGeneration,[title,desc]=views[current];
 $('#view-title').textContent=title;$('#view-description').textContent=desc;
 setupForm();
 $('#manage-panel').hidden=previewMode||!token||(actions[current]||[]).length===0;
 $('#mode-note').textContent=previewMode
  ? 'SAMPLE PREVIEW · Fictional [TEST] records only. These are NOT live database results; changes are disabled.'
  : token?'LIVE DIRECTORY · Visible results depend on your assigned permissions.'
  : 'SIGN-IN REQUIRED · The live registry is protected. Select Preview sample to inspect the interface without accessing JCF records.';
 if(!token&&!previewMode){
  $('#results').replaceChildren(ui('p','empty-state','Sign in with an authorized account to read live records, or choose Preview sample to inspect the layouts.'));
  return;
 }
 alertMsg('');
 const pane=$('#results');pane.replaceChildren(ui('p','loading','Loading directory…'));
 try{
  if(current==='approval'){
   if(previewMode){pane.replaceChildren();empty(pane,'Approval workflows are disabled in sample preview.');return}
   const rows=(await api('/api/v1/governance/requests')).items;
   if(generation!==viewGeneration)return;
   pane.replaceChildren();if(!rows.length)empty(pane,'No accessible change requests.');
   for(const item of rows){
    const wrap=ui('article','subpanel');wrap.append(ui('strong','',item.action+' — '+item.status));
    wrap.append(ui('p','',item.reference+' · Requested by '+item.requestedBy));
    wrap.append(ui('code','',JSON.stringify(item.payload)));
    if(item.status==='SUBMITTED'){wrap.append(button('Approve',()=>decide(item.id,'APPROVED')));
     wrap.append(button('Reject',()=>decide(item.id,'REJECTED')))}
    if(item.status==='APPROVED')wrap.append(button('Apply approved change',()=>apply(item.id)));pane.append(wrap);
   }
   return;
  }
  if(current==='import'){
   pane.replaceChildren();if(previewMode){empty(pane,'Bulk import is disabled in sample preview.');return}
   const file=document.createElement('input');file.type='file';file.accept='.csv,.xlsx';pane.append(file);
   pane.append(button('Preview import',async()=>{
    if(!file.files[0])return;
    const form=new FormData();form.append('file',file.files[0]);
    const r=await fetch('/api/v1/import/preview',{method:'POST',headers:{Authorization:'Bearer '+token},body:form});
    const data=await r.json();
    if(!r.ok){alertMsg(data.detail||'Import preview failed');return}
    const input=$('#f_operations');if(input)input.value=JSON.stringify(data.operations,null,2);
    alertMsg(data.operations.length+' valid operations and '+data.errors.length+' errors. Review before submitting for approval.');
   }));return;
  }
  if(current==='contacts'||current==='personnel'){
   pane.replaceChildren();
   empty(pane,'A searchable cross-entity listing is not yet available for this module. Its existing authorized entity-level APIs and change workflow remain in place.');
   return;
  }
  const d=await readDirectory();
  if(generation!==viewGeneration)return;
  pane.replaceChildren();
  if(d.unavailable.length) {
   const warning=ui('p','directory-warning','Some restricted collections could not be loaded: '+
    d.unavailable.join(', ')+'. Verify reader permissions. Counts for those collections may be incomplete.');
   pane.append(warning);
  }
  const tabs={overview:overviewView,locations:locationView,sections:sectionsView,
   mappings:mappingsView,facilities:buildingView,reporting:reportingView};
  tabs[current](pane,d);
 }catch(e){
  if(generation!==viewGeneration)return;
  pane.replaceChildren();empty(pane,'Unable to load the directory. '+e.message);
  alertMsg(e.message);
 }
}
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
$('#local-login-form').onsubmit=localSignIn;
$('#local-login-cancel').onclick=()=>{$('#local-login-form').hidden=true;$('#local-login-form').reset();alertMsg('')};
$('#refresh').onclick=()=>{directoryCache=null;loadView()};
$('#preview').onclick=()=>{previewMode=!previewMode;directoryCache=null;
 $('#preview').textContent=previewMode?'Exit sample preview':'Preview sample';loadView()};
$('#login').onclick=signIn;$('#logout').onclick=()=>{token=null;profile=null;$('#identity').textContent='Not signed in';
 $('#local-login-form').hidden=true;$('#local-login-form').reset();
 $('#login').hidden=false;$('#logout').hidden=true;directoryCache=null;previewMode=false;loadView()};
(async()=>{try{await authCallback()}catch(e){alertMsg(e.message)}loadView()})();
