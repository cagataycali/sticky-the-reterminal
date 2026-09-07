// landing.js — LANDING lane. Reveal-on-scroll + pinned frame crossfade. ~1 KB, no deps.
// transform/opacity only; prefers-reduced-motion → sections are static via CSS, we only mark them .in.
(function () {
  var root = document.getElementById('landing');
  if (!root) return;
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var reveals = root.querySelectorAll('.l-reveal');
  if (!('IntersectionObserver' in window) || reduce) {
    reveals.forEach(function (el) { el.classList.add('in'); });
  } else {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } });
    }, { rootMargin: '0px 0px -15% 0px', threshold: 0.15 });
    reveals.forEach(function (el) { io.observe(el); });
  }
  // pinned story: the step nearest the viewport centre picks the frame on the glass
  root.querySelectorAll('[data-frames]').forEach(function (sec) {
    var frames = sec.querySelectorAll('.frame');
    var steps = sec.querySelectorAll('[data-frame]');
    if (!frames.length || !steps.length) return;
    var show = function (i) { frames.forEach(function (f, k) { f.classList.toggle('is-on', k === i); }); };
    if (!('IntersectionObserver' in window)) return;
    var so = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { if (e.isIntersecting) show(+e.target.getAttribute('data-frame') || 0); });
    }, { rootMargin: '-45% 0px -45% 0px', threshold: 0 });
    steps.forEach(function (s) { so.observe(s); });
  });
  // product shots: crossfade real card captures — 4 s dwell, hold on hover/focus, dots pick, only while on screen
  root.querySelectorAll('[data-shots]').forEach(function (sec) {
    var frames = sec.querySelectorAll('.frame'), dots = sec.querySelectorAll('.l-dot'), label = sec.querySelector('[data-shot-label]');
    var i = 0, timer = null, held = false, seen = false;
    var show = function (n) {
      i = (n + frames.length) % frames.length;
      frames.forEach(function (f, k) { f.classList.toggle('is-on', k === i); });
      dots.forEach(function (d, k) { d.classList.toggle('is-on', k === i); d.setAttribute('aria-selected', k === i ? 'true' : 'false'); });
      if (label) label.textContent = dots[i] ? dots[i].getAttribute('aria-label') : '';
    };
    var start = function () { stop(); if (!reduce && seen && !held) timer = setInterval(function () { show(i + 1); }, 4000); };
    var stop = function () { if (timer) { clearInterval(timer); timer = null; } };
    dots.forEach(function (d) { d.addEventListener('click', function () { show(+d.getAttribute('data-shot')); start(); }); });
    sec.addEventListener('mouseenter', function () { held = true; stop(); });
    sec.addEventListener('mouseleave', function () { held = false; start(); });
    sec.addEventListener('focusin', function () { held = true; stop(); });
    sec.addEventListener('focusout', function () { held = false; start(); });
    if ('IntersectionObserver' in window) {
      new IntersectionObserver(function (es) { es.forEach(function (e) { seen = e.isIntersecting; seen ? start() : stop(); }); }, { threshold: 0.25 }).observe(sec);
    } else { seen = true; start(); }
  });
  // numbers: count up once when the strip reveals (600 ms, eased; static under reduced-motion)
  var strip = root.querySelector('.l-numbers');
  if (strip && !reduce && 'IntersectionObserver' in window) {
    var counted = false;
    new IntersectionObserver(function (es, o) {
      if (!es[0].isIntersecting || counted) return; counted = true; o.disconnect();
      strip.querySelectorAll('[data-count]').forEach(function (el) {
        var end = +el.getAttribute('data-count'), t0 = null;
        var step = function (ts) { if (!t0) t0 = ts; var k = Math.min(1, (ts - t0) / 600); k = 1 - Math.pow(1 - k, 3);
          el.textContent = Math.round(end * k); if (k < 1) requestAnimationFrame(step); };
        el.textContent = '0'; requestAnimationFrame(step);
      });
    }, { threshold: 0.4 }).observe(strip);
  }
  // hero: a slow 3D tilt as the reader scrolls away (transform only, rAF-throttled, ≤6°)
  var hero = root.querySelector('.bezel--hero');
  if (hero && !reduce) {
    var tilting = false;
    var tilt = function () {
      tilting = false;
      var p = Math.min(1, Math.max(0, window.scrollY / (window.innerHeight * 0.9)));
      hero.style.setProperty('--rx', (p * 6).toFixed(2) + 'deg');
      hero.style.setProperty('--ry', (p * -4).toFixed(2) + 'deg');
    };
    window.addEventListener('scroll', function () { if (!tilting) { tilting = true; requestAnimationFrame(tilt); } }, { passive: true });
    tilt();
  }
})();
