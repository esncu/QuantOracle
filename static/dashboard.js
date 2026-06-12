const TIMEFRAMES         = ['5M', '1H', '1D', '1W'];
const PREMIUM_TIMEFRAMES = ['5M', '1H'];   // require AlphaVantage premium
const pendingPolls = {};  // tracks in-progress training boxes

const params    = new URLSearchParams(window.location.search);
const session   = params.get('session');
const username  = localStorage.getItem('qo_username') || 'user';
const LS_KEY    = `qo_av_key_${username}`;

let allDashboards = [];
let activeDashId  = null;

// ---------------------------------------------------------------------------
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
        if (v) {
            localStorage.setItem(LS_KEY, v);
            document.getElementById('keyStatus').textContent = '✓ saved';
        }
    });
    loadAndRender();
});

// ---------------------------------------------------------------------------
// API helper
// ---------------------------------------------------------------------------
async function api(action, extra = {}) {
    const qp = new URLSearchParams({ session, action, ...extra });
    const res = await fetch(`/api/dashboard/?${qp}`, { method: 'POST' });
    if (res.status === 401) { window.location.href = 'login.html'; throw new Error('session expired'); }
    const data = await res.json();
    if (!res.ok) {
        console.error(`API ${action} failed [${res.status}]:`, data.detail || data);
        throw new Error(data.detail || `API error ${res.status}`);
    }
    return data;
}

// ---------------------------------------------------------------------------
// Load & render
// ---------------------------------------------------------------------------
async function loadAndRender() {
    const data = await api('GET');
    if (!data.data) return;
    allDashboards = data.data;
    if (!activeDashId || !allDashboards.find(d => d.id === activeDashId)) {
        activeDashId = allDashboards[0]?.id ?? null;
    }
    renderTabs();
    renderPanel();
}

