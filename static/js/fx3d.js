/* NarizMart 3D effects: pointer tilt on cards and scroll reveal. No dependencies. */
(() => {
  const fine = window.matchMedia('(hover:hover) and (pointer:fine)').matches;
  const calm = window.matchMedia('(prefers-reduced-motion:reduce)').matches;
  const SELECTOR = '.product-card,.category,.benefit,.hero-object';
  const MAX = calm ? 4 : 9;   // gentler tilt when the system asks for reduced motion

  function bindTilt(el) {
    if (el.dataset.tilt) return;
    el.dataset.tilt = '1';
    el.classList.add('tilt');
    el.addEventListener('pointermove', (e) => {
      if (e.pointerType !== 'mouse') return;
      const r = el.getBoundingClientRect();
      const px = (e.clientX - r.left) / r.width - 0.5;
      const py = (e.clientY - r.top) / r.height - 0.5;
      el.classList.add('is-tilting');
      el.style.setProperty('--ry', `${(px * MAX * 2).toFixed(2)}deg`);
      el.style.setProperty('--rx', `${(-py * MAX * 2).toFixed(2)}deg`);
      el.style.setProperty('--ty', '-6px');
    });
    el.addEventListener('pointerleave', () => {
      el.classList.remove('is-tilting');
      el.style.setProperty('--rx', '0deg');
      el.style.setProperty('--ry', '0deg');
      el.style.setProperty('--ty', '0');
    });
  }

  const io = 'IntersectionObserver' in window
    ? new IntersectionObserver((entries) => entries.forEach((en) => {
        if (en.isIntersecting) { en.target.classList.add('in'); io.unobserve(en.target); }
      }), { threshold: 0.12 })
    : null;

  function scan(root = document) {
    root.querySelectorAll(SELECTOR).forEach((el) => {
      if (fine && !el.classList.contains('hero-object')) bindTilt(el);
    });
    root.querySelectorAll('.product-card,.section-top,.story-section,.promo-wrap,.newsletter').forEach((el) => {
      if (el.dataset.rv || !io) return;
      el.dataset.rv = '1';
      el.classList.add('reveal');
      io.observe(el);
    });
  }

  // the little cart badge bumps when the number changes
  function watchCart() {
    const badge = document.getElementById('cartCount');
    if (!badge || !('MutationObserver' in window)) return;
    new MutationObserver(() => {
      badge.classList.remove('bump'); void badge.offsetWidth; badge.classList.add('bump');
    }).observe(badge, { childList: true, characterData: true, subtree: true });
  }

  // Shop All dropdown: opens on hover and keyboard focus; on touch screens the first tap opens it, Escape and outside clicks close it
  function initNavDrop() {
    document.querySelectorAll('[data-nav-drop]').forEach((drop) => {
      const btn = drop.querySelector('.nav-drop-btn');
      const set = (open) => { drop.classList.toggle('open', open); btn.setAttribute('aria-expanded', open ? 'true' : 'false'); };
      drop.addEventListener('mouseenter', () => set(true));
      drop.addEventListener('mouseleave', () => set(false));
      drop.addEventListener('focusin', () => set(true));
      drop.addEventListener('focusout', (e) => { if (!drop.contains(e.relatedTarget)) set(false); });
      btn.addEventListener('click', (e) => {
        if (window.matchMedia('(hover:none)').matches && !drop.classList.contains('open')) { e.preventDefault(); set(true); }
      });
      document.addEventListener('click', (e) => { if (!drop.contains(e.target)) set(false); });
      document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { set(false); btn.blur(); } });
    });
  }

  // Product page: the photo magnifies under the mouse and follows it (desktop only; touch screens keep the normal swipe gallery)
  function initZoom() {
    if (!fine) return;
    document.querySelectorAll('.detail-art-wrap .gallery-main').forEach((box) => {
      if (box.dataset.zoom) return;
      box.dataset.zoom = '1';
      box.classList.add('zoomable');
      box.addEventListener('pointermove', (e) => {
        if (e.pointerType !== 'mouse' || e.buttons) { box.classList.remove('is-zooming'); return; }   // dragging the slider is not zooming
        const r = box.getBoundingClientRect();
        box.style.setProperty('--zx', `${((e.clientX - r.left) / r.width * 100).toFixed(1)}%`);
        box.style.setProperty('--zy', `${((e.clientY - r.top) / r.height * 100).toFixed(1)}%`);
        box.classList.add('is-zooming');
      });
      box.addEventListener('pointerleave', () => box.classList.remove('is-zooming'));
    });
  }

  const start = () => {
    initNavDrop();
    initZoom();
    watchCart();
    scan();
    document.body.addEventListener('htmx:afterSwap', (e) => { scan(e.target); initZoom(); });
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
