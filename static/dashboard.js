const TIMEFRAMES         = ['5M', '1H', '1D', '1W'];
const PREMIUM_TIMEFRAMES = ['5M', '1H'];
const pendingPolls       = {};   // key=`${dashId}|${sym}|${tf}`

const params   = new URLSearchParams(window.location.search);
const session  = params.get('session');
const username = localStorage.getItem('qo_username') || 'user';
const LS_KEY   = `qo_av_key_${username}`;

let allDashboards = [];
let activeDashId  = null;

// API key
// ---------------------------------------------------------------------------
function getApiKey() {
    return document.getElementById('dashApiKey')?.value.trim() || '';
}

document.addEventListener('DOMContentLoaded', () => {
    const saved = localStorage.getItem(LS_KEY);
    if (saved) {
        document.getElementById('dashApiKey').value = saved;
        document.getElementById('keyStatus').textContent = '✓ key loaded';
    }
    document.getElementById('dashApiKey').addEventListener('change', () => {
        const v = document.getElementById('dashApiKey').value.trim();
        if (v) { localStorage.setItem(LS_KEY, v); document.getElementById('keyStatus').textContent = '✓ saved'; }
    });
    loadAndRender();
    renderTickers();
    renderFeedbackForm();
});

// API helpers
// ---------------------------------------------------------------------------
async function api(action, extra = {}) {
    const qp  = new URLSearchParams({ session, action, ...extra });
    const res = await fetch(`/api/dashboard/?${qp}`, { method: 'POST' });
    if (res.status === 401 || res.status === 403) { window.location.href = 'login.html'; throw new Error('session'); }
    const data = await res.json();
    if (!res.ok) { console.error(`API ${action} [${res.status}]:`, data.detail); throw new Error(data.detail || `HTTP ${res.status}`); }
    return data;
}

// Load & render
// ---------------------------------------------------------------------------
async function loadAndRender() {
    const data = await api('GET');
    if (!data.data) return;
    allDashboards = data.data;
    if (!activeDashId || !allDashboards.find(d => d.id === activeDashId))
        activeDashId = allDashboards[0]?.id ?? null;
    renderTabs();
    renderPanel();
}

function renderTabs() {
    const tabBar = document.getElementById('dashTabs');
    tabBar.innerHTML = '';
    allDashboards.forEach(dash => {
        const tab = document.createElement('div');
        tab.className = `dash-tab px-3 py-2 ${dash.id === activeDashId ? 'is-active' : ''}`;
        tab.textContent = dash.name;
        tab.onclick = () => { activeDashId = dash.id; renderTabs(); renderPanel(); };
        tab.ondblclick = (e) => {
            e.stopPropagation();
            const inp = document.createElement('input');
            inp.className = 'tab-rename-input';
            inp.value = dash.name;
            tab.textContent = '';
            tab.appendChild(inp);
            inp.focus();
            inp.onblur = inp.onkeydown = async (ev) => {
                if (ev.type === 'keydown' && ev.key !== 'Enter') return;
                const newName = inp.value.trim();
                if (newName && newName !== dash.name) { await api('RENAME', { dash_id: dash.id, name: newName }); await loadAndRender(); }
                else renderTabs();
            };
        };
        tabBar.appendChild(tab);
    });
    const addBtn = document.createElement('button');
    addBtn.className = 'button is-small is-light ml-2 mb-1';
    addBtn.textContent = '+ Dashboard';
    addBtn.onclick = createDashboard;
    tabBar.appendChild(addBtn);
}

