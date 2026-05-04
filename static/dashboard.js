const TIMEFRAMES = ["5M", "1H", "1D", "1W"];
const session = new URLSearchParams(window.location.search).get('session');
var dashboardData = null;

function getStatusBoxHTML(isTrained, isLink = false, linkData = {}) {
    const statusClass = isTrained ? "is-trained" : "is-untrained";
    const tagName = isLink ? "a" : "div";
    const href = isLink ? `href="chart.html?session=${session}&index=${linkData.index}&time=${linkData.time}"` : "";
    
    return `<${tagName} class="status-box ${statusClass}" ${href}></${tagName}>`;
}

function generateDashboardHTML(){
    const indexRows = dashboardData.data.map(indexData => {
        const rowHTML = `
            <div class="columns is-vcentered is-mobile index-row">
                <div class="column is-4"><p class="title is-5">${indexData.idx_name}</p></div>
                ${TIMEFRAMES.map(timeframe => {
                    const isTrained = indexData[timeframe];
                    const isLink = true;
                    const linkData = isLink ? { session, index: indexData.idx_name, time: timeframe } : {};
                    
                    return `<div class="column has-text-centered">${getStatusBoxHTML(isTrained, isLink, linkData)}</div>`;
                }).join('')}
                <div class="column is-narrow"><button class="button is-white">🗑️</button></div>
            </div>
        `;
        return rowHTML;
    }).join('');
    
    return indexRows;
}

async function renderDashboard() {
    const params = new URLSearchParams(window.location.search);
    dashboardData = await loadData();
    console.log(dashboardData);
    
    const indexContainer = document.getElementById('index-container');
    if (indexContainer) {
        indexContainer.innerHTML = generateDashboardHTML();
    }
}

async function loadData(){
    const res = await fetch(`http://localhost:8000/dashboard/?session=${session}&action=GET&index=foo`);
    return await res.json();
}
// Initialize dashboard when DOM is ready
document.addEventListener('DOMContentLoaded', renderDashboard);
