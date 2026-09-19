// One historical workout, replayed by recorded elapsed time. The real basemap requests only visible OpenStreetMap tiles.
if (htmlNode.__burroCleanup) htmlNode.__burroCleanup();
(() => {
  const root=htmlNode.querySelector('.br'), el=id=>htmlNode.getElementById(id);
  const media=window.matchMedia('(prefers-reduced-motion: reduce)');
  const state={race:null,points:[],pauses:[],xy:[],time:0,speed:60,playing:false,disposed:false,last:0,signature:''};
  const cleanups=[]; let raf=0, mapZoom=null, fitZoom=14, mapKey='', lastPaint=0;
  const number=v=>typeof v==='number'&&Number.isFinite(v);
  const text=(id,v)=>{el(id).textContent=String(v);};
  const clock=s=>{if(!number(s))return '—';s=Math.max(0,Math.round(s));const h=Math.floor(s/3600),m=Math.floor(s%3600/60),sec=s%60;return (h?h+':':'')+String(m).padStart(h?2:1,'0')+':'+String(sec).padStart(2,'0');};
  const listen=(node,event,fn)=>{node.addEventListener(event,fn);cleanups.push(()=>node.removeEventListener(event,fn));};
  const svg=(tag,attrs,parent)=>{const n=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,String(v));if(parent)parent.appendChild(n);return n;};
  const label=(parent,x,y,value,attrs={})=>{const n=svg('text',{x,y,fill:'#99b4aa','font-size':10,...attrs},parent);n.textContent=value;return n;};
  function rows(frame){
    if(!frame?.fields?.length)return [];
    const length=frame.length??frame.fields[0].values?.length??0;
    if(!Number.isInteger(length)||length<0)return [];
    // Grafana versions expose field values as either arrays or vector objects.
    return Array.from({length},(_,i)=>Object.fromEntries(frame.fields.map(f=>[f.name,typeof f.values?.get==='function'?f.values.get(i):f.values?.[i]??null])));
  }
  function bracket(a,t,key='elapsed_s'){let lo=0,hi=a.length;while(lo<hi){const mid=(lo+hi)>>1;if(a[mid][key]<=t)lo=mid+1;else hi=mid;}return Math.max(0,lo-1);}
  const sprite=htmlGraphics.customProperties?.sprite;
  if(typeof sprite==='string'&&sprite.startsWith('data:image/png;base64,')){el('runner-marker').querySelector('image').setAttribute('href',sprite);}
  function play(value){state.playing=Boolean(value&&state.race);root.classList.toggle('paused',!state.playing);text('play',state.playing?'Ⅱ Pause':'▶ Play');el('play').setAttribute('aria-label',state.playing?'Pause race replay':'Play race replay');el('play').setAttribute('aria-pressed',String(state.playing));state.last=performance.now();}
  function drawRoute(){
    const pts=state.points,group=el('course-markers');group.replaceChildren();
    text('no-route',pts.length?'':'No GPS route in this workout');
    el('runner-marker').style.display=pts.length?'':'none';
    if(!pts.length){el('map-tiles').replaceChildren();el('map-attribution').hidden=true;mapKey='';for(const id of ['route-shadow','route-base','route-progress','route-progress-glow'])el(id).setAttribute('d','');return;}
    const lat0=pts.reduce((sum,p)=>sum+p.lat,0)/pts.length;
    const lonOrigin=(pts[0].lon+180)/360;
    const coords=pts.map(p=>{let x=(p.lon+180)/360;while(x-lonOrigin>.5)x-=1;while(x-lonOrigin<-.5)x+=1;const lat=Math.max(-85.05112878,Math.min(85.05112878,p.lat))*Math.PI/180;return {x,y:(1-Math.log(Math.tan(lat)+1/Math.cos(lat))/Math.PI)/2};});
    let minX=Infinity,maxX=-Infinity,minY=Infinity,maxY=-Infinity;coords.forEach(p=>{minX=Math.min(minX,p.x);maxX=Math.max(maxX,p.x);minY=Math.min(minY,p.y);maxY=Math.max(maxY,p.y);});
    fitZoom=Math.max(2,Math.min(18,Math.floor(Math.log2(Math.min(650/Math.max(maxX-minX,1e-8),280/Math.max(maxY-minY,1e-8))/256))));
    const zoom=mapZoom??fitZoom,world=256*2**zoom,left=(minX+maxX)/2*world-460,top=(minY+maxY)/2*world-245;
    state.xy=coords.map(p=>({x:p.x*world-left,y:p.y*world-top}));
    const scale=world/(40075016.686*Math.cos(lat0*Math.PI/180));
    const real=Number(state.race.synthetic)!==1;
    root.classList.toggle('real-map',real);el('map-attribution').hidden=!real;
    text('map-caption',real?'':'FICTIONAL COURSE · NO REAL LOCATION');
    text('route-kind',real?'OpenStreetMap · north up':'Fictional route · north up');
    const key=real?[zoom,left.toFixed(3),top.toFixed(3)].join(':'):'demo';
    if(key!==mapKey){
      mapKey=key;const tiles=el('map-tiles');tiles.replaceChildren();
      if(real){
        const count=2**zoom;
        const template=htmlGraphics.customProperties?.tileUrl||'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
        for(let x=Math.floor(left/256);x<=Math.floor((left+920)/256);x++)for(let y=Math.floor(top/256);y<=Math.floor((top+420)/256);y++){
          if(y<0||y>=count)continue;
          const url=template.replace('{z}',zoom).replace('{x}',((x%count)+count)%count).replace('{y}',y);
          if(!url.startsWith('https://'))continue;
          const tile=svg('image',{x:x*256-left,y:y*256-top,width:256,height:256,href:url,'data-tile':zoom+'/'+x+'/'+y},tiles);
          tile.addEventListener('error',()=>{if(!state.disposed&&mapKey===key){text('route-kind','Map tiles unavailable · GPS trace retained');}},{once:true});
        }
      }
    }
    const path=state.xy.map((p,i)=>`${i&&pts[i].segment===pts[i-1].segment?'L':'M'}${p.x.toFixed(2)} ${p.y.toFixed(2)}`).join(' ');
    el('route-base').setAttribute('d',path);el('route-shadow').setAttribute('d',path);
    // Checkpoints are derived from cumulative recorded GPS distance, not invented positions.
    let next=1000;for(let i=1;i<pts.length;i++){const a=pts[i-1],b=pts[i];while(b.distance_m>=next&&b.distance_m>a.distance_m){const f=(next-a.distance_m)/(b.distance_m-a.distance_m),aa=state.xy[i-1],bb=state.xy[i],p={x:aa.x+(bb.x-aa.x)*f,y:aa.y+(bb.y-aa.y)*f};svg('circle',{cx:p.x,cy:p.y,r:9,fill:'#173034',stroke:'#547e73'},group);label(group,p.x,p.y+3,String(next/1000),{'text-anchor':'middle','font-size':9});next+=1000;}}
    const first=state.xy[0],last=state.xy[state.xy.length-1];
    svg('circle',{cx:first.x,cy:first.y,r:6,fill:'#bfe1c1',stroke:'#102521','stroke-width':2},group);label(group,first.x+12,first.y+17,'START',{'font-size':9,fill:'#bfe1c1'});
    svg('rect',{x:last.x-4,y:last.y-4,width:8,height:8,fill:'#f2e7c6',stroke:'#102521'},group);label(group,last.x+12,last.y-10,'FINISH',{'font-size':9,fill:'#f2e7c6'});
    const meters=[2000,1000,500,200,100,50,20,10].find(m=>m*scale<=150)||10;el('scale-line').setAttribute('d',`M0 -4V0H${(meters*scale).toFixed(1)}V-4`);text('scale-label',meters>=1000?(meters/1000)+' km':meters+' m');
  }
  function render(){
    if(!state.race)return;const t=state.time,duration=Math.max(1,state.race.elapsed_s),pts=state.points;
    text('replay-clock',clock(t));el('seek').value=Math.round(t/duration*1000);el('seek').setAttribute('aria-valuetext',`${clock(t)} of ${clock(duration)}`);
    const watchPause=state.pauses.find(p=>t>=p.start_s&&t<p.end_s);
    // The recorded elapsed clock continues during a watch pause; the position does not.
    // Freeze at the pause's beginning, even if a GPX includes drifting samples inside it.
    const positionTime=watchPause?watchPause.start_s:t;
    const beforeRoute=Boolean(pts.length&&positionTime<pts[0].elapsed_s);
    const afterRoute=Boolean(pts.length&&positionTime>pts[pts.length-1].elapsed_s);
    let gap=false,idx=0;
    if(pts.length&&!beforeRoute){
      idx=bracket(pts,positionTime);const a=pts[idx],b=pts[Math.min(idx+1,pts.length-1)];
      const f=b.elapsed_s>a.elapsed_s?Math.max(0,Math.min(1,(positionTime-a.elapsed_s)/(b.elapsed_s-a.elapsed_s))):0;
      gap=a.segment!==b.segment&&positionTime<b.elapsed_s;
      const aa=state.xy[idx],bb=state.xy[Math.min(idx+1,state.xy.length-1)];
      const xy={x:aa.x+(bb.x-aa.x)*(gap?0:f),y:aa.y+(bb.y-aa.y)*(gap?0:f)};
      el('runner-marker').setAttribute('transform',`translate(${xy.x.toFixed(2)} ${xy.y.toFixed(2)})`);
      let d=state.xy.slice(0,idx+1).map((q,i)=>`${i&&pts[i].segment===pts[i-1].segment?'L':'M'}${q.x.toFixed(2)} ${q.y.toFixed(2)}`).join(' ');
      if(!gap)d+=` L${xy.x.toFixed(2)} ${xy.y.toFixed(2)}`;
      el('route-progress').setAttribute('d',d);el('route-progress-glow').setAttribute('d',d);
    }else{el('route-progress').setAttribute('d','');el('route-progress-glow').setAttribute('d','');}
    el('runner-marker').style.display=pts.length&&!beforeRoute?'':'none';
    root.classList.toggle('gap',Boolean(gap||watchPause||beforeRoute||afterRoute||!pts.length));
    const ended=t>=duration;
    const replayLabel=ended?'FINISHED':watchPause?'WATCH PAUSED':!pts.length?'NO GPS ROUTE':beforeRoute?'AWAITING GPS':afterRoute?'GPS ENDED':gap?'GPS GAP':state.playing?`REPLAYING · ${state.speed}×`:'PAUSED · SCRUB TO EXPLORE';
    text('replay-label',replayLabel);
  }
  function update(data){
    if(state.disposed)return;if(data?.error||data?.errors?.length){play(false);el('data-notice').hidden=false;el('data-notice').className='notice error';text('data-notice','Workout query failed. Last replay is paused; check the Grafana data source.');return;}
    const frames={};for(const f of data?.series||[])frames[f.refId]=rows(f);
    if(!frames.A?.length){if(data?.state!=='Loading'&&data?.state!=='NotStarted'){play(false);el('data-notice').hidden=false;el('data-notice').className='notice error';text('data-notice','No workout row is available. Import a workout or create the fictional preview, then refresh.');}return;}
    const race=frames.A[0];if(!number(race.elapsed_s)||race.elapsed_s<=0){play(false);el('data-notice').hidden=false;el('data-notice').className='notice error';text('data-notice','The workout duration is missing or invalid. Reimport the workout before replaying.');return;}
    state.race=race;
    state.points=(frames.B||[]).filter(p=>number(p.elapsed_s)&&p.elapsed_s>=0&&p.elapsed_s<=race.elapsed_s&&number(p.lat)&&Math.abs(p.lat)<=90&&number(p.lon)&&Math.abs(p.lon)<=180&&number(p.distance_m)&&p.distance_m>=0&&number(p.segment)).sort((a,b)=>a.elapsed_s-b.elapsed_s);
    state.pauses=[];try{const pauses=JSON.parse(race.pauses_json||'[]');if(Array.isArray(pauses))state.pauses=pauses.filter(p=>number(p.start_s)&&number(p.end_s)&&p.end_s>p.start_s).map(p=>({start_s:Math.max(0,p.start_s),end_s:Math.min(race.elapsed_s,p.end_s)})).filter(p=>p.end_s>p.start_s).sort((a,b)=>a.start_s-b.start_s);}catch{}
    const signature=JSON.stringify([race.start_time,race.source,race.synthetic,race.point_count,race.gps_distance_m,race.elapsed_s]);const changed=signature!==state.signature;state.signature=signature;if(changed){state.time=0;mapZoom=null;mapKey='';}
    root.classList.remove('waiting');const simulated=Number(race.synthetic)===1;
    el('data-badge').classList.toggle('recorded',!simulated);text('data-badge',simulated?'FICTIONAL PREVIEW':'RECORDED · LOCAL');
    let warnings=[];try{const notes=JSON.parse(race.warnings_json||'[]');if(Array.isArray(notes))warnings=notes.filter(note=>typeof note==='string');}catch{}
    el('data-notice').className='notice';el('data-notice').hidden=!simulated&&!warnings.length;
    text('data-notice',simulated?'Fictional course for preview. Import an Apple Watch workout to replay your recorded route.':warnings.join(' · '));
    text('race-subtitle',simulated?'A fictional trail preview':race.recorded_start_time||race.start_time||'Recorded course');
    text('replay-end',clock(race.elapsed_s));
    text('quality-note','Historical replay · local SQLite');
    let device={};try{const metadata=JSON.parse(race.device_metadata_json||'{}');device=metadata.exported_device||metadata;}catch{}
    const model=race.device_label||device.model||'Device not provided';
    const hardware=device.hardware||device.hardwareVersion,software=device.software||device.softwareVersion||race.source_version;
    const provenance=race.device_label?'user-provided model':'exported model';
    const exported=[device.model,hardware,software?'software '+software:null].filter(Boolean).join(' / ');
    text('device-details',`${model} (${provenance})${exported?' · Export: '+exported:''}`);
    el('device-details').title=race.source?'Recorded by '+race.source:'Export source unavailable';
    drawRoute();state.time=Math.min(state.time,race.elapsed_s);if(changed)play(!media.matches);render();
  }
  listen(el('map-in'),'click',()=>{mapZoom=Math.min(19,(mapZoom??fitZoom)+1);drawRoute();render();});listen(el('map-out'),'click',()=>{mapZoom=Math.max(2,(mapZoom??fitZoom)-1);drawRoute();render();});listen(el('map-fit'),'click',()=>{mapZoom=null;drawRoute();render();});
  listen(el('play'),'click',()=>{if(state.time>=state.race?.elapsed_s)state.time=0;play(!state.playing);render();});
  listen(el('restart'),'click',()=>{state.time=0;render();});listen(el('seek'),'input',()=>{play(false);state.time=Number(el('seek').value)/1000*(state.race?.elapsed_s||0);render();});listen(el('speed'),'change',()=>{state.speed=Number(el('speed').value);render();});listen(media,'change',event=>{if(event.matches){play(false);render();}});
  function tick(now){if(state.disposed)return;if(state.playing&&state.race&&!document.hidden){state.time=Math.min(state.race.elapsed_s,state.time+Math.min(.25,(now-state.last)/1000)*state.speed);if(state.time>=state.race.elapsed_s)play(false);if(now-lastPaint>=50||!state.playing){render();lastPaint=now;}}state.last=now;raf=requestAnimationFrame(tick);}
  function cleanup(){if(state.disposed)return;state.disposed=true;cancelAnimationFrame(raf);cleanups.forEach(fn=>fn());htmlNode.removeEventListener('panelwillunmount',cleanup);if(htmlNode.__burroUpdate===update)delete htmlNode.__burroUpdate;if(htmlNode.__burroCleanup===cleanup)delete htmlNode.__burroCleanup;}
  htmlNode.__burroCleanup=cleanup;htmlNode.__burroUpdate=update;htmlNode.addEventListener('panelwillunmount',cleanup);play(false);update(htmlGraphics.data);raf=requestAnimationFrame(tick);
})();
