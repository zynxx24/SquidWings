/**
 * Agent Router — Web GUI Application
 * Advanced multi-model AI agent chat interface with real-time streaming,
 * dynamic model selector, deep reasoning thinking toggle, and session analytics.
 */

(function () {
  'use strict';

  // ─── Application State ──────────────────────────────────────────────
  const state = {
    sessionId: localStorage.getItem('agent_router_session_id') || '',
    activeModelId: 'ag/gemini-3.8-flash-high',
    activeModelName: 'Gemini 3.8 Flash',
    models: {},
    rawModelsList: [],
    providers: [],
    totalModels: 0,
    ws: null,
    wsConnected: false,
    isGenerating: false,
    reconnectTimer: null,
    reconnectAttempts: 0,
    currentStreamingMessageEl: null,
    currentThinkingEl: null,
    currentThinkingText: '',
    currentContentEl: null,
    currentContentText: '',
    typingIndicatorEl: null,
    messageCount: 0,
    // Chat History
    chatHistory: [],
    historySearchQuery: '',
  };

  // ─── DOM Element References ────────────────────────────────────────
  const DOM = {
    app: document.getElementById('app'),
    sidebar: document.getElementById('sidebar'),
    sidebarBackdrop: document.getElementById('sidebar-backdrop'),
    btnMobileMenu: document.getElementById('btn-mobile-menu'),

    // Chat History in Sidebar
    btnSidebarNew: document.getElementById('btn-sidebar-new'),
    historyCount: document.getElementById('history-count'),
    historySearchInput: document.getElementById('history-search-input'),
    historySearchClear: document.getElementById('history-search-clear'),
    historyList: document.getElementById('history-list'),
    historyEmpty: document.getElementById('history-empty'),

    // Active Model
    btnSelectModel: document.getElementById('btn-select-model'),
    activeModelDot: document.getElementById('active-model-dot'),
    activeModelName: document.getElementById('active-model-name'),
    activeModelBadge: document.getElementById('active-model-badge'),

    // Session Stats in Sidebar
    statSessionId: document.getElementById('stat-session-id'),
    statTurns: document.getElementById('stat-turns'),
    statTokens: document.getElementById('stat-tokens'),
    statLatency: document.getElementById('stat-latency'),

    // Sidebar Nav
    navNewChat: document.getElementById('nav-new-chat'),
    navSelectModel: document.getElementById('nav-select-model'),
    navStats: document.getElementById('nav-stats'),
    navClear: document.getElementById('nav-clear'),

    // Connection Status
    statusDot: document.getElementById('status-dot'),
    statusText: document.getElementById('status-text'),

    // Header Actions
    btnHeaderModel: document.getElementById('btn-header-model'),
    btnHeaderStats: document.getElementById('btn-header-stats'),
    btnHeaderClear: document.getElementById('btn-header-clear'),
    btnHeaderNew: document.getElementById('btn-header-new'),
    chatTitle: document.getElementById('chat-title'),
    chatSubtitle: document.getElementById('chat-subtitle'),

    // Messages
    chatMessages: document.getElementById('chat-messages'),
    messagesContainer: document.getElementById('messages-container'),
    welcomeScreen: document.getElementById('welcome-screen'),

    // Input Box
    chatInput: document.getElementById('chat-input'),
    sendBtn: document.getElementById('send-btn'),

    // Modals
    modelModal: document.getElementById('model-modal'),
    closeModelModal: document.getElementById('close-model-modal'),
    modelSearchInput: document.getElementById('model-search-input'),
    modalModelList: document.getElementById('modal-model-list'),

    statsModal: document.getElementById('stats-modal'),
    closeStatsModal: document.getElementById('close-stats-modal'),
    modalStatsContent: document.getElementById('modal-stats-content'),

    // Toast
    toast: document.getElementById('toast'),
    toastMessage: document.getElementById('toast-message'),
  };

  // ─── Initialize ────────────────────────────────────────────────────
  async function init() {
    setupMarkdown();
    bindEvents();

    await loadModels();
    await loadChatHistory();

    if (state.sessionId) {
      const exists = state.chatHistory.some((s) => s.session_id === state.sessionId);
      if (exists) {
        await loadSession(state.sessionId, false);
      } else if (state.chatHistory.length > 0) {
        await loadSession(state.chatHistory[0].session_id, false);
      } else {
        await handleNewChat(false);
      }
    } else if (state.chatHistory.length > 0) {
      await loadSession(state.chatHistory[0].session_id, false);
    } else {
      await handleNewChat(false);
    }

    connectWebSocket();
    checkTunnelStatus();
  }

  // ─── Cloudflare Tunnel Status ──────────────────────────────────────
  async function checkTunnelStatus() {
    try {
      const res = await fetch('/api/tunnel');
      if (!res.ok) return;
      const data = await res.json();
      const wrap = document.getElementById('tunnel-badge-wrap');
      const link = document.getElementById('tunnel-link');
      const text = document.getElementById('tunnel-text');
      if (data && data.public_url && wrap && link && text) {
        link.href = data.public_url;
        text.textContent = data.public_url.replace('https://', '');
        wrap.style.display = 'block';
        link.title = `Cloudflare Tunnel: ${data.public_url} (Klik untuk salin)`;
        link.onclick = (e) => {
          if (navigator.clipboard) {
            navigator.clipboard.writeText(data.public_url);
            showToast(`URL Cloudflare disalin: ${data.public_url}`, 3000);
          }
        };
      }
    } catch {
      // Tunnel not active or network error
    }
  }

  // ─── Markdown & Code Highlighting Setup ─────────────────────────────
  function setupMarkdown() {
    if (window.marked) {
      window.marked.setOptions({
        gfm: true,
        breaks: true,
        headerIds: false,
        mangle: false,
      });
    }
  }

  function renderMarkdown(text) {
    if (!text) return '';
    if (window.marked) {
      try {
        return window.marked.parse(text);
      } catch (e) {
        console.error('Markdown parse error:', e);
        return escapeHTML(text);
      }
    }
    return escapeHTML(text);
  }

  function enhanceCodeBlocks(container) {
    const codeBlocks = container.querySelectorAll('pre code');
    codeBlocks.forEach((codeEl) => {
      const pre = codeEl.parentElement;
      if (window.hljs && !codeEl.classList.contains('hljs')) {
        window.hljs.highlightElement(codeEl);
      }

      // Add copy button if not present
      if (!pre.querySelector('.copy-btn')) {
        const copyBtn = document.createElement('button');
        copyBtn.className = 'copy-btn';
        copyBtn.type = 'button';
        copyBtn.textContent = 'Copy';
        copyBtn.title = 'Copy code snippet';
        copyBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          const codeText = codeEl.innerText;
          navigator.clipboard.writeText(codeText).then(() => {
            copyBtn.textContent = 'Copied!';
            setTimeout(() => {
              copyBtn.textContent = 'Copy';
            }, 2000);
          });
        });
        pre.appendChild(copyBtn);
      }
    });
  }

  // ─── Session Management ────────────────────────────────────────────
  async function createNewSession() {
    try {
      const res = await fetch('/api/session/new');
      if (res.ok) {
        const data = await res.json();
        setSessionId(data.session_id);
      } else {
        setSessionId(generateLocalId());
      }
    } catch (e) {
      setSessionId(generateLocalId());
    }
  }

  function setSessionId(id) {
    state.sessionId = id;
    localStorage.setItem('agent_router_session_id', id);
    updateSessionDisplay();
  }

  function generateLocalId() {
    return 'ses_' + Math.random().toString(36).substring(2, 10);
  }

  function updateSessionDisplay() {
    if (DOM.statSessionId) {
      const shortId = state.sessionId ? state.sessionId.substring(0, 10) : '—';
      DOM.statSessionId.textContent = shortId;
      DOM.statSessionId.title = `Click to copy full ID (${state.sessionId})`;
    }
  }

  async function fetchSessionStats() {
    if (!state.sessionId) return;
    try {
      const res = await fetch(`/api/session/${state.sessionId}/stats`);
      if (res.ok) {
        const stats = await res.json();
        updateStatsDisplay(stats);
      }
    } catch (e) {
      console.warn('Failed to fetch session stats:', e);
    }
  }

  function updateStatsDisplay(stats) {
    if (!stats) return;
    if (DOM.statTurns) DOM.statTurns.textContent = stats.turn_count ?? '0';
    if (DOM.statTokens) DOM.statTokens.textContent = formatNumber(stats.total_tokens ?? 0);
    if (stats.override_model) {
      state.activeModelId = stats.override_model;
      const modelNameMap = {
        'ag/gemini-3.8-flash-high': 'Gemini 3.8 Flash',
        'gemini/gemini-4-argon': 'Gemini 4 Argon (High)',
        'kg/deepseek-4-1-flash': 'DeepSeek 4.1 Flash',
        'kg/dp4.1ftest': 'DeepSeek 4.1 Flash Test',
        'kg/deepseek-v4-pro': 'DeepSeek V4 Pro',
        'kg/qwen3.8max': 'Qwen 3.8 Max',
        'kg/gemini-3.7-flash': 'Gemini 3.7 Flash',
        'kg/gpt-5-5': 'GPT-5.5',
      };
      state.activeModelName = modelNameMap[stats.override_model] || stats.override_model;
      if (DOM.activeModelName) DOM.activeModelName.textContent = state.activeModelName;
      if (DOM.activeModelBadge) DOM.activeModelBadge.textContent = 'ACTIVE';
    }
  }

  // ─── Chat History List & Persistence ──────────────────────────────
  async function loadChatHistory() {
    try {
      const res = await fetch('/api/sessions');
      if (res.ok) {
        const data = await res.json();
        state.chatHistory = data.sessions || [];
        saveHistoryCache();
      } else {
        const cached = localStorage.getItem('agent_router_history');
        if (cached) {
          try { state.chatHistory = JSON.parse(cached); } catch (_) {}
        }
      }
    } catch (e) {
      console.warn('Failed to fetch chat history:', e);
      const cached = localStorage.getItem('agent_router_history');
      if (cached) {
        try { state.chatHistory = JSON.parse(cached); } catch (_) {}
      }
    }
    renderChatHistory();
  }

  function saveHistoryCache() {
    try {
      localStorage.setItem('agent_router_history', JSON.stringify(state.chatHistory));
    } catch (_) {}
  }

  function formatRelativeTime(timestamp) {
    if (!timestamp) return 'Just now';
    const now = Date.now() / 1000;
    const diff = Math.max(0, now - timestamp);
    if (diff < 60) return 'Just now';
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    if (diff < 172800) return 'Yesterday';
    const d = new Date(timestamp * 1000);
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  }

  function renderChatHistory(filterQuery = '') {
    if (!DOM.historyList) return;
    DOM.historyList.innerHTML = '';

    const query = (filterQuery || state.historySearchQuery || '').toLowerCase().trim();
    const filtered = state.chatHistory.filter((item) => {
      if (!query) return true;
      return (
        (item.title && item.title.toLowerCase().includes(query)) ||
        (item.preview && item.preview.toLowerCase().includes(query)) ||
        (item.session_id && item.session_id.toLowerCase().includes(query))
      );
    });

    if (DOM.historyCount) {
      DOM.historyCount.textContent = state.chatHistory.length;
    }

    if (DOM.historySearchClear) {
      DOM.historySearchClear.classList.toggle('hide', !query);
    }

    if (filtered.length === 0) {
      if (DOM.historyEmpty) {
        DOM.historyEmpty.style.display = 'flex';
        const txtEl = DOM.historyEmpty.querySelector('.empty-text');
        if (txtEl) {
          txtEl.textContent = query ? `No chats matching "${query}"` : 'No previous chats yet';
        }
      }
      return;
    }

    if (DOM.historyEmpty) {
      DOM.historyEmpty.style.display = 'none';
    }

    filtered.forEach((session) => {
      const itemEl = document.createElement('div');
      const isActive = session.session_id === state.sessionId;
      itemEl.className = `history-item ${isActive ? 'active' : ''}`;
      itemEl.dataset.sessionId = session.session_id;

      const timeText = formatRelativeTime(session.updated_at || session.created_at);
      const msgCountText = session.message_count > 0 ? `${session.message_count} msg` : 'Empty';

      itemEl.innerHTML = `
        <div class="history-item-icon">💬</div>
        <div class="history-item-content">
          <div class="history-item-title" title="${escapeHTML(session.title || 'Chat')}">${escapeHTML(session.title || 'Chat')}</div>
          <div class="history-item-meta">
            <span class="history-item-time">${timeText}</span>
            <span class="history-item-dot">•</span>
            <span class="history-item-turns">${msgCountText}</span>
          </div>
        </div>
        <div class="history-item-actions">
          <button class="history-delete-btn" type="button" title="Delete conversation" aria-label="Delete">🗑️</button>
        </div>
      `;

      itemEl.addEventListener('click', (e) => {
        if (e.target.closest('.history-delete-btn')) return;
        loadSession(session.session_id);
      });

      const delBtn = itemEl.querySelector('.history-delete-btn');
      if (delBtn) {
        delBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          deleteSession(session.session_id, session.title);
        });
      }

      DOM.historyList.appendChild(itemEl);
    });
  }

  async function loadSession(sessionId, showFeedback = true) {
    if (state.isGenerating) {
      showToast('Please wait for current response to finish', 2500);
      return;
    }

    state.sessionId = sessionId;
    localStorage.setItem('agent_router_session_id', sessionId);
    updateSessionDisplay();

    // Show loading state
    DOM.messagesContainer.innerHTML = `
      <div style="text-align: center; padding: 40px; color: var(--text-muted); font-size: 0.88rem;">
        <div class="typing-indicator" style="justify-content: center; margin-bottom: 12px;">
          <div class="typing-dot"></div>
          <div class="typing-dot"></div>
          <div class="typing-dot"></div>
        </div>
        Loading chat history...
      </div>
    `;

    try {
      const res = await fetch(`/api/session/${sessionId}/messages`);
      if (res.ok) {
        const data = await res.json();
        const messages = data.messages || [];
        state.messageCount = messages.length;

        if (DOM.chatSubtitle && data.title) {
          DOM.chatSubtitle.textContent = data.title;
        }

        if (data.model_used) {
          state.activeModelId = data.model_used;
          state.activeModelName = data.model_display || data.model_used;
          if (DOM.activeModelName) DOM.activeModelName.textContent = state.activeModelName;
        }

        renderLoadedMessages(messages);
      } else {
        DOM.messagesContainer.innerHTML = '';
        if (DOM.welcomeScreen) {
          DOM.welcomeScreen.style.display = 'flex';
          DOM.messagesContainer.appendChild(DOM.welcomeScreen);
        }
      }
    } catch (e) {
      console.error('Failed to load session messages:', e);
      DOM.messagesContainer.innerHTML = '';
      if (DOM.welcomeScreen) {
        DOM.welcomeScreen.style.display = 'flex';
        DOM.messagesContainer.appendChild(DOM.welcomeScreen);
      }
    }

    await fetchSessionStats();
    renderChatHistory();
    closeMobileSidebar();
    if (showFeedback) {
      showToast('Loaded chat session', 1500);
    }
  }

  function renderLoadedMessages(messages) {
    DOM.messagesContainer.innerHTML = '';

    if (!messages || messages.length === 0) {
      if (DOM.welcomeScreen) {
        DOM.welcomeScreen.style.display = 'flex';
        DOM.messagesContainer.appendChild(DOM.welcomeScreen);
      }
      return;
    }

    if (DOM.welcomeScreen) {
      DOM.welcomeScreen.style.display = 'none';
    }

    messages.forEach((msg) => {
      const timeStr = msg.timestamp
        ? new Date(msg.timestamp * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
        : '';

      if (msg.role === 'user') {
        const messageEl = document.createElement('div');
        messageEl.className = 'message user';
        messageEl.innerHTML = `
          <div class="message-header">
            <div class="message-avatar">👤</div>
            <div class="message-sender">You</div>
            <div class="message-time">${timeStr}</div>
          </div>
          <div class="message-body">
            <p>${escapeHTML(msg.content).replace(/\n/g, '<br>')}</p>
          </div>
        `;
        DOM.messagesContainer.appendChild(messageEl);
      } else if (msg.role === 'assistant') {
        const messageEl = document.createElement('div');
        messageEl.className = 'message assistant';
        const modelDisplay = msg.model_used ? escapeHTML(msg.model_used) : 'Agent Router';

        messageEl.innerHTML = `
          <div class="message-header">
            <div class="message-avatar">⚡</div>
            <div class="message-sender">Agent Router</div>
            <div class="message-time">${timeStr}</div>
          </div>
          <div class="message-body">
            <div class="content-body">${renderMarkdown(msg.content)}</div>
          </div>
          <div class="message-meta">
            ${msg.model_used ? `<div class="meta-chip"><span class="meta-icon">🤖</span> ${modelDisplay}</div>` : ''}
            ${msg.token_count ? `<div class="meta-chip"><span class="meta-icon">📊</span> ${formatNumber(msg.token_count)} tokens</div>` : ''}
          </div>
        `;
        DOM.messagesContainer.appendChild(messageEl);
        enhanceCodeBlocks(messageEl.querySelector('.content-body'));
      }
    });

    scrollToBottom();
  }

  async function deleteSession(sessionId, title = 'this chat') {
    const displayTitle = title ? `"${title}"` : 'this chat';
    if (!confirm(`Are you sure you want to delete ${displayTitle}?`)) {
      return;
    }

    try {
      await fetch(`/api/session/${sessionId}/delete`, { method: 'POST' });
    } catch (e) {
      console.warn('Failed to delete session on backend:', e);
    }

    state.chatHistory = state.chatHistory.filter((s) => s.session_id !== sessionId);
    saveHistoryCache();

    if (sessionId === state.sessionId) {
      if (state.chatHistory.length > 0) {
        await loadSession(state.chatHistory[0].session_id, false);
      } else {
        await handleNewChat(false);
      }
    } else {
      renderChatHistory();
    }

    showToast('Chat deleted', 2000);
  }

  // ─── WebSocket Connection ──────────────────────────────────────────
  function connectWebSocket() {
    if (state.ws && (state.ws.readyState === WebSocket.OPEN || state.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    clearTimeout(state.reconnectTimer);
    updateConnectionStatus('connecting');

    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${location.host}/ws/chat`;

    try {
      state.ws = new WebSocket(wsUrl);

      state.ws.onopen = () => {
        state.wsConnected = true;
        state.reconnectAttempts = 0;
        updateConnectionStatus('connected');
        console.log('[ws] Connected to agent router');
      };

      state.ws.onmessage = (event) => {
        handleWebSocketMessage(event.data);
      };

      state.ws.onclose = () => {
        state.wsConnected = false;
        updateConnectionStatus('disconnected');
        if (state.isGenerating) {
          endGeneration();
        }
        scheduleReconnect();
      };

      state.ws.onerror = (err) => {
        console.error('[ws] WebSocket error:', err);
        state.wsConnected = false;
        updateConnectionStatus('disconnected');
      };
    } catch (err) {
      console.error('[ws] Failed to initialize WebSocket:', err);
      scheduleReconnect();
    }
  }

  function scheduleReconnect() {
    const delay = Math.min(1000 * Math.pow(1.5, state.reconnectAttempts), 10000);
    state.reconnectAttempts++;
    state.reconnectTimer = setTimeout(() => {
      console.log(`[ws] Reconnecting (attempt ${state.reconnectAttempts})...`);
      connectWebSocket();
    }, delay);
  }

  function updateConnectionStatus(status) {
    if (!DOM.statusDot || !DOM.statusText) return;
    if (status === 'connected') {
      DOM.statusDot.className = 'status-dot connected';
      DOM.statusText.textContent = 'Connected';
    } else if (status === 'connecting') {
      DOM.statusDot.className = 'status-dot';
      DOM.statusText.textContent = 'Connecting...';
    } else {
      DOM.statusDot.className = 'status-dot';
      DOM.statusText.textContent = 'Offline';
    }
  }

  // ─── Message Handling ──────────────────────────────────────────────
  function handleWebSocketMessage(rawData) {
    let data;
    try {
      data = JSON.parse(rawData);
    } catch (e) {
      console.error('Failed to parse WebSocket JSON:', e, rawData);
      return;
    }

    switch (data.type) {
      case 'meta':
        handleMetaMessage(data);
        break;
      case 'thinking':
        handleThinkingChunk(data.content);
        break;
      case 'content':
      case 'content_chunk':
        handleContentChunk(data.content);
        break;
      case 'done':
        handleDoneMessage(data);
        break;
      case 'error':
        handleErrorMessage(data.content || 'An unexpected error occurred.');
        break;
      default:
        console.log('[ws] Unknown message type:', data);
    }
  }

  function handleMetaMessage(data) {
    removeTypingIndicator();

    if (!state.currentStreamingMessageEl) {
      createAssistantMessageElement();
    }

    if (data.event === 'classified') {
      const taskChip = document.createElement('div');
      taskChip.className = 'meta-chip';
      taskChip.innerHTML = `<span class="meta-icon">🎯</span> Task: <strong>${escapeHTML(data.task_type || 'general')}</strong> (${escapeHTML(data.difficulty || 'medium')}) • Conf: ${Math.round((data.confidence || 0) * 100)}%`;

      const metaArea = state.currentStreamingMessageEl.querySelector('.message-meta');
      if (metaArea) {
        metaArea.innerHTML = '';
        metaArea.appendChild(taskChip);
      }
    }
  }

  function handleThinkingChunk(chunk) {
    if (!chunk) return;
    removeTypingIndicator();

    if (!state.currentStreamingMessageEl) {
      createAssistantMessageElement();
    }

    state.currentThinkingText += chunk;

    if (!state.currentThinkingEl) {
      const messageBody = state.currentStreamingMessageEl.querySelector('.message-body');
      const thinkingBlock = document.createElement('div');
      thinkingBlock.className = 'thinking-block';

      thinkingBlock.innerHTML = `
        <div class="thinking-header">
          <div class="thinking-label">
            <span>🧠</span> Reasoning Thought Process
          </div>
          <span class="thinking-toggle">▼</span>
        </div>
        <div class="thinking-content"></div>
      `;

      thinkingBlock.querySelector('.thinking-header').addEventListener('click', () => {
        thinkingBlock.classList.toggle('collapsed');
      });

      // Insert before content element
      messageBody.insertBefore(thinkingBlock, state.currentContentEl);
      state.currentThinkingEl = thinkingBlock.querySelector('.thinking-content');
    }

    state.currentThinkingEl.textContent = state.currentThinkingText;
    scrollToBottom();
  }

  function handleContentChunk(chunk) {
    if (!chunk) return;
    removeTypingIndicator();

    if (!state.currentStreamingMessageEl) {
      createAssistantMessageElement();
    }

    state.currentContentText += chunk;

    // Render streaming markdown
    if (state.currentContentEl) {
      state.currentContentEl.innerHTML = renderMarkdown(state.currentContentText);
      enhanceCodeBlocks(state.currentContentEl);
    }

    scrollToBottom();
  }

  function handleDoneMessage(data) {
    removeTypingIndicator();

    if (state.currentContentEl && state.currentContentText) {
      state.currentContentEl.innerHTML = renderMarkdown(state.currentContentText);
      enhanceCodeBlocks(state.currentContentEl);
    }

    // Render metadata tags
    if (state.currentStreamingMessageEl) {
      const metaContainer = state.currentStreamingMessageEl.querySelector('.message-meta');
      if (metaContainer) {
        metaContainer.innerHTML = '';

        if (data.model_used) {
          const modelChip = document.createElement('div');
          modelChip.className = 'meta-chip';
          modelChip.innerHTML = `<span class="meta-icon">🤖</span> ${escapeHTML(data.model_used)}`;
          metaContainer.appendChild(modelChip);
        }

        if (data.latency_ms !== undefined) {
          const latencyChip = document.createElement('div');
          latencyChip.className = 'meta-chip';
          latencyChip.innerHTML = `<span class="meta-icon">⏱️</span> ${formatNumber(Math.round(data.latency_ms))} ms`;
          metaContainer.appendChild(latencyChip);
        }

        if (data.total_tokens) {
          const tokenChip = document.createElement('div');
          tokenChip.className = 'meta-chip';
          tokenChip.title = `Prompt: ${data.prompt_tokens || 0} | Completion: ${data.completion_tokens || 0}`;
          tokenChip.innerHTML = `<span class="meta-icon">📊</span> ${formatNumber(data.total_tokens)} tokens`;
          metaContainer.appendChild(tokenChip);
        }

        if (data.fallback_used) {
          const fallbackChip = document.createElement('div');
          fallbackChip.className = 'meta-chip';
          fallbackChip.style.borderColor = 'var(--warning)';
          fallbackChip.style.color = 'var(--warning)';
          fallbackChip.innerHTML = `<span class="meta-icon">⚠️</span> Fallback Triggered`;
          metaContainer.appendChild(fallbackChip);
        }

        if (data.task_type) {
          const taskChip = document.createElement('div');
          taskChip.className = 'meta-chip';
          taskChip.innerHTML = `<span class="meta-icon">🎯</span> ${escapeHTML(data.task_type)}`;
          metaContainer.appendChild(taskChip);
        }
      }
    }

    // Update sidebar latency & tokens
    if (DOM.statLatency && data.latency_ms !== undefined) {
      DOM.statLatency.textContent = `${formatNumber(Math.round(data.latency_ms))} ms`;
    }

    endGeneration();
    fetchSessionStats();
    loadChatHistory();
    scrollToBottom();
  }

  function handleErrorMessage(errorMessage) {
    removeTypingIndicator();

    if (!state.currentStreamingMessageEl) {
      createAssistantMessageElement();
    }

    const errorDiv = document.createElement('div');
    errorDiv.style.color = 'var(--error)';
    errorDiv.style.background = 'rgba(225, 112, 85, 0.1)';
    errorDiv.style.border = '1px solid var(--error)';
    errorDiv.style.borderRadius = 'var(--radius-md)';
    errorDiv.style.padding = '12px 16px';
    errorDiv.style.margin = '8px 0';
    errorDiv.style.fontSize = '0.85rem';
    errorDiv.innerHTML = `<strong>Error:</strong> ${escapeHTML(errorMessage)}`;

    const body = state.currentStreamingMessageEl.querySelector('.message-body');
    if (body) {
      body.appendChild(errorDiv);
    }

    showToast(`Error: ${errorMessage}`, 4000);
    endGeneration();
  }

  // ─── Sending User Input ────────────────────────────────────────────
  function sendMessage() {
    const text = DOM.chatInput.value.trim();
    if (!text || state.isGenerating) return;

    if (!state.wsConnected) {
      showToast('WebSocket is not connected. Reconnecting...', 3000);
      connectWebSocket();
      return;
    }

    // Hide welcome screen
    if (DOM.welcomeScreen) {
      DOM.welcomeScreen.style.display = 'none';
    }

    // Append User Message to UI
    appendUserMessage(text);
    DOM.chatInput.value = '';
    autoResizeTextarea();

    // Update Chat History immediately
    let existingSession = state.chatHistory.find((s) => s.session_id === state.sessionId);
    if (!existingSession) {
      let cleanTitle = text.replace(/\s+/g, ' ').trim();
      if (cleanTitle.length > 40) cleanTitle = cleanTitle.substring(0, 40) + '...';
      existingSession = {
        session_id: state.sessionId,
        title: cleanTitle,
        created_at: Date.now() / 1000,
        updated_at: Date.now() / 1000,
        message_count: 1,
        model: state.activeModelId,
        preview: text.substring(0, 80),
      };
      state.chatHistory.unshift(existingSession);
      saveHistoryCache();
      renderChatHistory();
      if (DOM.chatSubtitle) {
        DOM.chatSubtitle.textContent = cleanTitle;
      }
    } else {
      existingSession.updated_at = Date.now() / 1000;
      existingSession.message_count = (existingSession.message_count || 0) + 1;
      state.chatHistory = [
        existingSession,
        ...state.chatHistory.filter((s) => s.session_id !== state.sessionId),
      ];
      saveHistoryCache();
      renderChatHistory();
    }

    // Start generation state
    state.isGenerating = true;
    DOM.sendBtn.disabled = true;
    state.currentStreamingMessageEl = null;
    state.currentThinkingEl = null;
    state.currentThinkingText = '';
    state.currentContentEl = null;
    state.currentContentText = '';

    // Add typing indicator
    showTypingIndicator();

    // Send payload over WebSocket
    const payload = {
      message: text,
      session_id: state.sessionId,
    };

    try {
      state.ws.send(JSON.stringify(payload));
    } catch (e) {
      console.error('Failed to send WebSocket message:', e);
      handleErrorMessage('Failed to send message: ' + e.message);
    }

    scrollToBottom();
  }

  function appendUserMessage(text) {
    state.messageCount++;
    const messageEl = document.createElement('div');
    messageEl.className = 'message user';

    const timeStr = getCurrentTime();

    messageEl.innerHTML = `
      <div class="message-header">
        <div class="message-avatar">👤</div>
        <div class="message-sender">You</div>
        <div class="message-time">${timeStr}</div>
      </div>
      <div class="message-body">
        <p>${escapeHTML(text).replace(/\n/g, '<br>')}</p>
      </div>
    `;

    DOM.messagesContainer.appendChild(messageEl);
  }

  function createAssistantMessageElement() {
    state.messageCount++;
    const messageEl = document.createElement('div');
    messageEl.className = 'message assistant';

    const timeStr = getCurrentTime();

    messageEl.innerHTML = `
      <div class="message-header">
        <div class="message-avatar">⚡</div>
        <div class="message-sender">Agent Router</div>
        <div class="message-time">${timeStr}</div>
      </div>
      <div class="message-body">
        <div class="content-body"></div>
      </div>
      <div class="message-meta"></div>
    `;

    DOM.messagesContainer.appendChild(messageEl);
    state.currentStreamingMessageEl = messageEl;
    state.currentContentEl = messageEl.querySelector('.content-body');
    return messageEl;
  }

  function showTypingIndicator() {
    removeTypingIndicator();
    const ind = document.createElement('div');
    ind.className = 'typing-indicator';
    ind.id = 'active-typing-indicator';
    ind.innerHTML = `
      <div class="typing-dot"></div>
      <div class="typing-dot"></div>
      <div class="typing-dot"></div>
    `;
    DOM.messagesContainer.appendChild(ind);
    state.typingIndicatorEl = ind;
    scrollToBottom();
  }

  function removeTypingIndicator() {
    if (state.typingIndicatorEl && state.typingIndicatorEl.parentNode) {
      state.typingIndicatorEl.parentNode.removeChild(state.typingIndicatorEl);
      state.typingIndicatorEl = null;
    }
  }

  function endGeneration() {
    state.isGenerating = false;
    DOM.sendBtn.disabled = false;
    removeTypingIndicator();
    DOM.chatInput.focus();
  }

  function scrollToBottom() {
    DOM.chatMessages.scrollTop = DOM.chatMessages.scrollHeight;
  }

  // ─── Models Loading & Selection Modal ──────────────────────────────
  async function loadModels() {
    try {
      const res = await fetch('/api/models');
      if (res.ok) {
        const data = await res.json();
        state.models = data.models || {};
        state.providers = data.providers || [];
        state.totalModels = data.total || 0;

        // Flatten raw models list for quick search
        state.rawModelsList = [];
        Object.keys(state.models).forEach((p) => {
          state.rawModelsList.push(...state.models[p]);
        });

        renderModelModal();
      }
    } catch (e) {
      console.error('Failed to load models:', e);
    }
  }

  function renderModelModal(query = '') {
    if (!DOM.modalModelList) return;
    DOM.modalModelList.innerHTML = '';

    const lowerQuery = query.toLowerCase().trim();

    // Render allowed models grouped by provider

    // 2. Providers Groups
    state.providers.forEach((provider) => {
      const modelsInProvider = (state.models[provider] || []).filter((m) => {
        if (!lowerQuery) return true;
        return (
          m.id.toLowerCase().includes(lowerQuery) ||
          (m.display_name && m.display_name.toLowerCase().includes(lowerQuery)) ||
          provider.toLowerCase().includes(lowerQuery)
        );
      });

      if (modelsInProvider.length === 0) return;

      const groupEl = document.createElement('div');
      groupEl.className = 'provider-group';

      const providerClass = provider.toLowerCase();
      groupEl.innerHTML = `
        <div class="provider-label ${providerClass}">
          <span>✦</span> ${escapeHTML(provider.toUpperCase())} (${modelsInProvider.length})
        </div>
      `;

      modelsInProvider.forEach((model) => {
        const isSelected = state.activeModelId === model.id;
        const optEl = document.createElement('div');
        optEl.className = `model-option ${isSelected ? 'selected' : ''}`;

        const caps = [];
        if (model.supports_reasoning) caps.push('<span class="cap-icon active" title="Deep Reasoning">🧠</span>');
        if (model.supports_vision) caps.push('<span class="cap-icon active" title="Vision Multimodal">👁️</span>');
        if (model.supports_tools) caps.push('<span class="cap-icon active" title="Tool Calling">🛠️</span>');
        if (model.supports_search) caps.push('<span class="cap-icon active" title="Web Search">🔍</span>');

        optEl.innerHTML = `
          <div class="model-option-dot" style="background: ${getProviderColor(provider)}"></div>
          <div class="model-option-info">
            <div class="model-option-id">${escapeHTML(model.id)}</div>
            <div class="model-option-name">${escapeHTML(model.display_name || model.id)}</div>
          </div>
          <div class="model-option-caps">${caps.join('')}</div>
          <div class="model-option-context">${escapeHTML(model.context_window_display || '')}</div>
        `;

        optEl.addEventListener('click', () => {
          selectModel(model.id, model.display_name || model.id);
          closeModal(DOM.modelModal);
        });

        groupEl.appendChild(optEl);
      });

      DOM.modalModelList.appendChild(groupEl);
    });

    if (DOM.modalModelList.children.length === 0) {
      DOM.modalModelList.innerHTML = `
        <div style="text-align: center; padding: 30px; color: var(--text-muted); font-size: 0.85rem;">
          No models matching "<strong>${escapeHTML(query)}</strong>"
        </div>
      `;
    }
  }

  function getProviderColor(provider) {
    const p = provider.toLowerCase();
    if (p.includes('ag') || p.includes('deepseek')) return 'var(--provider-ag)';
    if (p.includes('kg') || p.includes('google')) return 'var(--provider-kg)';
    if (p.includes('th') || p.includes('openai')) return 'var(--provider-th)';
    return 'var(--accent-primary)';
  }

  async function selectModel(modelId, displayName) {
    try {
      const res = await fetch('/api/model/select', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: state.sessionId,
          model_id: modelId,
        }),
      });

      if (res.ok) {
        state.activeModelId = modelId;
        state.activeModelName = displayName;

        if (DOM.activeModelName) DOM.activeModelName.textContent = displayName;
        if (DOM.activeModelBadge) {
          DOM.activeModelBadge.textContent = 'ACTIVE';
          DOM.activeModelBadge.style.background = 'rgba(0, 206, 201, 0.15)';
          DOM.activeModelBadge.style.color = 'var(--accent-secondary)';
        }

        renderModelModal(DOM.modelSearchInput ? DOM.modelSearchInput.value : '');
        showToast(`Model switched to: ${displayName}`, 2500);
      }
    } catch (e) {
      console.error('Failed to set model override:', e);
      showToast('Error switching model', 3000);
    }
  }

  // ─── Analytics Modal ───────────────────────────────────────────────
  async function openStatsModal() {
    openModal(DOM.statsModal);
    if (!DOM.modalStatsContent) return;

    DOM.modalStatsContent.innerHTML = `
      <div style="text-align: center; padding: 20px; color: var(--text-muted);">
        Loading session metrics...
      </div>
    `;

    try {
      const res = await fetch(`/api/session/${state.sessionId}/stats`);
      if (res.ok) {
        const stats = await res.json();
        renderStatsModal(stats);
      } else {
        DOM.modalStatsContent.innerHTML = `<div style="padding: 20px; color: var(--error);">Failed to load stats.</div>`;
      }
    } catch (e) {
      DOM.modalStatsContent.innerHTML = `<div style="padding: 20px; color: var(--error);">${escapeHTML(e.message)}</div>`;
    }
  }

  function renderStatsModal(stats) {
    if (!DOM.modalStatsContent) return;

    const breakdown = stats.model_breakdown || {};
    const modelKeys = Object.keys(breakdown);

    let rowsHTML = '';
    modelKeys.forEach((mId) => {
      const m = breakdown[mId];
      rowsHTML += `
        <tr>
          <td><strong>${escapeHTML(mId)}</strong></td>
          <td style="text-align: center;">${m.calls}</td>
          <td style="text-align: right; font-family: var(--font-mono);">${formatNumber(m.total_tokens)}</td>
          <td style="text-align: right; font-family: var(--font-mono);">${formatNumber(Math.round(m.avg_latency_ms))} ms</td>
          <td style="text-align: center; color: var(--success);">${m.successes} / ${m.calls}</td>
        </tr>
      `;
    });

    if (modelKeys.length === 0) {
      rowsHTML = `<tr><td colspan="5" style="text-align: center; color: var(--text-muted);">No model calls recorded yet in this session.</td></tr>`;
    }

    DOM.modalStatsContent.innerHTML = `
      <div class="stats-grid">
        <div class="stat-box">
          <div class="stat-box-val">${stats.turn_count || 0}</div>
          <div class="stat-box-lbl">Turns</div>
        </div>
        <div class="stat-box">
          <div class="stat-box-val">${formatNumber(stats.total_tokens || 0)}</div>
          <div class="stat-box-lbl">Total Tokens</div>
        </div>
        <div class="stat-box">
          <div class="stat-box-val">${formatNumber(stats.total_prompt_tokens || 0)}</div>
          <div class="stat-box-lbl">Prompt In</div>
        </div>
        <div class="stat-box">
          <div class="stat-box-val">${formatNumber(stats.total_completion_tokens || 0)}</div>
          <div class="stat-box-lbl">Output Out</div>
        </div>
        <div class="stat-box">
          <div class="stat-box-val">${stats.total_api_calls || 0}</div>
          <div class="stat-box-lbl">API Calls</div>
        </div>
        <div class="stat-box">
          <div class="stat-box-val" style="color: var(--success);">${Math.round((stats.success_rate || 1.0) * 100)}%</div>
          <div class="stat-box-lbl">Success Rate</div>
        </div>
      </div>

      <div style="margin-top: 18px;">
        <h4 style="font-size: 0.85rem; font-weight: 600; margin-bottom: 10px; color: var(--text-bright);">
          Model Breakdown
        </h4>
        <div style="overflow-x: auto;">
          <table style="width: 100%; border-collapse: collapse; font-size: 0.78rem;">
            <thead>
              <tr style="background: var(--bg-tertiary);">
                <th style="padding: 8px 10px; text-align: left;">Model</th>
                <th style="padding: 8px 10px; text-align: center;">Calls</th>
                <th style="padding: 8px 10px; text-align: right;">Tokens</th>
                <th style="padding: 8px 10px; text-align: right;">Avg Latency</th>
                <th style="padding: 8px 10px; text-align: center;">Success</th>
              </tr>
            </thead>
            <tbody>
              ${rowsHTML}
            </tbody>
          </table>
        </div>
      </div>
    `;
  }

  // ─── Reset / Clear Chat ────────────────────────────────────────────
  async function clearChat() {
    if (confirm('Clear messages in the current chat?')) {
      try {
        if (state.sessionId) {
          await fetch(`/api/session/${state.sessionId}/clear`, { method: 'POST' });
        }
      } catch (e) {
        console.warn('Failed to clear session on backend:', e);
      }

      DOM.messagesContainer.innerHTML = '';
      if (DOM.welcomeScreen) {
        DOM.welcomeScreen.style.display = 'flex';
        DOM.messagesContainer.appendChild(DOM.welcomeScreen);
      }
      state.messageCount = 0;
      const cur = state.chatHistory.find((s) => s.session_id === state.sessionId);
      if (cur) {
        cur.message_count = 0;
        saveHistoryCache();
        renderChatHistory();
      }
      showToast('Current chat messages cleared', 2000);
      fetchSessionStats();
    }
  }

  async function handleNewChat(showToastMsg = true) {
    if (state.isGenerating) {
      showToast('Please wait for current response to finish', 2500);
      return;
    }

    await createNewSession();
    DOM.messagesContainer.innerHTML = '';
    if (DOM.welcomeScreen) {
      DOM.welcomeScreen.style.display = 'flex';
      DOM.messagesContainer.appendChild(DOM.welcomeScreen);
    }
    state.messageCount = 0;
    if (DOM.chatSubtitle) {
      DOM.chatSubtitle.textContent = 'Multi-Model Intelligent Routing & Reasoning Engine';
    }
    if (DOM.statTurns) DOM.statTurns.textContent = '0';
    if (DOM.statTokens) DOM.statTokens.textContent = '0';
    if (DOM.statLatency) DOM.statLatency.textContent = '—';

    renderChatHistory();
    if (showToastMsg) {
      showToast('New chat started. Previous chats are saved in history.', 2500);
    }
    closeMobileSidebar();
    DOM.chatInput.focus();
  }

  // ─── Modal Helpers ─────────────────────────────────────────────────
  function openModal(modal) {
    if (modal) {
      modal.classList.add('active');
    }
  }

  function closeModal(modal) {
    if (modal) {
      modal.classList.remove('active');
    }
  }

  // ─── Toast Notifications ───────────────────────────────────────────
  let toastTimeout = null;
  function showToast(message, duration = 2500) {
    if (!DOM.toast || !DOM.toastMessage) return;
    clearTimeout(toastTimeout);
    DOM.toastMessage.textContent = message;
    DOM.toast.classList.remove('hide');

    toastTimeout = setTimeout(() => {
      DOM.toast.classList.add('hide');
    }, duration);
  }

  // ─── UI Utilities ──────────────────────────────────────────────────
  function autoResizeTextarea() {
    const el = DOM.chatInput;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 160) + 'px';
  }

  function escapeHTML(str) {
    if (!str) return '';
    return str
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function formatNumber(num) {
    return new Intl.NumberFormat('en-US').format(num || 0);
  }

  function getCurrentTime() {
    const d = new Date();
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  function closeMobileSidebar() {
    if (DOM.sidebar) DOM.sidebar.classList.remove('open');
    if (DOM.sidebarBackdrop) DOM.sidebarBackdrop.classList.remove('active');
  }

  // ─── Event Listeners ───────────────────────────────────────────────
  function bindEvents() {
    // Input & Send
    DOM.chatInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });

    DOM.chatInput.addEventListener('input', autoResizeTextarea);
    DOM.sendBtn.addEventListener('click', sendMessage);

    // Quick Prompts on Welcome Screen
    DOM.messagesContainer.addEventListener('click', (e) => {
      const quickBtn = e.target.closest('.quick-action');
      if (quickBtn && quickBtn.dataset.prompt) {
        DOM.chatInput.value = quickBtn.dataset.prompt;
        autoResizeTextarea();
        sendMessage();
      }
    });

    // Mobile Sidebar Toggle
    if (DOM.btnMobileMenu) {
      DOM.btnMobileMenu.addEventListener('click', () => {
        DOM.sidebar.classList.toggle('open');
        DOM.sidebarBackdrop.classList.toggle('active');
      });
    }

    if (DOM.sidebarBackdrop) {
      DOM.sidebarBackdrop.addEventListener('click', closeMobileSidebar);
    }

    // Chat History Search
    if (DOM.historySearchInput) {
      DOM.historySearchInput.addEventListener('input', (e) => {
        state.historySearchQuery = e.target.value;
        renderChatHistory();
      });
    }

    if (DOM.historySearchClear) {
      DOM.historySearchClear.addEventListener('click', () => {
        DOM.historySearchInput.value = '';
        state.historySearchQuery = '';
        renderChatHistory();
        DOM.historySearchInput.focus();
      });
    }

    // Model Selector Buttons
    const openModelTriggers = [DOM.btnSelectModel, DOM.btnHeaderModel, DOM.navSelectModel];
    openModelTriggers.forEach((btn) => {
      if (btn) {
        btn.addEventListener('click', () => {
          openModal(DOM.modelModal);
          if (DOM.modelSearchInput) {
            DOM.modelSearchInput.value = '';
            DOM.modelSearchInput.focus();
            renderModelModal('');
          }
          closeMobileSidebar();
        });
      }
    });

    if (DOM.closeModelModal) {
      DOM.closeModelModal.addEventListener('click', () => closeModal(DOM.modelModal));
    }

    if (DOM.modelSearchInput) {
      DOM.modelSearchInput.addEventListener('input', (e) => {
        renderModelModal(e.target.value);
      });
    }

    // Stats Buttons
    const openStatsTriggers = [DOM.btnHeaderStats, DOM.navStats];
    openStatsTriggers.forEach((btn) => {
      if (btn) {
        btn.addEventListener('click', () => {
          openStatsModal();
          closeMobileSidebar();
        });
      }
    });

    if (DOM.closeStatsModal) {
      DOM.closeStatsModal.addEventListener('click', () => closeModal(DOM.statsModal));
    }

    // New Chat buttons
    if (DOM.btnSidebarNew) DOM.btnSidebarNew.addEventListener('click', () => handleNewChat());
    if (DOM.navNewChat) DOM.navNewChat.addEventListener('click', () => handleNewChat());
    if (DOM.btnHeaderNew) DOM.btnHeaderNew.addEventListener('click', () => handleNewChat());

    // Clear Chat buttons
    if (DOM.navClear) DOM.navClear.addEventListener('click', clearChat);
    if (DOM.btnHeaderClear) DOM.btnHeaderClear.addEventListener('click', clearChat);

    // Copy Session ID on Click
    if (DOM.statSessionId) {
      DOM.statSessionId.style.cursor = 'pointer';
      DOM.statSessionId.addEventListener('click', () => {
        navigator.clipboard.writeText(state.sessionId).then(() => {
          showToast(`Session ID copied: ${state.sessionId}`, 2000);
        });
      });
    }

    // Close Modals on Backdrop Click or Escape
    [DOM.modelModal, DOM.statsModal].forEach((modal) => {
      if (modal) {
        modal.addEventListener('click', (e) => {
          if (e.target === modal) {
            closeModal(modal);
          }
        });
      }
    });

    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        closeModal(DOM.modelModal);
        closeModal(DOM.statsModal);
        closeMobileSidebar();
      }
      // Ctrl+Shift+O for New Chat
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === 'O' || e.key === 'o')) {
        e.preventDefault();
        handleNewChat();
      }
    });
  }

  // ─── Start Application ─────────────────────────────────────────────
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
