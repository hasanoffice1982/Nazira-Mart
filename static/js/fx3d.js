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

  const start = () => {
    watchCart();
    scan();
    document.body.addEventListener('htmx:afterSwap', (e) => scan(e.target));
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
