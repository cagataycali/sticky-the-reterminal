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
