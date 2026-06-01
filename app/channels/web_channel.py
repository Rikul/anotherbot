"""FastHTML web channel — serves a chat UI and a JSON WebSocket endpoint.

The channel exposes two endpoints on the same uvicorn server:

    GET /          — FastHTML chat UI (HTML page, served to browsers)
    WS  /ws        — WebSocket endpoint (JSON framing)

Authentication: optional ``api_key`` query parameter, checked on connect.
Mismatched keys are rejected with WebSocket close code 4001.

WebSocket message framing::

    {"type": "message", "content": "..."}

Plain text is also accepted as a convenience.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime

import uvicorn
from fasthtml.common import (
    Button, Div, Head, Html, Link, Meta, Script, Span, Style,
    Textarea, Title, Body, H1, P, fast_app,
)
from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from .channel import Channel, ChannelType
from .message import IncomingMessage, OutgoingMessage
from .message_queue import MessageQueue

log = logging.getLogger(__name__)

_startup_time = datetime.now()

# --------------------------------------------------------------------------- #
# CSS                                                                          #
# --------------------------------------------------------------------------- #

_CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
    --bg:          #0f172a;
    --surface:     #1e293b;
    --surface2:    #273548;
    --border:      #334155;
    --text:        #e2e8f0;
    --text-muted:  #94a3b8;
    --accent:      #6366f1;
    --accent-h:    #818cf8;
    --user-bg:     #6366f1;
    --ai-bg:       #1e293b;
    --sidebar-w:   260px;
    --radius:      14px;
    --font:        'Inter', 'Segoe UI', system-ui, sans-serif;
}

[data-theme="light"] {
    --bg:        #f8fafc;
    --surface:   #ffffff;
    --surface2:  #f1f5f9;
    --border:    #e2e8f0;
    --text:      #0f172a;
    --text-muted:#64748b;
    --accent:    #6366f1;
    --accent-h:  #4f46e5;
    --user-bg:   #6366f1;
    --ai-bg:     #f1f5f9;
}

html, body { height: 100%; overflow: hidden; }

body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--font);
    font-size: 15px;
    line-height: 1.6;
    display: flex;
    transition: background .2s, color .2s;
}

/* ---- top-level layout: header on top, then sidebar + chat ---- */
#app {
    display: flex;
    flex-direction: column;
    width: 100%;
    height: 100vh;
}

#body-row {
    display: flex;
    flex-direction: row;
    flex: 1;
    overflow: hidden;
}

/* ---- sidebar ---- */
#sidebar {
    width: var(--sidebar-w);
    min-width: var(--sidebar-w);
    background: var(--surface);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    overflow: hidden;
    transition: width .25s ease, min-width .25s ease, border-color .2s;
    flex-shrink: 0;
}
#sidebar.collapsed {
    width: 0;
    min-width: 0;
    border-right-width: 0;
}

#sidebar-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 16px 10px;
    flex-shrink: 0;
}
#sidebar-header span {
    font-size: 0.75rem;
    font-weight: 600;
    letter-spacing: .06em;
    text-transform: uppercase;
    color: var(--text-muted);
    white-space: nowrap;
}
#new-conv-btn {
    background: var(--accent);
    border: none;
    border-radius: 8px;
    color: #fff;
    cursor: pointer;
    font-size: 0.78rem;
    font-weight: 600;
    padding: 5px 10px;
    white-space: nowrap;
    transition: background .15s;
}
#new-conv-btn:hover { background: var(--accent-h); }

#conv-list {
    flex: 1;
    overflow-y: auto;
    padding: 4px 8px 12px;
}
#conv-list::-webkit-scrollbar { width: 3px; }
#conv-list::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }

.conv-item {
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: 8px 10px;
    border-radius: 8px;
    cursor: pointer;
    transition: background .12s;
    white-space: nowrap;
    overflow: hidden;
}
.conv-item:hover { background: var(--surface2); }
.conv-item.active { background: var(--surface2); }
.conv-item.active .conv-name { color: var(--accent); }
.conv-name {
    font-size: 0.86rem;
    font-weight: 500;
    overflow: hidden;
    text-overflow: ellipsis;
    color: var(--text);
}
.conv-meta {
    font-size: 0.74rem;
    color: var(--text-muted);
}

/* ---- header (full-width, top of page) ---- */
#header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 20px;
    border-bottom: 1px solid var(--border);
    background: var(--surface);
    flex-shrink: 0;
    width: 100%;
    transition: background .2s, border-color .2s;
}

/* ---- chat panel ---- */
#main {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-width: 0;
    max-width: 820px;
    margin: 0 auto;
    width: 100%;
}
#header-left { display: flex; align-items: center; gap: 10px; }
#header h1 {
    font-size: 1.1rem;
    font-weight: 600;
    letter-spacing: -0.01em;
}
#header-right { display: flex; align-items: center; gap: 14px; }
#sidebar-toggle {
    background: none;
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--text-muted);
    cursor: pointer;
    font-size: 1rem;
    padding: 4px 8px;
    line-height: 1;
    transition: border-color .15s, color .15s;
}
#sidebar-toggle:hover { border-color: var(--accent); color: var(--accent); }
#status {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 0.8rem;
    color: var(--text-muted);
}
#theme-btn {
    background: none;
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--text-muted);
    cursor: pointer;
    font-size: 1rem;
    line-height: 1;
    padding: 4px 8px;
    transition: border-color .15s, color .15s;
}
#theme-btn:hover { border-color: var(--accent); color: var(--accent); }
#status-dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: #ef4444;
    transition: background .3s;
}
#status-dot.connected { background: #22c55e; }
#status-dot.thinking  { background: #f59e0b; animation: pulse 1s infinite; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }

/* ---- messages ---- */
/* Outer scroll container — must be separate from the flex container to allow scrolling to top */
#messages-wrap {
    flex: 1;
    overflow-y: auto;
    scroll-behavior: smooth;
}
#messages-wrap::-webkit-scrollbar { width: 4px; }
#messages-wrap::-webkit-scrollbar-track { background: transparent; }
#messages-wrap::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }

/* Inner flex container — min-height: 100% + justify-content: flex-end anchors messages to bottom */
#messages {
    display: flex;
    flex-direction: column;
    justify-content: flex-end;
    min-height: 100%;
    padding: 20px;
    gap: 16px;
}

.msg-row {
    display: flex;
    gap: 10px;
    max-width: 80%;
    animation: fadeUp .2s ease;
}
@keyframes fadeUp {
    from { opacity:0; transform:translateY(8px); }
    to   { opacity:1; transform:translateY(0); }
}
.msg-row.user   { align-self: flex-end;   flex-direction: row-reverse; }
.msg-row.ai     { align-self: flex-start; }
.msg-row.system { align-self: center; }

.avatar {
    width: 32px; height: 32px;
    border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-size: 0.85rem; font-weight: 700; flex-shrink: 0;
}
.user .avatar { background: var(--accent);   color: #fff; }
.ai   .avatar { background: var(--surface2); color: var(--text-muted); }

.bubble {
    padding: 10px 14px;
    border-radius: var(--radius);
    word-break: break-word;
    white-space: pre-wrap;
    max-width: 100%;
    font-size: 0.92rem;
}
.user   .bubble { background: var(--user-bg); color: #fff; border-bottom-right-radius: 4px; }
.ai     .bubble { background: var(--ai-bg); border: 1px solid var(--border); border-bottom-left-radius: 4px; transition: background .2s, border-color .2s; }
.system .bubble { background: transparent; border: 1px dashed var(--border); color: var(--text-muted); font-size: 0.82rem; padding: 6px 12px; border-radius: 8px; }

.bubble code { background: var(--surface2); border-radius: 4px; padding: 1px 5px; font-size: .85em; font-family: 'Fira Code','Cascadia Code',monospace; }
.bubble pre  { background: var(--surface2); border: 1px solid var(--border); border-radius: 8px; padding: 12px; overflow-x: auto; margin-top: 6px; }
.bubble pre code { background: none; padding: 0; }

/* thinking dots — sits between scroll area and input, always above the input box */
#thinking { display:none; align-self:flex-start; gap:10px; padding: 4px 20px 8px; flex-shrink: 0; }
#thinking.visible { display:flex; }
.thinking-bubble { background:var(--ai-bg); border:1px solid var(--border); border-radius:var(--radius); border-bottom-left-radius:4px; padding:12px 16px; }
.dots span { display:inline-block; width:6px; height:6px; background:var(--text-muted); border-radius:50%; margin:0 2px; animation:bounce .9s infinite; }
.dots span:nth-child(2) { animation-delay:.15s; }
.dots span:nth-child(3) { animation-delay:.3s;  }
@keyframes bounce { 0%,60%,100%{transform:translateY(0)} 30%{transform:translateY(-6px)} }

/* ---- empty state ---- */
#empty { flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:12px; color:var(--text-muted); pointer-events:none; }
#empty .icon { font-size:2.5rem; }
#empty p { font-size:0.9rem; }

/* ---- input area ---- */
#input-area {
    padding: 14px 20px;
    border-top: 1px solid var(--border);
    background: var(--surface);
    display: flex;
    gap: 10px;
    align-items: flex-end;
    flex-shrink: 0;
}
#msg-input {
    flex: 1;
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 10px;
    color: var(--text);
    font-family: var(--font);
    font-size: 0.92rem;
    padding: 10px 14px;
    resize: none;
    outline: none;
    min-height: 42px;
    max-height: 160px;
    transition: border-color .2s;
    overflow-y: auto;
}
#msg-input:focus { border-color: var(--accent); }
#msg-input::placeholder { color: var(--text-muted); }

#send-btn {
    background: var(--accent);
    border: none;
    border-radius: 10px;
    color: #fff;
    cursor: pointer;
    padding: 10px 16px;
    font-size: 0.9rem;
    font-weight: 600;
    transition: background .15s, transform .1s;
    height: 42px;
    white-space: nowrap;
}
#send-btn:hover   { background: var(--accent-h); }
#send-btn:active  { transform: scale(.97); }
#send-btn:disabled { background: var(--border); cursor: not-allowed; }
"""

