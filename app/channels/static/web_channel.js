(function() {
    const wsProto = location.protocol === 'https:' ? 'wss' : 'ws';
    const wsUrl = `${wsProto}://${location.host}/ws`;

    let ws = null;
    let reconnectDelay = 1000;
    let activeConvId = null;

    const messagesEl  = document.getElementById('messages');
    const scrollEl    = document.getElementById('messages-wrap');
    const thinkingEl  = document.getElementById('thinking');
    const inputEl     = document.getElementById('msg-input');
    const sendBtn     = document.getElementById('send-btn');
    const statusDot   = document.getElementById('status-dot');
    const statusTxt   = document.getElementById('status-text');
    const themeBtn    = document.getElementById('theme-btn');
    const sidebarEl   = document.getElementById('sidebar');
    const toggleBtn   = document.getElementById('sidebar-toggle');
    const convListEl  = document.getElementById('conv-list');
    const newConvBtn  = document.getElementById('new-conv-btn');
    const paletteEl   = document.getElementById('cmd-palette');
    const settingsBtn = document.getElementById('settings-btn');
    const overlayEl   = document.getElementById('settings-overlay');
    const closeBtn    = document.getElementById('settings-close');
    const modelInput  = document.getElementById('model-input');
    const modelSave   = document.getElementById('model-save');
    const modelFeedEl = document.getElementById('model-feedback');
    const mcpListEl   = document.getElementById('mcp-list');

    // ---- theme ----
    const savedTheme = localStorage.getItem('ab-theme') || 'dark';
    applyTheme(savedTheme);
    function applyTheme(theme) {
        document.documentElement.setAttribute('data-theme', theme);
        themeBtn.textContent = theme === 'dark' ? '☀' : '☾';
        localStorage.setItem('ab-theme', theme);
    }
    themeBtn.addEventListener('click', () => {
        applyTheme(document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark');
    });

    // ---- sidebar toggle ----
    const sidebarOpen = localStorage.getItem('ab-sidebar') !== 'closed';
    if (!sidebarOpen) sidebarEl.classList.add('collapsed');
    toggleBtn.addEventListener('click', () => {
        const collapsed = sidebarEl.classList.toggle('collapsed');
        localStorage.setItem('ab-sidebar', collapsed ? 'closed' : 'open');
        if (!collapsed) loadConversations(false);
    });

    // ---- conversations ----
    async function loadConversations(populateMessages) {
        try {
            const url = '/api/conversations';
            const res = await fetch(url);
            if (!res.ok) return;
            const { conversations, active_id } = await res.json();
            activeConvId = active_id;
            renderConversations(conversations, active_id);
            if (populateMessages && active_id) await loadMessages(active_id);
        } catch (e) { /* server may not be fully up yet */ }
    }

    async function loadMessages(convId) {
        if (!convId) return;
        try {
            const url = `/api/messages?conv_id=${convId}`;
            const res = await fetch(url);
            if (!res.ok) return;
            const { messages } = await res.json();
            if (!messages || !messages.length) return;
            const empty = document.getElementById('empty');
            if (empty) empty.style.display = 'none';
            messages.forEach(msg => appendMessage(msg.content, msg.role === 'user' ? 'user' : 'ai'));
        } catch (e) { /* ignore */ }
    }

    function fmtDate(iso) {
        if (!iso) return '';
        const d = new Date(iso);
        const now = new Date();
        const diffDays = Math.floor((now - d) / 86400000);
        if (diffDays === 0) return d.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
        if (diffDays === 1) return 'Yesterday';
        if (diffDays < 7)  return d.toLocaleDateString([], {weekday:'short'});
        return d.toLocaleDateString([], {month:'short', day:'numeric'});
    }

    function renderConversations(list, activeId) {
        convListEl.innerHTML = '';
        if (!list || !list.length) {
            convListEl.innerHTML = '<p style="padding:8px 10px;font-size:.8rem;color:var(--text-muted)">No conversations yet</p>';
            return;
        }
        list.forEach(conv => {
            const item = document.createElement('div');
            item.className = 'conv-item' + (conv.id === activeId ? ' active' : '');
            item.dataset.id = conv.id;
            item.innerHTML = `<span class="conv-name">${escapeHtml(conv.name || 'Untitled')}</span>` +
                             `<span class="conv-meta">${fmtDate(conv.updated_at)} · ${conv.message_count || 0} msgs</span>`;
            item.addEventListener('click', () => loadConv(conv.id));
            convListEl.appendChild(item);
        });
    }

    function loadConv(id) {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        ws.send(JSON.stringify({ type: 'message', content: `/load ${id}` }));
        activeConvId = id;
        convListEl.querySelectorAll('.conv-item').forEach(el => {
            el.classList.toggle('active', parseInt(el.dataset.id) === id);
        });
        clearMessages();
        loadMessages(id);
    }

    newConvBtn.addEventListener('click', () => {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        ws.send(JSON.stringify({ type: 'message', content: '/new' }));
        clearMessages();
        // list refresh is driven by the ws.onmessage handler when server responds with "created"
    });

    function clearMessages() {
        messagesEl.querySelectorAll('.msg-row').forEach(el => el.remove());
        const empty = document.getElementById('empty');
        if (empty) {
            empty.style.display = '';
        } else {
            const d = document.createElement('div');
            d.id = 'empty';
            d.innerHTML = '<div class="icon">✦</div><p>Ask me anything, or try /help for available commands.</p>';
            messagesEl.appendChild(d);
        }
        hideThinking();
    }

    // ---- status ----
    function setStatus(state, text) {
        statusDot.className = state;
        statusTxt.textContent = text;
    }

    // ---- WebSocket ----
    function connect() {
        setStatus('', 'Connecting…');
        ws = new WebSocket(wsUrl);

        ws.onopen = () => {
            reconnectDelay = 1000;
            setStatus('connected', 'Connected');
            sendBtn.disabled = false;
            clearMessages();       // always reset DOM before reloading — prevents duplicates on reconnect
            loadConversations(true);
            loadCommands();
        };

        ws.onclose = () => {
            setStatus('', 'Disconnected');
            sendBtn.disabled = true;
            hideThinking();
            setTimeout(connect, reconnectDelay);
            reconnectDelay = Math.min(reconnectDelay * 2, 30000);
        };

        ws.onerror = () => setStatus('', 'Error');

        ws.onmessage = (evt) => {
            hideThinking();
            try {
                const data = JSON.parse(evt.data);
                if (data.type === 'message') {
                    appendMessage(data.content, 'ai');
                    // agent_loop() calls _store.touch() after every response, so the
                    // sidebar's updated_at and message count change — refresh it
                    setTimeout(() => loadConversations(false), 300);
                } else if (data.type === 'system') {
                    appendMessage(data.content, 'system');
                    // always refresh after any system message; conversation-mutating
                    // commands (new, load, fork, rename) change the sidebar list too
                    setTimeout(() => loadConversations(false), 150);
                }
            } catch (e) {
                appendMessage(evt.data, 'ai');
                setTimeout(() => loadConversations(false), 300);
            }
        };
    }

    function showThinking() {
        thinkingEl.classList.add('visible');
        setStatus('thinking', 'Thinking…');
        scrollBottom();
    }
    function hideThinking() {
        thinkingEl.classList.remove('visible');
        if (ws && ws.readyState === WebSocket.OPEN) setStatus('connected', 'Connected');
    }

    // ---- rendering ----
    function escapeHtml(t) {
        return String(t).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }
    function formatMessage(text) {
        text = escapeHtml(text);
        text = text.replace(/```[\w]*\n?([\s\S]*?)```/g, '<pre><code>$1</code></pre>');
        text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
        return text;
    }
    function appendMessage(content, role) {
        const empty = document.getElementById('empty');
        if (empty) empty.style.display = 'none';

        const row = document.createElement('div');
        row.className = `msg-row ${role}`;
        const bubble = document.createElement('div');
        bubble.className = 'bubble';
        bubble.innerHTML = role === 'system' ? escapeHtml(content) : formatMessage(content);
        row.appendChild(bubble);
        messagesEl.appendChild(row);
        scrollBottom();
    }
    function scrollBottom() { scrollEl.scrollTop = scrollEl.scrollHeight; }

    // ---- send ----
    function send() {
        const text = inputEl.value.trim();
        if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;
        appendMessage(text, 'user');
        ws.send(JSON.stringify({ type: 'message', content: text }));
        inputEl.value = '';
        inputEl.style.height = '42px';
        if (text === '/new') {
            clearMessages();
        } else if (/^\/load\s+\d+/.test(text)) {
            const id = parseInt(text.split(/\s+/)[1]);
            clearMessages();
            loadMessages(id);
        } else if (!text.startsWith('/')) {
            showThinking();
        }
    }

    sendBtn.addEventListener('click', send);
    inputEl.addEventListener('keydown', (e) => {
        if (paletteOpen() && handlePaletteKey(e)) return;
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
    });
    inputEl.addEventListener('input', () => {
        inputEl.style.height = 'auto';
        inputEl.style.height = Math.min(inputEl.scrollHeight, 160) + 'px';
        updatePalette();
    });
    inputEl.addEventListener('blur', () => setTimeout(hidePalette, 150));

    // ---- slash command palette ----
    let commands = [];          // [{name, description}] from /api/commands
    let paletteIndex = 0;

    async function loadCommands() {
        try {
            const res = await fetch('/api/commands');
            if (!res.ok) return;
            const data = await res.json();
            commands = data.commands || [];
        } catch (e) { /* ignore */ }
    }

    function paletteOpen() { return paletteEl.classList.contains('visible'); }
    function hidePalette()  { paletteEl.classList.remove('visible'); paletteEl.innerHTML = ''; }

    function paletteMatches() {
        const text = inputEl.value;
        // only when input is a single "/word" token (no args yet, no newlines)
        if (!/^\/[a-z0-9_-]*$/i.test(text)) return null;
        const prefix = text.slice(1).toLowerCase();
        return commands.filter(c => c.name.toLowerCase().startsWith(prefix));
    }

    function updatePalette() {
        const matches = paletteMatches();
        if (!matches || !matches.length) { hidePalette(); return; }
        paletteIndex = Math.min(paletteIndex, matches.length - 1);
        paletteEl.innerHTML = '';
        matches.forEach((cmd, i) => {
            const item = document.createElement('div');
            item.className = 'cmd-item' + (i === paletteIndex ? ' selected' : '');
            item.innerHTML = `<span class="cmd-name">/${escapeHtml(cmd.name)}</span>` +
                             `<span class="cmd-desc">${escapeHtml(cmd.description || '')}</span>`;
            // mousedown (not click) so it fires before the textarea blur handler
            item.addEventListener('mousedown', (e) => { e.preventDefault(); pickCommand(cmd); });
            item.addEventListener('mousemove', () => {
                if (paletteIndex !== i) { paletteIndex = i; updatePalette(); }
            });
            paletteEl.appendChild(item);
        });
        paletteEl.classList.add('visible');
    }

    function pickCommand(cmd) {
        const takesArgs = /Usage:/i.test(cmd.description || '');
        inputEl.value = '/' + cmd.name + (takesArgs ? ' ' : '');
        hidePalette();
        inputEl.focus();
        if (!takesArgs) send();
    }

    function handlePaletteKey(e) {
        const matches = paletteMatches();
        if (!matches || !matches.length) return false;
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            paletteIndex = (paletteIndex + 1) % matches.length;
            updatePalette();
            return true;
        }
        if (e.key === 'ArrowUp') {
            e.preventDefault();
            paletteIndex = (paletteIndex - 1 + matches.length) % matches.length;
            updatePalette();
            return true;
        }
        if (e.key === 'Tab' || e.key === 'Enter') {
            e.preventDefault();
            pickCommand(matches[paletteIndex]);
            return true;
        }
        if (e.key === 'Escape') {
            e.preventDefault();
            hidePalette();
            return true;
        }
        return false;
    }

    // ---- settings modal ----
    function openSettings() {
        overlayEl.classList.add('visible');
        modelFeedEl.textContent = '';
        loadModel();
        loadMcpServers();
    }
    function closeSettings() { overlayEl.classList.remove('visible'); }

    settingsBtn.addEventListener('click', openSettings);
    closeBtn.addEventListener('click', closeSettings);
    overlayEl.addEventListener('click', (e) => { if (e.target === overlayEl) closeSettings(); });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && overlayEl.classList.contains('visible')) closeSettings();
    });

    async function loadModel() {
        try {
            const res = await fetch('/api/model');
            if (!res.ok) return;
            const { model } = await res.json();
            modelInput.value = model || '';
        } catch (e) { /* ignore */ }
    }

    async function saveModel() {
        const model = modelInput.value.trim();
        if (!model) { showModelFeedback('Model name cannot be empty.', true); return; }
        try {
            const res = await fetch('/api/model', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model }),
            });
            const data = await res.json();
            if (!res.ok) { showModelFeedback(data.error || 'Failed to set model.', true); return; }
            showModelFeedback(`Model set to ${data.model}`, false);
        } catch (e) {
            showModelFeedback('Failed to set model.', true);
        }
    }
    function showModelFeedback(text, isError) {
        modelFeedEl.textContent = text;
        modelFeedEl.className = isError ? 'error' : 'ok';
    }
    modelSave.addEventListener('click', saveModel);
    modelInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); saveModel(); }
    });

    async function loadMcpServers() {
        mcpListEl.innerHTML = '<p class="mcp-empty">Loading…</p>';
        try {
            const res = await fetch('/api/mcp/servers');
            if (!res.ok) { mcpListEl.innerHTML = '<p class="mcp-empty">Failed to load servers.</p>'; return; }
            const { servers } = await res.json();
            renderMcpServers(servers);
        } catch (e) {
            mcpListEl.innerHTML = '<p class="mcp-empty">Failed to load servers.</p>';
        }
    }

    function renderMcpServers(servers) {
        mcpListEl.innerHTML = '';
        if (!servers || !servers.length) {
            mcpListEl.innerHTML = '<p class="mcp-empty">No MCP servers configured. ' +
                'Add servers to mcp_servers.json and restart.</p>';
            return;
        }
        servers.forEach(s => {
            const enabled = !s.disabled;
            const stateCls = s.disabled ? 'off' : (s.connected ? 'on' : 'err');
            const stateTxt = s.disabled ? 'disabled' : (s.connected ? `connected · ${s.tool_count} tool${s.tool_count === 1 ? '' : 's'}` : 'connection failed');
            const row = document.createElement('div');
            row.className = 'mcp-item';
            row.innerHTML =
                `<div class="mcp-info">` +
                  `<span class="mcp-name"><span class="mcp-dot ${stateCls}"></span>${escapeHtml(s.name)}</span>` +
                  `<span class="mcp-meta">${escapeHtml(s.transport)} · ${escapeHtml(s.target)} · ${stateTxt}</span>` +
                `</div>` +
                `<label class="switch"><input type="checkbox" ${enabled ? 'checked' : ''}><span class="slider"></span></label>`;
            const checkbox = row.querySelector('input');
            checkbox.addEventListener('change', () => toggleMcpServer(s.name, checkbox.checked, checkbox));
            mcpListEl.appendChild(row);
        });
    }

    async function toggleMcpServer(name, enabled, checkbox) {
        checkbox.disabled = true;
        try {
            const res = await fetch('/api/mcp/servers', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, enabled }),
            });
            if (!res.ok) { checkbox.checked = !enabled; return; }
            const { servers } = await res.json();
            renderMcpServers(servers);
        } catch (e) {
            checkbox.checked = !enabled;
        } finally {
            checkbox.disabled = false;
        }
    }

    // sidebar pre-populate (messages already loaded via ws.onopen → loadConversations(true))
    if (sidebarOpen) loadConversations(false);

    connect();
})();
