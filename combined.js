'use strict';
const $=id=>document.getElementById(id);let jobId=null,pollTimer=null,assets=[];
function say(message){$('toast').textContent=message;$('toast').hidden=false;setTimeout(()=>$('toast').hidden=true,7000)}
async function api(url,options={}){const r=await fetch(url,options);let d;try{d=await r.json()}catch{throw Error('The server could not finish this request. Try again.')}if(!r.ok){if(r.status===401)$('accountNotice').textContent='Please log in to save your videos and use this workspace.';const error=Error(typeof d.detail==='string'?d.detail:'Please check your entries and try again.');error.status=r.status;throw error}return d}
function config(){if(!$('asset').value)throw Error('Upload and choose your lower video first.');if(!$('pageName').value.trim())throw Error('Enter your page name.');return {asset_id:$('asset').value,audio:$('audio').value,profile_url:$('profile').value.trim(),addition:$('addition').value.trim(),signature:$('signature').value.trim()}}
async function busy(button,task){button.disabled=true;try{await task()}catch(e){say(e.message)}finally{button.disabled=false}}
async function loadAssets(selected){const d=await api('/api/combined/assets');assets=d.assets;$('asset').replaceChildren();for(const a of assets){const o=document.createElement('option');o.value=a.id;o.textContent=a.name+' · '+Math.round(a.seconds)+'s';$('asset').append(o)}if(!assets.length){const o=document.createElement('option');o.value='';o.textContent='Upload your lower video';$('asset').append(o)}if(selected)$('asset').value=selected}
$('assetForm').onsubmit=e=>{e.preventDefault();busy($('uploadBottom'),async()=>{const data=new FormData();data.append('video',$('bottomUpload').files[0]);const result=await api('/api/combined/assets',{method:'POST',body:data});await loadAssets(result.id);$('bottomUpload').value='';say('Lower video saved for reuse.')})};
$('deleteAsset').onclick=()=>busy($('deleteAsset'),async()=>{if(!$('asset').value)return;if(!confirm('Delete this saved lower video?'))return;await api('/api/combined/assets/'+$('asset').value,{method:'DELETE'});await loadAssets();say('Lower video deleted.')});
function showResult(job){const output=job.outputs[0];$('placeholder').hidden=true;$('resultVideo').hidden=false;$('resultVideo').src=output.url;$('resultCaption').textContent=output.post_title;$('resultCaption').hidden=false;$('download').href=output.url;$('download').setAttribute('download',output.name);$('download').hidden=false;$('copyCaption').hidden=false;$('deleteResult').hidden=false;$('profileButton').hidden=!output.profile_url;if(output.profile_url)$('profileButton').href=output.profile_url;}
const jobKey='clipmagic.combined.job';let stopPoll=null;
const checkAgain=document.createElement('button');checkAgain.type='button';checkAgain.className='secondary';checkAgain.textContent='Check again';checkAgain.hidden=true;$('jobStatus').after(checkAgain);checkAgain.onclick=()=>poll();
function poll(){if(stopPoll)stopPoll();checkAgain.hidden=true;$('makePreview').disabled=true;
stopPoll=ClipJobs.watch(jobId,{
updated(job){$('jobStatus').textContent=job.message;$('progress').hidden=false;$('progress').value=job.progress;if(job.status==='completed'){showResult(job);$('makePreview').disabled=false}if(job.status==='failed'){$('makePreview').disabled=false;ClipJobs.remember(jobKey,null)}},
retrying(message){$('jobStatus').textContent=message},
stopped(error){$('jobStatus').textContent=error.status===404?'This preview expired or was removed during a server replacement. Create another preview.':error.message;$('makePreview').disabled=false;checkAgain.hidden=error.status===404||error.status===401;if(error.status===404)ClipJobs.remember(jobKey,null)}
})}
$('previewForm').onsubmit=async e=>{e.preventDefault();if($('makePreview').disabled)return;try{const options=config();$('makePreview').disabled=true;if(stopPoll)stopPoll();checkAgain.hidden=true;$('resultVideo').pause();$('resultVideo').removeAttribute('src');for(const id of ['resultVideo','resultCaption','download','copyCaption','deleteResult','profileButton'])$(id).hidden=true;$('placeholder').hidden=false;$('jobStatus').textContent='Uploading your source video…';$('progress').hidden=false;$('progress').value=0;const d=new FormData();d.append('video',$('sourceVideo').files[0]);d.append('options',JSON.stringify(options));d.append('source_caption',$('sourceCaption').value);d.append('page_name',$('pageName').value);d.append('seconds',$('seconds').value);const job=await api('/api/combined/preview',{method:'POST',body:d});jobId=job.id;ClipJobs.remember(jobKey,job.id);poll()}catch(e){$('jobStatus').textContent=e.message;$('makePreview').disabled=false}};
$('copyCaption').onclick=()=>busy($('copyCaption'),async()=>{await navigator.clipboard.writeText($('resultCaption').textContent);say('Caption copied.')});
$('deleteResult').onclick=()=>busy($('deleteResult'),async()=>{if(!jobId||!confirm('Delete this finished preview from the server?'))return;await api('/api/jobs/'+jobId,{method:'DELETE'});$('resultVideo').pause();$('resultVideo').removeAttribute('src');for(const id of ['resultVideo','resultCaption','download','copyCaption','deleteResult','profileButton','progress'])$(id).hidden=true;$('placeholder').hidden=false;$('jobStatus').textContent='Preview deleted. Your reusable lower video is still saved.';jobId=null;ClipJobs.remember(jobKey,null);checkAgain.hidden=true});
const platformNames={facebook:'Facebook',instagram:'Instagram',youtube:'YouTube',tiktok:'TikTok',x:'X',snapchat:'Snapchat'};
let currentProjects=[],connectedPlatforms=[];
function element(tag,text,className){const node=document.createElement(tag);if(text)node.textContent=text;if(className)node.className=className;return node}
function syncAccountChoice(){for(const input of $('destinations').querySelectorAll('input[type=checkbox]'))input.disabled=$('allAccounts').checked;}
$('allAccounts').onchange=syncAccountChoice;
async function loadDestinations(){
  const [d,oauth]=await Promise.all([api('/api/customer/apis'),api('/api/oauth/status')]);const previous=new Set([...$('destinations').querySelectorAll('input:checked')].map(x=>x.value));
  connectedPlatforms=[];$('destinations').replaceChildren();
  for(const [platform,name] of Object.entries(platformNames)){
    const c=d.configured[platform]||{},official=oauth.platforms[platform]||{},connected=Boolean(c.access_token||c.api_token);
    const row=element('div',null,'platform-row');
    if(connected){
      connectedPlatforms.push(platform);const label=element('label'),input=element('input');input.type='checkbox';input.value=platform;input.dataset.link=d.links[platform]||'';input.checked=previous.has(platform)||$('allAccounts').checked;
      label.append(input,document.createTextNode(name+' · Connected'));row.append(label);
    }else{row.append(element('strong',name+' · Not connected'))}
    const address=element('input');address.type='url';address.value=d.links[platform]||'';address.placeholder='https://…';address.setAttribute('aria-label',name+' destination account link');row.append(address);
    action(row,'Save '+name+' link',async()=>{
      let link;try{link=new URL(address.value.trim())}catch{throw Error('Enter a valid HTTPS account link.')}
      if(link.protocol!=='https:')throw Error('Use an HTTPS account link.');
      await api('/api/customer/apis',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({[platform]:{account_link:link.href}})});
      const choice=row.querySelector('input[type=checkbox]');if(choice)choice.dataset.link=link.href;
      say(name+' link saved. '+(connected?'Existing API authorization is reused.':'Connect the account to allow publishing.'));
    });
    const connect=element('a',(connected?'Reconnect ':'Connect ')+name,'button secondary');connect.href='/api/oauth/'+platform+'/start?next=/combined.html';row.append(connect);
    if(!official.available&&!connected)row.append(element('p','Developer app setup is still needed for this platform.','small'));
    $('destinations').append(row);
  }
  syncAccountChoice();
  $('automationStatus').textContent=connectedPlatforms.length?connectedPlatforms.length+' existing account authorizations available for combined videos.':'Use Connect below to authorize your social accounts. Existing connections appear automatically.';
}
$('refreshAccounts').onclick=()=>busy($('refreshAccounts'),async()=>{await loadDestinations();await loadConnections();say('Shared accounts refreshed.')});
function populateSelect(select,projects,emptyText){const selected=select.value;select.replaceChildren();const empty=element('option',emptyText);empty.value='';select.append(empty);for(const project of projects){const option=element('option',project.name+(project.status==='paused'?' · Paused':''));option.value=project.id;select.append(option)}if(projects.some(p=>p.id===selected))select.value=selected;}
async function saveConnection(){
  const combined=config(),all=$('allAccounts').checked;
  const destinations=[...$('destinations').querySelectorAll('input:checked')].map(x=>({platform:x.value,account_link:x.dataset.link}));
  if(!(all?connectedPlatforms.length:destinations.length))throw Error('Connect and select at least one posting account first.');
  if(!$('sourceAccount').value||!$('sourceName').value.trim())throw Error('Enter this combined-video connection’s source link and name.');
  const project=await api('/api/automation/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode:'combined',combined,destination_mode:all?'all_connected':'selected',source_url:$('sourceAccount').value,streamer_name:$('sourceName').value,page_name:$('pageName').value,name:$('pageName').value+' · Combined',clip_count:1,clip_length:Number($('seconds').value),monitor_new:true,import_history:false,destinations})});
  await loadConnections();$('postingConnection').value=project.id;
  say('Combined auto-posting saved. Upload this section’s source video or connect its source delivery.');
}
$('saveConnection').onclick=()=>busy($('saveConnection'),saveConnection);
async function changeProject(project,changes){await api('/api/automation/projects/'+project.id,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(changes)});await loadConnections();}
async function uploadForPosting(projectId,file,caption){
  if(!projectId)throw Error('Save and choose an automatic-posting connection first.');
  if(!file)throw Error('Choose a top video first.');
  const data=new FormData();data.append('video',file);data.append('source_caption',caption||'');
  await api('/api/automation/projects/'+projectId+'/ingest',{method:'POST',body:data});
  $('automationStatus').textContent='Video received. Combining and posting will continue on the server.';
  say('Video received for automatic combining and posting.');await loadConnections();
}
$('uploadAndPost').onclick=()=>busy($('uploadAndPost'),async()=>{
  await uploadForPosting($('postingConnection').value,$('sourceVideo').files[0],$('sourceCaption').value);
  $('automaticPosting').scrollIntoView({behavior:'smooth',block:'start'});
});
function action(card,label,task){const button=element('button',label,'secondary');button.type='button';button.onclick=()=>busy(button,task);card.append(button);return button;}
async function loadConnections(){
  const [d,q]=await Promise.all([api('/api/automation/projects'),api('/api/automation/queue')]);currentProjects=d.projects;
  const combinedProjects=d.projects.filter(p=>p.combined_config);
  populateSelect($('postingConnection'),combinedProjects,'Choose a saved connection');if(!$('postingConnection').value&&combinedProjects.length===1)$('postingConnection').value=combinedProjects[0].id;
  $('connections').replaceChildren();
  for(const project of combinedProjects){
    const card=element('div',null,'connection');card.append(element('strong',project.name));
    const waiting=(project.queue.queued||0)+(project.queue.retry||0)+(project.queue.posting||0);
    const state=project.status==='active'?'Auto-posting enabled':'Paused';
    card.append(element('p',state+' · '+(project.queue.posted||0)+' posted · '+waiting+' waiting · '+(project.queue.blocked||0)+' need authorization · '+(project.queue.failed||0)+' failed'));
    const destinationText=project.destinations.map(x=>platformNames[x.platform]||x.platform).join(', ')||'No authorized accounts connected';
    card.append(element('p',(project.destination_mode==='all_connected'?'All connected accounts: ':'Selected accounts: ')+destinationText));
    card.append(element('p','Source: '+project.streamer_name+' · '+project.source_url));
    if(project.last_checked_at)card.append(element('p','Last video received: '+new Date(project.last_checked_at*1000).toLocaleString()));
    if(project.last_error)card.append(element('p',project.last_error));
    const upload=action(card,'Upload source & auto-post',async()=>{
      const picker=element('input');picker.type='file';picker.accept='video/*';picker.onchange=()=>{if(!picker.files[0])return;const caption=prompt('Original caption (optional)','');if(caption!==null)busy(upload,()=>uploadForPosting(project.id,picker.files[0],caption))};picker.click();
    });
    upload.disabled=project.status!=='active';
    action(card,project.status==='active'?'Pause':'Resume',()=>changeProject(project,{status:project.status==='active'?'paused':'active'}));
    if(project.destination_mode!=='all_connected')action(card,'Use all connected accounts',()=>changeProject(project,{destination_mode:'all_connected'}));
    action(card,'Delete connection',async()=>{if(!confirm('Delete this connection and cancel posts that have not started?'))return;await api('/api/automation/projects/'+project.id,{method:'DELETE'});await loadConnections()});
    const details=element('details'),summary=element('summary','Separate source inbox setup');details.append(summary,element('p','An authorized source service can deliver video files here. Keep the inbox URL private.'));
    action(details,'Copy private inbox URL',async()=>{await navigator.clipboard.writeText(project.automatic_inbox);say('Private inbox URL copied.')});card.append(details);
    for(const event of (project.events||[]).slice(0,3))card.append(element('p',event.message));$('connections').append(card);
  }
  const ids=new Set(combinedProjects.map(p=>p.id)),items=q.items.filter(item=>ids.has(item.project_id));$('postingActivity').replaceChildren();
  if(!items.length)$('postingActivity').textContent='No posts queued yet. Incoming source videos and uploads will appear here.';
  for(const item of items.slice(0,18)){const row=element('div',null,'delivery');row.append(element('strong',platformNames[item.platform]||item.platform),element('span',item.status));if(item.last_error)row.append(element('p',item.last_error));$('postingActivity').append(row)}
}
(async()=>{try{await loadAssets();$('accountNotice').textContent='Your combined-video workspace · Social accounts connected to ClipMagic';const saved=ClipJobs.recalled(jobKey);if(saved&&/^[a-f0-9]{32}$/.test(saved)){jobId=saved;poll()}await loadDestinations();await loadConnections();const query=new URLSearchParams(location.search);const result=query.get('result'),platform=platformNames[query.get('connection')]||'Account';if(result){const messages={connected:platform+' connected. It is available for combined posting.',cancelled:'Account connection was cancelled.',owner_setup_required:'The owner must finish '+platform+' developer-app setup before connecting.',failed:platform+' authorization failed. Please reconnect.'};say(messages[result]||'Check the account connection below.')}setInterval(()=>loadConnections().catch(()=>{$('automationStatus').textContent='Connection interrupted. Refresh to check the latest posting status.'}),15000)}catch(e){$('accountNotice').textContent=e.message;$('destinations').textContent='Log in to use the same social accounts as normal clipping.'}})();
