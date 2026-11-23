const $=id=>document.getElementById(id), root=document.documentElement, app=$("app");
const api=()=>window.pywebview&&window.pywebview.api;
const modes=["Light","System","Dark"];
/* ---------- 多語言：文字由 Python 傳來（locales/<語言>/*.json 合併），t("代號",{變數}) 取字 ---------- */
let I18N={},LANG="zh-TW";
const LANGS=[["zh-TW","繁體中文"],["zh-CN","简体中文"],["en","English"],["ja","日本語"]];
const HTML_LANG={"zh-TW":"zh-Hant","zh-CN":"zh-Hans",en:"en",ja:"ja"};
const t=(k,v)=>{let s=I18N[k]??k;if(v)for(const n in v)s=s.split("{"+n+"}").join(v[n]);return s};
const setState=k=>{const e=$("state");e.dataset.k=k;e.textContent=t(k)};
function applyI18n(){
  root.lang=HTML_LANG[LANG]||"zh-Hant";
  const each=(attr,fn)=>document.querySelectorAll("["+attr+"]").forEach(e=>{const v=I18N[e.getAttribute(attr)];if(v!==undefined)fn(e,v)});
  each("data-i18n",(e,v)=>e.textContent=v);
  each("data-i18n-ph",(e,v)=>e.placeholder=v);
  each("data-i18n-aria",(e,v)=>e.setAttribute("aria-label",v));
  each("data-i18n-title",(e,v)=>e.title=v);
  const st=$("state");if(I18N[st.dataset.k]!==undefined)st.textContent=I18N[st.dataset.k];
  const first=$("lang").options[0];if(first&&I18N["lang.auto"]!==undefined)first.textContent=I18N["lang.auto"];
}
function buildLang(setting){
  const s=$("lang");s.innerHTML="";s.append(new Option(t("lang.auto"),"auto"));
  LANGS.forEach(([c,n])=>s.append(new Option(n,c)));s.value=setting;
}
$("lang").onchange=async()=>{
  if(!api())return;
  const r=await api().set_language($("lang").value);
  I18N=r.strings;LANG=r.lang;applyI18n();
};
let ready=false,running=false,cells=[],timer=null,cookieFile="",defaultDir="Download";

/* ---------- 主題 ---------- */
const seg=$("seg");
function setMode(m,save){
  m==="System"?root.removeAttribute("data-theme"):root.setAttribute("data-theme",m.toLowerCase());
  seg.querySelector("i").style.transform=`translateX(${modes.indexOf(m)*100}%)`;
  seg.querySelectorAll("button").forEach(b=>b.setAttribute("aria-pressed",b.textContent===m));
  if(save)persist({appearance:m});
}
seg.querySelectorAll("button").forEach(b=>b.onclick=()=>setMode(b.textContent,true));
setMode("System");

/* ---------- 視窗控制 ---------- */
$("wmin").onclick=()=>api()&&api().win_minimize();
$("wmax").onclick=()=>api()&&api().win_toggle_maximize();
let closing=false;
$("wclose").onclick=()=>{if(!api()||closing)return;closing=true;app.style.removeProperty("--wt");app.classList.add("closing");api().win_close()};
// 標題列拖曳視窗由 Python 處理（不用 pywebview 的拖曳區）；最大化時不拖曳（要移動請先還原）
// 原生移動迴圈會吃掉放開滑鼠的事件，dblclick 不可靠，所以雙擊改用兩次「按下」的間隔自己判斷（DBL_MS 毫秒內）
const DBL_MS=160;let lastDown={t:0,x:0,y:0};
$("drag").onmousedown=e=>{
  if(e.button!==0||!api())return;
  e.preventDefault();
  const now=performance.now();
  if(now-lastDown.t<=DBL_MS&&Math.abs(e.screenX-lastDown.x)<=4&&Math.abs(e.screenY-lastDown.y)<=4){
    lastDown.t=0;api().win_toggle_maximize();return}  // 雙擊：最大化／還原，不啟動拖曳
  lastDown={t:now,x:e.screenX,y:e.screenY};
  if(app.dataset.max!=="1")api().win_start_drag()};
document.querySelectorAll(".rs").forEach(h=>h.onmousedown=e=>{e.preventDefault();api()&&api().win_start_resize(h.dataset.e)});