function renderPanel() {
    const container = document.getElementById('dashPanels');
    const dash = allDashboards.find(d => d.id === activeDashId);
    if (!dash) {
        container.innerHTML = '<p class="has-text-grey p-4">No dashboards yet. Click "+ Dashboard" to create one.</p>';
        return;
    }
    const symbols  = dash.symbols || {};
    const symNames = Object.keys(symbols).sort();
    let html = '';

    // Header
    html += `<div class="index-row has-text-weight-bold has-text-grey mb-1">
        <div class="col-name">Index</div>
        ${TIMEFRAMES.map(tf => `<div class="col-tf" style="${PREMIUM_TIMEFRAMES.includes(tf) ? 'color:#b7950b;font-weight:700;' : ''}">${tf}</div>`).join('')}
        <div class="col-action"></div>
    </div>`;

    if (!symNames.length) {
        html += '<p class="has-text-grey my-3">No symbols tracked. Type one below and press Enter.</p>';
    } else {
        symNames.forEach(sym => {
            html += `<div class="index-row">
                <div class="col-name">${sym}</div>
                ${TIMEFRAMES.map(tf => {
                    const entry   = symbols[sym]?.[tf];
                    const state   = entry?.state || 'empty';
                    const pollKey = `${dash.id}|${sym}|${tf}`;
                    const boxId   = `box_${dash.id}_${sym}_${tf}`;
                    if (state === 'ready') {
                        if (pendingPolls[pollKey]) { clearInterval(pendingPolls[pollKey].intervalId); delete pendingPolls[pollKey]; }
                        return `<div class="col-tf"><a class="status-box is-ready"
                            href="chart.html?session=${session}&index=${encodeURIComponent(sym)}&time=${encodeURIComponent(tf)}&dash_id=${dash.id}"
                            title="View chart"></a></div>`;
                    }
                    // Orange if server says pending OR if we optimistically set it locally
                    if (state === 'pending' || pendingPolls[pollKey]) {
                        if (!pendingPolls[pollKey]) startPolling(dash.id, sym, tf);
                        return `<div class="col-tf"><div class="status-box is-loading-box" id="${boxId}" title="Training…"></div></div>`;
                    }
                    const premiumTf = PREMIUM_TIMEFRAMES.includes(tf);
                    return `<div class="col-tf"><div class="status-box ${premiumTf ? 'is-premium-empty' : 'is-pending'}"
                        onclick="generateEntry(${dash.id},'${sym}','${tf}')"
                        title="${premiumTf ? 'Requires premium AlphaVantage' : 'Click to generate'}"></div></div>`;
                }).join('')}
                <div class="col-action">
                    <button class="button is-white is-small" title="Remove" onclick="tmpDeleteSymbol(${dash.id},'${sym}')">🗑️</button>
                </div>
            </div>`;
        });
    }

    html += `<div class="mt-4">
        <input class="input" type="text" id="newSymbolInput_${dash.id}"
            placeholder="Type symbol + Enter to track (e.g. TSLA)">
    </div>
    <div class="mt-3 has-text-right">
        <button class="button is-danger is-small is-outlined" onclick="tmpDeleteDash(${dash.id})">Remove dashboard</button>
    </div>`;

    container.innerHTML = html;

    const inp = document.getElementById(`newSymbolInput_${dash.id}`);
    if (inp) {
        inp.addEventListener('keydown', async (e) => {
            if (e.key !== 'Enter') return;
            const sym = inp.value.trim().toUpperCase();
            if (!sym) return;
            inp.value = '';
            inp.disabled = true;
            await addSymbol(dash.id, sym);
            inp.disabled = false;
            inp.focus();
        });
    }
}

// Polling
// ---------------------------------------------------------------------------
function startPolling(dashId, sym, tf) {
    const pollKey = `${dashId}|${sym}|${tf}`;
    // Only skip if a real interval is already running (intervalId !== null)
    if (pendingPolls[pollKey]?.intervalId !== null && pendingPolls[pollKey]?.intervalId !== undefined) return;

    const intervalId = setInterval(async () => {
        try {
            const data = await api('GET');
            if (!data.data) return;
            allDashboards = data.data;
            const dash  = allDashboards.find(d => d.id === dashId);
            const state = dash?.symbols?.[sym]?.[tf]?.state;
            if (state !== 'pending') {
                clearInterval(intervalId);
                delete pendingPolls[pollKey];
            }
            if (!activeDashId || !allDashboards.find(d => d.id === activeDashId))
                activeDashId = allDashboards[0]?.id ?? null;
            renderTabs();
            renderPanel();
        } catch (e) { /* keep polling */ }
    }, 5000);   // poll every 5 seconds
    pendingPolls[pollKey] = { intervalId, countdown: 5 };
}

// Actions
// ---------------------------------------------------------------------------
async function createDashboard() {
    const name = prompt('Dashboard name:');
    if (!name?.trim()) return;
    const res = await api('CREATE', { name: name.trim() });
    if (res.id) activeDashId = res.id;
    await loadAndRender();
}

async function addSymbol(dashId, sym) {
    // Register all 4 timeframes — no data pull, no training
    await Promise.allSettled(TIMEFRAMES.map(async tf => {
        const qp  = new URLSearchParams({ session, action: 'GENERATE', dash_id: dashId, index: sym, time: tf });
        const res = await fetch(`/api/dashboard/?${qp}`, { method: 'POST' });
        if (res.status === 401) { window.location.href = 'login.html'; return; }
        if (!res.ok) { const b = await res.json().catch(() => ({})); console.error(`GENERATE ${sym} ${tf} [${res.status}]:`, b.detail); }
    }));
    await loadAndRender();
}

