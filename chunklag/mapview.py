# -*- coding: utf-8 -*-
"""
HTML 交互地图渲染 —— 把分析数据画成一张可缩放/平移/悬停下钻的区块评分热力图。

渲染：Canvas + ImageData 逐像素（任意缩放都稳定，放大按区块聚合自然）。
交互：滚轮缩放(围绕光标)、拖拽平移、悬停看因子、TOP 列表面板点击定位。
"""
import json

from . import mapdata as mapdata_mod

# 因子 key → 中文 在 JS 里需要，mapdata.factor_labels() 提供。

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
    border-radius:10px;font-size:12px;max-width:260px;}
  #panel h1{font-size:14px;margin:0 0 6px;color:#89b4fa;}
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
  <div id="meta">区块数: <span id="total"></span> · 总卡顿分: <span id="tscore"></span></div>
  <div id="legend">
    <span>低</span><canvas id="bar" width="120" height="12"></canvas><span>高</span>
  </div>
  <div style="margin-top:6px;font-size:11px;color:#a6e3a1;">按住左键拖拽平移 · 滚轮缩放 · 悬停看因子</div>
  <button class="btn" id="reset">重置视图</button>
  <div id="info">着色按卡顿评分(启发式)分档；非真实 mspt。</div>
</div>

<div id="arrow"><h2>🔴 最卡 TOP 区块</h2><div class="list" id="toplist"></div></div>
<div id="tooltip"></div>

<script id="mapdata" type="application/json">__DATA__</script>
<script id="factorlabels" type="application/json">__LABELS__</script>
<script>
const DATA = JSON.parse(document.getElementById('mapdata').textContent);
const LABELS = JSON.parse(document.getElementById('factorlabels').textContent);
const B = DATA.bounds;

const canvas = document.getElementById('map');
const ctx = canvas.getContext('2d');
let W=0,H=0,D=1;                // D = devicePixelRatio 缩放
let scale=1, offX=0, offY=0;
const chunks = new Map();
for (const c of DATA.chunks) chunks.set(c.x*100000+c.z, c);

function resize(){
  W = window.innerWidth; H = window.innerHeight;
  D = Math.min(window.devicePixelRatio||1, 2);
  canvas.width = W*D; canvas.height = H*D;
}
function mapW(){return B? B.maxX-B.minX+1 : 1;}
function mapZ(){return B? B.maxZ-B.minZ+1 : 1;}
function fit(){
  scale = Math.min(W/mapW(), H/mapZ());
  scale = Math.max(Math.min(scale,20),0.25);
  offX = (W - mapW()*scale)/2;
  offY = (H - mapZ()*scale)/2;
  render();
}
function sx(cx){return (cx-B.minX+0.5)*scale + offX;}
function sy(cz){return (cz-B.minZ+0.5)*scale + offY;}

function lerp(a,b,t){return Math.round(a+(b-a)*t);}
function colorFor(score){
  if(score<=0) return [35,35,42];
  const q50=Math.max(DATA.q50,1), q90=Math.max(DATA.q90,q50+1);
  let t;
  if(score<=q50) t=0.35*score/q50;
  else if(score<=q90) t=0.35+0.4*(score-q50)/(q90-q50);
  else t=0.75+0.25*Math.min(1,(score-q90)/Math.max(1,q90*2));
  // 色带: 绿 -> 黄 -> 橙 -> 红
  const stops=[[70,200,110],[220,220,70],[255,170,40],[230,60,60]];
  const seg=Math.min(3, Math.floor(t/(1/3)));
  const ft=(t-(seg)*(1/3))*3;
  const a=stops[seg], b=stops[Math.min(3,seg+1)];
  return [lerp(a[0],b[0],ft), lerp(a[1],b[1],ft), lerp(a[2],b[2],ft)];
}