/* ---------- 小工具 ---------- */
const log=(t,c="")=>{const d=document.createElement("div");d.className=c;d.textContent=t;$("log").append(d);$("log").scrollTop=1e9;
  while($("log").childElementCount>1500)$("log").firstChild.remove()};
const toast=t=>{const e=$("toast");e.textContent=t;e.classList.add("show");setTimeout(()=>e.classList.remove("show"),2200)};
const shake=()=>{const f=$("url");f.classList.remove("shake");void f.offsetWidth;f.classList.add("shake");f.focus()};
const frags=()=>$("boost").checked?+$("frag").value:1;
function setSel(id,v){const s=$(id);if(![...s.options].some(o=>o.value===v)){const o=document.createElement("option");o.textContent=v;s.append(o)}s.value=v}
function syncClear(){$("urlclr").classList.toggle("on",!!$("url").value&&!running);$("ckclr").classList.toggle("on",!!cookieFile&&!running)}

/* ---------- 彈窗 ---------- */
let modalDone=null,modalOpener=null,modalLocked=false;  // modalLocked：安裝進行中，Esc、點背景、關閉鈕都不能關掉彈窗
function closeModal(res){
  if(!modalDone)return;
  const done=modalDone;modalDone=null;modalLocked=false;$("mx").hidden=false;
  $("ov").classList.remove("show");$("ov").setAttribute("aria-hidden","true");
  $("app").inert=false;  // 背景恢復可操作，焦點回到開啟彈窗前的位置
  if(modalOpener&&modalOpener.isConnected)modalOpener.focus();
  modalOpener=null;
  done(res);
}
function askModal({title,body="",code="",note="",node=null,wide=false,input=null,locked=false,buttons=[]}){
  return new Promise(resolve=>{
    modalDone=resolve;modalLocked=locked;$("mx").hidden=locked;
    modalOpener=document.activeElement&&document.activeElement!==document.body?document.activeElement:null;
    $("dlg").classList.toggle("wide",wide);
    $("mt").textContent=title;$("mb").textContent=body;$("mb").hidden=!body;
    $("mc").textContent=code;$("mc").hidden=!code;$("mn").textContent=note;$("mn").hidden=!note;
    $("mnode").replaceChildren(...(node?[node]:[]));
    const mi=$("mi");mi.hidden=!input;mi.value="";if(input)mi.placeholder=input;
    const box=$("mbtns");box.innerHTML="";
    buttons.forEach(b=>{const e=document.createElement("button");e.type="button";e.className="btn"+(b.primary?" primary":"")+(b.danger?" danger":"");
      e.textContent=b.label;
      e.onclick=b.keep?()=>b.keep(e):()=>closeModal({id:b.id,value:mi.value.trim()});  // keep：按了不關閉彈窗（例如複製）
      box.append(e)});
    $("ov").classList.add("show");$("ov").setAttribute("aria-hidden","false");
    $("app").inert=true;  // 彈窗開著時 Tab 不能跑到背景的控制項
    setTimeout(()=>(box.querySelector(".primary")||(input?mi:locked?$("dlg"):$("mx"))).focus(),50);
  });
}
async function copyText(t){
  try{await navigator.clipboard.writeText(t);return true}catch(_){}
  const a=document.createElement("textarea");a.value=t;document.body.append(a);a.select();
  let ok=false;try{ok=document.execCommand("copy")}catch(_){}a.remove();return ok;
}
const copyButton=(label,text)=>({label,keep:async e=>{const ok=await copyText(text);e.textContent=ok?t("copy.done"):t("copy.fail");setTimeout(()=>e.textContent=label,2000)}});
const dismiss=()=>{if(!modalLocked)closeModal({id:"cancel"})};
$("mx").onclick=dismiss;
$("ov").onmousedown=e=>{if(e.target===$("ov"))dismiss()};
document.addEventListener("keydown",e=>{if(e.key==="Escape")dismiss()});
// 勾選框原生只吃空白鍵；Enter 也能切換（長按連發不算，避免一直來回切）
document.addEventListener("keydown",e=>{
  if(e.key==="Enter"&&!e.repeat&&e.target.matches&&e.target.matches('.chk input[type="checkbox"]')){e.preventDefault();e.target.click()}
});

