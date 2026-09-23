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
const propertyLabels={
 NOMINALNYY_TOK:'Номинальный ток',KOLICHESTVO_POLYUSOV:'Количество полюсов',
 NOMINALNOE_NAPRYAZHENIE:'Номинальное напряжение',
 NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST:'Отключающая способность',
 CHARACTERISTIC:'Характеристика срабатывания',KRATNOST_MIN:'Минимальная партия',
 TORGOVAYA_MARKA:'Производитель',TIP_USTANOVKI:'Тип установки',
 OBYEM:'Тип изделия',ARTIKULPOSTAVSHCHIKA:'Артикул производителя'
};
function detail(label,value){
 const row=document.createElement('div');row.className='detail-row';
 const term=document.createElement('dt');term.textContent=label;
 const description=document.createElement('dd');description.textContent=String(value);
 row.append(term,description);return row;
}
function productCard(p,reason=''){
 const card=document.createElement('article');card.className='product-card';
 const head=document.createElement('div');head.className='product-card__head';
 const name=document.createElement('h3');name.textContent=p.name;
 const identity=document.createElement('p');identity.className='product-card__identity';
 identity.textContent=`Артикул: ${p.article} · ID: ${p.id}`;
 head.append(name,identity);
 const category=document.createElement('p');category.className='product-card__category';
 category.textContent=`Категория: ${p.category||'не указана'}`;head.append(category);
 card.append(head);

 const body=document.createElement('div');body.className='product-card__body';
 const availability=document.createElement('div');availability.className='product-card__availability';
 const price=document.createElement('div');price.className='product-card__price';
 price.textContent=p.price==null?'Цена неизвестна':`${p.price} ₸`;
 const stock=document.createElement('div');stock.className='product-card__stock';
 stock.textContent=p.stock==null?'Остаток неизвестен':`В наличии: ${p.stock}`;
 availability.append(price,stock);body.append(availability);
 if(p.description){
  const description=document.createElement('p');description.className='product-card__description';
  description.textContent=p.description.replace(/<[^>]*>/g,' ').replace(/\s+/g,' ').trim();
  if(description.textContent)body.append(description);
 }
 const storesSection=document.createElement('section');storesSection.className='product-card__section';
 const storesTitle=document.createElement('h4');storesTitle.textContent='По складам';
 const storesList=document.createElement('dl');storesList.className='product-card__details';
 for(const store of p.stores||[])storesList.append(detail(store.name||'Склад',store.quantity??'неизвестно'));
 storesSection.append(storesTitle);
 if(storesList.children.length)storesSection.append(storesList);
 else{const empty=document.createElement('p');empty.textContent='Данные по складам не указаны';storesSection.append(empty);}
 body.append(storesSection);
 const properties=Object.entries(p.properties||{}).filter(([key,value])=>
  value!=null&&value!==''&&!/CERT|SERT|СЕРТИФ/i.test(key));
 if(properties.length){
  const section=document.createElement('section');section.className='product-card__section';
  const title=document.createElement('h4');title.textContent='Характеристики';
  const list=document.createElement('dl');list.className='product-card__details';
  for(const [key,value] of properties)list.append(detail(propertyLabels[key]||key.replaceAll('_',' '),value));
  section.append(title,list);body.append(section);
 }
 const certificates=document.createElement('section');certificates.className='product-card__section';
 const certTitle=document.createElement('h4');certTitle.textContent='Сертификаты';certificates.append(certTitle);
 if(p.certificates?.length){for(const [index,link] of p.certificates.entries()){
  const a=document.createElement('a');a.href=link;a.textContent=`Сертификат ${index+1} ↗`;
  a.target='_blank';a.rel='noopener noreferrer';certificates.append(a);
 }}else{const empty=document.createElement('p');empty.textContent='В данных каталога не найдены';certificates.append(empty);}
 body.append(certificates);
 if(reason){const note=document.createElement('p');note.className='product-card__note';note.textContent=`Почему аналог: ${reason}`;body.append(note);}
 for(const warning of p.warnings||[]){const note=document.createElement('p');note.className='product-card__warning';note.textContent=warning;body.append(note);}
 card.append(body);

 const actions=document.createElement('div');actions.className='product-card__actions';
 const label=document.createElement('label');label.textContent='Количество';
 const quantity=document.createElement('input');quantity.type='number';quantity.min=p.minimum;
 quantity.step=p.minimum;quantity.value=p.minimum;
 quantity.max=p.stock??'';quantity.setAttribute('aria-label','Количество '+p.name);
 label.append(quantity);
 const add=button('В корзину',()=>action(async()=>{
  if(!quantity.reportValidity())return;
  proposal(await api('/api/cart/proposals',{product_id:p.id,quantity:quantity.value}));
 }));
 add.disabled=p.stock==null||Number(p.stock)<=0||(p.warnings||[]).length>0;
 actions.append(label,add);card.append(actions);
 return card;
}
function cards(products,analogs=[]){
 const list=document.createElement('div');list.className='cards';
 for(const p of products)list.append(productCard(p));
 for(const analog of analogs)list.append(productCard(analog.product,analog.reason));
 $('conversation').append(list);
}
async function send(message){await action(async()=>{
 bubble(message,'user');$('message').value='';
 const result=await api('/api/chat',{message,attachment_text:attachmentText});attachmentText='';$('attachment').textContent='';
 const products=result.products||[],analogs=result.analogs||[];
 if(products.length||analogs.length)cards(products,analogs);
 else if(!result.proposal)bubble(result.message);
 if(result.proposal)proposal(result.proposal);
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