let pending=false;
function render(){
  if(!B) return;
  if(pending) return;
  pending=true;
  requestAnimationFrame(()=>{
    pending=false;
    const img = ctx.createImageData(W*D, H*D);
    const buf = img.data;
    for(let py=0; py<H*D; py++){
      const cssY = py/D;
      const cz = Math.floor((cssY-offY)/scale - 0.5 + B.minZ);
      const inz = cz>=B.minZ && cz<=B.maxZ;
      for(let px=0; px<W*D; px++){
        const i=(py*W*D+px)*4;
        if(!inz){ buf[i]=25;buf[i+1]=25;buf[i+2]=30;buf[i+3]=255; continue; }
        const cssX = px/D;
        const cx = Math.floor((cssX-offX)/scale - 0.5 + B.minX);
        const c = chunks.get(cx*100000+cz);
        let col = c? colorFor(c.s) : [40,40,50];
        buf[i]=col[0];buf[i+1]=col[1];buf[i+2]=col[2];buf[i+3]=255;
      }
    }
    ctx.putImageData(img,0,0);
    ctx.save();
    ctx.lineWidth=1.5*D; ctx.strokeStyle='rgba(255,255,255,.9)';
    for(const t of DATA.top){
      ctx.strokeRect(sx(t.x)*D, sy(t.z)*D, scale*D, scale*D);
    }
    ctx.restore();
  });
}

function renderBar(){
  const c=document.getElementById('bar').getContext('2d');
  const g=c.createLinearGradient(0,0,120,0);
  g.addColorStop(0,'rgb(70,200,110)');g.addColorStop(.5,'rgb(255,170,40)');
  g.addColorStop(1,'rgb(230,60,60)');
  c.fillStyle=g;c.fillRect(0,0,120,12);
}

// 缩放(围绕光标)
canvas.addEventListener('wheel',e=>{
  e.preventDefault();
  const mx=e.clientX, my=e.clientY;
  const factor = e.deltaY<0?1.15:1/1.15;
  const nx=Math.min(Math.max(scale*factor,0.05),80);
  // 保持光标在世界点不动
  const wx=(mx-offX)/scale + B.minX - 0.5;
  const wz=(my-offY)/scale + B.minZ - 0.5;
  offX = mx - (wx-B.minX+0.5)*nx;
  offY = my - (wz-B.minZ+0.5)*nx;
  scale=nx; render();
},{passive:false});

// 拖拽平移
let drag=false,lx=0,ly=0;
canvas.addEventListener('mousedown',e=>{drag=true;lx=e.clientX;ly=e.clientY;});
window.addEventListener('mouseup',()=>drag=false);
canvas.addEventListener('mousemove',e=>{
  if(drag){ offX+=e.clientX-lx; offY+=e.clientY-ly; lx=e.clientX; ly=e.clientY; render(); return; }
  // 悬停 tooltip
  const cx=Math.floor((e.clientX-offX)/scale - 0.5 + B.minX);
  const cz=Math.floor((e.clientY-offY)/scale - 0.5 + B.minZ);
  const c=chunks.get(cx*100000+cz);
  const tip=document.getElementById('tooltip');
  if(c){
    let html=`<b>区块 (${cx}, ${cz})</b> · 评分 <b style="color:#f38ba8">${c.s}</b>`;
    const keys=Object.keys(c.f);
    if(keys.length) html+='<br>'+keys.map(k=>`${LABELS[k]||k}: ${c.f[k]}`).join(' · ');
    else html+='<br>无卡顿因子';
    tip.innerHTML=html; tip.style.display='block';
    tip.style.left=Math.min(e.clientX+14, W-310)+'px';
    tip.style.top=Math.max(10,e.clientY+14)+'px';
  } else tip.style.display='none';
});

document.getElementById('reset').onclick=fit;
document.getElementById('total').textContent=DATA.total;
document.getElementById('tscore').textContent=DATA.top.length? '看 TOP':'--';

// TOP 列表
const tl=document.getElementById('toplist');
DATA.top.forEach((t,i)=>{
  const r=document.createElement('div'); r.className='row';
  r.innerHTML=`<span class="rank">${i+1}.</span>(${t.x}, ${t.z}) 评分 <b>${t.s}</b>`;
  r.onclick=()=>{
    offX = W/2 - (t.x-B.minX+0.5)*scale;
    offY = H/2 - (t.z-B.minZ+0.5)*scale;
    render();
  };
  tl.appendChild(r);
});

window.addEventListener('resize',()=>{resize();fit();});
resize(); fit(); renderBar();
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