/* ---------- 設定 ---------- */
let saveT=null;
const FIELDS={fmt:["fmt","v"],quality:["qual","v"],cookie_browser:["ck","v"],number:["num","c"],archive:["arch","c"],boost:["boost","c"],
  frags:["frag","n"],subs:["subs","c"],embed:["embed","c"],thumb:["thumb","c"],meta:["meta","c"],sub_langs:["langs","v"],
  open_file:["ofile","c"],open_folder:["ofolder","c"]};
function collect(){
  const o={};for(const k in FIELDS){const [id,t]=FIELDS[k],e=$(id);o[k]=t==="c"?e.checked:t==="n"?+e.value:e.value}
  return o;
}
const payload=()=>({...collect(),url:$("url").value.trim(),cookie_file:cookieFile,outdir:$("dir").value});
function persist(extra){
  if(!ready||!api())return;
  clearTimeout(saveT);
  saveT=setTimeout(()=>api().save_settings({...collect(),outdir:$("dir").value,...extra}),250);
}
Object.values(FIELDS).forEach(([id])=>$(id).addEventListener(id==="langs"?"input":"change",()=>persist()));

/* ---------- 進度條 ---------- */
let fragDecided=false;
function decideFrag(f){  // 每支影片的第一筆進度決定一次：分片下載維持分段，不是（-N 不會生效）就合併成一條
  if(fragDecided||cells.length<2)return;
  fragDecided=true;$("lanes").classList.toggle("merged",!f);
}
function buildLanes(){
  const box=$("lanes");box.innerHTML="";cells=[];fragDecided=false;box.classList.remove("merged");
  for(let i=0;i<frags();i++){
    const l=document.createElement("div");l.className="lane";l.style.animationDelay=i*25+"ms";
    l.innerHTML="<b></b>";box.append(l);cells.push(l.firstChild);
  }
}
function paint(p){const n=cells.length;cells.forEach((c,i)=>c.style.width=Math.max(0,Math.min(100,(p/100*n-i)*100))+"%");$("pct").textContent=Math.round(p)+"%"}
$("boost").addEventListener("change",()=>{$("frag").disabled=!$("boost").checked;if(!running)buildLanes()});
$("frag").addEventListener("change",()=>{if(!running)buildLanes()});
buildLanes();

function busy(on){
  running=on;
  ["go","info","url","batch","ckpick","pick","dirclr","example","upd"].forEach(id=>$(id).disabled=on);
  $("stop").disabled=!on;
  if(on)setState("state.running");
  syncClear();
}

/* ---------- Python → 網頁 ---------- */
window.__py=e=>{
  if(e.type==="log")log(e.text,e.cls||"");
  else if(e.type==="progress"){if(e.frag!==undefined)decideFrag(e.frag);if(e.pct!=null)paint(e.pct);$("speed").textContent=e.speed?(e.speed+(e.eta?t("speed.eta",{eta:e.eta}):"")):"—"}
  else if(e.type==="state"){busy(e.busy);if(e.busy){buildLanes();paint(0)}}
  else if(e.type==="video_start")fragDecided=false;  // 沿用目前樣式，等這支影片的第一筆進度再決定，避免每支影片都閃一下
  else if(e.type==="win"){app.dataset.max=e.maximized?"1":"0";settle();applyScale()}
  else if(e.type==="winhide"){  // 最小化：下移並縮小 5% 後淡出；instant 表示已經被最小化了，直接藏起來等還原
    app.style.setProperty("--wt",(e.instant?0:e.seconds)+"s");app.classList.add("hiding");void app.offsetWidth}
  else if(e.type==="winshow"){  // 從工作列還原：由下方、縮小 5% 淡入並上移、放大回原樣
    app.style.setProperty("--wt",e.seconds+"s");app.classList.remove("hiding")}
  else if(e.type==="info_done"){$("info").disabled=false;$("go").disabled=false}
  else if(e.type==="done")onDone(e);
  else if(e.type==="diag")onDiag(e);
  else if(e.type==="update_done")onUpdateDone(e);
  else if(e.type==="install_prompt")showPrompt(e.items);
  else if(e.type==="install_progress")onInstallProgress(e);
  else if(e.type==="install_done")onInstallDone();
  else if(e.type==="install_fail")onInstallFail(e);
};
async function onDone(e){
  $("speed").textContent="—";
  if(e.ok){setState("state.done");paint(100);log(t("end.ok",{message:e.message}),"ok");toast(t("state.done"));return}
  if(e.cancelled){setState("state.cancelled");log(t("end.cancelled"));return}
  setState("state.error");log(t("end.ok",{message:e.message}));
  let body=e.message;
  if(e.failures&&e.failures.length>1){body+="\n\n"+e.failures.map(f=>`• ${f.url}\n  ${f.message}`).join("\n")+(e.more?"\n"+t("fail.more",{n:e.more}):"")}
  const ff=e.code==="ffmpeg";
  const r=await askModal({title:ff?t("modal.ffmissing"):t("modal.fail"),body,buttons:ff?[{id:"ff",label:t("ffmpeg.open"),primary:true}]:[]});
  if(r.id==="ff"&&api())api().open_ffmpeg_page();
}

