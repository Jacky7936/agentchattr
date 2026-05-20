(function () {
    'use strict';

    const SESSION_TOKEN = window.__SESSION_TOKEN__ || '';
    const surface = window.__AGENTCHATTR_SURFACE__ || '';
    const headers = SESSION_TOKEN ? { 'X-Session-Token': SESSION_TOKEN } : {};

    function injectRuntimeStyle() {
        const style = document.createElement('style');
        style.textContent = `
            html, body { max-width: 100%; overflow-x: hidden; }
            button, a, input, select, textarea { touch-action: manipulation; }
            button, a { -webkit-tap-highlight-color: transparent; }
            .ac-route-chip {
                display: inline-flex; align-items: center; justify-content: center;
                min-height: 32px; padding: 5px 10px; border: 1px solid var(--border);
                border-radius: var(--radius-sm, 6px); color: var(--fg-2, currentColor);
                background: var(--surface, transparent); font-size: 12px; font-weight: 600;
                text-decoration: none;
            }
            .ac-route-chip:hover { background: var(--surface-2, transparent); text-decoration: none; }
            .ac-empty {
                padding: var(--space-6, 24px); border: 1px dashed var(--border);
                border-radius: var(--radius-md, 10px); color: var(--muted);
                background: color-mix(in oklab, var(--surface), transparent 35%);
                font-size: 13px; text-align: center;
            }
            @media (max-width: 700px) {
                .hdr, .nav-row { min-height: 56px; gap: 8px; }
                .hdr-actions, .nav-cta { gap: 6px; }
                .btn, .btn-soft, .btn-primary, .filter, .tab, .f-chip, .ac-route-chip {
                    min-height: 40px;
                }
                input, select, textarea { font-size: 16px !important; }
                .wrap, .shell { padding-left: max(12px, env(safe-area-inset-left)); padding-right: max(12px, env(safe-area-inset-right)); }
                .composer, footer { padding-bottom: max(12px, env(safe-area-inset-bottom)); }
            }
        `;
        document.head.appendChild(style);
    }

    async function api(path, fallback) {
        try {
            const response = await fetch(path, { headers });
            if (!response.ok) return fallback;
            return await response.json();
        } catch (error) {
            return fallback;
        }
    }

    function escapeHtml(value) {
        const div = document.createElement('div');
        div.textContent = value == null ? '' : String(value);
        return div.innerHTML;
    }

    function text(value, limit) {
        const s = String(value || '').replace(/\s+/g, ' ').trim();
        if (!limit || s.length <= limit) return s;
        return s.slice(0, Math.max(0, limit - 1)).trimEnd() + '...';
    }

    function timeAgo(ts) {
        if (!ts) return '';
        const diff = Date.now() - Number(ts) * 1000;
        if (diff < 60000) return 'just now';
        if (diff < 3600000) return Math.max(1, Math.round(diff / 60000)) + 'm';
        if (diff < 86400000) return Math.round(diff / 3600000) + 'h';
        return Math.round(diff / 86400000) + 'd';
    }

    function agentClass(sender) {
        const s = String(sender || '').toLowerCase();
        if (s.includes('claude')) return 'claude';
        if (s.includes('codex')) return 'codex';
        if (s.includes('gemini')) return 'gemini';
        if (s.includes('qwen')) return 'qwen';
        if (s.includes('kimi')) return 'kimi';
        if (s.includes('kilo')) return 'kilo';
        if (s.includes('minimax')) return 'minimax';
        return 'you';
    }

    function agentInitial(sender) {
        const cls = agentClass(sender);
        if (cls === 'codex') return 'X';
        if (cls === 'you') return 'JH';
        return (String(sender || '?').trim()[0] || '?').toUpperCase();
    }

    function installNavigation() {
        document.querySelectorAll('a[href="#"]').forEach((link) => {
            const label = (link.textContent || '').toLowerCase();
            if (label.includes('discord')) link.href = 'https://discord.gg/qzfn5YTT9a';
            else if (label.includes('github')) link.href = 'https://github.com/bcurts/agentchattr';
            else if (label.includes('chat') || label.includes('#general') || label.includes('channel')) link.href = '/';
        });
        document.querySelectorAll('.brand, .brand-logo, .hdr-logo').forEach((el) => {
            if (el.tagName === 'A') return;
            el.setAttribute('role', 'link');
            el.setAttribute('tabindex', '0');
            el.addEventListener('click', () => { window.location.href = '/'; });
            el.addEventListener('keydown', (event) => {
                if (event.key === 'Enter') window.location.href = '/';
            });
        });
        document.querySelectorAll('.back, .hdr-back').forEach((btn) => {
            btn.addEventListener('click', () => {
                if (history.length > 1) history.back();
                else window.location.href = '/';
            });
        });
    }

    function messageRow(msg, query) {
        const cls = agentClass(msg.sender);
        let body = escapeHtml(text(msg.text, 240));
        if (query) {
            const safe = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
            body = body.replace(new RegExp(safe, 'ig'), (m) => '<mark>' + escapeHtml(m) + '</mark>');
        }
        return `
            <div class="res">
                <div class="av ${cls}">${agentInitial(msg.sender)}</div>
                <div>
                    <div class="head">
                        <span class="name">${escapeHtml(msg.sender || 'unknown')}</span>
                        <span class="time">${escapeHtml(timeAgo(msg.timestamp) || msg.time || '')}</span>
                        <span class="ch"><a href="/">#${escapeHtml(msg.channel || 'general')}</a></span>
                    </div>
                    <div class="snippet">"${body}"</div>
                    <div class="meta-row">
                        <span class="badge">${escapeHtml(msg.type || 'message')}</span>
                        ${(msg.attachments || []).length ? '<span class="badge">attachment</span>' : ''}
                        ${msg.reply_to != null ? '<span class="badge thread">thread reply</span>' : ''}
                    </div>
                </div>
            </div>
        `;
    }

    async function hydrateSearch() {
        const messages = await api('/api/messages?limit=500', []);
        const jobs = await api('/api/jobs', []);
        const input = document.querySelector('.searchbar input');
        const query = new URLSearchParams(location.search).get('q') || (input && input.value) || '';
        if (input) input.value = query;
        const needle = query.trim().toLowerCase();
        const results = messages.filter((msg) => {
            if (!needle) return true;
            return String(msg.text || '').toLowerCase().includes(needle) ||
                String(msg.sender || '').toLowerCase().includes(needle) ||
                String(msg.channel || '').toLowerCase().includes(needle);
        });
        const attachments = messages.flatMap((m) => m.attachments || []);
        const count = document.querySelector('.results-h .count');
        if (count) count.textContent = `${results.length} messages · ${jobs.length} jobs · ${attachments.length} files`;
        const nums = document.querySelectorAll('.tabs .num');
        if (nums[0]) nums[0].textContent = results.length;
        if (nums[1]) nums[1].textContent = jobs.length;
        if (nums[2]) nums[2].textContent = attachments.length;
        const container = document.querySelector('.tabs')?.nextElementSibling;
        if (container) {
            container.innerHTML = results.length
                ? results.slice(0, 20).map((msg) => messageRow(msg, needle)).join('')
                : '<div class="ac-empty">No matching messages yet.</div>';
        }
        if (input) input.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') location.href = '/search?q=' + encodeURIComponent(input.value.trim());
        });
    }

    function attachmentType(name) {
        const ext = (String(name || '').split('.').pop() || '').toUpperCase().slice(0, 4);
        return ext || 'FILE';
    }

    function attachmentRows(messages) {
        const files = [];
        for (const msg of messages) {
            for (const att of msg.attachments || []) {
                files.push({ ...att, sender: msg.sender, channel: msg.channel || 'general', timestamp: msg.timestamp });
            }
        }
        return files;
    }

    async function hydrateFiles() {
        const messages = await api('/api/messages?limit=500', []);
        const files = attachmentRows(messages);
        const title = document.querySelector('.hdr-title span');
        if (title) title.textContent = `${files.length} files across ${new Set(messages.map((m) => m.channel || 'general')).size || 1} channels`;
        const grid = document.querySelector('.grid');
        if (grid) {
            grid.innerHTML = files.length
                ? files.slice(0, 12).map((file) => `
                    <div class="tile">
                        <div class="tile-preview">
                            <span class="file-type">${escapeHtml(attachmentType(file.name))}</span>
                            <div class="ph-img icon">${escapeHtml(attachmentType(file.name))}</div>
                            <span class="file-size">${escapeHtml(file.size || '')}</span>
                        </div>
                        <div class="tile-meta">
                            <b>${escapeHtml(file.name || 'attachment')}</b>
                            <div class="meta">
                                <div class="av ${agentClass(file.sender)}">${agentInitial(file.sender)}</div>
                                <span>${escapeHtml(file.sender || 'unknown')} · in</span>
                                <a href="/" class="ch">#${escapeHtml(file.channel)}</a>
                                <span style="margin-left: auto;">${escapeHtml(timeAgo(file.timestamp))}</span>
                            </div>
                        </div>
                    </div>
                `).join('')
                : '<div class="ac-empty" style="grid-column: 1 / -1;">No shared files yet. Paste or drop an image in chat to start the media library.</div>';
        }
        const stats = document.querySelectorAll('.stat-card .v');
        if (stats[0]) stats[0].innerHTML = `${files.filter((f) => /^image\//.test(f.type || '')).length} <small>images</small>`;
        if (stats[1]) stats[1].innerHTML = `${files.filter((f) => /\.(js|ts|py|json|html|css)$/i.test(f.name || '')).length} <small>code</small>`;
        if (stats[2]) stats[2].innerHTML = `${files.filter((f) => /\.(md|txt|pdf|docx?)$/i.test(f.name || '')).length} <small>docs</small>`;
        if (stats[3]) stats[3].innerHTML = `${files.length} <small>total</small>`;
    }

    async function hydrateNotifications() {
        const [settings, messages, jobs, rules, status] = await Promise.all([
            api('/api/settings', {}),
            api('/api/messages?limit=200', []),
            api('/api/jobs', []),
            api('/api/rules', []),
            api('/api/status', {}),
        ]);
        const user = String(settings.username || 'user').toLowerCase();
        const items = [];
        for (const msg of messages.slice(-80).reverse()) {
            const lower = String(msg.text || '').toLowerCase();
            if (lower.includes('@' + user) || msg.type === 'job_proposal' || msg.type === 'rule_proposal' || msg.sender === 'system') {
                items.push({ kind: msg.type || 'message', sender: msg.sender, text: msg.text, channel: msg.channel, timestamp: msg.timestamp });
            }
        }
        for (const job of jobs.slice(-5).reverse()) {
            items.push({ kind: 'job', sender: job.assignee || job.created_by || 'system', text: job.title, channel: job.channel, timestamp: job.updated_at || job.created_at });
        }
        const unread = items.filter((i) => String(i.text || '').toLowerCase().includes('@' + user)).length;
        const title = document.querySelector('.hdr-title span');
        if (title) title.textContent = `${unread} unread · ${items.length} recent`;
        const cards = document.querySelectorAll('.summary .s-card .v');
        if (cards[0]) cards[0].textContent = unread;
        if (cards[1]) cards[1].textContent = jobs.length;
        if (cards[2]) cards[2].textContent = rules.filter((r) => ['proposed', 'draft', 'pending'].includes(r.status)).length;
        if (cards[3]) cards[3].textContent = Object.keys(status.agents || status || {}).length || 0;
        const firstGroup = document.querySelector('.group-head');
        let cursor = firstGroup ? firstGroup.nextElementSibling : null;
        while (cursor && !cursor.classList.contains('group-head')) {
            const next = cursor.nextElementSibling;
            cursor.remove();
            cursor = next;
        }
        if (firstGroup) {
            firstGroup.insertAdjacentHTML('afterend', items.length ? items.slice(0, 12).map((item, index) => `
                <article class="notif ${index < unread ? 'unread' : ''}">
                    <div class="dot"></div>
                    <div class="av ${agentClass(item.sender)}">${agentInitial(item.sender)}</div>
                    <div class="body">
                        <p><b>${escapeHtml(item.sender || 'system')}</b> · <span class="type-tag ${item.kind === 'job' ? 'job' : 'mention'}">${escapeHtml(item.kind)}</span></p>
                        <div class="ch">in <a href="/">#${escapeHtml(item.channel || 'general')}</a></div>
                        <div class="quote ${index < unread ? 'mention' : ''}">"${escapeHtml(text(item.text, 180))}"</div>
                    </div>
                    <span class="time">${escapeHtml(timeAgo(item.timestamp))}</span>
                </article>
            `).join('') : '<div class="ac-empty">No notifications yet.</div>');
        }
    }

    async function hydrateThread() {
        const messages = await api('/api/messages?limit=300', []);
        const repliesByParent = new Map();
        for (const msg of messages) {
            if (msg.reply_to == null) continue;
            const list = repliesByParent.get(msg.reply_to) || [];
            list.push(msg);
            repliesByParent.set(msg.reply_to, list);
        }
        const parentId = Number(new URLSearchParams(location.search).get('id')) || Array.from(repliesByParent.keys()).pop();
        const parent = messages.find((m) => m.id === parentId) || messages[messages.length - 1];
        const replies = parent ? (repliesByParent.get(parent.id) || []) : [];
        if (!parent) return;
        const title = document.querySelector('.hdr-title span');
        if (title) title.innerHTML = `in <a href="/">#${escapeHtml(parent.channel || 'general')}</a> · started by <b>${escapeHtml(parent.sender)}</b>`;
        const parentBody = document.querySelector('.parent-body');
        if (parentBody) parentBody.innerHTML = `<p>${escapeHtml(parent.text || '')}</p>`;
        const name = document.querySelector('.parent .name');
        if (name) name.textContent = parent.sender || 'unknown';
        const av = document.querySelector('.parent .av');
        if (av) {
            av.className = 'av ' + agentClass(parent.sender);
            av.textContent = agentInitial(parent.sender);
        }
        const stats = document.querySelector('.thread-stats .num');
        if (stats) stats.textContent = replies.length;
        const container = document.querySelector('.replies');
        if (container) {
            container.innerHTML = replies.length ? replies.map((reply) => `
                <article class="reply ${String(reply.sender || '').toLowerCase() === 'user' ? 'me' : ''}">
                    <div class="av ${agentClass(reply.sender)}">${agentInitial(reply.sender)}</div>
                    <div class="bubble">
                        <div class="head"><b>${escapeHtml(reply.sender || 'unknown')}</b><span class="time">${escapeHtml(reply.time || timeAgo(reply.timestamp))}</span></div>
                        <div class="body"><p>${escapeHtml(reply.text || '')}</p></div>
                    </div>
                </article>
            `).join('') : '<div class="ac-empty">No replies in this thread yet.</div>';
        }
    }

    async function hydrateMember() {
        const [status, roles] = await Promise.all([api('/api/status', {}), api('/api/roles', {})]);
        const agents = status.agents || status || {};
        const names = Object.keys(agents);
        const requested = new URLSearchParams(location.search).get('agent') || names[0] || 'agent';
        document.querySelectorAll('.hdr-crumb b, .hero h1, .profile-head b').forEach((el) => { el.textContent = requested; });
        document.querySelectorAll('.hero .avatar, .profile-head .avatar').forEach((el) => {
            el.className = el.className.replace(/\b(claude|codex|gemini|qwen|you)\b/g, '') + ' ' + agentClass(requested);
            el.textContent = agentInitial(requested);
        });
        const role = roles[requested] || agents[requested]?.role || 'None';
        document.querySelectorAll('.role-picker .pill, .role-pill').forEach((el) => {
            if ((el.textContent || '').toLowerCase().includes('planner')) el.textContent = role || 'None';
        });
    }

    async function hydrateSettingsSurface() {
        const settings = await api('/api/settings', {});
        document.querySelectorAll('.row').forEach((row) => {
            const label = (row.querySelector('b')?.textContent || '').toLowerCase();
            if (label.includes('display name')) row.querySelector('span:last-child') && (row.querySelector('span:last-child').textContent = settings.username || 'user');
            if (label.includes('history')) row.querySelector('span:last-child') && (row.querySelector('span:last-child').textContent = String(settings.history_limit || 'all'));
            if (label.includes('loop')) row.querySelector('span:last-child') && (row.querySelector('span:last-child').textContent = String(settings.max_agent_hops || 4));
        });
    }

    async function hydrateSidebarSurface() {
        const [settings, messages, status] = await Promise.all([
            api('/api/settings', {}),
            api('/api/messages?limit=300', []),
            api('/api/status', {}),
        ]);
        const channels = settings.channels || ['general'];
        const list = document.querySelector('.list');
        if (!list) return;
        const latestByChannel = new Map();
        for (const msg of messages) latestByChannel.set(msg.channel || 'general', msg);
        const agentNames = Object.keys(status.agents || status || {});
        list.innerHTML = channels.map((channel, index) => {
            const msg = latestByChannel.get(channel);
            return `
                <div class="row ${index === 0 ? 'active' : ''}" onclick="location.href='/?channel=${encodeURIComponent(channel)}'">
                    <div class="row-icon channel">#</div>
                    <div class="row-body">
                        <div class="row-head"><div class="row-title">${escapeHtml(channel)}</div><div class="row-time">${escapeHtml(timeAgo(msg?.timestamp))}</div></div>
                        <div class="row-sub">${msg ? `<span class="author">${escapeHtml(msg.sender)}:</span> ${escapeHtml(text(msg.text, 70))}` : 'No messages yet'}</div>
                    </div>
                </div>
            `;
        }).join('') + agentNames.map((name) => `
            <div class="row" onclick="location.href='/member?agent=${encodeURIComponent(name)}'">
                <div class="row-icon-wrap"><div class="row-icon ${agentClass(name)}">${agentInitial(name)}</div></div>
                <div class="row-body">
                    <div class="row-head"><div class="row-title">${escapeHtml(name)}</div><div class="row-time">agent</div></div>
                    <div class="row-sub">Open profile and role controls</div>
                </div>
            </div>
        `).join('');
    }

    async function main() {
        injectRuntimeStyle();
        installNavigation();
        if (surface === 'search') await hydrateSearch();
        else if (surface === 'files') await hydrateFiles();
        else if (surface === 'notifications') await hydrateNotifications();
        else if (surface === 'thread') await hydrateThread();
        else if (surface === 'member') await hydrateMember();
        else if (surface === 'settings') await hydrateSettingsSurface();
        else if (surface === 'sidebar') await hydrateSidebarSurface();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', main);
    } else {
        main();
    }
})();
