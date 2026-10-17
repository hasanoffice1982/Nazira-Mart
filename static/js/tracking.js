/* Advertising / analytics tags. IDs come from Admin -> Site settings (read from #bm-tracking), nothing is hard-coded.
 * Loads Meta Pixel and/or the Google tag, then sends shopping events: view_item, add_to_cart, begin_checkout, purchase. */
(function () {
  const root = document.getElementById('bm-tracking');
  if (!root) return;
  const metaId = root.dataset.meta, googleId = root.dataset.google, sendEvents = root.dataset.events === '1';

  function loadScript(src) { const s = document.createElement('script'); s.async = true; s.src = src; document.head.appendChild(s); }

  // --- Meta Pixel (standard bootstrap) ---
  if (metaId) {
    (function (f, b, e, v, n, t, s) {
      if (f.fbq) return; n = f.fbq = function () { n.callMethod ? n.callMethod.apply(n, arguments) : n.queue.push(arguments); };
      if (!f._fbq) f._fbq = n; n.push = n; n.loaded = true; n.version = '2.0'; n.queue = [];
      t = b.createElement(e); t.async = true; t.src = v; s = b.getElementsByTagName(e)[0]; s.parentNode.insertBefore(t, s);
    })(window, document, 'script', 'https://connect.facebook.net/en_US/fbevents.js');
    fbq('init', metaId);
    fbq('track', 'PageView');
  }

  // --- Google tag (GA4 / Ads via gtag.js, or a Tag Manager container) ---
  const viaGtm = /^GTM-/.test(googleId || '');
  window.dataLayer = window.dataLayer || [];
  window.gtag = window.gtag || function () { window.dataLayer.push(arguments); };
  if (googleId) {
    if (viaGtm) {
      window.dataLayer.push({ 'gtm.start': Date.now(), event: 'gtm.js' });
      loadScript('https://www.googletagmanager.com/gtm.js?id=' + encodeURIComponent(googleId));
    } else {
      gtag('js', new Date());
      gtag('config', googleId);
      loadScript('https://www.googletagmanager.com/gtag/js?id=' + encodeURIComponent(googleId));
    }
  }

  const META_EVENTS = { view_item: 'ViewContent', add_to_cart: 'AddToCart', begin_checkout: 'InitiateCheckout', purchase: 'Purchase' };

  function send(d) {
    if (!sendEvents || !d || !META_EVENTS[d.event]) return;
    const items = d.items || [];
    if (metaId && window.fbq) {
      fbq('track', META_EVENTS[d.event], {
        content_ids: items.map((i) => i.id), content_type: 'product', contents: items.map((i) => ({ id: i.id, quantity: i.quantity })),
        content_name: items.length === 1 ? items[0].name : undefined, value: d.value, currency: d.currency, num_items: items.reduce((n, i) => n + i.quantity, 0),
      });
    }
    if (googleId) {
      const payload = { currency: d.currency, value: d.value, items: items.map((i) => ({ item_id: i.id, item_name: i.name, item_category: i.category, price: i.price, quantity: i.quantity })) };
      if (d.transaction_id) payload.transaction_id = d.transaction_id;
      if (viaGtm) window.dataLayer.push({ ecommerce: null }, { event: d.event, ecommerce: payload });
      else gtag('event', d.event, payload);
    }
  }
  window.bmTrack = send;

  // Page-level event (product page, checkout, order confirmation). A purchase is sent once per order, even on refresh.
  const holder = document.getElementById('bm-track-data');
  if (holder) {
    try {
      const data = JSON.parse(holder.textContent);
      let once = true;
      if (data.event === 'purchase') {
        const key = 'bm-purchase-' + data.transaction_id;
        try { once = !localStorage.getItem(key); if (once) localStorage.setItem(key, '1'); } catch (e) { /* storage blocked: send anyway */ }
      }
      if (once) send(data);
    } catch (e) { /* malformed data: skip */ }
  }

  // Add to cart: the server announces it in the HTMX response (HX-Trigger: track)
  document.addEventListener('track', (e) => send(e.detail));
})();
