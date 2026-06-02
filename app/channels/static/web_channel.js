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
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
    });
    inputEl.addEventListener('input', () => {
        inputEl.style.height = 'auto';
        inputEl.style.height = Math.min(inputEl.scrollHeight, 160) + 'px';
    });

    // sidebar pre-populate (messages already loaded via ws.onopen → loadConversations(true))
    if (sidebarOpen) loadConversations(false);

    connect();
})();
