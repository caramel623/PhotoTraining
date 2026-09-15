"""Self-contained Chinese review client; no CDN, analytics or external requests."""
HTML = r"""<!doctype html>
<html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>車輛資料集 — 區網人工覆核</title>
<style>
body{font:16px system-ui;margin:20px;background:#f3f5f8;color:#172234}
button,input,select{font:inherit;padding:8px;margin:4px}button{cursor:pointer}
header{position:sticky;top:0;background:#f3f5f8;z-index:1;padding:8px}
#work:not([hidden]){display:grid;grid-template-columns:300px minmax(0,1fr);gap:20px}
aside{position:sticky;top:16px;align-self:start;background:white;border-radius:10px;padding:12px}
#groups{max-height:calc(100vh - 265px);overflow-y:auto;display:flex;flex-direction:column;gap:6px}
.group-item{text-align:left;border:1px solid #e0e5ed;background:white;border-radius:7px;margin:0;padding:12px}
.group-item strong{display:block;font-size:23px;letter-spacing:1px;color:#173e6c}
.group-item small{display:block;margin-top:5px;color:#536277}
.group-item[aria-current=true]{border-color:#146ac7;background:#e7f1ff;box-shadow:inset 4px 0 #146ac7}
.group-heading{background:white;border-left:6px solid #146ac7;padding:16px 20px;border-radius:8px;margin-bottom:12px}
#groupPlate{font-size:36px;line-height:1.2;letter-spacing:2px;margin:4px 0;color:#123e70;overflow-wrap:anywhere}
#groupDetail{color:#536277;margin-top:8px}.main-review{min-width:0}
@media(max-width:760px){#work:not([hidden]){grid-template-columns:1fr}aside{position:static}#groups{max-height:240px}#groupPlate{font-size:30px}header{position:static}}
#grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px}
.card{background:white;border:3px solid transparent;border-radius:8px;padding:8px;cursor:pointer}
.card.selected{border-color:#146ac7}.card img{width:100%;height:170px;object-fit:contain}
.card.selected{outline:3px solid #146ac7;outline-offset:2px}
.card[data-status="verified_not_same_vehicle"]{background:#fff0f0;border-color:#d32f2f}
.card[data-status="verified_not_same_vehicle"] p{color:#a51d1d;font-weight:700}
.card p{overflow-wrap:anywhere}#notice{color:#9b3612;white-space:pre-wrap}
dialog{border:0;padding:0;background:transparent;max-width:95vw;max-height:95vh}
dialog::backdrop{background:#000b}dialog img{max-width:90vw;max-height:90vh;object-fit:contain}
</style>
<h1>區網人工覆核</h1>
<p>僅限可信任區網。照片不會上傳至外部服務。單擊選取，雙擊放大；放大後點空白處或 Esc 關閉。</p>
<div id="login"><input id="token" type="text" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" placeholder="6 位數字驗證碼" autocomplete="off" aria-label="6 位數字驗證碼"><button id="connect">連線</button></div>
<p id="notice" role="status"></p>
<section id="work" hidden>
<aside aria-label="車輛群組列表"><h2>車輛群組</h2>
<div><button id="prevGroups">上一頁</button><button id="nextGroups">下一頁</button><span id="groupPage"></span></div>
<nav id="groups" aria-label="選取車輛群組"></nav></aside>
<div class="main-review">
<header>
<div class="group-heading"><span>目前覆核群組</span><h2 id="groupPlate">請選取群組</h2><div id="groupDetail"></div></div>
<button id="reload">重新整理</button><button id="confirm">確認整組為同一車輛（Enter）</button>
<div><button id="prevImages">上一頁照片</button><span id="count"></span><button id="nextImages">下一頁照片</button></div>
<div id="actions"><button data-status="verified_same_vehicle">同一車輛</button>
<button data-status="verified_not_same_vehicle">不同車輛</button><button data-status="uncertain">不確定</button>
<button data-status="excluded">排除</button><button id="plate">修改所選照片車牌</button></div>
</header><div id="grid"></div></div>
</section><dialog id="preview"><img alt="放大檢視"></dialog>
<script>
const el=id=>document.getElementById(id);
let token='', vehicle='', revision='', selected=null, gp=0, ip=0, totalGroups=0, totalImages=0;
let busy=false, urls=[], groupRows=[];
const labels={unreviewed:'尚未覆核',verified_same_vehicle:'同一車輛',verified_not_same_vehicle:'不同車輛',uncertain:'不確定',excluded:'已排除'};
const groupLabels={automatic_only:'尚未確認',partially_verified:'部分已覆核',verified:'已確認'};
async function api(body,photo=false){
 const response=await fetch('/api',{method:'POST',headers:{'Content-Type':'application/json','X-Review-Token':token},body:JSON.stringify(body)});
 if(!response.ok)throw Error(await response.text());
 return photo?response.blob():response.json();
}
async function run(fn){
 if(busy)return;busy=true;el('work').inert=true;el('notice').textContent='';
 try{await fn()}catch(e){el('notice').textContent=e.message}
 finally{busy=false;el('work').inert=false}
}
async function groups(wanted=vehicle){
 const data=await api({action:'groups',page:gp});groupRows=data.groups;totalGroups=data.total;
 el('groups').replaceChildren();
 for(const g of groupRows){const o=document.createElement('button');o.className='group-item';o.dataset.vehicle=g.vehicle_id;
 const plate=document.createElement('strong');plate.textContent=g.plate_normalized||'無車牌';
 const detail=document.createElement('small');detail.textContent=g.vehicle_id+' · '+g.image_count+' 張 · '+(groupLabels[g.verification]||g.verification);
 o.append(plate,detail);o.onclick=()=>run(async()=>{vehicle=g.vehicle_id;ip=0;highlightGroup();await images();o.blur()});el('groups').append(o)}
 vehicle=groupRows.some(g=>g.vehicle_id===wanted)?wanted:(groupRows[0]?.vehicle_id||'');
 highlightGroup();el('groupPage').textContent='第 '+(gp+1)+' 頁／共 '+totalGroups+' 組';el('prevGroups').disabled=gp===0;el('nextGroups').disabled=(gp+1)*100>=totalGroups;
 await images();
}
function highlightGroup(){
 const group=groupRows.find(g=>g.vehicle_id===vehicle);
 el('groupPlate').textContent=group?(group.plate_normalized||'無車牌'):'請選取群組';
 el('groupDetail').textContent=group?group.vehicle_id+' · '+group.image_count+' 張影像 · '+(groupLabels[group.verification]||group.verification):'';
 for(const button of el('groups').children){button.setAttribute('aria-current',String(button.dataset.vehicle===vehicle))}
 const active=el('groups').querySelector('[aria-current=true]');if(active)active.scrollIntoView({block:'nearest'});
}
async function images(){
 urls.forEach(URL.revokeObjectURL);urls=[];selected=null;el('grid').replaceChildren();
 if(!vehicle){el('count').textContent='沒有群組';return}
 const data=await api({action:'images',vehicle,page:ip});revision=data.revision;totalImages=data.total;
 el('count').textContent='共 '+data.total+' 張，第 '+(ip+1)+' 頁';
 el('prevImages').disabled=ip===0;el('nextImages').disabled=(ip+1)*100>=totalImages;
 for(const row of data.images){
  const card=document.createElement('article');card.className='card';card.tabIndex=0;
  card.dataset.status=row.review_status;
  const img=document.createElement('img');img.alt=row.original_filename;
  const caption=document.createElement('p');caption.textContent=row.original_filename+' — '+(labels[row.review_status]||row.review_status);
  card.append(img,caption);el('grid').append(card);
  card.onclick=()=>{document.querySelectorAll('.selected').forEach(x=>x.classList.remove('selected'));card.classList.add('selected');selected=row.image_id};
  card.ondblclick=()=>{if(!img.src)return;el('preview').querySelector('img').src=img.src;el('preview').showModal()};
  try{const blob=await api({action:'photo',vehicle,image:row.image_id,revision},true);const url=URL.createObjectURL(blob);urls.push(url);img.src=url}
  catch(e){img.alt='無法載入：'+e.message}
 }
}
async function confirm(){
 if(!vehicle)return;
 const current=vehicle, index=groupRows.findIndex(g=>g.vehicle_id===current);
 await api({action:'confirm',vehicle,revision});ip=0;
 // Refresh server state before selecting the next unconfirmed group.
 const data=await api({action:'groups',page:gp});
 const order=data.groups.slice(index+1).concat(data.groups.slice(0,index));
 const next=order.find(g=>g.verification!=='verified');
 if(next){await groups(next.vehicle_id)}
 else if((gp+1)*100<totalGroups){gp++;await groups('')}
 else{await groups(current);el('notice').textContent='此頁沒有其他待覆核群組。仍可選取已確認群組修改單張照片。'}
}
el('connect').onclick=()=>run(async()=>{token=el('token').value.trim();await groups();el('work').hidden=false;el('login').hidden=true;el('token').value=''});
el('reload').onclick=()=>run(()=>groups());
el('confirm').onclick=()=>run(confirm);
el('prevGroups').onclick=()=>run(async()=>{gp=Math.max(0,gp-1);ip=0;await groups('')});
el('nextGroups').onclick=()=>run(async()=>{gp++;ip=0;await groups('')});
el('prevImages').onclick=()=>run(async()=>{ip=Math.max(0,ip-1);await images()});
el('nextImages').onclick=()=>run(async()=>{ip++;await images()});
el('actions').onclick=e=>{if(e.target.dataset.status)run(async()=>{
 if(selected===null)throw Error('請先單擊選取照片');
 await api({action:'status',vehicle,image:selected,revision,status:e.target.dataset.status});await groups();
})};
el('plate').onclick=()=>run(async()=>{
 if(selected===null)throw Error('請先單擊選取照片');
 const plate=prompt('正確的車牌文字：');if(plate===null)return;
 await api({action:'plate',vehicle,image:selected,revision,plate});await groups();
});
document.addEventListener('keydown',e=>{
 if(e.key==='Enter'&&!e.repeat&&!el('preview').open&&!el('work').hidden&&!['INPUT','SELECT','TEXTAREA','BUTTON'].includes(e.target.tagName)){
 e.preventDefault();run(confirm)}
});
el('preview').onclick=e=>{if(e.target===el('preview'))el('preview').close()};
</script></html>"""