/* ---------- 版面縮放與最大化過渡 ---------- */
function applyScale(){  // 視窗很大（含最大化）時整體放大，避免畫面太空；最多 1.3 倍
  const s=Math.min(1.3,Math.max(1,Math.min(innerWidth/1440,(innerHeight-54)/990)));  // 990 = 內容在不縮放時需要的高度，避免放大後超出畫面
  root.style.setProperty("--zoom",s.toFixed(3));
}
function settle(){const c=document.querySelector(".content");c.classList.remove("settle");void c.offsetWidth;c.classList.add("settle")}
window.addEventListener("resize",applyScale);applyScale();

/* ---------- 開始下載 ---------- */
function localPrepare(p){  // 瀏覽器預覽模式用的簡化判斷
  if(p.url.startsWith("file:")||!/[?&]list=/.test(p.url))return{ok:true,ask:null};
  return{ok:true,ask:"single",kind:/\/playlist/.test(p.url)?"playlist":"mixed",default_name:t("preview.playlist_name")};
}
async function askPlaylist(prep){
  if(prep.ask==="single"){
    const mixed=prep.kind==="mixed";
    const r=await askModal({title:t("playlist.title"),
      body:mixed?t("playlist.mixed.body"):t("playlist.pure.body"),
      input:t("playlist.input",{name:prep.default_name}),
      buttons:(mixed?[{id:"single",label:t("playlist.btn.single"),primary:true}]:[]).concat([{id:"playlist",label:t("playlist.btn.all"),primary:!mixed}])});
    return r.id==="cancel"?null:{mode:r.id,name:r.value};
  }
  const r=await askModal({title:t("playlist.title"),
    body:t("playlist.batch.body",{total:prep.total,pure:prep.pure}),
    buttons:[{id:"single",label:t("playlist.batch.single"),primary:true},{id:"playlist",label:t("playlist.btn.all")}]});
  return r.id==="cancel"?null:{mode:r.id,name:""};
}
async function start(){
  if(running||modalDone)return;
  const p=payload();
  if(!p.url){shake();log(t("err.no_url"));return}
  const prep=api()?await api().prepare_download(p):localPrepare(p);
  if(!prep.ok){log(prep.error);shake();return}
  if(prep.url&&prep.url!==p.url){p.url=prep.url;$("url").value=prep.url;syncClear()}  // 網址欄顯示整理後、實際要下載的網址
  let decision=null;
  if(prep.ask){decision=await askPlaylist(prep);if(!decision){log(t("log.cancelled"));return}}
  if(!api())return simulate(p);
  const r=await api().start_download(p,decision);
  if(!r.ok){log(r.error);shake()}
}
$("go").onclick=start;
$("url").onkeydown=e=>{if(e.key==="Enter")start()};
$("url").addEventListener("input",syncClear);
$("urlclr").onclick=()=>{$("url").value="";syncClear();$("url").focus()};
$("stop").onclick=()=>{$("stop").disabled=true;api()?api().cancel_download():stopSim()};
$("info").onclick=async()=>{
  const u=$("url").value.trim();
  if(!u){shake();log(t("err.no_url"));return}
  if(!api()){log(t("preview.info"),"hl");return}
  $("info").disabled=true;$("go").disabled=true;log(t("log.reading_info"));
  const r=await api().fetch_info(u,$("ck").value,cookieFile);
  if(!r.ok){log(r.error);$("info").disabled=false;$("go").disabled=false}
  else if(r.url&&r.url!==u){$("url").value=r.url;syncClear()}
};

