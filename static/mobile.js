(function (window) {
  'use strict';

  const root = window.document?.documentElement;
  if (!root || !window.matchMedia) return;

  // A phone remains mobile after rotation beyond the portrait width breakpoint.
  const mobile = window.matchMedia(
    '(max-width: 767px), (max-width: 1023px) and (max-height: 600px) and (hover: none) and (pointer: coarse)'
  );
  const viewport = window.visualViewport;
  let scheduled = false;

  function updateViewport() {
    scheduled = false;
    if (!mobile.matches) {
      root.style.removeProperty('--mobile-viewport-height');
      root.style.removeProperty('--mobile-viewport-top');
      root.removeAttribute('data-mobile-compact');
      return;
    }

    const height = viewport?.height > 0 ? viewport.height : window.innerHeight;
    const top = viewport ? Math.max(0, viewport.offsetTop || 0) : 0;
    root.style.setProperty('--mobile-viewport-height', height + 'px');
    root.style.setProperty('--mobile-viewport-top', top + 'px');
    root.toggleAttribute('data-mobile-compact', height <= 480);
  }

  function scheduleUpdate() {
    if (scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(updateViewport);
  }

  window.addEventListener('resize', scheduleUpdate, {passive: true});
  if (viewport) {
    viewport.addEventListener('resize', scheduleUpdate, {passive: true});
    viewport.addEventListener('scroll', scheduleUpdate, {passive: true});
  }
  if (mobile.addEventListener) mobile.addEventListener('change', scheduleUpdate);
  else if (mobile.addListener) mobile.addListener(scheduleUpdate);
  updateViewport();
})(window);
