/* site.js — SHELL lane. Inner-page behaviour, ≤6 KB, no dependencies.
   1. Keep the shared bar's active link honest across Material's instant navigation: the header is not
      re-rendered on a page swap, so the Jinja-rendered .is-active would go stale. Each link carries
      data-sk-pages = every page URL under that nav entry (relative to the page that rendered it).
   S5 adds reveal-on-scroll here. */
(function () {
  'use strict';
  var norm = function (u) { return new URL(u, location.href).pathname.replace(/index\.html$/, ''); };
  function syncActive() {
    var nav = document.querySelector('[data-sk-nav]');
    if (!nav) return;
    var here = norm(location.pathname), hit = null;
    nav.querySelectorAll('.sk-nav__link').forEach(function (a) {
      a.classList.remove('is-active'); a.removeAttribute('aria-current');
      if (!hit && (a.getAttribute('data-sk-pages') || '').split(/\s+/).some(function (u) { return u && norm(u) === here; })) hit = a;
    });
    if (hit) { hit.classList.add('is-active'); hit.setAttribute('aria-current', 'page'); }
  }
  if (window.document$ && window.document$.subscribe) window.document$.subscribe(syncActive);
  else document.addEventListener('DOMContentLoaded', syncActive);
})();
