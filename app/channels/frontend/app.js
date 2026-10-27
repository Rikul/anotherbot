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
    const stopBtn     = document.getElementById('stop-btn');
    const statusDot   = document.getElementById('status-dot');
    const modelBtn    = document.getElementById('model-btn');
    const themeBtn    = document.getElementById('theme-btn');
    const sidebarEl   = document.getElementById('sidebar');
    const toggleBtn   = document.getElementById('sidebar-toggle');
    const convListEl  = document.getElementById('conv-list');
    const newConvBtn  = document.getElementById('new-conv-btn');
    const fileInput   = document.getElementById('file-input');
    const attachEl    = document.getElementById('attachments');

    // Files staged for the next send (cleared after a successful upload).
    let selectedFiles = [];

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

    // ---- reply rendering: markdown (default) or raw text ----
    const renderToggle = document.getElementById('render-toggle');

    let renderMode = localStorage.getItem('ab-render') === 'raw' ? 'raw' : 'md';
    function applyRenderMode(mode) {
        renderMode = mode;
        localStorage.setItem('ab-render', mode);
        renderToggle.querySelectorAll('button').forEach(b => {
            const on = b.dataset.mode === mode;
            b.classList.toggle('active', on);
            b.setAttribute('aria-pressed', on);
        });
        messagesEl.querySelectorAll('.msg-row.ai .bubble').forEach(renderAiBubble);
    }

    if (renderToggle) {
        renderToggle.addEventListener('click', e => {
            const btn = e.target.closest('button[data-mode]');
            if (btn && btn.dataset.mode !== renderMode) applyRenderMode(btn.dataset.mode);
        });
    }

    const markdownReady = !!(window.marked && window.DOMPurify);
    // Text-only allowlist: nothing that fetches on render (img, media, iframe,
    // style/srcset/background attrs). An auto-loaded URL would leak the viewer's
    // IP to remote hosts, or make credentialed requests to this server.
    const MD_SANITIZE = {
        ALLOWED_TAGS: ['p', 'br', 'hr', 'strong', 'em', 'del', 's', 'code', 'pre', 'blockquote',
                       'ul', 'ol', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'a',
                       'table', 'thead', 'tbody', 'tr', 'th', 'td'],
        ALLOWED_ATTR: ['href', 'title', 'start', 'align'],
    };
    if (markdownReady) {
        // images become plain links: the URL is only fetched if the user clicks it
        marked.use({ renderer: {
            image({ href, title, text }) {
                const t = title ? ` title="${escapeAttr(title)}"` : '';
                return `<a href="${escapeAttr(href)}"${t}>${escapeHtml(text || href)}</a>`;
            },
        } });
        // links in replies open in a new tab instead of replacing the chat
        DOMPurify.addHook('afterSanitizeAttributes', node => {
            if (node.tagName === 'A' && node.hasAttribute('href')) {
                node.setAttribute('target', '_blank');
                node.setAttribute('rel', 'noopener noreferrer');
            }
        });
    }
    function renderMarkdown(text) {
        return DOMPurify.sanitize(marked.parse(text, { gfm: true, breaks: true }), MD_SANITIZE);
    }
    // The original text lives on the bubble so switching modes re-renders it.
    function renderAiBubble(bubble) {
        const raw = bubble.dataset.raw ?? '';
        if (renderMode === 'raw') {
            bubble.classList.remove('md');
            bubble.innerHTML = escapeHtml(raw);
            return;
        }
        // Without the vendored libs fall back to the old pre-wrap formatter;
        // the .md layout class would collapse its newlines.
        bubble.classList.toggle('md', markdownReady);
        bubble.innerHTML = markdownReady ? renderMarkdown(raw) : formatMessage(raw);
    }
    applyRenderMode(renderMode);

    // ---- sidebar toggle ----
    // On narrow screens the sidebar overlays the chat (see CSS), so it starts
    // closed, closes after picking a conversation, and its state isn't persisted.
    const narrowScreen = window.matchMedia('(max-width: 768px)');
    const sidebarOpen = !narrowScreen.matches && localStorage.getItem('ab-sidebar') !== 'closed';
    if (!sidebarOpen) sidebarEl.classList.add('collapsed');
    toggleBtn.addEventListener('click', () => {
        const collapsed = sidebarEl.classList.toggle('collapsed');
        if (!narrowScreen.matches) localStorage.setItem('ab-sidebar', collapsed ? 'closed' : 'open');
        if (!collapsed) loadConversations(false);
    });
    function closeSidebarOnNarrow() {
        if (narrowScreen.matches) sidebarEl.classList.add('collapsed');
    }
    // Rotating or resizing across the breakpoint: close the overlay when going
    // narrow, restore the persisted desktop state when going wide.
    narrowScreen.addEventListener('change', e => {
        const collapse = e.matches || localStorage.getItem('ab-sidebar') === 'closed';
        sidebarEl.classList.toggle('collapsed', collapse);
        if (!collapse) loadConversations(false);
    });

    // ---- auth ----
    // With WEB_PASSWORD set, an expired/missing session makes the API return 401
    // and the WebSocket handshake fail; send the user back to the login page.
    function checkAuth(res) {
        if (res.status === 401) { location.href = '/login'; return false; }
        return true;
    }

    // The logout button only makes sense when the server has a login enabled.
    const logoutBtn = document.getElementById('logout-btn');
    async function loadStatus() {
        try {
            const res = await fetch('/api/status');
            if (!checkAuth(res) || !res.ok) return;
            const { auth, model } = await res.json();
            logoutBtn.hidden = !auth;
            modelBtn.textContent = model;
            modelBtn.title = `Model: ${model} (click to change)`;
            modelBtn.hidden = !model;
            statusDot.title = `Model: ${model}`;   // the button is hidden on narrow screens
        } catch (e) { /* server may not be fully up yet */ }
    }
    logoutBtn.addEventListener('click', async () => {
        try { await fetch('/api/logout', { method: 'POST' }); } catch (e) { /* ignore */ }
        location.href = '/login';
    });

    // ---- conversations ----
    async function loadConversations(populateMessages) {
        try {
            const url = '/api/conversations';
            const res = await fetch(url);
            if (!checkAuth(res) || !res.ok) return;
            const { conversations, active_id } = await res.json();
            activeConvId = active_id;
            convList = conversations;
            renderConversations(conversations, active_id);
            if (populateMessages && active_id) await loadMessages(active_id);
        } catch (e) { /* server may not be fully up yet */ }
    }

    async function loadMessages(convId) {
        if (!convId) return;
        try {
            const url = `/api/messages?conv_id=${convId}`;
            const res = await fetch(url);
            if (!checkAuth(res) || !res.ok) return;
            const { messages } = await res.json();
            if (!messages || !messages.length) return;
            const empty = document.getElementById('empty');
            if (empty) empty.style.display = 'none';
            messages.forEach(msg => appendMessage(msg.content, msg.role === 'user' ? 'user' : 'ai',
                msg.has_trajectory ? msg.id : null));
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

    // The list is re-fetched after every reply. Re-rendering would destroy an
    // open actions menu or rename box, so while one is open the newest list is
    // parked here and rendered once the user is done.
    let pendingConvList = null;
    let openMenu = null;    // { menu, trigger } while a conversation menu is open
    let renaming = false;
    const sidebarBusy = () => !!openMenu || renaming;

    function flushPendingConversations() {
        if (!pendingConvList || sidebarBusy()) return;
        const [list, activeId] = pendingConvList;
        pendingConvList = null;
        renderConversations(list, activeId);
    }

    function renderConversations(list, activeId) {
        if (sidebarBusy()) { pendingConvList = [list, activeId]; return; }
        convListEl.innerHTML = '';
        if (!list || !list.length) {
            convListEl.innerHTML = '<p style="padding:8px 10px;font-size:.8rem;color:var(--text-muted)">No conversations yet</p>';
            return;
        }
        list.forEach(conv => {
            const name = conv.name || 'Untitled';
            const item = document.createElement('div');
            item.className = 'conv-item' + (conv.id === activeId ? ' active' : '');
            item.dataset.id = conv.id;
            item.innerHTML = `<div class="conv-text">` +
                             `<span class="conv-name">${escapeHtml(name)}</span>` +
                             `<span class="conv-meta">` +
                             (conv.channel ? `<span class="conv-channel conv-channel-${escapeHtml(conv.channel)}">${escapeHtml(conv.channel)}</span>` : '') +
                             `${fmtDate(conv.updated_at)} · ${conv.message_count || 0} msgs</span></div>` +
                             `<button type="button" class="conv-actions-btn" title="Rename, fork or export" ` +
                             `aria-label="Actions for ${escapeAttr(name)}" aria-haspopup="menu" aria-expanded="false">⋯</button>`;
            item.addEventListener('click', e => {
                if (e.target.closest('.conv-rename-input')) return;
                loadConv(conv.id);
            });
            item.querySelector('.conv-actions-btn').addEventListener('click', e => {
                e.stopPropagation();
                if (openMenu && openMenu.trigger === e.currentTarget) closeConvMenu(true);
                else openConvMenu(conv, e.currentTarget);
            });
            convListEl.appendChild(item);
        });
    }

    // ---- conversation actions: rename / fork / export ----
    function openConvMenu(conv, trigger) {
        closeConvMenu(false);
        const menu = document.createElement('div');
        menu.className = 'conv-menu';
        menu.setAttribute('role', 'menu');
        const items = [
            { label: 'Rename', action: () => startRename(conv) },
            // Fork switches the agent to the copy, like /load.
            { label: 'Fork',   action: () => forkConv(conv.id),
              disabled: switchLocked() ? SWITCH_LOCKED : '' },
            { label: 'Export', action: () => exportConv(conv.id) },
            // Destructive, so it takes a second click. While the agent is
            // working on this conversation, /delete would only run after the
            // turn (and its remaining replies would land in a cleared chat).
            { label: 'Delete', action: () => deleteConv(conv.id), danger: true,
              confirm: 'Confirm delete',
              disabled: agentBusy && conv.id === activeConvId ? 'Stop or wait for the agent first' : '' },
        ];
        items.forEach(({ label, action, danger, confirm, disabled }) => {
            const b = document.createElement('button');
            b.type = 'button';
            b.setAttribute('role', 'menuitem');
            b.textContent = label;
            if (danger) b.classList.add('danger');
            if (disabled) { b.disabled = true; b.title = disabled; }
            b.addEventListener('click', e => {
                e.stopPropagation();
                if (confirm && !b.classList.contains('confirming')) {
                    b.classList.add('confirming');
                    b.textContent = confirm;
                    return;
                }
                closeConvMenu(false);
                action();
            });
            menu.appendChild(b);
        });
        menu.addEventListener('keydown', e => {
            const items = [...menu.querySelectorAll('button:not(:disabled)')];
            const i = items.indexOf(document.activeElement);
            if (e.key === 'ArrowDown') { e.preventDefault(); items[(i + 1) % items.length].focus(); }
            else if (e.key === 'ArrowUp') { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
            else if (e.key === 'Tab') closeConvMenu(false);
        });
        document.body.appendChild(menu);
        // Below the button, right-aligned to it; flip above if it would overflow.
        const r = trigger.getBoundingClientRect();
        const below = r.bottom + 4;
        const top = below + menu.offsetHeight > window.innerHeight ? r.top - 4 - menu.offsetHeight : below;
        menu.style.top = `${Math.max(4, top)}px`;
        menu.style.left = `${Math.max(4, r.right - menu.offsetWidth)}px`;
        trigger.setAttribute('aria-expanded', 'true');
        openMenu = { menu, trigger };
        menu.querySelector('button').focus();
    }

    function closeConvMenu(restoreFocus) {
        if (!openMenu) return;
        const { menu, trigger } = openMenu;
        openMenu = null;
        menu.remove();
        trigger.setAttribute('aria-expanded', 'false');
        if (restoreFocus) trigger.focus();
        flushPendingConversations();
    }

    document.addEventListener('click', e => {
        if (openMenu && !openMenu.menu.contains(e.target)) closeConvMenu(false);
    });
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape' && openMenu) closeConvMenu(true);
    });
    convListEl.addEventListener('scroll', () => closeConvMenu(false));
    window.addEventListener('resize', () => closeConvMenu(false));

    async function errorDetail(res) {
        try { return (await res.json()).error || `HTTP ${res.status}`; }
        catch (e) { return `HTTP ${res.status}`; }
    }

    // Inline rename: Enter or blur saves, Escape cancels. Uses the HTTP API
    // (not /rename) so it applies immediately even while the agent is busy.
    function startRename(conv) {
        // Look the row up again: closing the menu may have re-rendered the list.
        const item = convListEl.querySelector(`.conv-item[data-id="${conv.id}"]`);
        const nameEl = item && item.querySelector('.conv-name');
        if (!nameEl) return;
        const oldName = nameEl.textContent;
        const input = document.createElement('input');
        input.className = 'conv-rename-input';
        input.value = conv.name || '';
        input.maxLength = 80;
        input.setAttribute('aria-label', 'Conversation name');
        nameEl.replaceWith(input);
        renaming = true;
        input.focus();
        input.select();

        let done = false;
        async function finish(save) {
            if (done) return;
            done = true;
            const name = input.value.trim();
            const changed = save && name && name !== conv.name;
            if (changed) nameEl.textContent = name;
            input.replaceWith(nameEl);
            renaming = false;
            if (changed) {
                try {
                    const res = await fetch(`/api/conversations/${conv.id}/rename`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ name }),
                    });
                    if (!checkAuth(res)) return;
                    if (!res.ok) {
                        nameEl.textContent = oldName;
                        appendMessage(`Rename failed: ${await errorDetail(res)}`, 'system');
                    }
                } catch (e) {
                    nameEl.textContent = oldName;
                    appendMessage('Rename failed: server unreachable.', 'system');
                }
            }
            pendingConvList = null;   // may predate the rename; fetch a fresh list
            loadConversations(false);
        }
        input.addEventListener('keydown', e => {
            if (e.key === 'Enter') { e.preventDefault(); finish(true); }
            else if (e.key === 'Escape') { e.preventDefault(); finish(false); }
        });
        input.addEventListener('blur', () => finish(true));
    }

    // Fork goes through the agent (/fork), which copies the conversation and
    // switches to the copy; the "Forked into …" reply refreshes the sidebar.
    async function forkConv(id) {
        const connected = () => ws && ws.readyState === WebSocket.OPEN;
        if (!connected()) {
            appendMessage('Fork failed: disconnected (please retry).', 'system');
            return;
        }
        // The menu may have been opened before a turn started.
        if (switchLocked()) { appendMessage(SWITCH_LOCKED_MSG, 'system'); return; }
        closeSidebarOnNarrow();
        // The fork is an exact copy, so show the source conversation's messages.
        // Load them before sending /fork so the reply lands below them.
        clearMessages();
        await loadMessages(id);
        if (!connected()) {
            appendMessage('Fork failed: disconnected (please retry).', 'system');
            return;
        }
        if (switchLocked()) {
            // a turn started while the history loaded: show its conversation again
            appendMessage(SWITCH_LOCKED_MSG, 'system');
            clearMessages();
            await loadMessages(activeConvId);
            return;
        }
        ws.send(JSON.stringify({ type: 'message', content: `/fork ${id}` }));
    }

    // Delete goes through the agent (/delete): deleting the conversation it
    // has active makes it start a new one. Its reply refreshes the sidebar.
    function deleteConv(id) {
        if (!ws || ws.readyState !== WebSocket.OPEN) {
            appendMessage('Delete failed: disconnected (please retry).', 'system');
            return;
        }
        ws.send(JSON.stringify({ type: 'message', content: `/delete ${id}` }));
        if (id === activeConvId) {
            pendingActiveDelete = id;   // the reply says which conversation is next
            closeSidebarOnNarrow();
        }
    }

    // Deleting the active conversation switches the agent to another one (the
    // channel's most recent, or a new one). Show that conversation's messages,
    // with the delete reply below them, instead of the deleted one's.
    let pendingActiveDelete = null;

    // True if the system message was the reply to an active-conversation delete.
    function handleDeleteReply(text) {
        if (pendingActiveDelete === null || !text.includes(`[${pendingActiveDelete}]`)) return false;
        pendingActiveDelete = null;
        const next = /(?:Switched to|Started new) conversation \[(\d+)\]/.exec(text);
        if (!next) return false;   // refused (e.g. open in another channel): leave the chat as is
        activeConvId = parseInt(next[1]);
        clearMessages();
        loadMessages(activeConvId).then(() => appendMessage(text, 'system'));
        return true;
    }

    // Fetch a JSON attachment and save it in the browser, named by the
    // server's Content-Disposition. Failures are shown as "<what> failed: …".
    async function downloadJson(apiUrl, fallbackName, what) {
        try {
            const res = await fetch(apiUrl);
            if (!checkAuth(res)) return;
            if (!res.ok) {
                appendMessage(`${what} failed: ${await errorDetail(res)}`, 'system');
                return;
            }
            const disposition = res.headers.get('Content-Disposition') || '';
            const filename = (/filename="([^"]+)"/.exec(disposition) || [])[1] || fallbackName;
            const url = URL.createObjectURL(await res.blob());
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            a.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (e) {
            appendMessage(`${what} failed: server unreachable.`, 'system');
        }
    }

    // Export downloads the JSON in the browser (the /export command writes it
    // on the server instead, which a remote browser can't reach).
    function exportConv(id) {
        return downloadJson(`/api/conversations/${id}/export`, `conversation-${id}.json`, 'Export');
    }

    function loadConv(id) {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        if (switchLocked()) {
            // Switching mid-turn would queue the /load behind the turn and show
            // the turn's replies in the wrong conversation.
            if (id !== activeConvId) appendMessage(SWITCH_LOCKED_MSG, 'system');
            return;
        }
        ws.send(JSON.stringify({ type: 'message', content: `/load ${id}` }));
        activeConvId = id;
        convListEl.querySelectorAll('.conv-item').forEach(el => {
            el.classList.toggle('active', parseInt(el.dataset.id) === id);
        });
        clearMessages();
        loadMessages(id);
        closeSidebarOnNarrow();
    }

    newConvBtn.addEventListener('click', () => {
        if (!ws || ws.readyState !== WebSocket.OPEN || switchLocked()) return;
        ws.send(JSON.stringify({ type: 'message', content: '/new' }));
        clearMessages();
        closeSidebarOnNarrow();
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
    }

    // ---- status ----
    function setStatus(state) {
        statusDot.className = state;
    }

    modelBtn.addEventListener('click', () => {
        setInput('/model ');
        inputEl.focus();
    });

    // ---- WebSocket ----
    function connect() {
        setStatus('');
        ws = new WebSocket(wsUrl);

        ws.onopen = () => {
            reconnectDelay = 1000;
            setStatus('connected');
            refreshSendState();
            loadStatus();
            if (!commands.length) loadCommands();
            // The server first replays the agent's state (and a running turn's
            // progress); hold those frames until the history is on screen so
            // they land below it.
            holdFrames(async () => {
                clearMessages();   // always reset DOM before reloading — prevents duplicates on reconnect
                await loadConversations(true);
            });
        };

        ws.onclose = () => {
            // A rejected handshake looks like any other close; ask the API whether we're logged out.
            fetch('/api/status').then(checkAuth).catch(() => {});
            setStatus('');
            sendBtn.disabled = true;
            // the server re-sends the agent's state when we reconnect
            agentBusy = pendingTurn = stopping = false;
            renderBusy();
            setTimeout(connect, reconnectDelay);
            reconnectDelay = Math.min(reconnectDelay * 2, 30000);
        };

        ws.onerror = () => setStatus('');

        ws.onmessage = (evt) => {
            let data;
            try { data = JSON.parse(evt.data); } catch (e) { data = { type: 'message', content: evt.data }; }
            if (heldFrames) heldFrames.push(data);
            else handleFrame(data, false);
        };
    }

    // While the chat is being (re)loaded from the server, incoming frames wait
    // here, then are handled in order on top of the loaded history.
    let heldFrames = null;
    async function holdFrames(load) {
        const outer = heldFrames === null;
        if (outer) heldFrames = [];
        try { await load(); }
        finally {
            if (outer) {
                const frames = markReplyInHistory(heldFrames);
                heldFrames = null;
                for (let i = 0; i < frames.length; i++) {
                    // a frame started another reload: the rest wait for that one
                    if (heldFrames) { heldFrames.push(...frames.slice(i)); break; }
                    handleFrame(frames[i], true);
                }
            }
        }
    }

    // A turn's reply is stored as it is sent, so the loaded history can already
    // end with the reply of a turn whose frames were held. Then that turn's
    // held lines are left out (they'd land below its reply) and its reply
    // frame only points at the history row, for the trajectory button.
    function markReplyInHistory(frames) {
        const last = lastAiRow();
        const raw = last && last.querySelector('.bubble').dataset.raw;
        const end = frames.findIndex(f => f.type === 'message' && f.final && f.content === raw);
        if (end < 0) return frames;
        return frames.flatMap((f, i) => {
            if (i === end) return [{ type: 'reply-in-history', row: last }];
            const turnLine = f.type === 'message' && f.conversation_id != null;
            return i < end && turnLine ? [] : [f];
        });
    }

    // A turn frame for a conversation this tab isn't showing (e.g. a stale
    // tab) is left out; the conversation's history has its reply.
    const otherConv = data => data.conversation_id != null && activeConvId != null &&
                              data.conversation_id !== activeConvId;

    // caughtUp: the frame waited while the history loaded, so the history may
    // already contain what it shows.
    function handleFrame(data, caughtUp) {
        if (data.type === 'busy') {
            onBusy(data, caughtUp);
        } else if (data.type === 'trajectory') {
            if (otherConv(data)) return;
            // follows the turn's reply once its trajectory is stored; a turn
            // with no reply (stopped, or empty) gets no button anywhere
            if (turnReplyRow) addTrajectoryButton(turnReplyRow, data.message_id);
            turnReplyRow = null;
        } else if (data.type === 'reply-in-history') {
            turnReplyRow = data.row;
        } else if (data.type === 'message') {
            if (data.not_started && !agentBusy && pendingTurn) {
                // e.g. "Restarting, please send that again": the turn never started
                pendingTurn = false;
                renderBusy();
            }
            if (otherConv(data)) return;
            const row = appendMessage(data.content, 'ai');
            // only the turn's reply, not status lines ("> tool [...]") or thinking text
            if (data.final) turnReplyRow = row;
            // agent_loop() calls _store.touch() after every response, so the
            // sidebar's updated_at and message count change — refresh it
            setTimeout(() => loadConversations(false), 300);
        } else if (data.type === 'system') {
            if (!handleDeleteReply(data.content)) appendMessage(data.content, 'system');
            // always refresh after any system message; conversation-mutating
            // commands (new, load, fork, rename) change the sidebar list too,
            // and /model changes the model shown in the header
            setTimeout(() => { loadConversations(false); loadStatus(); }, 150);
        }
    }

    function lastAiRow() {
        const rows = messagesEl.querySelectorAll('.msg-row');
        const last = rows[rows.length - 1];
        return last && last.classList.contains('ai') ? last : null;
    }

    // ---- agent busy state + stop ----
    // The server sends {"type":"busy"} when an agent turn starts and ends (and
    // on connect), to every tab. pendingTurn covers the gap between sending a
    // message and its turn starting, so the dots show up immediately.
    let agentBusy = false;
    let pendingTurn = false;
    let stopping = false;
    // The running turn's reply row (a "final" message): gets the trajectory download.
    let turnReplyRow = null;

    // All tabs share one agent, and a /load, /new or /fork sent mid-turn
    // would only run after the turn (whose replies would then land in the
    // wrong conversation), so switching waits for the turn to end.
    const switchLocked = () => agentBusy || pendingTurn;
    const SWITCH_LOCKED = 'Wait for the agent to finish (or stop it) first';
    const SWITCH_LOCKED_MSG = 'The agent is still working. Wait for it to finish, or stop it, before switching conversations.';

    function onBusy({ busy, stopped, conversation_id: convId }, caughtUp) {
        agentBusy = busy;
        if (busy) {
            const mine = pendingTurn;
            pendingTurn = false;
            turnReplyRow = null;
            loadStatus();   // the model is shared: another channel may have changed it
            // A turn this tab didn't start (another tab, or a message sent
            // before a reload) or one in another conversation: show that
            // conversation from the history, which already has its message.
            const elsewhere = convId != null && convId !== activeConvId;
            if (convId != null && (elsewhere || (!mine && !caughtUp))) {
                activeConvId = convId;
                holdFrames(async () => {
                    clearMessages();
                    await loadMessages(convId);
                });
                loadConversations(false);
            }
        }
        else stopping = false;
        renderBusy();
        if (!busy && stopped) appendMessage('Stopped.', 'system');
    }

    function renderBusy() {
        const working = agentBusy || pendingTurn;
        const wasVisible = thinkingEl.classList.contains('visible');
        thinkingEl.classList.toggle('visible', working);
        if (working && !wasVisible) scrollBottom();
        if (ws && ws.readyState === WebSocket.OPEN) setStatus(working ? 'thinking' : 'connected');
        stopBtn.hidden = !agentBusy;
        stopBtn.disabled = stopping;
        const locked = switchLocked();
        newConvBtn.disabled = locked;
        newConvBtn.title = locked ? SWITCH_LOCKED : 'New conversation';
        convListEl.classList.toggle('locked', locked);
        convListEl.title = locked ? SWITCH_LOCKED : '';
        const label = stopping ? 'Stopping after the current step…' : 'Stop';
        stopBtn.title = label;
        stopBtn.setAttribute('aria-label', label);
    }

    // Stopping takes effect after the agent's current step (model call or tool
    // batch), so the button reads "Stopping…" until the turn actually ends.
    stopBtn.addEventListener('click', () => {
        if (!ws || ws.readyState !== WebSocket.OPEN || stopping) return;
        ws.send(JSON.stringify({ type: 'message', content: '/stop' }));
        stopping = true;
        renderBusy();
    });

    // ---- rendering ----
    function escapeHtml(t) {
        return String(t).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }
    function escapeAttr(t) {
        return escapeHtml(t).replace(/"/g, '&quot;');
    }
    function formatMessage(text) {
        text = escapeHtml(text);
        text = text.replace(/```[\w]*\n?([\s\S]*?)```/g, '<pre><code>$1</code></pre>');
        text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
        return text;
    }
    // trajectoryId: message ID whose trajectory can be downloaded (replies only).
    // Returns the new row.
    function appendMessage(content, role, trajectoryId = null) {
        const empty = document.getElementById('empty');
        if (empty) empty.style.display = 'none';

        const row = document.createElement('div');
        row.className = `msg-row ${role}`;
        const bubble = document.createElement('div');
        bubble.className = 'bubble';
        if (role === 'ai') {
            bubble.dataset.raw = content;
            renderAiBubble(bubble);
            // on the row, not in the bubble: re-rendering replaces the bubble's HTML
            const actions = document.createElement('div');
            actions.className = 'msg-actions';
            actions.appendChild(copyButton(bubble));
            row.appendChild(actions);
            if (trajectoryId) addTrajectoryButton(row, trajectoryId);
        } else if (role === 'user') {
            renderUserBubble(bubble, content);
        } else {
            bubble.innerHTML = escapeHtml(content);
        }
        row.prepend(bubble);
        messagesEl.appendChild(row);
        scrollBottom();
        return row;
    }

    // ---- download a reply's trajectory (the turn's tool calls, results, usage) ----
    const DOWNLOAD_ICON = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" ' +
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
        '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/>' +
        '<line x1="12" y1="15" x2="12" y2="3"/></svg>';

    function addTrajectoryButton(row, messageId) {
        const actions = row.querySelector('.msg-actions');
        if (!actions || actions.querySelector('.traj-btn')) return;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'copy-btn traj-btn';
        btn.innerHTML = DOWNLOAD_ICON;
        btn.title = 'Download trajectory';
        btn.setAttribute('aria-label', 'Download trajectory');
        btn.addEventListener('click', () => downloadJson(
            `/api/messages/${messageId}/trajectory`, `trajectory-${messageId}.json`, 'Trajectory download'));
        actions.appendChild(btn);
        row.classList.add('has-trajectory');
    }

    // ---- copy a reply ----
    const COPY_ICON = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" ' +
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
        '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/></svg>';
    const CHECK_ICON = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" ' +
        'stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
        '<polyline points="20 6 9 17 4 12"/></svg>';

    // Copies the reply's original text (the markdown source), in either view.
    function copyButton(bubble) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'copy-btn';
        const show = (icon, label, copied) => {
            btn.innerHTML = icon;
            btn.title = label;
            btn.setAttribute('aria-label', label);
            btn.classList.toggle('copied', copied);
        };
        show(COPY_ICON, 'Copy reply', false);
        btn.addEventListener('click', async () => {
            const ok = await copyText(bubble.dataset.raw ?? '');
            btn.focus();   // the fallback below moves focus to a temporary textarea
            show(ok ? CHECK_ICON : COPY_ICON, ok ? 'Copied' : 'Copy failed', ok);
            clearTimeout(btn.resetTimer);
            btn.resetTimer = setTimeout(() => show(COPY_ICON, 'Copy reply', false), 1500);
        });
        return btn;
    }

    async function copyText(text) {
        if (navigator.clipboard && window.isSecureContext) {
            try { await navigator.clipboard.writeText(text); return true; } catch (e) { /* fall back */ }
        }
        // The Clipboard API needs HTTPS or localhost; plain http://<lan-ip>
        // (e.g. Docker) doesn't have it, so copy via a hidden textarea.
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.setAttribute('readonly', '');
        ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0;pointer-events:none';
        document.body.appendChild(ta);
        ta.select();
        let ok = false;
        try { ok = document.execCommand('copy'); } catch (e) { /* unsupported */ }
        ta.remove();
        return ok;
    }
    function scrollBottom() { scrollEl.scrollTop = scrollEl.scrollHeight; }

    // ---- attachments in user messages ----
    // The agent stores attachments in history as "[Attachment: <stored name>]"
    // markers (stored name = "<uuid hex>_<original name>"). Show them as image
    // thumbnails (click → lightbox) or file chips instead of raw text.
    const ATTACHMENT_RE = /\s*\[Attachment: ([^\]\n]+)\]/g;
    const INLINE_IMAGE_RE = /\.(png|jpe?g|gif|webp)$/i;   // what /api/uploads serves inline
    const uploadUrl = name => `/api/uploads/${encodeURIComponent(name)}`;
    const displayName = name => name.replace(/^[0-9a-f]{32}_/, '');

    function renderUserBubble(bubble, content) {
        const names = [];
        const text = String(content).replace(ATTACHMENT_RE, (_, name) => {
            names.push(name.trim());
            return '';
        }).trim();
        if (text) {
            const textEl = document.createElement('div');
            textEl.innerHTML = formatMessage(text);
            bubble.appendChild(textEl);
        }
        if (!names.length) return;
        const box = document.createElement('div');
        box.className = 'msg-attachments';
        names.forEach(name => box.appendChild(attachmentNode(name)));
        bubble.appendChild(box);
    }

    function attachmentNode(name) {
        if (!INLINE_IMAGE_RE.test(name)) return fileChip(name, true);
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'msg-thumb';
        btn.title = displayName(name);
        btn.setAttribute('aria-label', `View ${displayName(name)}`);
        const img = document.createElement('img');
        img.src = uploadUrl(name);
        img.alt = displayName(name);
        img.loading = 'lazy';
        img.decoding = 'async';
        // e.g. a CLI attachment, which never went through the web upload dir
        img.addEventListener('error', () => btn.replaceWith(fileChip(name, false)), { once: true });
        btn.addEventListener('click', () => openLightbox(name, btn));
        btn.appendChild(img);
        return btn;
    }

    function fileChip(name, downloadable) {
        const el = document.createElement(downloadable ? 'a' : 'span');
        el.className = 'msg-file';
        // same paperclip as the attach button (an emoji may lack a font)
        el.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" ' +
                       'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
                       '<path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66' +
                       'l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>';
        const label = document.createElement('span');
        label.textContent = displayName(name);
        el.appendChild(label);
        if (downloadable) {
            el.href = uploadUrl(name);   // served as a download
            el.title = `Download ${displayName(name)}`;
        } else {
            el.title = 'File not available';
        }
        return el;
    }

    // Same markers the agent stores, so a just-sent message looks exactly
    // like it will after a reload.
    function withAttachmentMarkers(text, uploaded) {
        return [text, ...uploaded.map(f => `[Attachment: ${f.path}]`)].filter(Boolean).join(' ');
    }

    // ---- lightbox ----
    let lightbox = null;   // { el, returnFocus } while open

    // trigger gets focus back on close (a clicked button isn't always focused)
    function openLightbox(name, trigger) {
        closeLightbox();
        const el = document.createElement('div');
        el.className = 'lightbox';
        el.setAttribute('role', 'dialog');
        el.setAttribute('aria-modal', 'true');
        el.setAttribute('aria-label', displayName(name));
        el.innerHTML = '<figure><img><figcaption><span class="lightbox-name"></span>' +
                       '<a class="lightbox-open" target="_blank" rel="noopener">Open original ↗</a>' +
                       '</figcaption></figure>' +
                       '<button type="button" class="lightbox-close" aria-label="Close">×</button>';
        const img = el.querySelector('img');
        img.src = uploadUrl(name);
        img.alt = displayName(name);
        el.querySelector('.lightbox-name').textContent = displayName(name);
        el.querySelector('.lightbox-open').href = uploadUrl(name);
        // Clicking the backdrop or × closes; clicking the image doesn't.
        el.addEventListener('click', e => {
            if (e.target === el || e.target.closest('.lightbox-close')) closeLightbox();
        });
        document.body.appendChild(el);
        lightbox = { el, returnFocus: trigger || document.activeElement };
        el.querySelector('.lightbox-close').focus();
    }

    function closeLightbox() {
        if (!lightbox) return;
        const { el, returnFocus } = lightbox;
        lightbox = null;
        el.remove();
        if (returnFocus && returnFocus.focus) returnFocus.focus();
    }

    document.addEventListener('keydown', e => {
        if (!lightbox) return;
        if (e.key === 'Escape') { e.preventDefault(); closeLightbox(); }
        else if (e.key === 'Tab') {
            // keep focus inside the dialog (its link and close button)
            const stops = [...lightbox.el.querySelectorAll('a, button')];
            const i = stops.indexOf(document.activeElement);
            e.preventDefault();
            stops[(i + (e.shiftKey ? -1 : 1) + stops.length) % stops.length].focus();
        }
    });

    // ---- attachments ----
    function renderAttachments() {
        attachEl.innerHTML = '';
        attachEl.classList.toggle('has-files', selectedFiles.length > 0);
        selectedFiles.forEach((file, idx) => {
            const chip = document.createElement('div');
            chip.className = 'attach-chip';
            const label = document.createElement('span');
            label.className = 'attach-name';
            label.textContent = file.name;
            label.title = `${file.name} (${fmtSize(file.size)})`;
            const remove = document.createElement('button');
            remove.className = 'attach-remove';
            remove.type = 'button';
            remove.textContent = '×';
            remove.title = 'Remove attachment';
            remove.addEventListener('click', () => {
                selectedFiles.splice(idx, 1);
                renderAttachments();
                refreshSendState();
            });
            chip.appendChild(label);
            chip.appendChild(remove);
            attachEl.appendChild(chip);
        });
    }

    function fmtSize(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(0) + ' KB';
        return (bytes / 1024 / 1024).toFixed(1) + ' MB';
    }

    function refreshSendState() {
        const connected = ws && ws.readyState === WebSocket.OPEN;
        const hasInput = inputEl.value.trim().length > 0 || selectedFiles.length > 0;
        sendBtn.disabled = !connected || !hasInput;
    }

    // The attach control is a <label for="file-input">, so clicking it opens
    // the picker natively — no JS click needed (and avoids a double-open).
    // Copy the FileList into a real array before clearing .value: Edge can
    // invalidate the live FileList once .value is reset mid-handler.
    fileInput.addEventListener('change', () => {
        const picked = Array.from(fileInput.files || []);
        fileInput.value = '';   // allow re-selecting the same file later
        addFiles(picked);
    });

    function addFiles(files) {
        if (!files.length) return;
        selectedFiles.push(...files);
        renderAttachments();
        refreshSendState();
    }

    // Paste files (screenshots, or files copied in a file manager) straight
    // into the message box. If the clipboard also has plain text, paste that
    // as usual instead: spreadsheets and some editors put an image of the
    // selection next to the text, and the text is what was meant.
    inputEl.addEventListener('paste', e => {
        const data = e.clipboardData;
        const files = Array.from((data && data.files) || []);
        if (!files.length || data.getData('text/plain')) return;
        e.preventDefault();
        addFiles(files.map((f, i) => renameIfGeneric(f, i)));
    });

    // Browsers call every pasted screenshot "image.png"; give each a
    // timestamped name so several pastes can be told apart.
    function renameIfGeneric(file, index) {
        if (file.name && !/^image\.\w+$/.test(file.name)) return file;
        const ext = (file.type.split('/')[1] || 'bin').replace('jpeg', 'jpg').replace(/\W.*$/, '');
        const d = new Date();   // local time, like the clock the user sees
        const p2 = n => String(n).padStart(2, '0');
        const stamp = `${d.getFullYear()}${p2(d.getMonth() + 1)}${p2(d.getDate())}-` +
                      `${p2(d.getHours())}${p2(d.getMinutes())}${p2(d.getSeconds())}`;
        const suffix = index ? `-${index + 1}` : '';
        return new File([file], `pasted-${stamp}${suffix}.${ext}`, { type: file.type, lastModified: file.lastModified });
    }

    async function uploadFiles(files) {
        const form = new FormData();
        files.forEach(f => form.append('files', f, f.name));
        const res = await fetch('/api/upload', { method: 'POST', body: form });
        if (!checkAuth(res)) return [];
        if (!res.ok) {
            let detail = '';
            try { detail = (await res.json()).error || ''; } catch (e) { /* ignore */ }
            throw new Error(detail || `Upload failed (${res.status})`);
        }
        const { files: saved } = await res.json();
        return saved;   // [{path, name}, ...]
    }

    // ---- send ----
    async function send() {
        const text = inputEl.value.trim();
        const files = selectedFiles.slice();
        if (sendBtn.disabled) return;
        if (switchLocked() && /^\/(new|load|fork)\b/i.test(text)) {
            appendMessage(SWITCH_LOCKED_MSG, 'system');
            return;
        }

        let uploaded = [];
        if (files.length) {
            sendBtn.disabled = true;
            try {
                uploaded = await uploadFiles(files);
            } catch (e) {
                appendMessage('Attachment upload failed: ' + e.message, 'system');
                refreshSendState();
                return;
            }
        }

        const payload = { type: 'message', content: text };
        if (uploaded.length) payload.files = uploaded.map(f => f.path);
        try {
            ws.send(JSON.stringify(payload));
        } catch (err) {
            appendMessage('Send failed: disconnected (please retry).', 'system');
            refreshSendState();
            return;
        }

        appendMessage(withAttachmentMarkers(text, uploaded), 'user');
        selectedFiles = [];
        renderAttachments();
        inputEl.value = '';
        inputEl.style.height = '42px';
        refreshSendState();
        updateSuggestions();

        if (text === '/new') {
            clearMessages();
        } else if (/^\/load\s+\d+/.test(text)) {
            const id = parseInt(text.split(/\s+/)[1]);
            clearMessages();
            loadMessages(id);
        } else if (/^\/delete\s+\d+$/.test(text)) {
            const id = parseInt(text.split(/\s+/)[1]);
            if (id === activeConvId) pendingActiveDelete = id;
        } else if (text === '/stop') {
            if (agentBusy) { stopping = true; renderBusy(); }
        } else if (!text.startsWith('/')) {
            pendingTurn = true;
            renderBusy();
        }
    }

    sendBtn.addEventListener('click', send);
    inputEl.addEventListener('keydown', (e) => {
        if (suggestions.length && handleSuggestKey(e)) return;
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
    });
    inputEl.addEventListener('input', () => {
        inputEl.style.height = 'auto';
        inputEl.style.height = Math.min(inputEl.scrollHeight, 160) + 'px';
        refreshSendState();
        updateSuggestions();
    });
    inputEl.addEventListener('focus', updateSuggestions);
    inputEl.addEventListener('blur', () => { suggestions = []; renderSuggestions(); });

    // ---- slash-command autocomplete ----
    // Typing "/" lists the commands from /api/commands; after a command that
    // takes a conversation ID, it lists conversations from the sidebar (match
    // by ID prefix or a word of the name). Tab/Enter accept, arrows move,
    // Escape closes until the input changes.
    const suggestEl = document.getElementById('cmd-suggest');
    const ID_COMMANDS = new Set(['load', 'fork', 'rename', 'export', 'delete']);
    let commands = [];            // [{name, description}]
    let convList = [];            // last list from /api/conversations
    let suggestions = [];         // [{value, label, hint, detail}] on screen
    let suggestIndex = 0;
    let suggestDismissed = null;  // input value the list was closed on

    async function loadCommands() {
        try {
            const res = await fetch('/api/commands');
            if (!checkAuth(res) || !res.ok) return;
            ({ commands } = await res.json());
        } catch (e) { /* retried on reconnect */ }
    }

    // "Load a conversation. Usage: /load <id>" → description + argument hint
    function splitUsage(description) {
        const [desc, usage = ''] = description.split(/\s*Usage:\s*/);
        return { desc: desc.replace(/\.$/, ''), args: usage.replace(/^\/\S+\s*/, '') };
    }

    function computeSuggestions(value) {
        let m = /^\/(\S*)$/.exec(value);
        if (m) {
            const q = m[1].toLowerCase();
            return commands.filter(c => c.name.startsWith(q)).map(c => {
                const { desc, args } = splitUsage(c.description);
                // a trailing space after commands with arguments, ready to type them
                return { value: `/${c.name}${args ? ' ' : ''}`, label: `/${c.name}`, hint: args, detail: desc };
            });
        }
        m = /^\/(\w+) (\S*)$/.exec(value);
        if (m && ID_COMMANDS.has(m[1].toLowerCase())) {
            const cmd = m[1].toLowerCase();
            const q = m[2].toLowerCase();
            return convList
                .filter(c => String(c.id).startsWith(q) || (c.name || '').toLowerCase().includes(q))
                .map(c => ({
                    value: `/${cmd} ${c.id}${cmd === 'rename' ? ' ' : ''}`,   // rename: the name comes next
                    label: String(c.id), hint: c.name || 'Untitled', detail: c.channel || '',
                }));
        }
        return [];
    }

    function updateSuggestions() {
        const value = inputEl.value;
        suggestions = value === suggestDismissed ? [] : computeSuggestions(value);
        // An exact match goes first; a list that only repeats the input is
        // noise (but "/rename 1" still offers "/rename 1 ", ready for the name).
        const exact = suggestions.findIndex(s => s.value.trimEnd() === value.trimEnd());
        if (exact > 0) suggestions.unshift(...suggestions.splice(exact, 1));
        if (suggestions.length === 1 && suggestions[0].value === value) suggestions = [];
        suggestIndex = 0;
        renderSuggestions();
    }

    function renderSuggestions() {
        suggestEl.innerHTML = '';
        const open = suggestions.length > 0;
        suggestEl.hidden = !open;
        inputEl.setAttribute('aria-expanded', open);
        if (!open) { inputEl.removeAttribute('aria-activedescendant'); return; }
        suggestions.forEach((s, i) => {
            const opt = document.createElement('div');
            opt.id = `cmd-opt-${i}`;
            opt.className = 'cmd-option' + (i === suggestIndex ? ' selected' : '');
            opt.setAttribute('role', 'option');
            opt.setAttribute('aria-selected', i === suggestIndex);
            for (const [cls, text] of [['cmd-label', s.label], ['cmd-hint', s.hint], ['cmd-detail', s.detail]]) {
                const span = document.createElement('span');
                span.className = cls;
                span.textContent = text;
                opt.appendChild(span);
            }
            // mousedown (not click) so the input keeps focus
            opt.addEventListener('mousedown', e => { e.preventDefault(); acceptSuggestion(i); });
            suggestEl.appendChild(opt);
        });
        inputEl.setAttribute('aria-activedescendant', `cmd-opt-${suggestIndex}`);
        suggestEl.children[suggestIndex].scrollIntoView({ block: 'nearest' });
    }

    // Returns true if the key was handled by the suggestion list.
    function handleSuggestKey(e) {
        const n = suggestions.length;
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            suggestIndex = (suggestIndex + (e.key === 'ArrowDown' ? 1 : -1) + n) % n;
            renderSuggestions();
        } else if (e.key === 'Escape') {
            suggestDismissed = inputEl.value;
            updateSuggestions();
        } else if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) {
            // Enter on a suggestion the input already equals sends instead
            if (e.key === 'Enter' && suggestions[suggestIndex].value.trimEnd() === inputEl.value.trimEnd()) return false;
            acceptSuggestion(suggestIndex);
        } else {
            return false;
        }
        e.preventDefault();
        return true;
    }

    function acceptSuggestion(i) {
        setInput(suggestions[i].value);   // the input event shows the next stage, e.g. "/load " → conversations
        inputEl.focus();
    }

    function setInput(value) {
        inputEl.value = value;
        inputEl.setSelectionRange(value.length, value.length);
        inputEl.dispatchEvent(new Event('input'));   // resize, send button, suggestions
    }

    loadCommands();

    // sidebar pre-populate (messages already loaded via ws.onopen → loadConversations(true))
    if (sidebarOpen) loadConversations(false);

    loadStatus();
    connect();
})();
