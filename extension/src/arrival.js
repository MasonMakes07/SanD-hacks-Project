// One bounded completion cue. Source selection and graph layout never call this.
export function createResultArrival(root) {
  const media = root.ownerDocument.defaultView.matchMedia('(prefers-reduced-motion: reduce)');
  let sequence = 0, animations = [], claim = null;

  function cancel() {
    sequence++;
    for (const animation of animations) animation.cancel();
    animations = [];
    claim?.classList.remove('is-arrival-emphasis', 'is-arrival-static');
    claim = null;
  }

  function play() {
    cancel();
    if (root.hidden) return;
    claim = root.querySelector('.result-card');
    if (!claim) return;
    if (media.matches || typeof claim.animate !== 'function') {
      claim.classList.add('is-arrival-static');
      return;
    }
    const current = sequence;
    claim.classList.add('is-arrival-emphasis');
    // Paper settles first, then the evidence headings. Board geometry is untouched.
    const targets = [claim, root.querySelector('.evidence-inspection > summary'),
      root.querySelector('.network-card > h2'), root.querySelector('.sources-section > h2')].filter(Boolean);
    animations = targets.map((element, index) => element.animate(
      [{ opacity: .96, transform: 'translateY(3px)' }, { opacity: 1, transform: 'translateY(0)' }],
      { id: `truthtrace-arrival-section-${index}`, duration: 200, delay: index * 40,
        easing: 'cubic-bezier(.16, 1, .3, 1)' },
    ));
    animations.push(claim.animate([
      { outlineColor: 'transparent' }, { outlineColor: 'rgba(49, 90, 73, .45)', offset: .35 },
      { outlineColor: 'transparent' },
    ], { id: 'truthtrace-arrival-emphasis', duration: 340, easing: 'ease-out' }));
    Promise.allSettled(animations.map((animation) => animation.finished)).then(() => {
      if (sequence !== current) return;
      claim?.classList.remove('is-arrival-emphasis');
      animations = [];
    });
  }

  function preferenceChanged() {
    const previous = claim;
    cancel();
    claim = previous;
    if (media.matches) claim?.classList.add('is-arrival-static');
  }
  media.addEventListener('change', preferenceChanged);
  return { play, cancel, destroy() { cancel(); media.removeEventListener('change', preferenceChanged); } };
}
