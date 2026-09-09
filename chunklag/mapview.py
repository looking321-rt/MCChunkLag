# -*- coding: utf-8 -*-
"""
HTML 交互地图渲染 —— 把分析数据画成一张可缩放/平移/悬停下钻的区块评分热力图。

渲染方案（性能友好）：
  - 先构建一张**离屏位图**（每区块 1 像素），
  - 主 Canvas 每次用 GPU `drawImage` 缩放到视口（缩放/平移极快，不再逐像素遍历）。
交互：滚轮缩放(围绕光标)、拖拽平移、悬停看因子、实时坐标、TOP 红色标记。
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
  #panel{position:fixed;top:12px;left:12px;background:rgba(20,20,30,.85);padding:10px 14px;
    border-radius:10px;font-size:12px;max-width:280px;}
  #panel h1{font-size:14px;margin:0 0 6px;color:#89b4fa;}
  #coords{margin-top:6px;font-size:14px;color:#a6e3a1;font-weight:bold;}
  #legend{margin-top:6px;display:flex;align-items:center;gap:6px;font-size:11px;}
  #legend canvas{width:120px;height:12px;display:block;}
  #arrow{position:fixed;top:12px;right:12px;width:230px;background:rgba(20,20,30,.85);
    border-radius:10px;max-height:70vh;overflow:auto;font-size:12px;}
  #arrow h2{font-size:13px;margin:8px 12px;color:#f38ba8;}
  #arrow .row{padding:4px 12px;cursor:pointer;border-top:1px solid #222;}
  #arrow .row:hover{background:#2a2a3a;}
  #arrow .rank{color:#89b4fa;font-weight:bold;margin-right:6px;}
  #tooltip{position:fixed;pointer-events:none;background:rgba(15,15,22,.95);padding:8px 10px;
    border-radius:8px;font-size:12px;display:none;max-width:300px;line-height:1.5;}
  #tooltip b{color:#a6e3a1;}
  #info{color:#7f849c;font-size:11px;margin-top:8px;line-height:1.5;}
  .btn{background:#313244;border:0;color:#e5e5e5;padding:3px 8px;border-radius:6px;
    cursor:pointer;font-size:11px;margin-top:6px;margin-right:4px;}
  .btn:hover{background:#45475a;}
</style>
</head>
<body>
<div id="wrap"><canvas id="map"></canvas></div>

<div id="panel">
  <h1>MC 区块卡顿热力图</h1>
  <div id="meta">区块数: <span id="total"></span></div>
  <div id="coords">坐标: —</div>
  <div id="legend">
    <span>低</span><canvas id="bar" width="120" height="12"></canvas><span>高</span>
  </div>
  <div style="margin-top:6px;font-size:11px;color:#a6e3a1;">拖拽平移 · 滚轮缩放 · 悬停看因子</div>
  <button class="btn" id="reset">重置视图</button>
  <div id="info">着色按卡顿评分(启发式)分档；红块=最卡TOP标记；非真实mspt。</div>
</div>

<div id="arrow"><h2>🔴 最卡 TOP 区块</h2><div class="list" id="toplist"></div></div>
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
let W=0, H=0, DPR=1;
let scale=1, offX=0, offY=0;
const mapW = B.maxX-B.minX+1, mapZ = B.maxZ-B.minZ+1;
const chunks = new Map();
for(const c of DATA.chunks) chunks.set(c.x*100000+c.z, c);

function lerp(a,b,t){return Math.round(a+(b-a)*t);}
function colorFor(score){
  if(score<=0) return [40,40,48];
  const q50=Math.max(DATA.q50,1), q90=Math.max(DATA.q90,q50+1);
  let t;
  if(score<=q50) t=0.35*score/q50;
  else if(score<=q90) t=0.35+0.4*(score-q50)/(q90-q50);
  else t=0.75+0.25*Math.min(1,(score-q90)/Math.max(1,q90*2));
  const stops=[[70,200,110],[220,220,70],[255,170,40],[230,60,60]];
  const seg=Math.min(3,Math.floor(t*3)), ft=(t-seg/3)*3;
  const a=stops[seg], b=stops[Math.min(3,seg+1)];
  return [lerp(a[0],b[0],ft), lerp(a[1],b[1],ft), lerp(a[2],b[2],ft)];
}

// ---- 构建离屏位图（每区块 1 像素） ----
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

function resize(){
  W=window.innerWidth; H=window.innerHeight; DPR=Math.min(window.devicePixelRatio||1,2);
  canvas.width=W*DPR; canvas.height=H*DPR;
  canvas.style.width=W+'px'; canvas.style.height=H+'px';
  ctx.setTransform(DPR,0,0,DPR,0,0);
}
function fit(){
  scale = Math.min(W/mapW, H/mapZ);
  scale = Math.max(Math.min(scale,40), 0.05);
  offX = (W - mapW*scale)/2;
  offY = (H - mapZ*scale)/2;
  render();
}
let raf=0;
function render(){
  cancelAnimationFrame(raf);
  raf=requestAnimationFrame(()=>{
    ctx.imageSmoothingEnabled = false;   // 格状热力图，透明边界更清晰
    ctx.fillStyle='#1a1a22'; ctx.fillRect(0,0,W,H);
    ctx.drawImage(off, offX, offY, mapW*scale, mapZ*scale);
    // 网格线（放得够大时）
    if(scale>=12){
      ctx.strokeStyle='rgba(255,255,255,.10)'; ctx.lineWidth=1;
      const c0=Math.floor((0-offX)/scale+B.minX), c1=Math.ceil((W-offX)/scale+B.minX);
      const r0=Math.floor((0-offY)/scale+B.minZ), r1=Math.ceil((H-offY)/scale+B.minZ);
      ctx.beginPath();
      for(let cx=c0;cx<=c1;cx++){ctx.moveTo(sx(cx),0);ctx.lineTo(sx(cx),H);}
      for(let cz=r0;cz<=r1;cz++){ctx.moveTo(0,sy(cz));ctx.lineTo(W,sy(cz));}
      ctx.stroke();
    }
    // TOP 红色标记
    for(const t of DATA.top){
      ctx.fillStyle='rgba(255,60,60,.5)';
      ctx.fillRect(sx(t.x), sy(t.z), Math.max(scale,3), Math.max(scale,3));
    }
  });
}

function renderBar(){
  const c=document.getElementById('bar').getContext('2d');
  const g=c.createLinearGradient(0,0,120,0);
  g.addColorStop(0,'rgb(70,200,110)');g.addColorStop(.5,'rgb(255,170,40)');
  g.addColorStop(1,'rgb(230,60,60)');
  c.fillStyle=g;c.fillRect(0,0,120,12);
}

canvas.addEventListener('wheel',e=>{
  e.preventDefault();
  const mx=e.clientX, my=e.clientY;
  const f=e.deltaY<0?1.15:1/1.15;
  const nx=Math.min(Math.max(scale*f,0.05),80);
  const wx=(mx-offX)/scale + B.minX, wz=(my-offY)/scale + B.minZ;
  offX = mx - (wx-B.minX)*nx;
  offY = my - (wz-B.minZ)*nx;
  scale=nx; render();
},{passive:false});

let drag=false,lx=0,ly=0;
canvas.addEventListener('mousedown',e=>{drag=true;lx=e.clientX;ly=e.clientY;});
window.addEventListener('mouseup',()=>drag=false);
canvas.addEventListener('mousemove',e=>{
  if(drag){ offX+=e.clientX-lx; offY+=e.clientY-ly; lx=e.clientX; ly=e.clientY; render(); return; }
  const cx=Math.floor((e.clientX-offX)/scale+B.minX);
  const cz=Math.floor((e.clientY-offY)/scale+B.minZ);
  const c=chunks.get(cx*100000+cz);
  const info=document.getElementById('coords');
  if(c){ info.textContent=`区块 (${cx}, ${cz}) · 评分 ${c.s}`; }
  else { info.textContent=`区块 (${cx}, ${cz})`; }
  const tip=document.getElementById('tooltip');
  if(c){
    let html=`<b>区块 (${cx}, ${cz})</b> · 评分 <b style="color:#f38ba8">${c.s}</b>`;
    const keys=Object.keys(c.f);
    html += keys.length? '<br>'+keys.map(k=>`${LABELS[k]||k}: ${c.f[k]}`).join(' · ') : '<br>无卡顿因子';
    tip.innerHTML=html; tip.style.display='block';
    tip.style.left=Math.min(e.clientX+14, W-320)+'px';
    tip.style.top=Math.max(10,e.clientY+14)+'px';
  } else tip.style.display='none';
});

document.getElementById('reset').onclick=fit;
document.getElementById('total').textContent=DATA.total;

const tl=document.getElementById('toplist');
DATA.top.forEach((t,i)=>{
  const r=document.createElement('div'); r.className='row';
  r.innerHTML=`<span class="rank">${i+1}.</span>(${t.x}, ${t.z}) 评分 <b>${t.s}</b>`;
  r.onclick=()=>{ offX=W/2-(t.x-B.minX)*scale; offY=H/2-(t.z-B.minZ)*scale; render(); };
  tl.appendChild(r);
});

window.addEventListener('resize',()=>{resize();fit();});
resize(); fit(); renderBar();
}
</script>
</body>
</html>
"""


def render_html_map(result, out_path, top_n=20):
    """生成 HTML 交互地图到 out_path。"""
    data = mapdata_mod.build_map_data(result, top_n=top_n)
    labels = mapdata_mod.factor_labels()
    html = (_TEMPLATE
            .replace("__DATA__", json.dumps(data, ensure_ascii=False))
            .replace("__LABELS__", json.dumps(labels, ensure_ascii=False)))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path