/* ---------- 檔案與資料夾 ---------- */
$("pick").onclick=async()=>{if(!api())return;const d=await api().choose_folder();if(d){$("dir").value=d;persist()}};
$("dirclr").onclick=()=>{$("dir").value=defaultDir;persist()};
$("ckpick").onclick=async()=>{if(!api())return;const f=await api().pick_cookie_file();if(f){cookieFile=f;$("ckf").value=f;$("ckf").title=f;syncClear()}};
$("ckclr").onclick=()=>{cookieFile="";$("ckf").value="";syncClear()};
async function applyBatch(r,from){
  if(r.cancelled)return;
  if(!r.ok){await askModal({title:r.missing?t("modal.example.missing"):t("modal.batch.unusable"),body:r.error});return}
  $("url").value="file:"+r.path;syncClear();
  log(from==="example"?t("log.example_loaded"):t("log.batch_imported",{path:r.path,count:r.count}));
  if(r.ignored>0)log(t("log.batch_ignored",{count:r.ignored}));
}
$("batch").onclick=async()=>{if(!api())return log(t("preview.no_dialog"));applyBatch(await api().pick_batch_file(),"batch")};
$("example").onclick=async()=>{if(!api())return log(t("preview.no_example"));applyBatch(await api().load_example(),"example")};

/* ---------- 說明、診斷、歷史、檢查更新 ---------- */
const previewMsg=()=>t("preview.msg");
$("help").onclick=async()=>{
  if(!api())return askModal({title:t("quick.help"),body:previewMsg()});
  const h=await api().get_help();
  const credit=document.createElement("div");credit.className="credit";credit.textContent=h.credit;
  askModal({title:t("quick.help"),body:h.text,node:credit,wide:true});
};
$("diag").onclick=()=>{
  if(!api())return askModal({title:t("quick.diag"),body:previewMsg()});
  $("diag").disabled=true;api().run_diagnostics();
};
$("locate").onclick=async()=>{
  if(!api())return askModal({title:t("quick.locate"),body:previewMsg()});
  const r=await api().open_file_location($("dir").value);
  if(!r||!r.ok)toast(t("loc.missing"));
};
$("hist").onclick=async()=>{
  if(!api())return askModal({title:t("quick.history"),body:previewMsg()});
  const rows=await api().get_history();
  if(!rows.length)return askModal({title:t("quick.history"),body:t("hist.empty")});
  const list=document.createElement("div");list.className="hist";
  rows.forEach(r=>{
    const d=document.createElement("div");d.className="hrow";
    const tm=Object.assign(document.createElement("span"),{className:"tm",textContent:`[${r.time}]`});
    const st=Object.assign(document.createElement("span"),{className:"st "+(r.ok?"ok":"no"),textContent:r.ok?t("hist.ok"):t("hist.fail")});
    const mt=Object.assign(document.createElement("span"),{className:"mt2",textContent:`${r.fmt}　${r.quality}`});
    const a=Object.assign(document.createElement("button"),{className:"lnk",type:"button",textContent:r.title,title:r.url});
    a.onclick=()=>api().open_target(r.url);
    d.append(tm,st,mt,a);list.append(d);
  });
  const r=await askModal({title:t("hist.title",{n:rows.length}),node:list,wide:true,buttons:[{id:"clear",label:t("hist.clear"),danger:true}]});
  if(r.id==="clear"){
    const c=await askModal({title:t("hist.clear"),body:t("hist.clear.confirm"),buttons:[{id:"yes",label:t("hist.clear.yes"),danger:true}]});
    if(c.id==="yes"){await api().clear_history();toast(t("hist.cleared"))}
  }
};
$("upd").onclick=async()=>{
  if(!api())return askModal({title:t("quick.update"),body:previewMsg()});
  const r=await api().check_update();
  if(!r.ok){log(r.error);return}
  $("upd").disabled=true;log(t("upd.checking"));
};
async function onUpdateDone(e){
  $("upd").disabled=running;
  log(e.text);
  const buttons=[];
  if(e.python_url)buttons.push({id:"py",label:t("python.open")});
  if(e.command)buttons.push(copyButton(e.command.label,e.command.text));
  if(e.ffmpeg_url)buttons.push({id:"ff",label:t("ffmpeg.open")});
  const code=e.command?e.command.text:"",note=e.command?t("upd.note",{title:e.command.title,note:e.command.note}):"";
  if(e.items&&e.items.length){await askInstall(e.items,{body:e.text,buttons,code,note});return}  // 有新版：接上同一組安裝彈窗
  const r=await askModal({title:t("quick.update"),body:e.text,buttons,code,note});
  if(r.id==="py")api().open_python_page();else if(r.id==="ff")api().open_ffmpeg_page();
}

