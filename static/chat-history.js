// Account history is stored on the server; each dialog has its own context.
let activeConversationId = null;
async function setupChatHistory(user) {
  const bar=document.createElement('div');bar.className='history-bar';
  const notice=document.createElement('small');
  notice.textContent=user?'Диалоги сохраняются в аккаунте до удаления вами.':'Войдите в аккаунт, чтобы сохранять и выбирать диалоги.';
  bar.append(notice);document.getElementById('composer').prepend(bar);
  if(!user)return;
  const initialIntro=document.querySelector('#conversation .intro')?.cloneNode(true);
  const title=document.createElement('strong');title.textContent='Новый чат';bar.prepend(title);
  const create=button('Новый чат',()=>action(async()=>{
    const data=await api('/api/chat/conversations/new',{});
    setConversation(data);document.getElementById('message').value='';
    attachmentText='';document.getElementById('attachment').textContent='';
    date.value='';await list();openChat();
  }));
  const remove=button('Удалить диалог',()=>action(async()=>{
    if(!activeConversationId||!window.confirm('Удалить выбранный диалог и его сообщения?'))return;
    await api('/api/chat/history/clear',{});
    activeConversationId=null;date.value='';await restore();await list();
  }));
  const picker=document.createElement('details');picker.className='history-picker';
  const summary=document.createElement('summary');summary.textContent='Мои диалоги';
  const label=document.createElement('label');label.textContent='Дата переписки ';
  const date=document.createElement('input');date.type='date';date.setAttribute('aria-label','Дата переписки');label.append(date);
  const reset=button('Все даты',()=>action(async()=>{date.value='';await list();}));
  const rows=document.createElement('div');rows.className='history-list';
  const more=button('Ещё диалоги',()=>action(()=>list(true)));more.hidden=true;
  picker.append(summary,label,reset,rows,more);bar.append(create,remove,picker);
  let offset=0, entries=[], hasOlder=false;
  const localDate=stamp=>new Date(stamp*1000).toLocaleString('ru-RU');
  function openChat(){
    document.querySelector('a.brand').click();
    picker.open=false;
  }
  function setConversation(data){
    activeConversationId=data.conversation?.id||null;
    title.textContent=data.conversation?.title||'Новый чат';
    remove.disabled=!activeConversationId;
    entries=data.entries;hasOlder=data.has_more;
    paint();
  }
  function paint(){
    const view=document.getElementById('conversation');
    view.replaceChildren();
    const heading=document.createElement('h2');heading.id='conversation-title';heading.textContent=title.textContent;view.append(heading);
    if(!entries.length){if(initialIntro)view.append(initialIntro.cloneNode(true));return;}
    if(hasOlder){
      const older=button('Предыдущие сообщения',()=>action(async()=>{
        const before=entries[0].id;
        const data=await api('/api/chat/history?conversation_id='+activeConversationId+'&before='+before);
        const height=view.scrollHeight,top=view.scrollTop;
        entries=[...data.entries,...entries];hasOlder=data.has_more;paint();
        view.scrollTop=top+view.scrollHeight-height;
      }));view.append(older);
    }
    const note=document.createElement('p');note.className='history-notice';
    note.textContent='Сохранённые цены и остатки относятся к моменту ответа. Перед добавлением они проверяются заново.';
    view.append(note);
    for(const entry of entries){
      const userBubble=bubble(entry.message,'user');
      const stamp=document.createElement('small');stamp.textContent=localDate(entry.created_at);stamp.className='history-time';
      userBubble.prepend(stamp);
      renderChatResult(entry.response,true);
    }
  }
  async function list(append=false){
    if(!append){offset=0;rows.replaceChildren();}
    const params=new URLSearchParams({offset:String(offset)});
    if(date.value){
      const from=new Date(date.value+'T00:00:00'),to=new Date(from);to.setDate(to.getDate()+1);
      params.set('start',String(Math.floor(from.getTime()/1000)));
      params.set('end',String(Math.floor(to.getTime()/1000)));
    }
    const data=await api('/api/chat/conversations?'+params);
    for(const item of data.conversations){
      const select=button('',()=>action(async()=>{
        const dialog=await api('/api/chat/conversations/select',{conversation_id:item.id});
        setConversation(dialog);document.getElementById('message').value='';
        attachmentText='';document.getElementById('attachment').textContent='';await list();openChat();
      }));
      select.className='history-item';
      select.textContent=item.title+' · '+localDate(item.updated_at);
      select.setAttribute('aria-pressed',String(item.id===activeConversationId));rows.append(select);
    }
    offset+=data.conversations.length;more.hidden=!data.has_more;
    if(!rows.children.length)rows.textContent='За выбранную дату диалогов нет.';
  }
  async function restore(){
    const data=await api('/api/chat/history');
    if(data.conversation){
      setConversation(await api('/api/chat/conversations/select',{conversation_id:data.conversation.id}));
    }else setConversation(data);
  }
  date.addEventListener('change',()=>action(()=>list()));
  window.addEventListener('conversation:saved',async event=>{
    activeConversationId=event.detail;
    const savedId=activeConversationId;
    remove.disabled=false;
    try{
      const data=await api('/api/chat/history?conversation_id='+savedId);
      if(activeConversationId!==savedId)return;
      title.textContent=data.conversation.title;
      const heading=document.getElementById('conversation-title');
      if(heading)heading.textContent=title.textContent;
      if(!entries.length)hasOlder=data.has_more;
      const merged=new Map(entries.map(entry=>[entry.id,entry]));
      for(const entry of data.entries)merged.set(entry.id,entry);
      entries=[...merged.values()].sort((a,b)=>a.id-b.id);
      // Do not rerender live confirmation controls after a response.
      await list();
    }catch(error){notice.textContent='Не удалось обновить список диалогов: '+error.message;}
  });
  try{await restore();await list();}
  catch(error){bubble('Не удалось загрузить диалоги: '+error.message,'error');}
}
