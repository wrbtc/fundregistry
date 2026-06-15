(function() {
  var card = document.getElementById('cb-card');
  var toggle = document.getElementById('cb-toggle');
  var form = document.getElementById('cb-form');
  var msg = document.getElementById('cb-msg');
  var email = document.getElementById('cb-email');
  var honey = document.getElementById('cb-website');
  var btn = document.getElementById('cb-send');
  var status = document.getElementById('cb-status');

  function open() { card.classList.add('open'); msg.focus(); }
  function close() { card.classList.remove('open'); }

  function showForm() {
    form.style.display = '';
    status.style.display = 'none';
  }
  function showStatus(text) {
    form.style.display = 'none';
    status.style.display = '';
    status.textContent = text;
  }

  toggle.addEventListener('click', function() {
    if (card.classList.contains('open')) close(); else open();
  });

  card.querySelector('.cb-close').addEventListener('click', close);

  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') close();
  });

  document.addEventListener('click', function(e) {
    if (card.classList.contains('open') && !card.contains(e.target) && !toggle.contains(e.target)) {
      close();
    }
  });

  btn.addEventListener('click', function() {
    var text = msg.value.trim();
    if (!text) { msg.focus(); return; }

    btn.disabled = true;
    btn.textContent = 'Sending\u2026';

    fetch('/v1/messages', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: text,
        email: email.value.trim(),
        page_url: location.pathname,
        website: honey.value
      })
    }).then(function(res) {
      if (res.ok) {
        showStatus('Thanks! We\u2019ll take a look.');
        msg.value = '';
        email.value = '';
        setTimeout(function() { close(); showForm(); }, 3000);
      } else if (res.status === 429) {
        showStatus('Too many messages. Try again later.');
        setTimeout(showForm, 4000);
      } else if (res.status === 400) {
        res.json().then(function(d) {
          showStatus(d.detail || 'Please check your message and try again.');
        }).catch(function() {
          showStatus('Please check your message and try again.');
        });
        setTimeout(showForm, 4000);
      } else {
        showStatus('Something went wrong. Try again?');
        setTimeout(showForm, 4000);
      }
    }).catch(function() {
      showStatus('Something went wrong. Try again?');
      setTimeout(showForm, 4000);
    }).finally(function() {
      btn.disabled = false;
      btn.textContent = 'Send';
    });
  });
})();
