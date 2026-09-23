// Account data is rendered as text; passwords/tokens never enter browser storage.
(async function setupAccounts(){
  const panel=document.getElementById('profile-panel');
  let user=null;
  const channel=typeof BroadcastChannel==='function'?new BroadcastChannel('ekt-account'):null;
  if(channel)channel.onmessage=()=>location.reload();
  window.addEventListener('pageshow',event=>{if(event.persisted)location.reload();});
  const el=id=>document.getElementById(id);
  const field=(form,name)=>form.elements.namedItem(name);
  const initials=name=>name.trim().split(/\s+/).slice(0,2).map(word=>Array.from(word)[0]).join('').toUpperCase();

  function display(next){
    user=next;
    el('account-loading').hidden=true;
    el('account-guest').hidden=!!user;
    el('account-member').hidden=!user;
    el('account-title').textContent=user?'Ваш профиль.':'Рады знакомству.';
    el('account-subtitle').textContent=user?'Личные данные и безопасность аккаунта.':'Ваш аккаунт в EKT Ассистенте.';
    const link=el('account-link');
    link.lastElementChild.textContent=user?user.name:'Войти';
    link.firstElementChild.textContent=user?initials(user.name):'↗';
    link.setAttribute('aria-label',user?'Открыть профиль: '+user.name:'Войти или создать аккаунт');
    if(!user)return;
    el('profile-avatar').textContent=initials(user.name);
    el('profile-name').textContent=user.name;
    el('profile-email').textContent=user.email;
    el('profile-company').textContent=user.company||'Не указана';
    el('profile-created').textContent=new Date(user.created_at*1000).toLocaleDateString('ru-RU',{day:'numeric',month:'long',year:'numeric'});
    const form=el('profile-form');
    for(const key of ['name','email','phone','city','company'])field(form,key).value=user[key]||'';
  }

  function message(form,text,success=false){
    const box=form.querySelector('.form-message');
    box.textContent=text;box.hidden=!text;box.classList.toggle('success',success);
  }

  function bindForm(id,handler){
    const form=el(id);
    form.addEventListener('submit',async event=>{
      event.preventDefault();
      if(form.dataset.busy||!form.reportValidity())return;
      message(form,'');
      const data=Object.fromEntries(new FormData(form));
      const controls=Array.from(form.querySelectorAll('input,button'));
      form.dataset.busy='1';form.setAttribute('aria-busy','true');
      controls.forEach(control=>control.disabled=true);
      try{await handler(data,form);}catch(error){message(form,error.message);}
      finally{controls.forEach(control=>control.disabled=false);delete form.dataset.busy;form.removeAttribute('aria-busy');}
    });
  }

  function transition(){
    channel?.postMessage('changed');
    // Reload only at an identity boundary; profile navigation preserves the live chat.
    location.replace('/profile');
  }

  function selectTab(tab,focus=false){
    for(const name of ['login','register']){
      const selected=name===tab;
      el(name+'-tab').setAttribute('aria-selected',String(selected));
      el(name+'-tab').tabIndex=selected?0:-1;
      el(name+'-form').hidden=!selected;
    }
    if(focus)el(tab+'-tab').focus();
  }

  try{
    const template=await fetch('/static/profile.html');
    if(!template.ok)throw new Error('Не удалось загрузить страницу профиля. Обновите страницу.');
    // Trusted, same-origin static template. User content uses textContent below.
    panel.innerHTML=await template.text();
    const session=await appReady;
    if(!session)throw new Error('Не удалось открыть сессию. Обновите страницу.');
    display(session.user);
    const notice=sessionStorage.getItem('ekt-account-notice');
    if(notice){el('account-notice').textContent=notice;el('account-notice').hidden=false;sessionStorage.removeItem('ekt-account-notice');}

    for(const tab of ['login','register']){
      el(tab+'-tab').addEventListener('click',()=>selectTab(tab));
      el(tab+'-tab').addEventListener('keydown',event=>{
        if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){
          event.preventDefault();selectTab(event.key==='Home'?'login':event.key==='End'?'register':tab==='login'?'register':'login',true);
        }
      });
    }
    for(const button of panel.querySelectorAll('[data-password]')){
      button.addEventListener('click',()=>{
        const input=el(button.dataset.password),show=input.type==='password';
        input.type=show?'text':'password';button.textContent=show?'Скрыть':'Показать';
        button.setAttribute('aria-label',show?'Скрыть пароль':'Показать пароль');
        button.setAttribute('aria-pressed',String(show));
      });
    }
    bindForm('login-form',async data=>{await api('/api/auth/login',data);transition();});
    bindForm('register-form',async data=>{
      if(data.password!==data.confirm_password)throw new Error('Пароли не совпадают. Проверьте повторный ввод.');
      delete data.confirm_password;
      await api('/api/auth/register',data);transition();
    });
    bindForm('profile-form',async(data,form)=>{
      delete data.email;
      const result=await api('/api/auth/profile',data);display(result.user);
      message(form,'Изменения сохранены.',true);
    });
    bindForm('password-form',async data=>{
      if(data.new_password!==data.confirm_password)throw new Error('Новые пароли не совпадают.');
      delete data.confirm_password;
      const result=await api('/api/auth/password',data);
      sessionStorage.setItem('ekt-account-notice',result.message);transition();
    });
    el('account-logout').addEventListener('click',async()=>{
      const button=el('account-logout');button.disabled=true;el('logout-error').hidden=true;
      try{await api('/api/auth/logout',{});transition();}
      catch(error){el('logout-error').textContent=error.message;el('logout-error').hidden=false;button.disabled=false;}
    });
    window.addEventListener('profile:open',async()=>{
      try{
        const result=await api('/api/auth/me');
        if(result.user?.id!==user?.id){location.reload();return;}
      }catch(error){el('account-notice').textContent=error.message;el('account-notice').hidden=false;}
    });
  }catch(error){
    const notice=document.createElement('p');notice.className='form-message';notice.setAttribute('role','alert');notice.textContent=error.message;panel.replaceChildren(notice);
  }
})();
