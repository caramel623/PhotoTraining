"""Self-contained Chinese review client; no CDN, analytics or external requests."""
HTML = r"""<!doctype html>
<html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>車輛資料集 — 區網人工覆核</title>
<style>
body{font:16px system-ui;margin:20px;background:#f3f5f8;color:#172234}
button,input,select{font:inherit;padding:8px;margin:4px}button{cursor:pointer}
header{position:sticky;top:0;background:#f3f5f8;z-index:1;padding:8px}
#grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px}
.card{background:white;border:3px solid transparent;border-radius:8px;padding:8px;cursor:pointer}
.card.selected{border-color:#146ac7}.card img{width:100%;height:170px;object-fit:contain}
.card p{overflow-wrap:anywhere}#notice{color:#9b3612;white-space:pre-wrap}
dialog{border:0;padding:0;background:transparent;max-width:95vw;max-height:95vh}
dialog::backdrop{background:#000b}dialog img{max-width:90vw;max-height:90vh;object-fit:contain}
</style>
<h1>區網人工覆核</h1>
<p>僅限可信任區網。照片不會上傳至外部服務。單擊選取，雙擊放大；放大後點空白處或 Esc 關閉。</p>
<div id="login"><input id="token" type="password" placeholder="桌面設定顯示的存取碼" autocomplete="off"><button id="connect">連線</button></div>
<p id="notice" role="status"></p>
<section id="work" hidden>
<header>
<button id="prevGroups">上一頁群組</button><select id="groups"></select><button id="nextGroups">下一頁群組</button>
<button id="reload">重新整理</button><button id="confirm">確認整組為同一車輛（Enter）</button>
<div><button id="prevImages">上一頁照片</button><span id="count"></span><button id="nextImages">下一頁照片</button></div>
<div id="actions"><button data-status="verified_same_vehicle">同一車輛</button>
<button data-status="verified_not_same_vehicle">不同車輛</button><button data-status="uncertain">不確定</button>
<button data-status="excluded">排除</button><button id="plate">修改所選照片車牌</button></div>
</header><div id="grid"></div>
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
 for(const g of groupRows){const o=document.createElement('option');o.value=g.vehicle_id;
 o.textContent=(g.plate_normalized||'無車牌')+' · '+g.image_count+' 張 · '+(groupLabels[g.verification]||g.verification);el('groups').append(o)}
 vehicle=groupRows.some(g=>g.vehicle_id===wanted)?wanted:(groupRows[0]?.vehicle_id||'');
 el('groups').value=vehicle;el('prevGroups').disabled=gp===0;el('nextGroups').disabled=(gp+1)*100>=totalGroups;
 await images();
}
async function images(){
 urls.forEach(URL.revokeObjectURL);urls=[];selected=null;el('grid').replaceChildren();
 if(!vehicle){el('count').textContent='沒有群組';return}
 const data=await api({action:'images',vehicle,page:ip});revision=data.revision;totalImages=data.total;
 el('count').textContent='共 '+data.total+' 張，第 '+(ip+1)+' 頁';
 el('prevImages').disabled=ip===0;el('nextImages').disabled=(ip+1)*100>=totalImages;
 for(const row of data.images){
  const card=document.createElement('article');card.className='card';card.tabIndex=0;
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
el('groups').onchange=()=>run(async()=>{vehicle=el('groups').value;ip=0;await images()});
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
