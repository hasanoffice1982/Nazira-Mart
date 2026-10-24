/* Checkout autosave for signed-out visitors: remembers name, email and phone even if the order is never placed.
   An external file because the site's Content-Security-Policy blocks inline scripts. */
(function () {
  var script = document.currentScript, form = document.querySelector("form.auth-card");
  if (!script || !form || !form.elements.csrfmiddlewaretoken) return;
  var url = script.dataset.url, fields = ["email", "full_name", "phone"], timer;

  function send() {
    var data = new FormData();
    fields.forEach(function (n) {
      var el = form.elements[n];
      if (el && el.value) data.append(n === "full_name" ? "name" : n, el.value);
    });
    data.append("csrfmiddlewaretoken", form.elements.csrfmiddlewaretoken.value);
    try { fetch(url, { method: "POST", body: data, credentials: "same-origin", keepalive: true }); } catch (e) { /* tracking must never break checkout */ }
  }

  fields.forEach(function (n) {
    var el = form.elements[n];
    if (el) el.addEventListener("change", function () { clearTimeout(timer); timer = setTimeout(send, 400); });
  });
  document.addEventListener("visibilitychange", function () { if (document.visibilityState === "hidden") send(); });
})();
