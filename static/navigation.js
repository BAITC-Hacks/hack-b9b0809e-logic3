// Keep the live chat DOM (and its event handlers) while visiting the cart.
// No messages or uploaded document contents are copied to persistent storage.
function setupChatNavigation(refreshCart) {
  const chat = document.getElementById('conversation');
  const cart = document.getElementById('cart-panel');
  const composer = document.getElementById('composer');
  const profile = document.getElementById('profile-panel');
  const panels = {chat, cart, profile};
  const error = document.createElement('p');
  error.setAttribute('role', 'alert');
  error.className = 'error';
  cart.prepend(error);
  const scroll = {chat: 0, cart: 0, profile: 0};
  const current = () => location.pathname === '/profile' ? 'profile' : location.pathname === '/cart' ? 'cart' : 'chat';
  let active = current();

  function show() {
    active = current();
    chat.hidden = composer.hidden = active !== 'chat';
    cart.hidden = active !== 'cart';
    profile.hidden = active !== 'profile';
    // Restore only after unhiding: hidden containers have no scrollable height.
    panels[active].scrollTop = scroll[active];
    if(active === 'profile') window.dispatchEvent(new Event('profile:open'));
  }

  async function updateCart() {
    error.textContent = '';
    try { await refreshCart(); }
    catch (e) { error.textContent = e.message; }
  }

  function navigate(path, push = true) {
    scroll[active] = panels[active].scrollTop;
    if (push && path !== location.pathname) history.pushState(null, '', path);
    show();
    // The chat is retained locally, but the basket always comes from the server.
    void updateCart();
  }

  document.addEventListener('click', event => {
    if (event.defaultPrevented || event.button !== 0 || event.ctrlKey ||
        event.metaKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest('a[href]');
    if (!link || link.hasAttribute('download') ||
        (link.target && link.target !== '_self')) return;
    const url = new URL(link.href, location.href);
    if (url.origin !== location.origin || url.search || url.hash ||
        !['/', '/cart', '/profile'].includes(url.pathname)) return;
    event.preventDefault();
    navigate(url.pathname);
  });
  window.addEventListener('popstate', () => navigate(location.pathname, false));
  window.addEventListener('pageshow', event => {
    if (event.persisted) { show(); void updateCart(); }
  });
  show();
}
