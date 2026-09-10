const tg=window.Telegram?.WebApp;tg?.ready();tg?.expand();
const initData=tg?.initData||'';
const headers={'X-Telegram-Init-Data':initData};
let timer,statusTimer,supportTimer,licenseActive=false,currentTaskId=null;
const saved=JSON.parse(localStorage.getItem('neverk-theme')||'{}');
function setTheme(t,a){document.documentElement.dataset.theme=t;document.documentElement.style.setProperty('--accent',a);localStorage.setItem('neverk-theme',JSON.stringify({t,a}))}
setTheme(saved.t||(matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light'),saved.a||'#7c5cff');
const q=s=>document.querySelector(s);
const request=async(url,o={})=>{const r=await fetch(url,{...o,headers:{...headers,...o.headers}}),d=await r.json();if(!r.ok)throw Error(d.detail||'Ошибка');return d};

// Универсальный обработчик закрытия диалогов
document.querySelectorAll('.dialog-close').forEach(btn=>{
  btn.addEventListener('click',e=>{
    e.preventDefault();e.stopPropagation();
    const d=btn.closest('dialog');
    if(d){if(d.id==='payment')clearInterval(timer);if(d.id==='supportDialog')clearInterval(supportTimer);d.close();}
  });
});
// Закрытие по клику на backdrop
document.querySelectorAll('dialog').forEach(d=>{
  d.addEventListener('click',e=>{
    if(e.target===d){
      if(d.id==='payment')clearInterval(timer);
      if(d.id==='supportDialog')clearInterval(supportTimer);
      d.close();
    }
  });
});

function show(id){document.querySelectorAll('.screen').forEach(x=>x.classList.remove('active'));document.querySelector('#'+id).classList.add('active');document.querySelectorAll('.nav').forEach(x=>x.classList.toggle('active',x.dataset.screen===id));scrollTo(0,0)}
document.querySelectorAll('.nav').forEach(x=>x.addEventListener('click',()=>show(x.dataset.screen)));
document.querySelector('#settings').addEventListener('click',()=>document.querySelector('#theme').showModal());
document.querySelectorAll('[data-theme]').forEach(x=>x.addEventListener('click',e=>{e.preventDefault();setTheme(x.dataset.theme,getComputedStyle(document.documentElement).getPropertyValue('--accent').trim())}));
document.querySelector('#accent').addEventListener('input',e=>setTheme(document.documentElement.dataset.theme,e.target.value));
document.querySelector('#saveTheme').addEventListener('click',()=>document.querySelector('#theme').close());

async function plans(){const p=await request('/api/plans');document.querySelector('#plans').innerHTML=p.map((x,i)=>`<article class="plan ${i===1?'chosen':''}">${i===1?'<b class="tag">ВЫГОДНО</b>':''}<h2>${x.days===null?'Навсегда':x.days+' дней'}</h2><p>${x.days===null?'Единоразовая покупка':'Полный доступ на период'}</p><div class="price">${x.price}<small> USDT</small></div><button class="buy" type="button" data-plan="${x.id}">Купить</button></article>`).join('');document.querySelectorAll('[data-plan]').forEach(x=>x.addEventListener('click',()=>buy(x.dataset.plan)))}
let currentPayUrl='';
const payButton=document.querySelector('#payLink');
payButton.addEventListener('click',event=>{event.preventDefault();if(!currentPayUrl)return;if(tg?.openLink)tg.openLink(currentPayUrl);else window.location.assign(currentPayUrl)});
async function buy(plan){try{const d=await request('/api/checkout',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({plan})});currentPayUrl=d.pay_url;payButton.href=d.pay_url;document.querySelector('#payment').showModal();clearInterval(timer);timer=setInterval(()=>check(d.invoice_id),5000)}catch(e){alert(e.message)}}
async function check(id){try{const d=await request('/api/orders/'+id);if(d.status==='paid'){clearInterval(timer);document.querySelector('#paymentStatus').textContent='Готово! Ваш ключ: '+d.key;tg?.HapticFeedback?.notificationOccurred('success');me()}}catch{}}

function date(x){return x?new Date(x).toLocaleDateString('ru-RU'):'Бессрочно'}
function timeAgo(x){if(!x)return'';const d=new Date(x),n=new Date(),s=Math.floor((n-d)/1000);if(s<60)return'только что';if(s<3600)return Math.floor(s/60)+' мин назад';if(s<86400)return Math.floor(s/3600)+' ч назад';return d.toLocaleDateString('ru-RU')}
const profileCard=q('#profileCard'),termsDialog=q('#terms'),agreeButton=q('#agree'),activationStatus=q('#activationStatus'),licenseKey=q('#licenseKey');

// ============= РАССЫЛКА =============
async function loadAccounts(){try{return await request('/api/accounts')}catch{return[]}}

async function renderBroadcast(step='home',state={}){
 const root=q('#broadcastApp');if(!root)return;
 if(!licenseActive){root.innerHTML='<div class="card locked"><h2>Нужен активный ключ</h2><p class="muted">Активируйте ключ в профиле, чтобы открыть панель рассылки.</p><button class="buy" type="button" id="goProfile">Открыть профиль</button></div>';q('#goProfile').addEventListener('click',()=>show('profile'));return}
 const accounts=await loadAccounts();
 if(!accounts.length){root.innerHTML='<div class="card locked"><h2>Нет аккаунтов</h2><p class="muted">Подключите Telegram-аккаунт в профиле, чтобы начать рассылку.</p><button class="buy" type="button" id="goProfile2">Открыть профиль</button></div>';q('#goProfile2').addEventListener('click',()=>show('profile'));return}

 if(step==='home'){
   root.innerHTML='<div class="card"><h2>Ваши аккаунты <small>'+accounts.length+'</small></h2><div class="account-list">'+accounts.map((a,i)=>'<div>◉ '+(a.phone||('Аккаунт '+(i+1)))+'</div>').join('')+'</div></div><button class="buy" type="button" id="beginBroadcast">Создать рассылку</button>';
   q('#beginBroadcast').addEventListener('click',()=>renderBroadcast('accounts'));
   return;
 }
 if(step==='accounts'){
   root.innerHTML='<div class="card"><h2>1. Выберите аккаунты</h2><button class="accountChoice" type="button" id="allAccounts">Все аккаунты <i>○</i></button>'+accounts.map(a=>'<button class="accountChoice" type="button" data-id="'+a.id+'">'+(a.phone||('ID '+a.id))+' <i>○</i></button>').join('')+'<button class="buy" type="button" id="confirmAccounts">Подтвердить</button></div>';
   const mark=(button,on)=>{button.classList.toggle('selected',on);button.querySelector('i').textContent=on?'✓':'○'};
   q('#allAccounts').addEventListener('click',()=>{const on=!q('#allAccounts').classList.contains('selected');mark(q('#allAccounts'),on);document.querySelectorAll('[data-id]').forEach(x=>mark(x,on))});
   document.querySelectorAll('[data-id]').forEach(x=>x.addEventListener('click',()=>{mark(x,!x.classList.contains('selected'));mark(q('#allAccounts'),[...document.querySelectorAll('[data-id]')].every(y=>y.classList.contains('selected')))}));
   q('#confirmAccounts').addEventListener('click',()=>{const ids=[...document.querySelectorAll('[data-id].selected')].map(x=>Number(x.dataset.id));if(!ids.length)return alert('Выберите хотя бы один аккаунт.');renderBroadcast('mode',{ids})});
   return;
 }
 if(step==='mode'){
   root.innerHTML='<div class="card"><h2>2. Выберите режим</h2><button class="mode selected" type="button" id="normalMode"><b>⚡ Обычный</b><small>Один текст для каждого цикла</small></button><button class="mode" type="button" id="safeMode"><b>🛡 Безопасный</b><small>Три текста, ротация, ±20% интервал</small></button></div>';
   q('#normalMode').addEventListener('click',()=>renderBroadcast('compose',{...state,mode:'normal'}));
   q('#safeMode').addEventListener('click',()=>renderBroadcast('compose',{...state,mode:'safe'}));
   return;
 }
 const count=state.mode==='safe'?3:1;
 if(step==='compose'){
   root.innerHTML='<div class="card"><h2>3. Настройка</h2><p class="muted">'+(state.mode==='safe'?'Безопасный режим: три варианта текста.':'Обычный режим: один текст.')+'</p>'+Array.from({length:count},(_,i)=>'<label>Текст '+(i+1)+'<textarea class="demoText" placeholder="Текст сообщения '+(i+1)+'"></textarea></label>').join('')+'<label>Интервал <select id="demoInterval"><option value="30">30 минут</option><option value="60" selected>60 минут</option><option value="120">120 минут</option></select></label><button class="buy" type="button" id="startDemo">Запустить рассылку</button></div>';
   q('#startDemo').addEventListener('click',async()=>{
     const texts=[...document.querySelectorAll('.demoText')].map(x=>x.value.trim()).filter(Boolean);
     if(state.mode==='safe'&&texts.length<3)return alert('Заполните все три текста.');
     if(state.mode==='normal'&&texts.length<1)return alert('Введите текст.');
     const btn=q('#startDemo');
     btn.disabled=true;btn.textContent='Запускаю…';
     try{
       const d=await request('/api/broadcast/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({account_ids:state.ids,messages:texts,interval_minutes:Number(q('#demoInterval').value),safe_mode:state.mode==='safe'})});
       currentTaskId=d.task_id;
       tg?.HapticFeedback?.notificationOccurred('success');
       await renderRunning(d.task_id);
     }catch(e){
       alert(e.message);
       btn.disabled=false;btn.textContent='Запустить рассылку';
     }
   });
   return;
 }
}

async function renderRunning(taskId){
  currentTaskId=taskId;
  const root=q('#broadcastApp');if(!root)return;
  root.innerHTML='<div class="card status-card"><p class="eyebrow">РАССЫЛКА АКТИВНА</p><h2>Цикл <b id="cycle">—</b></h2><p class="muted" id="taskMeta">Загрузка…</p><div class="status-number"><b id="sentCount">0</b><span>отправлено в чаты</span></div><div class="progress-info" id="progressInfo"></div><button id="stopDemo" class="danger" type="button">Завершить рассылку</button></div>';
  q('#stopDemo').addEventListener('click',async()=>{
    if(currentTaskId){
      q('#stopDemo').disabled=true;q('#stopDemo').textContent='Останавливаю…';
      try{await request('/api/broadcast/stop/'+currentTaskId,{method:'POST'})}catch{}
    }
    clearInterval(statusTimer);currentTaskId=null;
    renderBroadcast('home');
  });
  clearInterval(statusTimer);
  const refresh=async()=>{
    if(!currentTaskId)return;
    try{
      const d=await request('/api/broadcast/status/'+currentTaskId);
      const c=q('#cycle');if(c)c.textContent=d.current_cycle;
      const s=q('#sentCount');if(s)s.textContent=d.sent_count;
      const m=q('#taskMeta');if(m)m.textContent=(d.safe_mode?'Безопасный':'Обычный')+' · Групп: '+d.groups_count;
      const p=q('#progressInfo');
      if(p){
        let nextIn='';
        if(d.last_sent_at){
          const last=new Date(d.last_sent_at).getTime();
          const base=d.interval_minutes*60*1000;
          const elapsed=Date.now()-last;
          const left=Math.max(0,base-elapsed);
          const min=Math.floor(left/60000);
          nextIn='Следующий цикл ~через '+min+' мин';
        }
        p.innerHTML='<div class="progress-note">'+(nextIn||'Идёт отправка…')+'</div>';
      }
      if(d.status!=='active'){
        clearInterval(statusTimer);
        currentTaskId=null;
        const p2=q('#progressInfo');
        if(p2)p2.innerHTML='<div class="progress-note" style="color:#e44b5e">Рассылка завершена</div>';
        setTimeout(()=>renderBroadcast('home'),1200);
      }
    }catch{}
  };
  await refresh();
  statusTimer=setInterval(refresh,5000);
}

// ============= ПОДКЛЮЧЕНИЕ АККАУНТА =============
const connectDialog=q('#connectDialog');
q('#connectAccount').addEventListener('click',()=>{if(!licenseActive){alert('Сначала активируйте лицензию.');return}resetConnectDialog();connectDialog.showModal()});
function resetConnectDialog(){
  q('#connectStep1').hidden=false;q('#connectStep2').hidden=true;q('#connectStep3').hidden=true;
  q('#connectPhone').value='';q('#connectCode').value='';q('#connectPassword').value='';q('#connectStatus').textContent='';
}
q('#sendCodeBtn').addEventListener('click',async()=>{
  const phone=q('#connectPhone').value.trim();if(!phone.startsWith('+'))return q('#connectStatus').textContent='Номер должен начинаться с +.';
  q('#connectStatus').textContent='Отправка…';
  try{await request('/api/accounts/send-code',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone})});q('#connectStep1').hidden=true;q('#connectStep2').hidden=false;q('#connectStatus').textContent='Код отправлен.'}
  catch(e){q('#connectStatus').textContent=e.message}
});
q('#verifyCodeBtn').addEventListener('click',async()=>{
  const code=q('#connectCode').value.trim();if(!code)return;
  q('#connectStatus').textContent='Проверка…';
  try{const d=await request('/api/accounts/verify-code',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code})});
    if(d.need_password){q('#connectStep2').hidden=true;q('#connectStep3').hidden=false;q('#connectStatus').textContent='Введите пароль 2FA.';return}
    connectDialog.close();me();
  }catch(e){q('#connectStatus').textContent=e.message}
});
q('#verifyPasswordBtn').addEventListener('click',async()=>{
  const password=q('#connectPassword').value;if(!password)return;
  q('#connectStatus').textContent='Проверка…';
  try{await request('/api/accounts/verify-password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password})});connectDialog.close();me()}
  catch(e){q('#connectStatus').textContent=e.message}
});

// ============= ПОДДЕРЖКА (ЧАТ) =============
function renderSupportMessages(messages){
  const box=q('#supportChat');if(!box)return;
  if(!messages.length){box.innerHTML='<p class="muted" style="text-align:center;padding:20px">Напишите первое сообщение — администратор ответит.</p>';return}
  box.innerHTML=messages.map(m=>`<div class="support-msg ${m.sender==='admin'?'admin':'user'}"><div class="support-bubble">${escapeHtml(m.message)}</div><div class="support-time">${m.sender==='admin'?'Поддержка':'Вы'} · ${timeAgo(m.created_at)}</div></div>`).join('');
  box.scrollTop=box.scrollHeight;
}
function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function loadSupport(){try{const d=await request('/api/support/ticket');renderSupportMessages(d.messages);return d.ticket_id}catch{return null}}

q('#support').addEventListener('click',async()=>{
  q('#supportDialog').showModal();
  q('#supportStatus').textContent='';
  await loadSupport();
  clearInterval(supportTimer);
  supportTimer=setInterval(loadSupport,7000);
});

q('#closeSupport').addEventListener('click',(e)=>{
  e.preventDefault();e.stopPropagation();
  clearInterval(supportTimer);
  q('#supportDialog').close();
});

q('#sendSupport').addEventListener('click',async(e)=>{
  e.preventDefault();e.stopPropagation();
  const text=q('#supportText').value.trim();
  if(!text)return;
  const btn=q('#sendSupport');
  btn.disabled=true;btn.textContent='Отправка…';
  try{
    await request('/api/support/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text})});
    q('#supportText').value='';
    await loadSupport();
    q('#supportStatus').textContent='Отправлено';
  }catch(err){
    q('#supportStatus').textContent=err.message;
  }finally{
    btn.disabled=false;btn.textContent='Отправить';
  }
});

// ============= АДМИН =============
async function loadAdmin(){try{const d=await request('/api/admin/summary');q('#adminPaid').textContent=d.paid_total;q('#adminOrders').textContent=d.orders_total;q('#adminUsers').textContent=d.users_total;q('#adminRevenue').textContent=d.revenue_usdt+' USDT'}catch(e){alert(e.message)}}
q('#refreshAdmin').addEventListener('click',loadAdmin);
q('#createManualKey').addEventListener('click',async()=>{try{const d=await request('/api/admin/keys',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({duration_days:Number(q('#manualDays').value)})});q('#manualKeyResult').textContent='Ключ: '+d.key+' · '+(d.duration_days===-1?'бессрочно':d.duration_days+' дней')}catch(e){q('#manualKeyResult').textContent=e.message}});
async function loadUsers(){try{const users=await request('/api/admin/users');q('#usersList').innerHTML=users.length?users.map(x=>'<p><b>'+x.telegram_id+'</b><br><small>'+(x.license_key||'Нет активного ключа')+'</small></p>').join(''):'<p class="muted">Пользователей пока нет.</p>'}catch(e){alert(e.message)}}
q('#loadUsers').addEventListener('click',loadUsers);
async function loadTickets(){try{const tickets=await request('/api/admin/support/tickets');q('#ticketsList').innerHTML=tickets.length?tickets.map(x=>'<article><b>#'+x.id+' · '+x.telegram_id+'</b><p>'+escapeHtml(x.last_message)+'</p><textarea data-reply="'+x.id+'" placeholder="Ответ пользователю"></textarea><button class="replyTicket buy" type="button" data-ticket="'+x.id+'">Ответить</button></article>').join(''):'<p class="muted">Обращений пока нет.</p>';document.querySelectorAll('.replyTicket').forEach(button=>button.addEventListener('click',async()=>{const id=button.dataset.ticket,box=q('[data-reply="'+id+'"]');if(!box.value.trim())return;try{await request('/api/admin/support/tickets/'+id+'/reply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:box.value})});box.value='';button.textContent='Отправлено';setTimeout(loadTickets,800)}catch(e){alert(e.message)}}))}catch(e){alert(e.message)}}
q('#loadTickets').addEventListener('click',loadTickets);

async function me(){
  try{
    const d=await request('/api/me');
    q('#adminNav').hidden=!d.is_admin;
    if(d.is_admin)loadAdmin();
    licenseActive=Boolean(d.license_key);
    profileCard.innerHTML=`<div class="avatar">◉</div><div><b>Пользователь Telegram</b><p class="muted">ID: ${d.telegram_id}</p></div><div class="license"><small>${d.license_key?'ДОСТУП АКТИВЕН':'НЕТ ДОСТУПА'}</small><b>${d.license_key?'до '+date(d.expires_at):'—'}</b></div>`;
    if(!d.terms_accepted)termsDialog.showModal();
    if(d.active_task_id){
      await renderRunning(d.active_task_id);
    }else{
      await renderBroadcast();
    }
  }catch{
    profileCard.innerHTML='<b>Откройте приложение через Telegram</b>';
  }
}

agreeButton.addEventListener('click',async()=>{try{await request('/api/terms/accept',{method:'POST'});termsDialog.close();me()}catch(e){alert(e.message)}});
q('#activateKey').addEventListener('click',async()=>{try{const d=await request('/api/keys/activate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:licenseKey.value})});activationStatus.textContent='Ключ активирован. Доступ: '+date(d.expires_at);me()}catch(e){activationStatus.textContent=e.message}});

plans();
me();
