(() => {
  window.propertyDeskScrollCleanup?.();

  const main = document.querySelector('[data-testid="stMainBlockContainer"]')
    || document.querySelector('[data-testid="stAppScrollToBottomContainer"], [data-testid="stMain"], .stMain');
  if (!main) return;

  const timers = [];
  let frame;
  let stopped = false;
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const scroll = () => {
    if (stopped) return;
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      const messages = main.querySelectorAll('[data-testid="stChatMessage"]');
      messages[messages.length - 1]?.scrollIntoView({
        behavior: reducedMotion ? 'instant' : 'smooth',
        block: 'end',
      });
    });
  };

  const observer = new MutationObserver(scroll);
  const cleanup = () => {
    stopped = true;
    observer.disconnect();
    timers.forEach(clearTimeout);
    cancelAnimationFrame(frame);
    main.removeEventListener('wheel', cleanup);
    main.removeEventListener('touchstart', cleanup);
    window.removeEventListener('keydown', onKey);
  };
  const onKey = event => {
    if (['PageUp', 'PageDown', 'Home', 'End', 'ArrowUp', 'ArrowDown'].includes(event.key)) cleanup();
  };

  // Stop following messages when the user scrolls.
  observer.observe(main, { childList: true, subtree: true, characterData: true });
  main.addEventListener('wheel', cleanup, { passive: true });
  main.addEventListener('touchstart', cleanup, { passive: true });
  window.addEventListener('keydown', onKey);
  window.propertyDeskScrollCleanup = cleanup;
  scroll();
  timers.push(setTimeout(scroll, 150), setTimeout(scroll, 450), setTimeout(cleanup, 2500));
})();