/* ---------- 啟動檢查與自動安裝：缺少套件確認／有更新確認／安裝中（彈窗樣式沿用 askModal） ---------- */
const promptSeen=new Set();
let inst=null,dotT=null;
const waitFree=fn=>{if(ready&&!modalDone)fn();else setTimeout(()=>waitFree(fn),300)};  // 等語言載入完成、且沒有別的彈窗開著
function showPrompt(items){
  const key=JSON.stringify(items||[]);
  if(!items||!items.length||promptSeen.has(key))return;
  promptSeen.add(key);
  waitFree(()=>askInstall(items));
}
async function askInstall(items,extra={}){
  const missing=items.some(i=>i.action==="install");
  const list=document.createElement("ul");list.className="chklist";
  items.forEach(i=>{const li=document.createElement("li");
    li.textContent=t("check.item."+i.id)+"（"+t(i.action==="upgrade"?"check.action.upgrade":"check.action.install")+"）";list.append(li)});
  const body=(extra.body?extra.body+"\n\n":"")+t(missing?"check.missing.body":"check.update.body");
  const r=await askModal({title:t(missing?"check.missing.title":"check.update.title"),body,node:list,code:extra.code||"",note:extra.note||"",
    buttons:[{id:"ok",label:t("check.confirm"),primary:true},...(extra.buttons||[]),{id:"later",label:t("check.later")}]});
  if(r.id==="py")api().open_python_page();
  else if(r.id==="ff")api().open_ffmpeg_page();
  if(r.id!=="ok")return;
  showInstalling();  // 先開「安裝中」再通知 Python 開始，這樣最早的進度與失敗事件都接得到
  const res=await api().confirm_install(items);
  if(!res.ok){stopInstalling();closeModal({id:"err"});log(res.error);toast(res.error)}
}
function showInstalling(){
  const wrap=document.createElement("div");wrap.className="inst";
  const name=document.createElement("p"),bar=document.createElement("div"),fill=document.createElement("i"),pct=document.createElement("p");
  name.className="inst-name";bar.className="inst-bar";pct.className="sub";bar.append(fill);wrap.append(name,bar,pct);
  inst={name,fill,pct,label:t("check.installing.title"),dots:1};
  clearInterval(dotT);dotT=setInterval(()=>{if(inst){inst.dots=inst.dots%3+1;renderInst()}},450);  // . → .. → ... 循環
  askModal({title:t("check.installing.title"),node:wrap,locked:true,buttons:[]});
  renderInst();
}
function renderInst(){if(inst)inst.name.textContent=inst.label+".".repeat(inst.dots)}
function onInstallProgress(e){
  if(!inst)return;
  const name=t("check.item."+e.item);
  inst.label=e.action==="upgrade"?t("check.installing.upgrade",{name}):t("check.installing.install",{name});
  inst.fill.style.width=Math.min(100,e.pct)+"%";
  inst.pct.textContent=t("check.installing.step",{index:e.index,total:e.total})+"　"+Math.round(e.pct)+"%";
  renderInst();
}
function stopInstalling(){clearInterval(dotT);dotT=null;inst=null}
function onInstallDone(){
  if(!inst)return;
  clearInterval(dotT);dotT=null;inst.fill.style.width="100%";inst.name.textContent=t("check.installing.done");inst.pct.textContent="";
}
async function onInstallFail(e){
  stopInstalling();closeModal({id:"fail"});
  const pages=(e.pages||[]).map(id=>({id:"page:"+id,label:t("check.open_page",{name:t("check.item."+id)}),primary:true}));
  const r=await askModal({title:t("check.fail.title"),body:t("check.fail.body",{name:e.item?t("check.item."+e.item):"",message:e.message}),
    buttons:[...pages,{id:"close",label:t("check.close")}]});
  if(r.id&&r.id.startsWith("page:"))api().open_download_page(r.id.slice(5));
}
const exportButton=text=>({label:t("diag.export"),keep:async e=>{
  e.disabled=true;
  const note=msg=>{$("mn").textContent=msg;$("mn").hidden=false};
  try{
    const r=await api().export_diagnostics(text);
    if(r&&r.ok)note(t("diag.export.done"));
    else if(r&&!r.cancelled)note(t("diag.export.fail",{error:r.error||""}));
  }catch(err){note(t("diag.export.fail",{error:String(err)}))}
  e.disabled=false;
}});
async function onDiag(e){
  $("diag").disabled=false;
  askModal({title:t("quick.diag"),code:e.text,buttons:[copyButton(t("copy.diag"),e.text),exportButton(e.text)]});
}

