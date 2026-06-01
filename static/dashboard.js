const TIMEFRAMES = ["5M", "1H", "1D", "1W"];
const session = new URLSearchParams(window.location.search).get('session');
var dashboardData = null;

function getStatusBoxHTML(isTrained, index, timeframe) {
    const statusClass = isTrained ? "is-trained" : "is-untrained";
    // Untrained boxes are not clickable links
    if (!isTrained) {
        return `<div class="status-box ${statusClass}"></div>`;
    }
    return `<a class="status-box ${statusClass}" href="chart.html?session=${session}&index=${encodeURIComponent(index)}&time=${encodeURIComponent(timeframe)}"></a>`;
}

function generateDashboardHTML() {
    return dashboardData.data.map(indexData => `
        <div class="columns is-vcentered is-mobile index-row">
            <div class="column is-4"><p class="title is-5">${indexData.idx_name}</p></div>
            ${TIMEFRAMES.map(tf =>
                `<div class="column has-text-centered">${getStatusBoxHTML(indexData[tf], indexData.idx_name, tf)}</div>`
            ).join('')}
            <div class="column is-narrow">
                <button class="button is-white" title="Remove" onclick="tmpDelete('${indexData.idx_name}')">🗑️</button>
            </div>
        </div>
    `).join('');
}

async function tmpDelete(index) {
    if (!confirm(`Flag all timeframes for "${index}" for deletion?`)) return;
    // Flag each trained timeframe
    const entry = dashboardData.data.find(d => d.idx_name === index);
    if (!entry) return;
    const promises = TIMEFRAMES
        .filter(tf => entry[tf])
        .map(tf =>
            fetch(`/api/dashboard/?session=${session}&action=TMPDELETE&index=${encodeURIComponent(index)}&time=${encodeURIComponent(tf)}`, { method: 'POST' })
        );
    await Promise.all(promises);
    await renderDashboard();
}

async function renderDashboard() {
    dashboardData = await loadData();
    const indexContainer = document.getElementById('index-container');
    if (indexContainer) {
        indexContainer.innerHTML = dashboardData.data.length
            ? generateDashboardHTML()
            : '<p class="has-text-grey has-text-centered my-4">No entries on your dashboard yet.</p>';
    }
}

async function loadData() {
    // FIX: use relative URL (no hardcoded localhost:8000), no dummy index param
    const res = await fetch(`/api/dashboard/?session=${session}&action=GET`, { method: 'POST' });
    if (!res.ok) {
        window.location.href = `login.html`;
        throw new Error('Session invalid');
    }
    return res.json();
}

document.addEventListener('DOMContentLoaded', renderDashboard);