function renderTabs() {
    const tabBar = document.getElementById('dashTabs');
    tabBar.innerHTML = '';

    allDashboards.forEach(dash => {
        const tab = document.createElement('div');
        tab.className = `dash-tab px-3 py-2 ${dash.id === activeDashId ? 'is-active' : ''}`;
        tab.textContent = dash.name + (dash.deleted ? ' 🗑' : '');
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
                if (newName && newName !== dash.name) {
                    await api('RENAME', { dash_id: dash.id, name: newName });
                    await loadAndRender();
                } else {
                    renderTabs();
                }
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
        container.innerHTML = '<p class="has-text-grey">No dashboards yet. Click "+ Dashboard" to create one.</p>';
        return;
    }

    const symbols  = dash.symbols || {};
    const symNames = Object.keys(symbols).sort();

    let html = '';

    // Header row
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
                        if (pendingPolls[pollKey]) {
                            clearInterval(pendingPolls[pollKey].intervalId);
                            delete pendingPolls[pollKey];
                        }
                        return `<div class="col-tf"><a class="status-box is-ready"
                            href="chart.html?session=${session}&index=${encodeURIComponent(sym)}&time=${encodeURIComponent(tf)}&dash_id=${dash.id}"
                            title="View chart"></a></div>`;
                    }
                    if (state === 'pending') {
                        if (!pendingPolls[pollKey]) startPolling(dash.id, sym, tf);
                        const cd = pendingPolls[pollKey]?.countdown ?? 10;
                        return `<div class="col-tf"><div class="status-box is-loading-box"
                            id="${boxId}" title="Training… ${cd}s">⟳</div></div>`;
                    }
                    // empty
                    const premiumTf = PREMIUM_TIMEFRAMES.includes(tf);
                    return `<div class="col-tf"><div class="status-box ${premiumTf ? 'is-premium-empty' : 'is-pending'}"
                        onclick="generateEntry(${dash.id},'${sym}','${tf}')"
                        title="${premiumTf ? 'Requires premium AlphaVantage' : 'Click to generate'}"></div></div>`;
                }).join('')}
                <div class="col-action">
                    <button class="button is-white is-small" title="Remove"
                        onclick="tmpDeleteSymbol(${dash.id},'${sym}')">🗑️</button>
                </div>
            </div>`;
        });
    }

    // Symbol input — use a stable id based on active dash
    html += `<div class="mt-4">
        <input class="input" type="text" id="symbolInput"
            placeholder="Type symbol + Enter to track (e.g. TSLA)">
    </div>`;

    html += `<div class="mt-3 has-text-right">
        <button class="button is-danger is-small is-outlined"
            onclick="tmpDeleteDash(${dash.id})">Remove dashboard</button>
    </div>`;

    container.innerHTML = html;

    // Wire input — capture dashId in closure at render time
    const capturedDashId = dash.id;
    document.getElementById('symbolInput').addEventListener('keydown', async (e) => {
        if (e.key !== 'Enter') return;
        const sym = e.target.value.trim().toUpperCase();
        if (!sym) return;
        e.target.value = '';
        e.target.disabled = true;
        await addSymbol(capturedDashId, sym);
        // Re-focus the newly rendered input (loadAndRender replaces the DOM)
        const newInp = document.getElementById('symbolInput');
        if (newInp) { newInp.disabled = false; newInp.focus(); }
    });
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------
async function createDashboard() {
    const name = prompt('Dashboard name:');
    if (!name?.trim()) return;
    const res = await api('CREATE', { name: name.trim() });
    if (res.id) activeDashId = res.id;
    await loadAndRender();
}

// addSymbol: only registers DB rows, NO data pull — user clicks red box to generate
async function addSymbol(dashId, sym) {
    // Use a plain fetch directly to avoid the throwing api() wrapper —
    // we want to register all 4 timeframes and handle errors per-tf.
    const results = await Promise.allSettled(TIMEFRAMES.map(async tf => {
        const qp = new URLSearchParams({ session, action: 'GENERATE', dash_id: dashId, index: sym, time: tf });
        const res = await fetch(`/api/dashboard/?${qp}`, { method: 'POST' });
        if (res.status === 401) { window.location.href = 'login.html'; return; }
        if (!res.ok) {
            const body = await res.json().catch(() => ({}));
            console.error(`GENERATE ${sym} ${tf} failed [${res.status}]:`, body.detail || body);
        }
    }));
    const failed = results.filter(r => r.status === 'rejected');
    if (failed.length) console.error('Some timeframes failed to register:', failed);
    await loadAndRender();
}

// generateEntry: called when user clicks a red box — pulls data for that tf
async function generateEntry(dashId, sym, tf) {
    const key = getApiKey();
    if (!key) {
        alert('Enter your AlphaVantage API key in the bar above first.');
        return;
    }
    await api('GENERATE', { dash_id: dashId, index: sym, time: tf, api_key: key });
    await loadAndRender();
    window.location.href = `chart.html?session=${session}&index=${encodeURIComponent(sym)}&time=${encodeURIComponent(tf)}&dash_id=${dashId}`;
}

function startPolling(dashId, sym, tf) {
    const pollKey = `${dashId}|${sym}|${tf}`;
    if (pendingPolls[pollKey]) return;
    let countdown = 10;
    const intervalId = setInterval(async () => {
        countdown--;
        const box = document.getElementById(`box_${dashId}_${sym}_${tf}`);
        if (box) box.title = `Training… ${countdown}s`;
        if (countdown <= 0) {
            countdown = 10;
            try {
                const data = await api('GET');
                if (!data.data) return;
                allDashboards = data.data;
                const dash  = allDashboards.find(d => d.id === dashId);
                const state = dash?.symbols?.[sym]?.[tf]?.state;
                if (state === 'ready') {
                    clearInterval(intervalId);
                    delete pendingPolls[pollKey];
                }
                // Re-render regardless to update box appearance
                if (!activeDashId || !allDashboards.find(d => d.id === activeDashId))
                    activeDashId = allDashboards[0]?.id ?? null;
                renderTabs();
                renderPanel();
            } catch (e) { /* keep polling */ }
        }
    }, 1000);
    pendingPolls[pollKey] = { intervalId, countdown: 10 };
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
    await loadAndRender();
}
