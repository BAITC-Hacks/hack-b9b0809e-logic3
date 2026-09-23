// Keep the live chat DOM (and its event handlers) while visiting the cart.
// No messages or uploaded document contents are copied to persistent storage.
function setupChatNavigation(refreshCart) {
  const chat = document.getElementById('conversation');
  const cart = document.getElementById('cart-panel');
  const composer = document.getElementById('composer');
  const error = document.createElement('p');
  error.setAttribute('role', 'alert');
  error.className = 'error';
  cart.prepend(error);
  const scroll = {chat: 0, cart: 0};
  let active = location.pathname === '/cart' ? 'cart' : 'chat';

  function show() {
    active = location.pathname === '/cart' ? 'cart' : 'chat';
    chat.hidden = composer.hidden = active === 'cart';
    cart.hidden = active !== 'cart';
    // Restore only after unhiding: hidden containers have no scrollable height.
    (active === 'cart' ? cart : chat).scrollTop = scroll[active];
  }

  async function updateCart() {
    error.textContent = '';
    try { await refreshCart(); }
    catch (e) { error.textContent = e.message; }
  }

  function navigate(path, push = true) {
    scroll[active] = (active === 'cart' ? cart : chat).scrollTop;
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
        !['/', '/cart'].includes(url.pathname)) return;
    event.preventDefault();
    navigate(url.pathname);
  });
  window.addEventListener('popstate', () => navigate(location.pathname, false));
  window.addEventListener('pageshow', event => {
    if (event.persisted) { show(); void updateCart(); }
  });
  show();
}
