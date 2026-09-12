# -*- coding: utf-8 -*-
"""
HTML 交互地图渲染 —— 借鉴 chunkbase 的成熟地图交互：
  - 离屏位图(每区块1像素) + GPU drawImage 缩放平移（流畅）
  - X/Z 坐标轴刻度（nice interval 自适应，仿 chunkbase）
  - 侧边栏坐标数据面板（视图中心/鼠标坐标+评分/视口范围/比例尺）
  - 右下角缩放控件（+/−/适配）
  - 悬停下钻 tooltip、点击选中区块、TOP 红色标记
"""
import json

from . import mapdata as mapdata_mod

_TEMPLATE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>MC 区块卡顿热力图</title>
<style>
  html,body{margin:0;height:100%;overflow:hidden;background:#12121a;color:#e5e5e5;
    font-family:Segoe UI,Microsoft YaHei,sans-serif;}
  #wrap{position:fixed;inset:0;}
  canvas{display:block;width:100%;height:100%;cursor:crosshair;}
  .card{position:fixed;background:rgba(18,18,26,.88);border-radius:10px;padding:10px 14px;
    font-size:12px;box-shadow:0 2px 12px rgba(0,0,0,.4);}
  #panel{top:12px;left:12px;max-width:280px;}
  #panel h1{font-size:14px;margin:0 0 6px;color:#89b4fa;}
  #legend{margin-top:8px;display:flex;align-items:center;gap:6px;font-size:11px;}
  #legend canvas{width:120px;height:12px;display:block;}
  #coordbar{bottom:12px;left:12px;line-height:1.7;min-width:230px;}
  #coordbar b{color:#89b4fa;}
  #coordbar .mouse{color:#a6e3a1;font-size:13px;}
  #coordrow{margin-top:6px;display:flex;gap:8px;align-items:center;}
  #scalebar{flex:1;height:5px;background:#333;border-radius:3px;position:relative;}
  #scalebar i{position:absolute;left:0;top:0;height:5px;background:#89b4fa;border-radius:3px;}
  #zoom{right:12px;bottom:12px;display:flex;flex-direction:column;gap:4px;}
  #zoom button{width:34px;height:34px;border:0;border-radius:8px;background:#313244;
    color:#e5e5e5;font-size:16px;cursor:pointer;}
  #zoom button:hover{background:#45475a;}
  #arrow{top:12px;right:12px;width:230px;max-height:70vh;overflow:auto;}
  #arrow h2{font-size:13px;margin:2px 0 6px;color:#f38ba8;}
  #arrow .row{padding:4px 0;cursor:pointer;border-top:1px solid #26262f;}
  #arrow .row:hover{color:#a6e3a1;}
  #arrow .rank{color:#89b4fa;font-weight:bold;margin-right:6px;}
  #tooltip{position:fixed;pointer-events:none;background:rgba(15,15,22,.95);padding:8px 10px;
    border-radius:8px;font-size:12px;display:none;max-width:300px;line-height:1.5;}
  #tooltip b{color:#a6e3a1;}
  .btn{background:#313244;border:0;color:#e5e5e5;padding:3px 8px;border-radius:6px;
    cursor:pointer;font-size:11px;margin-top:6px;margin-right:4px;}
  .btn:hover{background:#45475a;}
  #hint{color:#7f849c;font-size:11px;margin-top:8px;line-height:1.5;}
</style>
</head>
<body>
<div id="wrap"><canvas id="map"></canvas></div>

<div id="panel" class="card">
  <h1>MC 区块卡顿热力图</h1>
  <div id="total">区块数: —</div>
  <div id="pinfo"></div>
  <div id="legend"><span>低</span><canvas id="bar" width="120" height="12"></canvas><span>高</span></div>
  <div style="margin-top:8px;font-size:11px;color:#a6e3a1;">拖拽平移 · 滚轮缩放 · 悬停看因子 · 点击选中</div>
  <button class="btn" id="reset">适配视图</button>
  <div id="regionlegend" style="margin-top:6px;font-size:11px;line-height:1.7;"></div>
  <div id="hint">着色按卡顿分(启发式)；红块=最卡 TOP；非真实 mspt。</div>
</div>

<div id="coordbar" class="card">
  <div><b>视图中心</b> <span id="ccenter">(—, —)</span></div>
  <div><b>鼠标</b> <span id="cmouse" class="mouse">(—, —)</span></div>
  <div><b>范围</b> <span id="crange">—</span></div>
  <div id="coordrow">
    <b style="white-space:nowrap">比例尺</b>
    <div id="scalebar"><i style="width:50%"></i></div><b id="scaletext" style="width:64px">—</b>
  </div>
  <div id="sdetail" style="color:#a6e3a1;margin-top:4px;"></div>
</div>

<div id="zoom" class="card">
  <button id="zin">+</button><button id="zout">−</button><button id="zfit">⊡</button>
</div>

<div id="arrow" class="card"><h2>🔴 最卡 TOP 区块</h2><div id="toplist"></div></div>
<div id="tooltip"></div>

<script id="mapdata" type="application/json">__DATA__</script>
<script id="factorlabels" type="application/json">__LABELS__</script>
<script>
const DATA = JSON.parse(document.getElementById('mapdata').textContent);
const LABELS = JSON.parse(document.getElementById('factorlabels').textContent);
const B = DATA.bounds;
if(!B){ document.body.innerHTML='<h3 style="padding:30px">无区块数据</h3>'; }
else{
const canvas = document.getElementById('map');
const ctx = canvas.getContext('2d');
let W=0,H=0,DPR=1;
let scale=1, offX=0, offY=0;
let hover=null, selected=null;
const mapW = B.maxX-B.minX+1, mapZ = B.maxZ-B.minZ+1;
const chunks = new Map();
for(const c of DATA.chunks) chunks.set(c.x*100000+c.z, c);

function lerp(a,b,t){return Math.round(a+(b-a)*t);}
function colorFor(score){
  // 固定阈值离散 5 档：灰=0 · 绿<=5 · 黄<=20 · 橘<=60 · 红>60
  if(score<=0) return [50,50,58];
  if(score<=5) return [70,200,90];
  if(score<=20) return [230,220,60];
  if(score<=60) return [240,150,40];
  return [230,50,50];
}

const REGION_COLOR={spawn:'rgba(80,230,80,.95)',forced:'rgba(190,120,255,.95)',mod:'rgba(255,145,40,.95)',portal:'rgba(180,80,255,.95)',portal_core:'rgba(170,50,255,.95)',portal_red:'rgba(200,130,255,.75)',portal_lazy:'rgba(215,185,255,.5)'};
// ---- 离屏位图（每区块 1 像素） ----
const off = document.createElement('canvas');
off.width = mapW; off.height = mapZ;
const octx = off.getContext('2d');
octx.fillStyle = '#28282e'; octx.fillRect(0,0,mapW,mapZ);
for(const c of DATA.chunks){
  const col=colorFor(c.s);
  octx.fillStyle='rgb('+col[0]+','+col[1]+','+col[2]+')';
  octx.fillRect(c.x-B.minX, c.z-B.minZ, 1, 1);
}

function sx(cx){return (cx-B.minX)*scale + offX;}
function sy(cz){return (cz-B.minZ)*scale + offY;}

// 坐标换算：区块坐标 → 方块坐标（MC 游戏 F3 的 X/Z，1 区块=16 方块）
function bx(cx){return cx*16;}         // 区块左上角方块坐标
function bz(cz){return cz*16;}
function bcx(cx){return cx*16+8;}      // 区块中心方块坐标
function bcz(cz){return cz*16+8;}

function resize(){
  W=window.innerWidth; H=window.innerHeight; DPR=Math.min(window.devicePixelRatio||1,2);
  canvas.width=W*DPR; canvas.height=H*DPR;
  canvas.style.width=W+'px'; canvas.style.height=H+'px';
  ctx.setTransform(DPR,0,0,DPR,0,0);
}
function fit(){
  scale=Math.min(W/mapW,H/mapZ);
  scale=Math.max(Math.min(scale,80),1.6);
  offX=(W-mapW*scale)/2; offY=(H-mapZ*scale)/2;
  render();
}

// nice interval：让刻度间距约 90px
function niceInterval(){
  const blocks=90/scale; let pow=1;
  while(pow<blocks) pow*=2;
  const cands=[pow/2,pow,pow*2];
  let best=cands[0];
  for(const c of cands) if(Math.abs(c-blocks)<Math.abs(best-blocks)) best=c;
  return Math.max(1, best);
}

function worldToScreen(cx,cz){return [sx(cx),sy(cz)];}

// 坐标轴 + 比例尺 + 坐标面板
function drawChrome(){
  const iv=niceInterval();
  const cx0=Math.ceil(((0-offX)/scale+B.minX)/iv)*iv;
  const cx1=Math.floor(((W-offX)/scale+B.minX)/iv)*iv;
  const cz0=Math.ceil(((0-offY)/scale+B.minZ)/iv)*iv;
  const cz1=Math.floor(((H-offY)/scale+B.minZ)/iv)*iv;
  ctx.textBaseline='middle'; ctx.font='12px Segoe UI,Microsoft YaHei';
  ctx.fillStyle='rgba(0,0,0,.55)';
  // X 轴（顶部）—— 显示方块坐标（16 的倍数）
  for(let cx=cx0; cx<=cx1; cx+=iv){
    const x=sx(cx); if(x<-40||x>W+40) continue;
    const txt=String(bx(cx));
    const w=ctx.measureText(txt).width+12;
    ctx.fillRect(x-w/2,4,w,18);
    ctx.fillStyle='#fff'; ctx.fillText(txt,x,13); ctx.fillStyle='rgba(0,0,0,.55)';
  }
  // Z 轴（左侧）—— 显示方块坐标
  for(let cz=cz0; cz<=cz1; cz+=iv){
    const y=sy(cz); if(y<-40||y>H+40) continue;
    const txt=String(bz(cz));
    const w=ctx.measureText(txt).width+12;
    ctx.fillRect(4,y-9,w,18);
    ctx.fillStyle='#fff'; ctx.fillText(txt,10,y); ctx.fillStyle='rgba(0,0,0,.55)';
  }
  // 坐标面板（方块坐标为主，区块坐标括注）
  const ccx=Math.floor((W/2-offX)/scale+B.minX), ccz=Math.floor((H/2-offY)/scale+B.minZ);
  document.getElementById('ccenter').textContent=`方块(${bcx(ccx)}, ${bcz(ccz)}) · 区块(${ccx}, ${ccz})`;
  const x0=Math.floor((0-offX)/scale+B.minX), x1=Math.floor((W-offX)/scale+B.minX);
  const z0=Math.floor((0-offY)/scale+B.minZ), z1=Math.floor((H-offY)/scale+B.minZ);
  document.getElementById('crange').textContent=`方块X ${bx(x0)}…${bx(x1)+15} · Z ${bz(z0)}…${bz(z1)+15}`;
  // 比例尺：缩放区间 16~80 px/区块（1区块=16方块=16m → 1px = 1m ~ 0.2m）
  // 进度条 = 缩放级别（16px→0%，80px→100%）
  const SCALE_MIN=1.6, SCALE_MAX=80;
  const mPerPx=16/scale;
  const sdet=document.getElementById('scaletext');
  sdet.textContent = mPerPx>=1 ? ('1px='+mPerPx.toFixed(2)+'m') : ('1px='+Math.round(mPerPx*100)+'cm');
  const sbar=document.querySelector('#scalebar i');
  sbar.style.width = Math.round((scale-SCALE_MIN)/(SCALE_MAX-SCALE_MIN)*100) + '%';
}

function render(){
  requestAnimationFrame(()=>{
    ctx.imageSmoothingEnabled=false;
    ctx.fillStyle='#1a1a22'; ctx.fillRect(0,0,W,H);
    ctx.drawImage(off, offX, offY, mapW*scale, mapZ*scale);
    if(scale>=12){
      ctx.strokeStyle='rgba(255,255,255,.10)'; ctx.lineWidth=1;
      ctx.beginPath();
      const c0=Math.floor((0-offX)/scale+B.minX), c1=Math.ceil((W-offX)/scale+B.minX);
      const r0=Math.floor((0-offY)/scale+B.minZ), r1=Math.ceil((H-offY)/scale+B.minZ);
      for(let cx=c0;cx<=c1;cx++){ctx.moveTo(sx(cx),0);ctx.lineTo(sx(cx),H);}
      for(let cz=r0;cz<=r1;cz++){ctx.moveTo(0,sy(cz));ctx.lineTo(W,sy(cz));}
      ctx.stroke();
    }
    // 选中高亮
    if(selected){ ctx.strokeStyle='#fff'; ctx.lineWidth=2;
      ctx.strokeRect(sx(selected.x)-1, sy(selected.z)-1, scale+2, scale+2); }
    // 常加载区：每个来源画一个大外接框（不逐片填充/描边，避免跟卡顿色块混）
    if(DATA.regions){
      const lw=Math.max(2,Math.min(scale*0.35,6));
      for(const rg of DATA.regions){
        if(!rg.chunks || !rg.chunks.length) continue;
        let mnx=1e9,mxx=-1e9,mnz=1e9,mxz=-1e9;
        for(const p of rg.chunks){ const cx=p[0],cz=p[1];
          mnx=Math.min(mnx,cx);mxx=Math.max(mxx,cx);mnz=Math.min(mnz,cz);mxz=Math.max(mxz,cz); }
        const x0=sx(mnx)-lw, y0=sy(mnz)-lw, w=(mxx-mnx+1)*scale+2*lw, h=(mxz-mnz+1)*scale+2*lw;
        ctx.strokeStyle=REGION_COLOR[rg.type]||'#999'; ctx.lineWidth=lw;
        ctx.strokeRect(x0,y0,w,h);
      }
    }
    // TOP 红块（以区块为中心：尺寸下限 3px 在缩小视图下会大于一个区块，
    // 左对齐会单侧溢出盖住右/下邻居，看着像"红块盖到无卡顿区域"）
    for(const t of DATA.top){
      if(selected && t.x===selected.x && t.z===selected.z) continue;
      const msz=Math.max(scale,3), moff=(msz-scale)/2;
      ctx.fillStyle='rgba(255,60,60,.5)';
      ctx.fillRect(sx(t.x)-moff, sy(t.z)-moff, msz, msz);
    }
    // 玩家位置标记
    if(DATA.player){
      const ppx=sx(DATA.player.blockX/16), ppy=sy(DATA.player.blockZ/16);
      ctx.fillStyle='rgba(80,160,255,.35)'; ctx.strokeStyle='rgba(255,255,255,.95)';
      ctx.lineWidth=2; ctx.beginPath(); ctx.arc(ppx,ppy,10,0,Math.PI*2); ctx.fill(); ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(ppx-14,ppy);ctx.lineTo(ppx-4,ppy); ctx.moveTo(ppx+4,ppy);ctx.lineTo(ppx+14,ppy);
      ctx.moveTo(ppx,ppy-14);ctx.lineTo(ppx,ppy-4); ctx.moveTo(ppx,ppy+4);ctx.lineTo(ppx,ppy+14);
      ctx.stroke();
    }
    drawChrome();
  });
}

function renderBar(){
  const c=document.getElementById('bar').getContext('2d');
  const cols=['rgb(70,200,90)','rgb(230,220,60)','rgb(240,150,40)','rgb(230,50,50)'];
  for(let i=0;i<4;i++){ c.fillStyle=cols[i]; c.fillRect(i*30,0,30,12); }
}

canvas.addEventListener('wheel',e=>{
  e.preventDefault();
  const mx=e.clientX,my=e.clientY,f=e.deltaY<0?1.15:1/1.15;
  const nx=Math.min(Math.max(scale*f,1.6),80);
  const wx=(mx-offX)/scale+B.minX, wz=(my-offY)/scale+B.minZ;
  offX=mx-(wx-B.minX)*nx; offY=my-(wz-B.minZ)*nx; scale=nx; render();
},{passive:false});

let drag=false,lx=0,ly=0,sx0=0,sy0=0,moved=false;
canvas.addEventListener('mousedown',e=>{drag=true;moved=false;lx=sx0=e.clientX;ly=sy0=e.clientY;});
window.addEventListener('mouseup',e=>{
  if(drag && !moved){
    // 点击选中
    const cx=Math.floor((e.clientX-offX)/scale+B.minX);
    const cz=Math.floor((e.clientY-offY)/scale+B.minZ);
    const c=chunks.get(cx*100000+cz);
    selected = c? {x:cx,z:cz} : null;
    render(); updateDetail(cx,cz);
  }
  drag=false;
});
canvas.addEventListener('mousemove',e=>{
  if(drag){
    const dx=e.clientX-lx, dy=e.clientY-ly;
    if(Math.abs(e.clientX-sx0)+Math.abs(e.clientY-sy0)>4) moved=true;
    offX+=dx; offY+=dy; lx=e.clientX; ly=e.clientY; render(); return;
  }
  const cx=Math.floor((e.clientX-offX)/scale+B.minX);
  const cz=Math.floor((e.clientY-offY)/scale+B.minZ);
  const c=chunks.get(cx*100000+cz);
  hover=c? {x:cx,z:cz} : null;
  document.getElementById('cmouse').textContent=`方块(${bcx(cx)}, ${bcz(cz)})${c? ' · 评分 '+c.s : ''}`;
  const tip=document.getElementById('tooltip');
  if(c){
    let html=`<b>方块 (${bcx(cx)}, ${bcz(cz)})</b><br>区块 (${cx}, ${cz}) · 评分 <b style="color:#f38ba8">${c.s}</b>`;
    const keys=Object.keys(c.f);
    html += keys.length? '<br>'+keys.map(k=>`${LABELS[k]||k}: ${c.f[k]}`).join(' · ') : '<br>无卡顿因子';
    tip.innerHTML=html; tip.style.display='block';
    tip.style.left=Math.min(e.clientX+14, W-320)+'px';
    tip.style.top=Math.max(10,e.clientY+14)+'px';
  } else tip.style.display='none';
});

function updateDetail(cx,cz){
  const c=chunks.get(cx*100000+cz);
  const sd=document.getElementById('sdetail');
  if(c){
    const keys=Object.keys(c.f);
    sd.textContent = '选中 方块('+bcx(cx)+','+bcz(cz)+') · 区块('+cx+','+cz+') · 评分 '+c.s + (keys.length? ' | '+keys.map(k=>LABELS[k]+':'+c.f[k]).join(' '):' | 无因子');
  } else sd.textContent='';
}

document.getElementById('reset').onclick=fit;
document.getElementById('zin').onclick=()=>{zoomAround(W/2,H/2,1.25);};
document.getElementById('zout').onclick=()=>{zoomAround(W/2,H/2,0.8);};
document.getElementById('zfit').onclick=fit;
function zoomAround(mx,my,f){
  const nx=Math.min(Math.max(scale*f,1.6),80);
  const wx=(mx-offX)/scale+B.minX, wz=(my-offY)/scale+B.minZ;
  offX=mx-(wx-B.minX)*nx; offY=my-(wz-B.minZ)*nx; scale=nx; render();
}
    if(DATA.player){
      const p=DATA.player;
      document.getElementById('pinfo').innerHTML =
        `玩家 方块(${Math.round(p.blockX)}, ${Math.round(p.blockZ)}) · 区块(${p.chunkX},${p.chunkZ})<br>`+
        `模拟距离 ${p.sim_dist} · 加载区 ${p.load_width}×${p.load_width}区块 · 已加载 ${DATA.total} / 全部 ${DATA.total_all}`;
      document.getElementById('total').textContent='已加载区块: '+DATA.total;
    } else {
      document.getElementById('total').textContent='区块数: '+DATA.total;
    }
    const rl=document.getElementById('regionlegend');
    if(DATA.regions && DATA.regions.length){
      const seen=new Set();
      rl.innerHTML=DATA.regions.filter(rg=>{ if(seen.has(rg.label)) return false; seen.add(rg.label); return true; })
        .map(rg=>'<span style="display:inline-block;width:10px;height:10px;background:'+(REGION_COLOR[rg.type]||'#888')+';margin-right:4px"></span>'+rg.label)
        .join('<br>');
    }

const tl=document.getElementById('toplist');
DATA.top.forEach((t,i)=>{
  const r=document.createElement('div'); r.className='row';
  r.innerHTML=`<span class="rank">${i+1}.</span>方块(${bcx(t.x)}, ${bcz(t.z)}) · 区块(${t.x}, ${t.z}) 评分 <b>${t.s}</b>`;
  r.onclick=()=>{ offX=W/2-(t.x-B.minX)*scale; offY=H/2-(t.z-B.minZ)*scale; selected=t; render(); updateDetail(t.x,t.z); };
  tl.appendChild(r);
});

window.addEventListener('resize',()=>{resize();fit();});
resize(); fit(); renderBar();
}
</script>
</body>
</html>
"""


def render_html_map(result_or_data, out_path, top_n=20):
    """生成 HTML 交互地图到 out_path。接受 result 或已构建的数据 dict。"""
    if isinstance(result_or_data, dict) and "chunks" in result_or_data:
        data = result_or_data
    else:
        data = mapdata_mod.build_map_data(result_or_data, top_n=top_n)
    labels = mapdata_mod.factor_labels()
    html = (_TEMPLATE
            .replace("__DATA__", json.dumps(data, ensure_ascii=False))
            .replace("__LABELS__", json.dumps(labels, ensure_ascii=False)))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path
