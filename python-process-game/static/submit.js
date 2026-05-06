(function () {
  const form = document.getElementById('submit-form');
  if (!form) return;

  const verdictBox = document.getElementById('verdict');
  const submitBtn = document.getElementById('submit-btn');
  const history = document.getElementById('history');

  function setVerdict(text, cls) {
    verdictBox.textContent = text;
    verdictBox.className = 'verdict ' + cls;
  }

  function prependHistory(data) {
    if (!history) return;
    const empty = history.querySelector('.empty');
    if (empty) empty.remove();
    const li = document.createElement('li');
    li.innerHTML =
      '<span class="verdict-tag v-' + data.verdict + '">' + data.verdict + '</span>' +
      '<span>' + data.tests_passed + ' / ' + data.tests_total + '</span>' +
      '<span class="muted">' + data.runtime_ms + ' ms</span>';
    history.prepend(li);
  }

  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    submitBtn.disabled = true;
    setVerdict('Running...', 'v-pending');

    try {
      const res = await fetch('/submit', { method: 'POST', body: new FormData(form) });
      if (res.status === 423) {
        setVerdict('Time is up', 'v-RE');
        return;
      }
      if (!res.ok) {
        setVerdict('Server error: ' + res.status, 'v-RE');
        return;
      }
      const data = await res.json();
      setVerdict(
        data.verdict + ' \u2014 ' + data.tests_passed + '/' + data.tests_total +
        ' \u2014 ' + data.runtime_ms + ' ms \u2014 ' + data.detail,
        'v-' + data.verdict
      );
      prependHistory(data);
    } catch (err) {
      setVerdict('Network error: ' + err.message, 'v-RE');
    } finally {
      submitBtn.disabled = false;
    }
  });
})();
