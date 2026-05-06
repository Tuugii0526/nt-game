(function () {
  const el = document.getElementById('timer');
  if (!el) return;

  let secs = parseInt(el.dataset.secondsLeft, 10) || 0;
  const submitBtn = document.getElementById('submit-btn');

  function fmt(s) {
    const m = Math.floor(s / 60);
    const r = s % 60;
    return m + ':' + String(r).padStart(2, '0');
  }

  function render() {
    if (secs <= 0) {
      el.textContent = 'Time is up';
      el.classList.add('expired');
      if (submitBtn) submitBtn.disabled = true;
      return false;
    }
    el.textContent = 'Time left: ' + fmt(secs);
    return true;
  }

  if (!render()) return;
  const handle = setInterval(function () {
    secs -= 1;
    if (!render()) clearInterval(handle);
  }, 1000);
})();
