/* JCF-EMP PMS/HRIS test-data reconciliation. Uses the existing governed change form. */
'use strict';

function prepareRegistryCorrection(action,fields={}){
 if(previewMode||!token){alertMsg('Sign in to propose a correction.');return}
 const choose=$('#action');
 if(![...choose.options].some(o=>o.value===action)){alertMsg('Correction action is unavailable');return}
 choose.value=action;
 fieldForm(action);
 $('#manage-panel').hidden=false;
 $('#manage-panel').open=true;
 for(const [name,value] of Object.entries(fields)){
  if(value===undefined||value===null)continue;
  const el=$('#f_'+name);
  if(!el)continue;
  if(el.tagName==='SELECT'&&el.reload){
   el.reload().then(()=>{
    if(![...el.options].some(o=>o.value===String(value))){
     el.append(new Option('Selected ('+String(value).slice(0,8)+')',String(value)));
    }
    el.value=String(value);
   });
  }else el.value=String(value);
 }
 $('#reference').value='PMS_TEST_RECONCILIATION';
 $('#manage-panel').scrollIntoView({behavior:'smooth',block:'start'});
}

function reconciliationView(pane,report){
 const entries=report.items||[],totals=report.totals||{};
 metrics(pane,[
  ['PMS Locations',totals.importedLocations||0,'Awaiting reconciliation'],
  ['PMS Section/Unit definitions',totals.importedSections||0,'Source reference records'],
  ['Records with issues',totals.recordsWithIssues||0,'Parent/section/duplicate warnings']]);
 const notice=ui('div','directory-warning');
 notice.textContent='TEST IMPORT — Names, duplicates and relationships require verification. Source identifiers remain linked to independent JCF-EMP UUIDs. Corrections use the existing approval workflow; imported records must not be used as approved production master data.';
 pane.append(notice);
 let query='',kind='';
 const {holder}=searchField(pane,'Find imported names, codes or issues',v=>{query=v.trim().toLowerCase();draw()});
 selectFilter(holder,'Types',['LOCATION','SECTION_UNIT'],v=>{kind=v;draw()});
 const results=ui('div','');pane.append(results);
 const today=()=>new Date().toISOString().slice(0,10);
 function correct(item,action,extra){
  prepareRegistryCorrection(action,{...extra});
 }
 function draw(){
  results.replaceChildren();
  const items=entries.filter(x=>(!kind||x.entityType===kind)&&
   [x.name,x.code,...(x.issues||[])].join(' ').toLowerCase().includes(query));
  if(!items.length){empty(results,'No imported records match this filter.');return}
  const wrapper=ui('div','table-scroll'),table=ui('table','');
  const head=ui('tr','');
  ['Type','Imported record','Review findings','Prepare correction'].forEach(h=>head.append(ui('th','',h)));
  const thead=ui('thead','');thead.append(head);table.append(thead);
  const body=ui('tbody','');
  for(const item of items){
   const tr=ui('tr',''),type=ui('td','',item.entityType==='LOCATION'?item.locationType:'Section/Unit');
   const label=ui('td','');
   label.append(ui('strong','',item.name));
   label.append(ui('small','registry-reference',item.code));
   const review=ui('td','');
   if(!(item.issues||[]).length)review.append(ui('span','pill','Review source record'));
   for(const issue of item.issues||[])review.append(ui('div','recon-issue',issue.replaceAll('_',' ')));
   if((item.potentialDuplicateIds||[]).length)review.append(
    ui('small','registry-reference',item.potentialDuplicateIds.length+' potential duplicate(s)'));
   const action=ui('td','recon-actions');
   if(item.entityType==='LOCATION'){
    action.append(button('Correct name',()=>correct(item,'LOCATION_RENAME',{
     locationId:item.id,name:item.name,effectiveFrom:today()})));
    action.append(button(item.parentLocationId?'Move to parent':'Assign parent',()=>correct(item,
      item.parentLocationId?'LOCATION_PARENT_REASSIGN':'LOCATION_PARENT',{
       childId:item.id,validFrom:today(),effectiveFrom:today()})));
    action.append(button('Retire duplicate',()=>correct(item,'LOCATION_RETIRE',{
     locationId:item.id,effectiveFrom:today()})));
   }else{
    action.append(button('Correct name',()=>correct(item,'SECTION_RENAME',{
     sectionDefinitionId:item.id,name:item.name,effectiveFrom:today()})));
    action.append(button('Assign to Location',()=>correct(item,'SECTION_ATTACH',{
     sectionDefinitionId:item.id,validFrom:today()})));
    action.append(button('Retire duplicate',()=>correct(item,'SECTION_DEFINITION_RETIRE',{
     sectionDefinitionId:item.id,effectiveFrom:today()})));
   }
   tr.append(type,label,review,action);body.append(tr);
  }
  table.append(body);wrapper.append(table);results.append(wrapper);
 }
 draw();
}
