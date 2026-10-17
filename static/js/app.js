/* NarizMart storefront UI helpers (drawers, toast, art hydration, PWA registration). */
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  let toastT;

  window.showToast = function (msg) {
    const el = $('#toast');
    if (!el) return;
    el.textContent = msg;
    el.classList.add('show');
    clearTimeout(toastT);
    toastT = setTimeout(() => el.classList.remove('show'), 2400);
  };

  window.closePanels = function () {
    document.querySelectorAll('.scrim,.drawer,.modal').forEach((x) => x.classList.remove('open'));
    document.body.style.overflow = '';
  };
  function open(id) {
    closePanels();
    $('#scrim').classList.add('open');
    $(id).classList.add('open');
    document.body.style.overflow = 'hidden';
  }
  window.openCart = () => open('#cartDrawer');
  window.openMenu = () => open('#menuDrawer');

  // Illustrations + icons are drawn client-side from data attributes (see narizmart-art.js).
  function hydrate(root) {
    if (typeof art !== 'function') return;
    root.querySelectorAll('[data-art]').forEach((e) => { e.innerHTML = art(e.dataset.art); });
    root.querySelectorAll('[data-art-into]').forEach((e) => { e.innerHTML = art(e.dataset.artInto); });
    hydrateIcons(root);
  }
  document.addEventListener('DOMContentLoaded', () => hydrate(document));
  document.addEventListener('htmx:load', (e) => hydrate(e.target));

  // Declarative UI actions (no inline handlers, so the CSP can forbid them)
  const actions = { 'open-cart': () => openCart(), 'open-menu': () => openMenu(), 'close-panels': () => closePanels() };
  document.addEventListener('click', (e) => {
    const el = e.target.closest('[data-action]');
    if (el && actions[el.dataset.action]) actions[el.dataset.action]();
  });
  document.addEventListener('submit', (e) => {
    const msg = e.target.dataset && e.target.dataset.confirm;
    if (msg && !window.confirm(msg)) e.preventDefault();
  });

  // Checkout: show the new-address fields only when "new address" (or no picker) applies
  function syncAddressFields() {
    const box = $('[data-new-address-fields]');
    if (!box) return;
    const picked = document.querySelector('input[name="address_choice"]:checked');
    box.hidden = !!picked && picked.value !== 'new';
  }
  document.addEventListener('change', (e) => { if (e.target.name === 'address_choice') syncAddressFields(); });
  document.addEventListener('DOMContentLoaded', syncAddressFields);

  // Variant picker: show chosen names, price/stock for the selection, and grey out unavailable options.
  function initPicker(root) {
    const picker = root.closest ? root.closest('[data-variant-picker]') || root : root;
    if (!picker || !picker.querySelector) return;
    const variants = [...picker.querySelectorAll('.vdata li')].map((li) => ({
      color: li.dataset.color, size: li.dataset.size, stock: +li.dataset.stock, price: li.dataset.price,
    }));
    const status = picker.querySelector('[data-variant-status]');
    const priceEl = (picker.closest('.modal-details, .modal-details') || document).querySelector('[data-price-text]');
    const val = (name) => (picker.querySelector(`input[name="${name}"]:checked`) || {}).value || '';
    const fmt = (n) => (picker.dataset.currency || '') + Number(n).toLocaleString('en-US', { minimumFractionDigits: Number(n) % 1 ? 2 : 0, maximumFractionDigits: 2 });
    function refresh() {
      const color = val('color'), size = val('size');
      ['color', 'size'].forEach((name) => {
        const chosen = picker.querySelector(`input[name="${name}"]:checked`);
        const out = picker.querySelector(`[data-chosen="${name}"]`);
        if (out) out.textContent = chosen ? chosen.dataset.name : 'choose';
      });
      picker.querySelectorAll('input[name="size"]').forEach((input) => {
        const ok = variants.some((v) => v.size === input.value && (!color || v.color === color) && v.stock > 0);
        input.closest('label').classList.toggle('is-unavailable', !ok);
      });
      picker.querySelectorAll('input[name="color"]').forEach((input) => {
        const ok = variants.some((v) => v.color === input.value && (!size || v.size === size) && v.stock > 0);
        input.closest('label').classList.toggle('is-unavailable', !ok);
      });
      const hasColor = !!picker.querySelector('input[name="color"]'), hasSize = !!picker.querySelector('input[name="size"]');
      const match = variants.find((v) => (v.color || '') === (hasColor ? color : '') && (v.size || '') === (hasSize ? size : ''));
      if (match && priceEl) priceEl.textContent = fmt(match.price);
      if (!status) return;
      if (match) {
        status.textContent = match.stock < 1 ? 'Out of stock' : match.stock <= 5 ? `Only ${match.stock} left` : 'In stock';
        status.classList.toggle('oos', match.stock < 1);
      } else if ((hasColor && color) || (hasSize && size)) {
        status.textContent = 'Choose all options to see availability';
      } else { status.textContent = ''; }
    }
    if (!picker.dataset.ready) { picker.dataset.ready = '1'; picker.addEventListener('change', refresh); }
    refresh();
  }
  const initPickers = (root) => root.querySelectorAll('[data-variant-picker]').forEach(initPicker);
  document.addEventListener('DOMContentLoaded', () => initPickers(document));
  document.addEventListener('htmx:load', (e) => initPickers(e.target));

  // Product gallery. One photo -> plain image (no slider). Several -> Swiper slider with arrows, dots, swipe and thumbnails.
  // Choosing a colour rebuilds the list: that colour's photos first, then the shared ones.
  function initGallery(gallery) {
    if (!gallery.matches || !gallery.matches('[data-gallery]') || gallery.dataset.ready) return;
    gallery.dataset.ready = '1';
    const items = [...gallery.querySelectorAll('[data-gallery-data] li')].map((li) => ({ color: li.dataset.color, src: li.dataset.src, thumb: li.dataset.thumb || li.dataset.src, alt: li.dataset.alt }));
    if (!items.length) return;
    const single = gallery.querySelector('[data-gallery-single]');
    const sliderEl = gallery.querySelector('[data-gallery-swiper]');
    const thumbs = gallery.querySelector('[data-gallery-thumbs]');
    let swiper = null;

    const img = (item, cls) => { const i = document.createElement('img'); i.src = item.src; i.alt = item.alt; if (cls) i.className = cls; i.decoding = 'async'; return i; };
    function teardown() { if (swiper) { swiper.destroy(true, true); swiper = null; } sliderEl.querySelector('.swiper-wrapper').textContent = ''; }

    function render(color) {
      const shared = items.filter((i) => !i.color);
      const list = color ? items.filter((i) => i.color === color).concat(shared) : (shared.length ? shared : items);
      teardown();
      thumbs.textContent = '';
      if (list.length <= 1) {                       // single photo: no slider, no thumbnails
        sliderEl.hidden = true; single.hidden = false;
        if (list.length) { single.textContent = ''; single.appendChild(img(list[0], 'media-img')); }
        return;
      }
      single.hidden = true; sliderEl.hidden = false;
      const wrapper = sliderEl.querySelector('.swiper-wrapper');
      list.forEach((item) => {
        const slide = document.createElement('div'); slide.className = 'swiper-slide';
        slide.appendChild(img(item, 'media-img')); wrapper.appendChild(slide);
      });
      const buttons = list.map((item, index) => {
        const b = document.createElement('button');
        b.type = 'button'; b.setAttribute('aria-label', `Show photo ${index + 1}`);
        const t = img({ ...item, src: item.thumb }); t.alt = ''; b.appendChild(t); thumbs.appendChild(b);
        return b;
      });
      const mark = (i) => buttons.forEach((b, n) => b.classList.toggle('is-active', n === i));
      if (window.Swiper) {
        swiper = new Swiper(sliderEl, {
          loop: false, keyboard: { enabled: true }, a11y: true,
          navigation: { nextEl: sliderEl.querySelector('.swiper-button-next'), prevEl: sliderEl.querySelector('.swiper-button-prev') },
          pagination: { el: sliderEl.querySelector('.swiper-pagination'), clickable: true },
          on: { slideChange: (s) => mark(s.activeIndex) },
        });
        buttons.forEach((b, n) => b.addEventListener('click', () => swiper.slideTo(n)));
      } else {                                      // Swiper failed to load: fall back to thumbnail swapping
        single.hidden = false; sliderEl.hidden = true; single.textContent = ''; single.appendChild(img(list[0], 'media-img'));
        buttons.forEach((b, n) => b.addEventListener('click', () => { single.textContent = ''; single.appendChild(img(list[n], 'media-img')); mark(n); }));
      }
      mark(0);
    }
    gallery.addEventListener('gallery:color', (e) => render(e.detail));
    render('');
  }
  const initGalleries = (root) => root.querySelectorAll('[data-gallery]').forEach(initGallery);
  document.addEventListener('DOMContentLoaded', () => initGalleries(document));
  document.addEventListener('htmx:load', (e) => initGalleries(e.target));
  // choosing a colour in the variant picker swaps the gallery next to it
  document.addEventListener('change', (e) => {
    if (e.target.name !== 'color') return;
    const scope = e.target.closest('.detail-grid, .modal-grid');
    const gallery = scope && scope.querySelector('[data-gallery]');
    if (gallery) gallery.dispatchEvent(new CustomEvent('gallery:color', { detail: e.target.value }));
  });

  // Server-driven UI events (HX-Trigger)
  document.addEventListener('toast', (e) => showToast(e.detail.value));
  document.addEventListener('openCart', () => { closePanels(); openCart(); });
  document.addEventListener('htmx:afterRequest', (e) => {
    if (e.target.closest && e.target.closest('.qv-hit') && e.detail.target && e.detail.target.id === 'quickBody' && e.detail.successful) {
      $('#scrim').classList.add('open');
      $('#quickModal').classList.add('open');
      document.body.style.overflow = 'hidden';
    }
  });
  document.body.addEventListener('htmx:responseError', (e) => { if (e.detail.xhr.status !== 429) showToast('Something went wrong — please try again'); });
  document.body.addEventListener('htmx:sendError', () => showToast('You appear to be offline'));

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closePanels();
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      (innerWidth < 820 ? $('#mobileSearch') : $('#desktopSearch')).focus();
    }
  });

  // PWA: register the service worker; when a new release is waiting, offer a one-tap refresh.
  // Home slider (Swiper): sliding animation, drag/swipe, arrows, dots, autoplay with a pause button (also pauses on hover)
  document.querySelectorAll('[data-hero-swiper]').forEach((el) => {
    const hero = el.closest('[data-hero]');
    const slides = el.querySelectorAll('.swiper-slide').length;
    if (slides < 2 || typeof Swiper === 'undefined') return;
    const seconds = parseInt(hero.dataset.seconds, 10) || 0;
    const swiper = new Swiper(el, {
      loop: true, speed: 700, grabCursor: true, a11y: { enabled: true },
      autoplay: seconds > 0 ? { delay: seconds * 1000, disableOnInteraction: false, pauseOnMouseEnter: true } : false,
      navigation: { nextEl: el.querySelector('.hero-next'), prevEl: el.querySelector('.hero-prev') },
      pagination: { el: el.querySelector('.swiper-pagination'), clickable: true },
    });
    const pause = el.querySelector('.hero-pause');
    if (!pause) return;
    if (seconds <= 0) { pause.remove(); return; }
    pause.addEventListener('click', () => {
      const paused = pause.getAttribute('aria-pressed') !== 'true';
      pause.setAttribute('aria-pressed', paused ? 'true' : 'false');
      pause.setAttribute('aria-label', paused ? 'Play slideshow' : 'Pause slideshow');
      if (paused) swiper.autoplay.stop(); else swiper.autoplay.start();
    });
  });

  // Categories: a simple slider: swipe / drag sideways, with arrow icons and dots underneath (hidden when everything already fits)
  document.querySelectorAll('[data-category-swiper]').forEach((el) => {
    if (typeof Swiper === 'undefined') return;
    const nav = el.parentElement.querySelector('[data-category-nav]');
    const swiper = new Swiper(el, {
      slidesPerView: 2.4, spaceBetween: 12, watchOverflow: true, a11y: { enabled: true }, grabCursor: true,
      mousewheel: { forceToAxis: true },
      navigation: { nextEl: nav.querySelector('.cat-next'), prevEl: nav.querySelector('.cat-prev'), disabledClass: 'swiper-button-disabled' },
      pagination: { el: nav.querySelector('.cat-dots'), clickable: true },
      breakpoints: { 600: { slidesPerView: 3.4 }, 820: { slidesPerView: 4.4 }, 1100: { slidesPerView: 5.3, spaceBetween: 16 } },
    });
    const sync = () => { nav.hidden = swiper.isLocked; };
    swiper.on('lock', sync); swiper.on('unlock', sync); swiper.on('resize', sync); sync();
  });

  // Promo banners: slide by themselves, arrows, dots, swipe; pauses on hover
  document.querySelectorAll('[data-promo-swiper]').forEach((el) => {
    const wrap = el.closest('[data-promo]');
    if (typeof Swiper === 'undefined' || el.querySelectorAll('.swiper-slide').length < 2) return;
    const seconds = parseInt(wrap.dataset.seconds, 10) || 0;
    new Swiper(el, {
      loop: true, speed: 700, grabCursor: true, a11y: { enabled: true },
      autoplay: seconds > 0 ? { delay: seconds * 1000, disableOnInteraction: false, pauseOnMouseEnter: true } : false,
      navigation: { nextEl: el.querySelector('.promo-next'), prevEl: el.querySelector('.promo-prev') },
      pagination: { el: el.querySelector('.promo-dots'), clickable: true },
    });
  });

  // A dismissed prompt stays away for the rest of the visit; the next visit offers the update again.
  const DISMISS_KEY = 'bm-update-dismissed';
  const dismissed = () => { try { return sessionStorage.getItem(DISMISS_KEY) === '1'; } catch (e) { return false; } };
  function offerUpdate(reg) {
    if (dismissed() || document.getElementById('update-banner')) return;
    const bar = document.createElement('div');
    bar.id = 'update-banner'; bar.className = 'update-banner'; bar.setAttribute('role', 'status');
    const text = document.createElement('span'); text.textContent = 'A new version of NarizMart is ready.';
    const btn = document.createElement('button'); btn.type = 'button'; btn.textContent = 'Refresh';
    const later = document.createElement('button'); later.type = 'button'; later.className = 'update-later'; later.textContent = 'Later'; later.setAttribute('aria-label', 'Dismiss, update later');
    later.addEventListener('click', () => { try { sessionStorage.setItem(DISMISS_KEY, '1'); } catch (e) { /* storage blocked: it just may reappear */ } bar.remove(); });
    btn.addEventListener('click', () => {
      bar.remove();
      const waiting = reg.waiting || reg.installing;   // always the newest worker, never a stale reference
      if (waiting) waiting.postMessage({ type: 'SKIP_WAITING' });
      setTimeout(() => location.reload(), waiting ? 1500 : 0);   // controllerchange normally reloads first
    });
    bar.append(text, btn, later); document.body.appendChild(bar);
  }
  if ('serviceWorker' in navigator) {
    let reloading = false;
    navigator.serviceWorker.addEventListener('controllerchange', () => { if (!reloading && window.__hadController) { reloading = true; location.reload(); } });
    window.addEventListener('load', async () => {
      window.__hadController = !!navigator.serviceWorker.controller;
      try {
        const reg = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
        if (reg.waiting && navigator.serviceWorker.controller) offerUpdate(reg);
        reg.addEventListener('updatefound', () => {
          const incoming = reg.installing;
          if (incoming) incoming.addEventListener('statechange', () => {
            if (incoming.state === 'installed' && navigator.serviceWorker.controller) offerUpdate(reg);
          });
        });
      } catch (err) { /* unsupported context (private mode, embedded browsers): the site works without it */ }
    });
  }
  let deferredPrompt;
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredPrompt = e;
    const hint = document.createElement('button');
    hint.className = 'btn btn-dark install-btn';
    hint.textContent = 'Install app';
    hint.style.cssText = 'position:fixed;left:16px;bottom:80px;z-index:90;height:40px';
    hint.onclick = async () => { hint.remove(); deferredPrompt.prompt(); deferredPrompt = null; };
    document.body.appendChild(hint);
  });
})();
