(function() {
    const form    = document.getElementById('login-form');
    const pwInput = document.getElementById('password');
    const errorEl = document.getElementById('login-error');
    const submit  = form.querySelector('button[type="submit"]');

    function showError(text) {
        errorEl.textContent = text;
        errorEl.hidden = false;
    }

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        submit.disabled = true;
        try {
            const res = await fetch('/api/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ password: pwInput.value }),
            });
            if (res.ok) { location.replace('/'); return; }
            let detail = '';
            try { detail = (await res.json()).error || ''; } catch (err) { /* ignore */ }
            showError(detail || `Login failed (${res.status})`);
            pwInput.select();
        } catch (err) {
            showError('Server unreachable, please retry.');
        } finally {
            submit.disabled = false;
        }
    });
})();
