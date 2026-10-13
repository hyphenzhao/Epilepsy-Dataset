/* RuiSource web workspace. All patient data is fetched through authenticated routes. */
(() => {
  'use strict';
  const root = document.getElementById('eeg-workspace');
  if (!root) return;
  const $ = id => document.getElementById(id);
  const apiRoot = '/eeg/';
  const patientRoot = `${apiRoot}patients/${root.dataset.patient}/`;
  const writable = root.dataset.write === 'true';
  const csrf = root.querySelector('[name=csrfmiddlewaretoken]').value;
  let records = [], selectedId = null, wave = null, selectedChannels = [], bads = [], renames = {};
  let shownJob = null, latestWaveJob = null, loading = false, uploading = false, geometry = null;
  let waveGeneration = 0, resultGeneration = 0, waveResultGeneration = 0;
  let channelOffset = 0, dragStart = null, geometryYaw = .5, geometryPitch = -.2, geometryZoom = 1;
  const pendingJobs = new Map();
  const operationNames = {inspect:'读取数据', waveform:'波形', psd:'Welch 频谱', tfr:'时频分析', source:'源定位', preprocess:'预处理导出'};
  const statusNames = {queued:'排队中', running:'计算中', succeeded:'已完成', completed:'已完成', failed:'失败', cancelled:'已取消', cancelling:'正在取消'};
  const done = status => ['succeeded','completed','failed','cancelled'].includes(status);
  const record = () => records.find(r => r.id === selectedId);
  const voltageTypes = new Set(['eeg','seeg','ecog','eog','ecg','emg']);
  const eligibleChannels = r => (r?.metadata?.channels || []).filter(c=>typeof c==='string'||voltageTypes.has(c.type));
  const number = (id, fallback = 0) => $(id).value.trim() === '' ? fallback : Number($(id).value);
  const text = (tag, value, className) => { const e = document.createElement(tag); e.textContent = value; if(className)e.className=className; return e; };
  const formatBytes = n => n > 1024**2 ? `${(n/1024**2).toFixed(1)} MB` : `${(n/1024).toFixed(1)} KB`;
  function notice(message, kind='info') { $('eeg-notice').textContent=message; $('eeg-notice').dataset.kind=kind; }
  async function api(path, options={}) {
    const headers = {'X-CSRFToken':csrf, ...options.headers};
    if(options.json !== undefined) { headers['Content-Type']='application/json'; options.body=JSON.stringify(options.json); delete options.json; }
    const response = await fetch(path, {...options,headers,credentials:'same-origin',cache:'no-store'});
    if(response.redirected && response.url.includes('/login')) throw new Error('登录已过期，请重新登录后继续。');
    let data; try { data=await response.json(); } catch(_) { throw new Error(`服务暂时无法处理请求（${response.status}）。`); }
    if(!response.ok) { const e=new Error(data.error || data.detail || `请求失败（${response.status}）`); e.data=data; e.status=response.status; throw e; }
    return data;
  }
  function guard(fn) { return async (...args) => { try { await fn(...args); } catch(e) { notice(e.message,'error'); } }; }
  function button(label, fn, cls='eeg-btn eeg-quiet') { const b=text('button',label,cls); b.type='button'; b.addEventListener('click',guard(fn)); return b; }
  function resetSelection(r) {
    waveGeneration++; resultGeneration++; waveResultGeneration++;
    wave=null; selectedChannels=eligibleChannels(r).map(c=> typeof c==='string'?c:c.name).slice(0,32); bads=[];renames={};channelOffset=0;
    $('window-start').value=0; $('time-slider').value=0; $('segment-start').value=0; shownJob=null; latestWaveJob=null;
    $('result-content').replaceChildren(text('p','选择已完成任务查看结果。','eeg-help'));
    $('geometry-wrap').hidden=true; $('wave-empty').style.display='grid'; drawWave();
  }
  function selectRecord(id) { selectedId=id; resetSelection(record()); render(); if(record()?.metadata?.geometry){geometry=record().metadata.geometry;$('geometry-wrap').hidden=false;drawGeometry();} }
  function render() {
    $('record-count').textContent=records.length;
    $('recordings').replaceChildren(...records.map(r=>{
      const b=button('',()=>selectRecord(r.id),'eeg-record'+(r.id===selectedId?' selected':''));
      b.append(text('strong',r.title),text('small',`${r.modality==='seeg'?'sEEG':'EEG'} · ${(r.files||[]).filter(f=>f.complete).length} 个文件`));return b;
    }));
    const r=record(), meta=r?.metadata || {};
    $('record-heading').textContent=r?.title || '选择或新建一份检查';
    $('record-meta').textContent=meta.sfreq ? `${r.modality==='seeg'?'sEEG':'EEG'} · ${meta.channels?.length || 0} 通道 · ${meta.sfreq} Hz · ${(meta.duration/60).toFixed(1)} 分钟` : '上传文件后读取通道与采样信息';
    $('time-slider').max=Math.max(0,(meta.duration || 0)-number('window-duration',10));
    $('channels-open').disabled=!meta.channels?.length;
    $('record-files').replaceChildren(...(r?.files || []).map(f=>{
      const row=document.createElement('div');row.className='eeg-file';
      const info=text('span',f.name);info.append(text('small',`${f.role} · ${formatBytes(f.size || 0)}${f.complete?'':' · 未完成'}`));row.append(info);
      if(f.complete){ const a=text('a','下载');a.href=`${apiRoot}files/${f.id}/download/`;row.append(a); }
      else if(writable)row.append(button('放弃上传',async()=>{if(!confirm('删除这份未完成上传的临时数据，并释放配额？'))return;await api(`${apiRoot}uploads/${f.id}/discard/`,{method:'POST',json:{}});await refresh();}));
      return row;
    }));
    if(!r?.files?.length)$('record-files').append(text('p','尚未上传文件。','eeg-help'));
    renderJobs();
    const locked=!r || !writable;
    document.querySelectorAll('[data-operation],#inspect,#load-wave,#apply-filters,#upload-start').forEach(b=>b.disabled=locked || (b.id==='upload-start'&&uploading));
    document.querySelector('[data-operation=source]').disabled=locked || r?.modality==='seeg';
  }
  function renderJobs() {
    const jobs=(record()?.jobs || []).slice().sort((a,b)=>String(b.created_at).localeCompare(String(a.created_at)));
    $('job-count').textContent=jobs.length;
    $('job-list').replaceChildren(...jobs.map(j=>{
      const box=document.createElement('div');box.className='eeg-job';
      const title=document.createElement('div');title.className='eeg-job-title';title.append(text('strong',operationNames[j.operation] || j.operation),text('span',statusNames[j.status] || j.status,`eeg-state-${j.status}`));box.append(title);
      if(!done(j.status)){const p=document.createElement('progress');p.max=100;p.value=j.progress||0;box.append(p);}
      box.append(text('small',j.error || j.message || new Date(j.created_at).toLocaleString()));
      if(['succeeded','completed'].includes(j.status))box.append(button('查看结果',()=>loadResult(j.id)));
      if(!done(j.status)&&writable)box.append(button('取消',async()=>{await api(`${apiRoot}jobs/${j.id}/cancel/`,{method:'POST',json:{}});await refresh();}));
      return box;
    }));
    if(!jobs.length)$('job-list').append(text('p','计算任务可在离开页面后继续执行。','eeg-help'));
  }
  async function refresh() {
    if(loading)return;loading=true;
    try {
      const state=await api(`${patientRoot}state/`);records=state.recordings || [];
      if(!selectedId&&records.length){selectedId=records[0].id;resetSelection(record());}
      render();
      for(const [id, entry] of pendingJobs) {
        const j=records.flatMap(r=>r.jobs || []).find(j=>j.id===id);
        if(!j||!done(j.status))continue;
        pendingJobs.delete(id);
        if(entry.recording!==selectedId)continue;
        if(['succeeded','completed'].includes(j.status)) {
          if(j.operation==='inspect'){selectedChannels=eligibleChannels(record()).map(c=>typeof c==='string'?c:c.name).slice(0,32);await loadResult(id);notice('数据已读取，可以浏览波形并选择分析片段。');await requestWave();}
          else if(j.operation!=='waveform'||id===latestWaveJob)await loadResult(id);
        } else notice(j.error || `${operationNames[j.operation]}${statusNames[j.status]}`,'error');
      }
    } finally {loading=false;}
  }
  function params(isWave=false) {
    const p={start:number(isWave?'window-start':'segment-start'),duration:number(isWave?'window-duration':'segment-duration',5),picks:selectedChannels,bads,renames,l_freq:number('high-pass',null),h_freq:number('low-pass',null),notch:number('notch',null),reference:$('reference').value};
    if(p.reference==='bipolar')p.bipolar_pairs=$('bipolar-pairs').value.split(/\n/).map(s=>s.split(',').map(t=>t.trim())).filter(a=>a.length===2&&a.every(Boolean)).map(([anode,cathode])=>({anode,cathode}));
    return p;
  }
  async function submit(operation, p) {
    if(!record())throw new Error('请先选择或新建检查。');
    const recordingId=selectedId;
    const response=await api(`${apiRoot}recordings/${recordingId}/jobs/`,{method:'POST',json:{operation,parameters:p || {}}});
    const j=response.job || response;pendingJobs.set(j.id,{recording:recordingId,operation});
    const submitted=records.find(r=>r.id===recordingId);
    if(submitted)submitted.jobs=[j,...(submitted.jobs || [])];renderJobs();
    return j;
  }
  async function requestWave() {
    if(!record()?.metadata?.channels?.length)return;
    const recordingId=selectedId, generation=++waveGeneration;
    const previousWaveJob=latestWaveJob;
    if(previousWaveJob && pendingJobs.has(previousWaveJob)) { await api(`${apiRoot}jobs/${previousWaveJob}/cancel/`,{method:'POST',json:{}});pendingJobs.delete(previousWaveJob); }
    if(recordingId!==selectedId || generation!==waveGeneration)return;
    const max=Math.max(0,record().metadata.duration-number('window-duration',10));
    $('window-start').value=Math.max(0,Math.min(max,number('window-start'))).toFixed(2);$('time-slider').value=$('window-start').value;
    const j=await submit('waveform',params(true));
    if(recordingId!==selectedId || generation!==waveGeneration){pendingJobs.delete(j.id);await api(`${apiRoot}jobs/${j.id}/cancel/`,{method:'POST',json:{}});return;}
    latestWaveJob=j.id;notice('正在读取所选时间窗…');
  }
  async function analyse(operation) {
    const p=params();p.fmin=number('fmin',2);p.fmax=number('fmax',40);
    if(operation==='tfr')p.tfr_method=$('tfr-method').value;
    if(operation==='source')Object.assign(p,{picks:[],method:$('source-method').value,head_model:$('head-model').value,demo_only:$('head-model').value==='sphere',baseline_start:number('baseline-start'),baseline_end:number('baseline-end',2),snr:number('snr',3),n_dipoles:number('n-dipoles',1),alpha:number('source-alpha',50),reg:number('source-reg',.05),event_times:$('event-times').value.split(/[,，\s]+/).filter(Boolean).map(Number),tmin:number('epoch-tmin',-.2),tmax:number('epoch-tmax',.5)});
    if(!Number.isFinite(p.start)||!Number.isFinite(p.duration)||p.duration<=0)throw new Error('请填写有效的分析片段。');
    if(operation==='source'&&p.duration>10)throw new Error('源定位每次最多分析 10 秒，请缩短分析片段。事件平均可另外填写事件时刻。');
    if(operation==='source' && p.method==='gamma_map'){p.alpha=number('source-gamma-alpha',.5);p.gamma_alpha=p.alpha;}
    await submit(operation,p);notice(`${operationNames[operation]}已加入后台任务。计算期间可以继续浏览波形。`);
  }
  function artifactUrl(job,name) { return `${apiRoot}jobs/${job}/artifacts/${encodeURIComponent(name)}/`; }
  async function loadResult(id) {
    const forWave=record()?.jobs?.find(job=>job.id===id)?.operation==='waveform';
    const selected=selectedId, generation=waveGeneration, resultToken=forWave?++waveResultGeneration:++resultGeneration;
    const j=await api(`${apiRoot}jobs/${id}/`);if(selected!==selectedId || resultToken!==(forWave?waveResultGeneration:resultGeneration))return;
    const result=j.result || {};
    if(j.operation==='waveform') {
      if(generation!==waveGeneration)return;
      wave=result;channelOffset=0;$('wave-empty').style.display='none';drawWave();notice('波形已更新。拖动选择片段后可进行分析。');return;
    }
    if(j.operation==='inspect') {
      geometry=result.geometry;$('geometry-wrap').hidden=!geometry;if(geometry)drawGeometry();
      $('result-content').replaceChildren(text('p',`${result.channels?.length||0} 通道，${result.sfreq} Hz，${(result.duration||0).toFixed(2)} 秒。`));
      for(const a of result.annotations || [])$('result-content').append(text('p',`${Number(a.onset).toFixed(2)} s · ${a.description}`));return;
    }
    shownJob=id;$('result-label').textContent=operationNames[j.operation] || j.operation;
    const target=$('result-content');target.replaceChildren();
    if(result.summary)target.append(text('p',typeof result.summary==='string'?result.summary:JSON.stringify(result.summary)));
    for(const w of result.warnings || [])target.append(text('p',w,'eeg-warning'));
    const channelImages=(result.artifacts || []).filter(a=>a.channel&&(a.kind==='image'||/\.png$/i.test(a.name)));
    if(channelImages.length){
      const label=text('label','时频通道 '),select=document.createElement('select');select.setAttribute('aria-label','时频结果通道');
      channelImages.forEach(a=>{const option=text('option',a.channel);option.value=a.name;select.append(option);});label.append(select);target.append(label);
      const img=document.createElement('img'),link=text('a','↓ 下载当前通道时频图');img.loading='lazy';target.append(img,link);
      const show=()=>{const a=channelImages.find(a=>a.name===select.value);img.src=artifactUrl(id,a.name);img.alt=a.label||a.channel;link.href=img.src;link.download=a.name;};
      select.addEventListener('change',show);img.addEventListener('click',()=>{$('image-enlarged').src=img.src;$('image-dialog').showModal();});show();
    }
    for(const a of result.artifacts || []) {
      if(channelImages.includes(a))continue;
      const url=artifactUrl(id,a.name);
      if(a.kind==='image'||/\.png$/i.test(a.name)) {
        const img=document.createElement('img');img.src=url;img.alt=a.label || a.name;img.loading='lazy';img.addEventListener('click',()=>{$('image-enlarged').src=url;$('image-dialog').showModal();});target.append(img);
      }
      const link=text('a',`↓ ${a.label || a.name}`);link.href=url;link.download=a.name;target.append(link);
    }
    if(result.peaks?.length) {
      target.append(text('p',`峰值坐标：${result.coord_frame || result.geometry?.coord_frame || '请参见运行记录中的坐标系'}；数值单位按算法区分。`,'eeg-help'));
      const scroll=document.createElement('div');scroll.className='eeg-table-scroll';const table=document.createElement('table');const header=document.createElement('tr');
      const frequency=result.time_axis==='frequency'||result.peaks.some(p=>p.frequency!=null || p.frequency_hz!=null);
      target.append(text('p',frequency?'DICS 显示所选频带的平均功率；频率为该频带的代表值（Hz），不是频谱主峰，也没有对应的峰值时刻。':result.temporal_reference==='event_relative'?'时间单位：秒，以事件起点为 0。':'时间单位：秒，从记录起点计时。','eeg-help'));
      const keys=['x','y','z',...(frequency?[result.peaks.some(p=>p.frequency_hz!=null)?'frequency_hz':'frequency']:['time']),'value','unit'];if(result.peaks.some(p=>p.gof!==undefined))keys.push('gof');keys.forEach(k=>header.append(text('th',k==='gof'?'GOF %':k)));table.append(header);
      result.peaks.slice(0,30).forEach(p=>{const row=document.createElement('tr');keys.forEach(k=>row.append(text('td',typeof p[k]==='number'?Number(p[k]).toPrecision(5):(p[k]??'—'))));table.append(row);});scroll.append(table);target.append(scroll);
    }
    const details=document.createElement('details');details.append(text('summary','处理参数与运行记录'));
    details.append(text('pre',JSON.stringify({parameters:j.parameters,provenance:result.provenance,method:result.method},null,2)));target.append(details);
    geometry=result.geometry || null;$('geometry-wrap').hidden=!geometry;if(geometry){geometryZoom=1;drawGeometry();}
    notice(`${operationNames[j.operation]}已完成，结果已保存。`);
  }
  async function upload() {
    if(!record())throw new Error('请先新建或选择检查。');
    const files=[...$('upload-files').files];if(!files.length)throw new Error('请选择文件。');
    const recording=selectedId,role=$('upload-role').value;uploading=true;render();$('upload-progress').hidden=false;
    try {
      for(const file of files) {
        if(file.size>1024**3)throw new Error(`${file.name} 超过单文件 1 GB 上限。`);
        const key=`eeg-upload:${recording}:${file.name}:${file.size}:${file.lastModified}`;
        let saved;try{saved=JSON.parse(sessionStorage.getItem(key));}catch(_){}
        if(saved)try{saved=await api(`${apiRoot}uploads/${saved.id}/`);}catch(_){saved=null;}
        if(!saved)saved=await api(`${apiRoot}recordings/${recording}/uploads/`,{method:'POST',json:{name:file.name,size:file.size,role}});
        sessionStorage.setItem(key,JSON.stringify({id:saved.id}));let offset=saved.offset || 0;
        while(offset<file.size) {
          let uploaded=false;
          for(let attempt=0;attempt<4&&!uploaded;attempt++) {
            try { const r=await api(`${apiRoot}uploads/${saved.id}/chunk/?offset=${offset}`,{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file.slice(offset,offset+1024**2)});offset=r.offset;uploaded=true; }
            catch(e) { if(attempt===3)throw e;await new Promise(resolve=>setTimeout(resolve,1000*(attempt+1)));const status=await api(`${apiRoot}uploads/${saved.id}/`);offset=status.offset || 0; }
          }
          $('upload-progress').value=100*offset/file.size;$('upload-status').textContent=`${file.name} · ${Math.round(100*offset/file.size)}%`;
        }
        if(!saved.complete)await api(`${apiRoot}uploads/${saved.id}/complete/`,{method:'POST',json:{}});
        sessionStorage.removeItem(key);
      }
      $('upload-status').textContent='上传完成';$('upload-files').value='';await refresh();notice('文件已上传。成套文件齐全后，点击“读取数据”。');
    } finally {uploading=false;render();}
  }
  function canvasSize(canvas) {
    const rect=canvas.getBoundingClientRect(),ratio=window.devicePixelRatio || 1;
    if(canvas.width!==Math.round(rect.width*ratio)||canvas.height!==Math.round(rect.height*ratio)){canvas.width=Math.round(rect.width*ratio);canvas.height=Math.round(rect.height*ratio);}
    const ctx=canvas.getContext('2d');ctx.setTransform(ratio,0,0,ratio,0,0);return {ctx,w:rect.width,h:rect.height};
  }
  function drawWave() {
    const {ctx,w,h}=canvasSize($('eeg-wave'));ctx.clearRect(0,0,w,h);if(!wave?.data_uv?.length)return;
    const margin=70, bottom=22,rows=Math.min(16,wave.data_uv.length),rowHeight=(h-bottom)/rows;
    const t=wave.times,minimum=t[0] ?? wave.start,maximum=t[t.length-1] ?? (wave.start+wave.duration),span=maximum-minimum || 1;
    const xOf=value=>margin+(value-minimum)/span*(w-margin-10);
    ctx.font='10px system-ui';ctx.textBaseline='middle';
    for(let i=Math.ceil(minimum);i<=maximum;i++){const x=xOf(i);ctx.strokeStyle=i%5===0?'#d2e1e6':'#edf2f4';ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,h-bottom);ctx.stroke();ctx.fillStyle='#829aa7';ctx.fillText(`${i}s`,x+2,h-9);}
    const selectedStart=number('segment-start'),selectedEnd=selectedStart+number('segment-duration');
    if(selectedEnd>minimum&&selectedStart<maximum){const x1=Math.max(margin,xOf(selectedStart)),x2=Math.min(w,xOf(selectedEnd));ctx.fillStyle='#1a969c19';ctx.fillRect(x1,0,x2-x1,h-bottom);}
    for(let row=0;row<rows;row++) {
      const index=(channelOffset+row)%wave.data_uv.length,data=wave.data_uv[index],y=rowHeight*(row+.5),scale=number('wave-scale',100);
      ctx.strokeStyle='#ecf1f4';ctx.beginPath();ctx.moveTo(margin,y);ctx.lineTo(w,y);ctx.stroke();
      ctx.fillStyle='#375768';ctx.fillText(String(wave.channels[index]).slice(0,10),7,y);
      ctx.strokeStyle='#23485e';ctx.lineWidth=.85;ctx.beginPath();data.forEach((v,i)=>{const x=xOf(t[i]);const yy=y-Math.max(-2,Math.min(2,v/scale))*rowHeight*.4;if(i===0)ctx.moveTo(x,yy);else ctx.lineTo(x,yy);});ctx.stroke();
    }
    ctx.fillStyle='#087f87';ctx.fillText(`${number('wave-scale')} µV`,w-58,10);
    $('selection-label').textContent=`已选 ${selectedStart.toFixed(2)}–${selectedEnd.toFixed(2)} s（${(selectedEnd-selectedStart).toFixed(2)} 秒）`;
  }
  function canvasTime(event) {
    const rect=$('eeg-wave').getBoundingClientRect();const x=Math.max(70,Math.min(rect.width-10,event.clientX-rect.left));
    return wave.times[0]+(x-70)/(rect.width-80)*(wave.times[wave.times.length-1]-wave.times[0]);
  }
  function drawGeometry() {
    if(!geometry)return;const {ctx,w,h}=canvasSize($('eeg-geometry'));ctx.clearRect(0,0,w,h);
    const vertices=geometry.vertices || [],sources=geometry.sources || [],electrodes=geometry.electrodes || [];
    const unpack=p=>Array.isArray(p)?p:[p.x,p.y,p.z];
    const all=[...vertices,...sources.map(unpack),...electrodes.map(unpack)].filter(p=>p.length>=3&&p.every(Number.isFinite));if(!all.length)return;
    const bounds=all.reduce((b,p)=>b.map((v,i)=>[Math.min(v[0],p[i]),Math.max(v[1],p[i])]),[[Infinity,-Infinity],[Infinity,-Infinity],[Infinity,-Infinity]]);
    const center=bounds.map(b=>(b[0]+b[1])/2);
    const extent=Math.max(...bounds.map(b=>(b[1]-b[0])/2),.05);
    const project=p=>{const [x,y,z]=unpack(p).map((v,i)=>(v-center[i])/extent),a=x*Math.cos(geometryYaw)-y*Math.sin(geometryYaw),b=x*Math.sin(geometryYaw)+y*Math.cos(geometryYaw);return [w/2+a*Math.min(w,h)*.37*geometryZoom,h/2-(z*Math.cos(geometryPitch)-b*Math.sin(geometryPitch))*Math.min(w,h)*.37*geometryZoom,b];};
    ctx.strokeStyle='#9cb6c023';ctx.lineWidth=.6;
    const faces=geometry.faces || [];for(let i=0;i<faces.length;i+=Math.max(1,Math.floor(faces.length/5000))){const points=faces[i].map(v=>project(vertices[v]));ctx.beginPath();points.forEach((p,j)=>j?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));ctx.closePath();ctx.stroke();}
    if(!faces.length){ctx.fillStyle='#91afbf40';vertices.filter((_,i)=>i%Math.max(1,Math.floor(vertices.length/4000))===0).forEach(v=>{const p=project(v);ctx.fillRect(p[0],p[1],1.5,1.5);});}
    const maxValue=Math.max(...sources.map(s=>Math.abs(s.value || 0)),1e-30);
    sources.slice().sort((a,b)=>project(a)[2]-project(b)[2]).forEach(s=>{const p=project(s),v=Math.abs(s.value||0)/maxValue;ctx.fillStyle=`rgba(230,${Math.round(160-100*v)},50,${.3+.65*v})`;ctx.beginPath();ctx.arc(p[0],p[1],2+4*v,0,Math.PI*2);ctx.fill();});
    ctx.font='9px system-ui';electrodes.forEach(e=>{const p=project(e);ctx.fillStyle='#138b97';ctx.beginPath();ctx.arc(p[0],p[1],2.3,0,Math.PI*2);ctx.fill();ctx.fillStyle='#3a677a';if(electrodes.length<70)ctx.fillText(e.name||'',p[0]+4,p[1]);});
    $('geometry-caption').textContent=`${geometry.coord_frame || 'Head 坐标系'} · 拖动旋转，滚轮缩放 · 蓝色：电极；暖色：源强度（当前结果内归一化）`;
  }
  function channelDialog() {
    $('channel-list').replaceChildren(...(record()?.metadata?.channels || []).map(c=>{
      const name=typeof c==='string'?c:c.name,row=document.createElement('div');row.className='eeg-channel-row';row.dataset.name=name;
      const visible=document.createElement('input');visible.type='checkbox';visible.className='channel-visible';visible.checked=selectedChannels.includes(name);visible.setAttribute('aria-label',`${name} 显示`);
      visible.disabled=typeof c!=='string'&&!voltageTypes.has(c.type);
      const label=text('span',name);label.append(text('small',` ${c.type || ''}`));const rename=document.createElement('input');rename.type='text';rename.className='channel-rename';rename.value=renames[name]||name;rename.setAttribute('aria-label',`${name} 新名称`);
      const badLabel=text('label','坏道 '),bad=document.createElement('input');bad.type='checkbox';bad.className='channel-bad';bad.checked=bads.includes(name);badLabel.append(bad);row.append(visible,label,rename,badLabel);return row;
    }));$('channels-dialog').showModal();
  }
  if(writable){$('new-record').addEventListener('click',guard(async()=>{const title=$('record-title').value.trim();if(!title)throw new Error('请填写检查名称。');const r=await api(`${patientRoot}recordings/`,{method:'POST',json:{title,modality:$('record-modality').value}});await refresh();selectRecord((r.recording || r).id);$('record-title').value='';notice('检查已建立，可上传原始记录与辅助文件。');}));$('upload-start').addEventListener('click',guard(upload));}
  $('inspect').addEventListener('click',guard(async()=>{await submit('inspect',{});notice('正在读取文件头、通道和采样信息…');}));
  document.querySelectorAll('[data-operation]').forEach(b=>b.addEventListener('click',guard(()=>analyse(b.dataset.operation))));
  ['load-wave','apply-filters'].forEach(id=>$(id).addEventListener('click',guard(requestWave)));
  $('previous-window').addEventListener('click',guard(async()=>{$('window-start').value=Math.max(0,number('window-start')-number('window-duration',10));await requestWave();}));
  $('next-window').addEventListener('click',guard(async()=>{$('window-start').value=number('window-start')+number('window-duration',10);await requestWave();}));
  $('time-slider').addEventListener('change',guard(async()=>{$('window-start').value=$('time-slider').value;await requestWave();}));
  $('wave-scale').addEventListener('change',drawWave);['segment-start','segment-duration'].forEach(id=>$(id).addEventListener('input',drawWave));
  $('eeg-wave').addEventListener('pointerdown',e=>{if(!wave)return;dragStart=canvasTime(e);$('eeg-wave').setPointerCapture(e.pointerId);});
  $('eeg-wave').addEventListener('pointermove',e=>{if(dragStart===null)return;const end=canvasTime(e);$('segment-start').value=Math.min(dragStart,end).toFixed(3);$('segment-duration').value=Math.max(.01,Math.abs(end-dragStart)).toFixed(3);drawWave();});
  $('eeg-wave').addEventListener('pointerup',()=>{dragStart=null;});
  let wheelTimer;
  $('eeg-wave').addEventListener('wheel',e=>{if(!wave)return;e.preventDefault();const rect=$('eeg-wave').getBoundingClientRect();if(e.ctrlKey){const sel=$('wave-scale');sel.selectedIndex=Math.max(0,Math.min(sel.options.length-1,sel.selectedIndex+(e.deltaY>0?1:-1)));drawWave();}else if(e.clientX-rect.left<70){channelOffset=Math.max(0,Math.min(wave.data_uv.length-1,channelOffset+(e.deltaY>0?1:-1)));drawWave();}else{$('window-start').value=Math.max(0,number('window-start')+(e.deltaY>0?1:-1)*number('window-duration',10)/2);clearTimeout(wheelTimer);wheelTimer=setTimeout(guard(requestWave),350);}},{passive:false});
  $('channels-open').addEventListener('click',channelDialog);
  $('channels-all').addEventListener('click',()=>{let n=0;document.querySelectorAll('.channel-visible').forEach(c=>{c.checked=!c.disabled&&n<32;if(c.checked)n++;});});
  $('channels-invert').addEventListener('click',()=>{let n=0;document.querySelectorAll('.channel-visible').forEach(c=>{c.checked=!c.disabled&&!c.checked&&n<32;if(c.checked)n++;});});
  $('channels-save').addEventListener('click',guard(async()=>{const rows=[...document.querySelectorAll('.eeg-channel-row')];selectedChannels=rows.filter(r=>r.querySelector('.channel-visible').checked).map(r=>r.dataset.name);if(!selectedChannels.length||selectedChannels.length>32)throw new Error('请选择 1–32 个显示通道。');bads=rows.filter(r=>r.querySelector('.channel-bad').checked).map(r=>r.dataset.name);renames={};rows.forEach(r=>{const name=r.querySelector('.channel-rename').value.trim();if(name!==r.dataset.name)renames[r.dataset.name]=name;});$('channels-dialog').close();await requestWave();}));
  let gDrag=null;
  $('eeg-geometry').addEventListener('pointerdown',e=>{gDrag=[e.clientX,e.clientY];$('eeg-geometry').setPointerCapture(e.pointerId);});
  $('eeg-geometry').addEventListener('pointermove',e=>{if(!gDrag)return;geometryYaw+=(e.clientX-gDrag[0])*.01;geometryPitch+=(e.clientY-gDrag[1])*.01;gDrag=[e.clientX,e.clientY];drawGeometry();});
  $('eeg-geometry').addEventListener('pointerup',()=>gDrag=null);
  $('eeg-geometry').addEventListener('wheel',e=>{e.preventDefault();geometryZoom=Math.max(.4,Math.min(4,geometryZoom*(e.deltaY>0?.9:1.1)));drawGeometry();},{passive:false});
  $('eeg-geometry').addEventListener('dblclick',()=>{geometryYaw=.5;geometryPitch=-.2;geometryZoom=1;drawGeometry();});
  window.addEventListener('resize',()=>{drawWave();if(geometry)drawGeometry();});
  const methodHelp={LCMV:'时域波束形成：需要足够的有效样本估计数据协方差。',DICS:'频域波束形成：使用频率范围内的交叉谱密度。',dipole:'非线性偶极子拟合：需要可用的导体模型；GOF 为拟合优度。',rap_music:'递归子空间定位：指定偶极子数量。',trap_music:'截断子空间定位：指定偶极子数量。',mixed_norm:'稀疏时空估计：α 越大，解越稀疏。',ir_mxne:'迭代重加权混合范数：比线性方法计算更慢。',tf_mixed_norm:'时频稀疏估计：需要足够长度的片段。',gamma_map:'贝叶斯稀疏估计：使用噪声协方差白化。'};
  $('source-method').addEventListener('change',()=>{$('algorithm-help').textContent=methodHelp[$('source-method').value] || '分布式最小范数估计：需要正向模型、有效电极位置与噪声基线。';});
  guard(async()=>{await refresh();notice(writable?'选择检查，或新建检查并上传 EEG / sEEG 数据。':'当前账户为只读，可以查看已有检查与分析结果。');})();
  setInterval(()=>{if(!document.hidden)guard(refresh)();},2500);
})();