# --------------------------------------------------------------------------- #
# JavaScript                                                                   #
# --------------------------------------------------------------------------- #

_JS = """
(function() {
    const API_KEY = window._apiKey || '';
    // Strip api_key from address bar so it doesn't leak via browser history or copy-paste
    if (API_KEY) {
        const p = new URLSearchParams(location.search);
        p.delete('api_key');
        history.replaceState(null, '', location.pathname + (p.toString() ? '?' + p : ''));
    }
    const wsProto = location.protocol === 'https:' ? 'wss' : 'ws';
    const wsUrl = `${wsProto}://${location.host}/ws${API_KEY ? '?api_key=' + encodeURIComponent(API_KEY) : ''}`;

    let ws = null;
    let reconnectDelay = 1000;
    let activeConvId = null;
    let modelName = 'AI';
    let modelLabel = '';

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
        if (!collapsed) loadConversations();
    });

    // ---- model name ----
    async function loadStatus() {
        try {
            const url = '/api/status' + (API_KEY ? '?api_key=' + encodeURIComponent(API_KEY) : '');
            const res = await fetch(url);
            if (!res.ok) return;
            const { model } = await res.json();
            if (model) {
                const seg = model.split('/').pop();
                modelLabel = seg;
                modelName  = seg.substring(0, 4).toUpperCase();
            }
        } catch (e) { /* server may not be fully up yet */ }
    }

    // ---- conversations ----
    async function loadConversations() {
        try {
            const url = '/api/conversations' + (API_KEY ? '?api_key=' + encodeURIComponent(API_KEY) : '');
            const res = await fetch(url);
            if (!res.ok) return;
            const { conversations, active_id } = await res.json();
            activeConvId = active_id;
            renderConversations(conversations, active_id);
        } catch (e) { /* server may not be fully up yet */ }
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
        // optimistically highlight
        convListEl.querySelectorAll('.conv-item').forEach(el => {
            el.classList.toggle('active', parseInt(el.dataset.id) === id);
        });
        clearMessages();
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
            loadConversations();
            loadStatus();
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
                } else if (data.type === 'system') {
                    appendMessage(data.content, 'system');
                    // refresh list after conversation-mutating commands
                    if (/loaded|created|forked|renamed/i.test(data.content)) {
                        setTimeout(loadConversations, 150);
                    }
                }
            } catch (e) {
                appendMessage(evt.data, 'ai');
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
        text = text.replace(/```[\\w]*\\n?([\\s\\S]*?)```/g, '<pre><code>$1</code></pre>');
        text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
        return text;
    }
    function appendMessage(content, role) {
        const empty = document.getElementById('empty');
        if (empty) empty.style.display = 'none';

        const row = document.createElement('div');
        row.className = `msg-row ${role}`;
        if (role !== 'system') {
            const av = document.createElement('div');
            av.className = 'avatar';
            av.textContent = role === 'user' ? 'U' : modelName;
            if (role !== 'user') av.title = modelLabel || modelName;
            row.appendChild(av);
        }
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
        if (!text.startsWith('/')) showThinking();
    }

    sendBtn.addEventListener('click', send);
    inputEl.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
    });
    inputEl.addEventListener('input', () => {
        inputEl.style.height = 'auto';
        inputEl.style.height = Math.min(inputEl.scrollHeight, 160) + 'px';
    });

    // initial conversation load if sidebar starts open
    if (sidebarOpen) loadConversations();

    connect();
})();
"""


