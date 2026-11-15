// Driven only by actual capture/request boundaries. Elapsed updates are outside
// the live region, so screen readers announce transitions rather than seconds.
export function createLoading({ title, text, elapsed }) {
  let timer = null;
  let generation = 0;
  function stop() {
    generation += 1;
    clearInterval(timer);
    timer = null;
    elapsed.textContent = '';
  }
  function start(phase, { kind = 'selection', timeoutMs = 45000 } = {}) {
    stop();
    const token = generation;
    const started = performance.now();
    title.textContent = phase === 'capture' ? 'Reading the current page…' : 'Waiting for the investigation…';
    text.textContent = phase === 'capture'
      ? (kind === 'selection' ? 'Capturing the text you selected and its page context.' : 'Capturing the current article or video context.')
      : `The analysis request is pending. The configured wait limit is ${Math.ceil(timeoutMs / 1000)} seconds. Cancelling stops this panel’s wait; server work may continue.`;
    timer = setInterval(() => {
      if (token !== generation) return;
      const seconds = Math.floor((performance.now() - started) / 1000);
      if (seconds >= 5) elapsed.textContent = `${seconds}s elapsed · ${phase === 'capture' ? 'Still waiting for page capture.' : 'Still waiting for the analysis response.'}`;
    }, 1000);
  }
  return { start, stop };
}
