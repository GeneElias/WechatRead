const $ = (s) => document.querySelector(s);
let saved = null, qrUrl = null;
const labels = {queued:'等待执行',running:'执行中',verified:'阅读已确认',pending_verification:'待确认',needs_login:'需要扫码',failed:'执行失败',interrupted:'已中断',confirmed_by_page:'官方页面字段已确认',not_configured:'未配置'};
function message(text) { $('#message').textContent = text; }
async function api(path, options={}) {
  const response = await fetch('/api/'+path, {headers:{'Content-Type':'application/json'},...options});
  if (!response.ok) {
    let detail; try { detail=(await response.json()).detail; } catch { detail=await response.text().catch(()=> ''); }
    if(response.status===401) { $('#workspace').hidden=true; $('#auth').hidden=false; }
    throw new Error(typeof detail==='string' ? detail : Array.isArray(detail) ? detail.map(x=>x.msg).join('；') : '操作失败，请重新登录或检查配置');
  }
  return response.status===204 ? null : response.json();
}
function showSettings(settings) {
  saved=settings;
  for (const [key,value] of Object.entries(settings)) {
    const field=$('#settings-form').elements.namedItem(key);
    if(field.type==='checkbox') field.checked=value; else field.value=value;
  }
  $('#schedule').textContent=settings.scheduled ? settings.daily_time+' · 每天' : '未开启';
}
async function refresh() {
  const [status,jobs]=await Promise.all([api('status'),api('jobs')]);
  $('#session').textContent=status.session_saved?'会话已保存':'等待扫码';
  $('#running').textContent=status.running?'正在阅读':'等待开始';
  $('#run').disabled=status.running || status.browser_busy;
  $('#cancel').disabled=!status.running;
  $('#empty').hidden=jobs.length>0;
  $('#jobs').replaceChildren();
  for(const job of jobs) {
    const row=document.createElement('tr'); const r=job.result;
    const times=(r.before_seconds!=null || r.after_seconds!=null) ? ` 官方字段：${r.before_seconds??'未知'} → ${r.after_seconds??'未知'} 秒。` : '';
    for(const value of [job.created.replace('T',' ').replace('+08:00',''),labels[job.status]??job.status,labels[r.reading]??'—',labels[r.checkin]??'—',(r.message??'')+times+(r.elapsed_seconds!=null?` 本地运行 ${r.elapsed_seconds} 秒。`:'')]) {
      const cell=document.createElement('td');cell.textContent=value;row.append(cell);
    }
    $('#jobs').append(row);
  }
}
async function enter() {
  showSettings(await api('settings'));
  $('#auth').hidden=true;$('#workspace').hidden=false;
  await refresh();
}
async function act(button, action) {
  button.disabled=true;
  try { await action(); } catch(error) { message(error.message); } finally { button.disabled=false; }
}
$('#auth-form').addEventListener('submit',e=>{e.preventDefault();act(e.submitter,async()=>{await api('auth',{method:'POST',body:JSON.stringify({token:$('#token').value})});$('#token').value='';await enter();message('已进入个人阅读空间');});});
$('#settings-form').addEventListener('submit',e=>{e.preventDefault();act(e.submitter,async()=>{
  const form=e.target,settings={};
  for(const field of form.elements) { if(!field.name) continue;settings[field.name]=field.type==='checkbox'?field.checked:field.type==='number'?Number(field.value):field.value.trim(); }
  showSettings(await api('settings',{method:'PUT',body:JSON.stringify(settings)}));message('阅读计划已保存');
});});
async function screenshot(){
  const response=await fetch('/api/browser/screenshot');
  if(!response.ok) throw new Error('截图不可用，请先打开登录页；任务运行时无法刷新截图');
  if(qrUrl) URL.revokeObjectURL(qrUrl);
  qrUrl=URL.createObjectURL(await response.blob());$('#qr').src=qrUrl;$('#qr').hidden=false;
  $('#login-hint').textContent='使用微信扫描官方二维码。扫码后可刷新截图查看页面状态。';
}
$('#login').onclick=e=>act(e.target,async()=>{await api('browser/login',{method:'POST'});await screenshot();await refresh();message('官方页面已打开，请扫码登录');});
$('#refresh-qr').onclick=e=>act(e.target,async()=>{await screenshot();await refresh();});
$('#disconnect').onclick=e=>act(e.target,async()=>{await api('browser/disconnect',{method:'POST'});$('#qr').hidden=true;await refresh();message('本地登录态已清除；如需撤销远程设备，请在官方客户端操作');});
$('#run').onclick=e=>act(e.target,async()=>{await api('jobs',{method:'POST'});await refresh();message('任务已启动，结果将记录在下方');});
$('#cancel').onclick=e=>act(e.target,async()=>{await api('jobs/cancel',{method:'POST'});await refresh();message('已停止任务，官方结果仍需核对');});
$('#refresh').onclick=e=>act(e.target,refresh);
$('#logout').onclick=e=>act(e.target,async()=>{await api('logout',{method:'POST'});$('#workspace').hidden=true;$('#auth').hidden=false;if(qrUrl)URL.revokeObjectURL(qrUrl);$('#qr').hidden=true;message('已退出管理页面');});
enter().catch(()=>{});
setInterval(()=>{if(!$('#workspace').hidden)refresh().catch(e=>message(e.message));},5000);