/* ---------- 瀏覽器預覽模式：模擬下載 ---------- */
function simulate(p){
  buildLanes();busy(true);log(`yt-dlp ${p.boost?"-N "+p.frags+" ":""}${p.url}`,"hl");
  const gain=Math.pow(frags(),.6);let v=0,tick=0;
  timer=setInterval(()=>{tick++;if(tick===1)decideFrag(/m3u8|\.mpd/.test(p.url));  // 預覽：網址含 m3u8／mpd 視為分片
    v=Math.min(100,v+(.5+Math.random())*gain/(cells.length>1?1.2:1));paint(v);
    const mb=(3.8+Math.random()*1.2)*gain;$("speed").textContent=mb.toFixed(1)+" MiB/s";
    if(tick%12===0)log(`[download] ${v.toFixed(1)}% of 84.2MiB at ${mb.toFixed(1)}MiB/s`);
    if(v>=100){clearInterval(timer);busy(false);onDone({ok:true,message:t("state.done")})}},150);
}
function stopSim(){clearInterval(timer);busy(false);paint(0);onDone({cancelled:true})}

/* ---------- 啟動 ---------- */
async function init(){
  if(ready&&!api())return;
  if(!api()){await loadPreviewLocale();buildLang("auto");$("note").hidden=false;ready=true;syncClear();root.classList.remove("pending");return}
  $("note").hidden=true;
  const s=await api().get_state();
  I18N=s.strings||{};LANG=s.lang||"zh-TW";buildLang(s.language||"auto");applyI18n();
  $("ver").textContent="v"+s.version;defaultDir=s.default_dir;$("dir").value=s.outdir;
  for(const k in FIELDS){const [id,t]=FIELDS[k],e=$(id);
    if(t==="c")e.checked=!!s[k];else if(e.tagName==="SELECT")setSel(id,String(s[k]));else e.value=s[k]}
  $("frag").disabled=!s.boost;buildLanes();setMode(s.appearance,false);ready=true;syncClear();root.classList.remove("pending");
  try{const pr=await api().get_install_prompt();if(pr)showPrompt(pr.items)}catch(_){}  // 啟動檢查比網頁先做完時補領（同一組項目只會顯示一次）
}
async function loadPreviewLocale(){  // 瀏覽器預覽模式沒有 Python；能讀到 locales 就用繁中，讀不到就維持畫面上寫死的繁中
  try{  // 新增語言檔案時，這份清單要跟 locales/zh-TW/ 底下的檔名同步
    const parts=await Promise.all(["strings","errors","logs"].map(n=>fetch(`../locales/zh-TW/${n}.json`).then(r=>r.json())));
    I18N=Object.assign({},...parts);LANG="zh-TW";applyI18n()
  }catch(_){}
}
setTimeout(()=>root.classList.remove("pending"),3000);  // 保險：無論如何 3 秒後一定顯示畫面
/* 保險：整個畫面（含自己畫的標題列）絕對不該被捲動；萬一有東西讓頁面捲動了（例如聚焦到畫面外的元素），立刻捲回原位 */
addEventListener("scroll",()=>{if(scrollX||scrollY)scrollTo(0,0)},{passive:true});
window.addEventListener("pywebviewready",init);
setTimeout(()=>{if(!ready)init()},600);
