/* site.js — SHELL lane. Inner-page behaviour, ≤6 KB, no dependencies.
   1. Keep the shared bar's active link honest across Material's instant navigation: the header is not
      re-rendered on a page swap, so the Jinja-rendered .is-active would go stale. Each link carries
      data-sk-pages = every page URL under that nav entry (relative to the page that rendered it).
   2. Tables: mark ones with 3+ columns .sk-wide (phone scrolls them instead of folding rows) and keep a paper fade
      on the wrapper's right edge only while there is more table to the right (.has-more).
   3. Motion: below-the-fold blocks (figures, cards, tables, code, notes, .glass) rise 10 px into place the first time
      they scroll in. Only when JS runs (html.sk-js), only once per block, never above the fold, never when the
      reader asked for reduced motion. Home has its own choreography in landing.js and is skipped. */
(function () {
  'use strict';
  document.documentElement.classList.add('sk-js');
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
  function tables() {
    document.querySelectorAll('.md-typeset__scrollwrap').forEach(function (wrap) {
      var box = wrap.querySelector('.md-typeset__table'), t = wrap.querySelector('table');
      if (!box || !t) return;
      if ((t.tHead && t.tHead.rows[0] ? t.tHead.rows[0].cells.length : 0) >= 3) wrap.classList.add("sk-wide");   // on the wrapper: a class on the table itself would defeat Material's table:not([class])
      var update = function () { wrap.classList.toggle('has-more', box.scrollWidth - box.clientWidth - box.scrollLeft > 8); };
      box.addEventListener('scroll', update, { passive: true }); window.addEventListener('resize', update, { passive: true }); update();
    });
  }
  var SEL = '.md-typeset > figure, .md-typeset > .glass-grid, .md-typeset > .glass, .md-typeset > .md-typeset__scrollwrap, ' +
            '.md-typeset > .highlight, .md-typeset > pre, .md-typeset > .admonition, .md-typeset > details, .md-typeset > .tabbed-set, .md-typeset > .grid';
  function reveal() {
    if (!document.querySelector('.sk-header ~ .md-container') || document.getElementById('landing')) return;
    if (!('IntersectionObserver' in window) || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    var fold = innerHeight;
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add('is-in'); io.unobserve(e.target); } });
    }, { rootMargin: '0px 0px -8% 0px' });
    document.querySelectorAll(SEL).forEach(function (el) {
      if (el.classList.contains('sk-reveal')) return;
      if (el.getBoundingClientRect().top < fold) return;      // already on screen: never hide what the reader can see
      el.classList.add('sk-reveal'); io.observe(el);
    });
  }
  function boot() { syncActive(); tables(); reveal(); }
  if (window.document$ && window.document$.subscribe) window.document$.subscribe(boot);
  else document.addEventListener('DOMContentLoaded', boot);
})();