async function generateEntry(dashId, sym, tf) {
    const key = getApiKey();
    if (!key) { alert('Enter your AlphaVantage API key in the bar above first.'); return; }

    const pollKey = `${dashId}|${sym}|${tf}`;

    pendingPolls[pollKey] = { intervalId: null, countdown: 10 };
    renderPanel();

    try {
        const qp  = new URLSearchParams({ session, action: 'GENERATE', dash_id: dashId, index: sym, time: tf, api_key: key });
        const res = await fetch(`/api/dashboard/?${qp}`, { method: 'POST' });
        if (res.status === 401) { window.location.href = 'login.html'; return; }
        const data = await res.json();
        if (!res.ok) {
            alert(data.detail || 'Generate failed');
            delete pendingPolls[pollKey];
            renderPanel();
            return;
        }
        startPolling(dashId, sym, tf);
    } catch (e) {
        delete pendingPolls[pollKey];
        renderPanel();
    }
}

async function tmpDeleteSymbol(dashId, sym) {
    if (!confirm(`Flag all timeframes for "${sym}" for deletion?`)) return;
    await Promise.allSettled(TIMEFRAMES.map(tf =>
        api('TMPDELETE', { dash_id: dashId, index: sym, time: tf })
    ));
    await loadAndRender();
}

async function tmpDeleteDash(dashId) {
    if (!confirm('Flag this entire dashboard for deletion? An admin can restore it.')) return;
    await api('TMPDELETE_DASH', { dash_id: dashId });
    allDashboards = allDashboards.filter(d => d.id !== dashId);
    activeDashId  = allDashboards[0]?.id ?? null;
    renderTabs();
    renderPanel();
}

// Ticker examples
// ---------------------------------------------------------------------------
function renderTickers() {
    const el = document.getElementById('tickerExamples');
    if (!el) return;
    const groups = {
        'Tech':     ['AAPL', 'NVDA', 'MSFT', 'GOOGL', 'META'],
        'Metals':   ['GLD', 'SLV', 'COPX', 'PLTM', 'GDX'],
        'Currency': ['UUP', 'FXE', 'FXY', 'FXB', 'FXC'],
        'Banks':    ['JPM', 'BAC', 'WFC', 'GS', 'C'],
    };
    el.innerHTML = Object.entries(groups).map(([cat, tickers]) => `
        <div class="mb-2">
            <span class="has-text-weight-semibold has-text-grey-dark is-size-7">${cat}:</span>
            ${tickers.map(t => `<span class="tag is-light ml-1 is-size-7">${t}</span>`).join('')}
        </div>
    `).join('');
}

// Feedback form
// ---------------------------------------------------------------------------
function renderFeedbackForm() {
    const el = document.getElementById('feedbackSection');
    if (!el) return;
    el.innerHTML = `
        <div class="box mt-5">
            <h2 class="subtitle is-6 mb-3">Send Feedback to Admin</h2>
            <div id="feedbackMsg" class="notification is-hidden mb-3"></div>
            <div class="field">
                <div class="control">
                    <textarea id="feedbackText" class="textarea" maxlength="1000"
                        placeholder="Your message… (max 1000 characters)" rows="4"></textarea>
                </div>
                <p class="help has-text-right"><span id="feedbackCount">0</span>/1000</p>
            </div>
            <div class="field">
                <div class="control">
                    <button class="button is-primary" onclick="submitFeedback()">Send</button>
                </div>
            </div>
        </div>
    `;
    document.getElementById('feedbackText').addEventListener('input', () => {
        document.getElementById('feedbackCount').textContent =
            document.getElementById('feedbackText').value.length;
    });
}

async function submitFeedback() {
    const msg = document.getElementById('feedbackText').value.trim();
    const msgEl = document.getElementById('feedbackMsg');

    function setFbMsg(text, type) {
        msgEl.className = `notification ${type}`;
        msgEl.textContent = text;
        msgEl.classList.remove('is-hidden');
    }

    if (!msg) { setFbMsg('Please enter a message.', 'is-warning'); return; }
    if (msg.length > 1000) { setFbMsg('Message exceeds 1000 characters.', 'is-warning'); return; }

    try {
        const res  = await fetch(
            `/api/send_feedback?session=${session}&message=${encodeURIComponent(msg)}`,
            { method: 'POST' }
        );
        if (res.status === 401) { window.location.href = 'login.html'; return; }
        if (!res.ok) { const d = await res.json(); setFbMsg(d.detail || 'Failed to send.', 'is-danger'); return; }
        setFbMsg('Feedback sent!', 'is-success');
        document.getElementById('feedbackText').value = '';
        document.getElementById('feedbackCount').textContent = '0';
    } catch (e) {
        setFbMsg(`Network error: ${e.message}`, 'is-danger');
    }
}
