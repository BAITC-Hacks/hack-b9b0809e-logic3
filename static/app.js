let csrf = '', attachmentText = '', busy = false;
const $ = id => document.getElementById(id);
async function api(path, body) {
  const options = body === undefined ? {} : {method:'POST',headers:{'X-CSRF-Token':csrf}};
  if(body instanceof FormData) options.body=body;
  else if(body !== undefined){options.headers['Content-Type']='application/json';options.body=JSON.stringify(body);}
  const r=await fetch(path,options); const data=await r.json();
  if(!r.ok) throw new Error(typeof data.detail==='string'?data.detail:'Проверьте введённые данные.');
  return data;
}
function bubble(text, kind=''){const el=document.createElement('div');el.className='bubble '+kind;el.textContent=text;$('conversation').append(el);el.scrollIntoView({block:'end'});return el;}
function button(label, action){const b=document.createElement('button');b.textContent=label;b.addEventListener('click',action);return b;}
function configureCatalogExample(mode){
 const example=document.querySelector('[data-query="DEMO-C16-A"]');
 if(mode==='live'&&example){example.dataset.query='автомат 16 А ИЭК';example.textContent='Автоматы 16 А ↗';}
}
async function action(fn){if(busy)return;busy=true;$('send').disabled=true;try{await fn();}catch(e){bubble(e.message,'error');}finally{busy=false;$('send').disabled=false;}}
function proposal(p){
 const el=bubble(p.message);const b=button('Да, добавить '+p.quantity,()=>action(async()=>{
   const r=await api('/api/cart/confirm',{proposal_id:p.id,confirmed:true});b.disabled=true;bubble(r.message);await refreshCart();
   const a=document.createElement('a');a.href='/cart';a.textContent='Открыть корзину →';$('conversation').append(a);
 }));b.className='confirm';el.append(document.createElement('br'),b);
}
function cards(products){
 const list=document.createElement('div');list.className='cards';
 for(const p of products){const c=document.createElement('div');c.className='card';const name=document.createElement('strong');name.textContent=p.name;
 const info=document.createElement('small');info.textContent=`${p.article} · ${p.price??'Цена неизвестна'} ₸ · Остаток: ${p.stock??'неизвестен'}`;
 const n=document.createElement('input');n.type='number';n.min=p.minimum;n.step=p.minimum;n.value=p.minimum;n.setAttribute('aria-label','Количество '+p.name);
 const b=button('В корзину',()=>action(async()=>proposal(await api('/api/cart/proposals',{product_id:p.id,quantity:n.value}))));
 b.disabled=p.stock===null||Number(p.stock)<=0||(p.warnings||[]).length>0;
 c.append(name,info,n,b);
 for(const link of p.certificates||[]){const a=document.createElement('a');a.href=link;a.textContent='Сертификат ↗';a.target='_blank';a.rel='noopener noreferrer';c.append(document.createElement('br'),a);}
 list.append(c);} $('conversation').append(list);
}
async function send(message){await action(async()=>{
 bubble(message,'user');$('message').value='';
 const result=await api('/api/chat',{message,attachment_text:attachmentText});attachmentText='';$('attachment').textContent='';
 if(!result.proposal)bubble(result.message);if(result.products)cards(result.products);if(result.analogs)cards(result.analogs.map(x=>x.product));if(result.proposal)proposal(result.proposal);
 if(result.cart){await refreshCart();const a=document.createElement('a');a.href='/cart';a.textContent='Открыть корзину →';$('conversation').append(a);}
 if(result.partial_catalog)bubble('Поиск выполнен по ограниченной выборке каталога.');
 if(result.catalog_stale)bubble('Список каталога обновляется в фоне. Цены и остатки найденных товаров проверяются отдельно.');
 });}
async function refreshCart(){const cart=await api('/api/cart');$('cart-count').textContent='Корзина · '+cart.items.length;$('cart-items').replaceChildren();
 for(const p of cart.items){const el=document.createElement('div');el.className='bubble';el.textContent=`${p.name}\n${p.quantity} × ${p.price} ₸`;$('cart-items').append(el);}
 if(!cart.items.length)$('cart-items').textContent='Корзина пока пуста.';$('cart-total').textContent='Итого: '+cart.total+' ₸';}
$('chat-form').addEventListener('submit',e=>{e.preventDefault();if($('message').value.trim())send($('message').value.trim());});
document.querySelectorAll('[data-query]').forEach(b=>b.addEventListener('click',()=>send(b.dataset.query)));
$('file').addEventListener('change',()=>action(async()=>{const file=$('file').files[0];if(!file)return;if(file.size>5*1024*1024)throw new Error('Максимальный размер — 5 МБ.');const data=new FormData();data.append('file',file);$('attachment').textContent='Читаю файл…';try{const r=await api('/api/upload',data);attachmentText=r.text;$('attachment').textContent='Прикреплено: '+file.name+'. '+r.notice;}finally{$('file').value='';}}));
(async()=>{try{const session=await api('/api/session');csrf=session.csrf;configureCatalogExample(session.mode);$('mode').textContent=(session.mode==='demo'?'Синтетический каталог':'Каталог ekt.kz')+' · '+(session.llm?'ИИ подключён':'Без LLM');await refreshCart();if(location.pathname==='/cart'){$('cart-panel').hidden=false;$('conversation').hidden=true;$('composer').hidden=true;}}catch(e){bubble(e.message,'error');}})();