# --------------------------------------------------------------------------- #
# FastHTML page builder                                                        #
# --------------------------------------------------------------------------- #

def _build_page() -> Html:
    # api_key is read from ?api_key= URL param in the browser — never embedded in HTML
    api_key_script = "window._apiKey = new URLSearchParams(location.search).get('api_key') || '';"
    return Html(
        Head(
            Meta(charset="utf-8"),
            Meta(name="viewport", content="width=device-width, initial-scale=1"),
            Title("anotherbot"),
            Link(rel="preconnect", href="https://fonts.googleapis.com"),
            Link(rel="stylesheet",
                 href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap"),
            Style(_CSS),
        ),
        Body(
            Div(
                # ---- Header (full width) ----
                Div(
                    Div(
                        Button("☰", id="sidebar-toggle", title="Toggle sidebar"),
                        H1("anotherbot"),
                        id="header-left",
                    ),
                    Div(
                        Div(
                            Span(id="status-dot"),
                            Span("Connecting…", id="status-text"),
                            id="status",
                        ),
                        Button("☾", id="theme-btn", title="Toggle light/dark"),
                        id="header-right",
                    ),
                    id="header",
                ),
                # ---- Body row: sidebar + chat ----
                Div(
                    # Sidebar
                    Div(
                        Div(
                            Span("Conversations"),
                            Button("+ New", id="new-conv-btn"),
                            id="sidebar-header",
                        ),
                        Div(id="conv-list"),
                        id="sidebar",
                    ),
                    # Chat panel
                    Div(
                        # Scroll container (outer) + messages (inner flex, bottom-anchored)
                        Div(
                            Div(
                                Div(
                                    Div("✦", cls="icon"),
                                    P("Ask me anything, or try /help for available commands."),
                                    id="empty",
                                ),
                                id="messages",
                            ),
                            id="messages-wrap",
                        ),
                        # Thinking dots — always just above the input box
                        Div(
                            Div(cls="avatar", style="background:var(--surface2);color:var(--text-muted)"),
                            Div(Div(Span(), Span(), Span(), cls="dots"), cls="thinking-bubble"),
                            id="thinking",
                        ),
                        # Input area
                        Div(
                            Textarea(
                                id="msg-input",
                                placeholder="Message anotherbot…  (Enter to send, Shift+Enter for newline)",
                                autocomplete="off",
                                rows="1",
                            ),
                            Button("Send", id="send-btn", disabled=True),
                            id="input-area",
                        ),
                        id="main",
                    ),
                    id="body-row",
                ),
                id="app",
            ),
            Script(api_key_script),
            Script(_JS),
        ),
        lang="en",
    )


# --------------------------------------------------------------------------- #
# Channel class                                                                #
# --------------------------------------------------------------------------- #

class WebChannel(Channel):
    """FastHTML web channel.

    Serves the chat UI at ``GET /`` and accepts WebSocket connections at
    ``WS /ws``.  Multiple concurrent clients are supported — each gets a
    unique UUID stored in ``_connections``.
    """

    def __init__(
        self,
        mq: MessageQueue,
        host: str = "127.0.0.1",
        port: int = 8765,
        api_key: str | None = None,
    ) -> None:
        self.mq = mq
        self.host = host
        self.port = port
        self.api_key = api_key
        self.stopped: bool = False
        self._connections: dict[str, WebSocket] = {}
        self._send_locks: dict[str, asyncio.Lock] = {}
        self._conn_lock = asyncio.Lock()
        mq.register(self, self.send_message)

    # -- Channel ABC --------------------------------------------------------

    @property
    def has_stopped(self) -> bool:
        return self.stopped

    def clear_stopped(self) -> None:
        self.stopped = False

    @property
    def channel_type(self) -> ChannelType:
        return ChannelType.WEB

    @property
    def default_metadata(self) -> dict:
        return {}

    async def error_handler(self, update: object, context: object) -> None:
        log.error(f"WebChannel error: {context}")

    async def process_message(self, message: object) -> None:
        pass  # handled inline in the WebSocket endpoint

    # -- Lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Build the FastHTML app + WebSocket route."""
        log.info(f"Building web channel on {self.host}:{self.port}")

        self._fasthtml_app, rt = fast_app(hdrs=())

        @rt("/")
        def index():
            return _build_page()

        @rt("/api/conversations")
        def conversations_api(req):
            from starlette.responses import JSONResponse, Response
            from ..infra.conversations import ConversationStore
            from ..core import runtime as _rt
            api_key_val = req.query_params.get("api_key", req.headers.get("x-api-key", ""))
            if self.api_key and api_key_val != self.api_key:
                return Response(status_code=401)
            store = ConversationStore()
            ch = ChannelType.WEB.value
            convs = store.list(ch)
            active_id = _rt.get(f"conversation_id:{ch}")
            return JSONResponse({"conversations": convs, "active_id": active_id})

        @rt("/api/status")
        def status_api(req):
            from starlette.responses import JSONResponse, Response
            from .. import config as _cfg
            from ..core import runtime as _rt
            api_key_val = req.query_params.get("api_key", req.headers.get("x-api-key", ""))
            if self.api_key and api_key_val != self.api_key:
                return Response(status_code=401)
            model = _rt.get("model", _cfg.get("model", "AI"))
            return JSONResponse({"model": model})

        # Starlette WebSocket route (low-level, for multi-client management)
        async def _ws_endpoint(ws: WebSocket) -> None:
            await ws.accept()

            api_key_param = ws.query_params.get("api_key", "")
            if self.api_key and api_key_param != self.api_key:
                await ws.close(code=4001, reason="Unauthorized — bad api_key")
                log.warning("WebSocket connection rejected: invalid api_key")
                return

            client_id = str(uuid.uuid4())
            log.info(f"WebSocket client connected: {client_id}")

            async with self._conn_lock:
                self._connections[client_id] = ws

            try:
                while True:
                    raw = await ws.receive_text()
                    if len(raw) > 65_536:
                        await ws.close(code=1009, reason="Message too large")
                        return
                    content = self._extract_content(raw)
                    if not content:
                        continue

                    if content.startswith("/"):
                        cmd = content[1:].split(maxsplit=1)
                        if not cmd:
                            continue
                        name = cmd[0].lower()
                        if name == "whoami":
                            await self._safe_send_json(
                                client_id,
                                {"type": "system", "content": f"Connection ID: {client_id}"},
                            )
                            continue
                        if name == "stop":
                            self.stopped = True
                            await self._safe_send_json(
                                client_id,
                                {"type": "system", "content": "Agent stopped."},
                            )
                            continue
                        if name == "help":
                            await self._safe_send_json(
                                client_id,
                                {"type": "system", "content": (
                                    "Available commands: /help · /whoami · /stop · "
                                    "/new · /list · /load <n> · /fork · /rename <name> · "
                                    "/export · /model [name] · /status"
                                )},
                            )
                            continue
                        if name == "status":
                            from .. import config as _cfg
                            from ..core import runtime as _rt
                            uptime = datetime.now() - _startup_time
                            h, rem = divmod(int(uptime.total_seconds()), 3600)
                            m, s = divmod(rem, 60)
                            ch_str = ChannelType.WEB.value
                            model = _rt.get("model", _cfg.get("model", "unknown"))
                            conv_id = _rt.get(f"conversation_id:{ch_str}", "—")
                            conv_name = _rt.get(f"conversation_name:{ch_str}", "—")
                            await self._safe_send_json(client_id, {"type": "system", "content": (
                                f"model: {model}  |  uptime: {h}h {m}m {s}s  |  "
                                f"conversation: [{conv_id}] {conv_name}  |  "
                                f"clients: {len(self._connections)}"
                            )})
                            continue

                    await self.mq.incoming.put(
                        IncomingMessage(
                            content=content,
                            channel=ChannelType.WEB,
                            metadata={
                                "websocket_id": client_id,
                                "is_command": content.startswith("/"),
                            },
                        )
                    )
            except WebSocketDisconnect:
                log.info(f"WebSocket client disconnected: {client_id}")
            except Exception:
                log.exception(f"WebSocket error for client {client_id}")
            finally:
                async with self._conn_lock:
                    self._connections.pop(client_id, None)
                    self._send_locks.pop(client_id, None)

        # Mount the WebSocket route on the FastHTML (Starlette) app
        self._fasthtml_app.router.routes.insert(
            0, WebSocketRoute("/ws", _ws_endpoint)
        )

    async def run_polling(self) -> None:
        """Start uvicorn and serve until cancelled."""
        config = uvicorn.Config(
            app=self._fasthtml_app,
            host=self.host,
            port=self.port,
            log_level="info",
        )
        server = uvicorn.Server(config)
        log.info(f"Web UI at http://{self.host}:{self.port}/")
        log.info(f"WebSocket at ws://{self.host}:{self.port}/ws")
        await server.serve()

    # -- Message delivery ---------------------------------------------------

    async def send_message(self, message: OutgoingMessage) -> None:
        client_id = message.metadata.get("websocket_id")
        is_command = message.metadata.get("is_command", False)
        msg_type = "system" if is_command else "message"
        payload = {"type": msg_type, "content": message.content}
        if client_id:
            await self._safe_send_json(client_id, payload)
        else:
            # No specific client (e.g. scheduled task delivery) — broadcast to all connected clients
            async with self._conn_lock:
                targets = list(self._connections.keys())
            for cid in targets:
                await self._safe_send_json(cid, payload)

    # -- Helpers ------------------------------------------------------------

    @staticmethod
    def _extract_content(raw: str) -> str | None:
        raw = raw.strip()
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw
        if isinstance(data, dict) and data.get("type") == "message":
            return data.get("content", "").strip() or None
        return raw

    async def _safe_send_json(self, client_id: str, payload: dict) -> None:
        async with self._conn_lock:
            ws = self._connections.get(client_id)
            if ws is None:
                return
            lock = self._send_locks.setdefault(client_id, asyncio.Lock())
        async with lock:
            try:
                await ws.send_json(payload)
            except Exception:
                log.exception(f"Failed to send to WebSocket client {client_id}")
                async with self._conn_lock:
                    self._connections.pop(client_id, None)
                    self._send_locks.pop(client_id, None)
