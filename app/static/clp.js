// ====================================
// CLP Detalhes - reusa o estilo de tabela do /clps
// ====================================

let sortState = {};       // { secaoIdx: {colIdx, asc} }
let totalPontosCount = 0; // contagem real para o stat-card "Pontos I/O"

document.addEventListener('DOMContentLoaded', function () {
    renderizarHeader();
    renderizarTabelas();
    verificarStatus();
    inicializarNavegacao();
});

// ---------- HEADER ----------
function renderizarHeader() {
    document.getElementById('clp-titulo').textContent = CLP_DATA.titulo || 'CLP';
    document.getElementById('clp-descricao').textContent = CLP_DATA.descricao || '';
    document.getElementById('clp-modelo').textContent = CLP_DATA.modelo ? '· ' + CLP_DATA.modelo : '';

    const modulos = CLP_DATA.modulos_expansao || [];
    document.getElementById('clp-modulos-count').textContent = modulos.length;
    const lbl = document.getElementById('clp-modulos-label');
    if (lbl) lbl.title = modulos.length ? modulos.join(', ') : 'Sem módulos extras';
}

// ---------- NAVEGAÇÃO entre CLPs ----------
function inicializarNavegacao() {
    const idx = CLP_IPS_ORDERED.indexOf(CLP_IP);
    const total = CLP_IPS_ORDERED.length;
    const counter = document.getElementById('clp-nav-counter');
    if (counter && idx >= 0) counter.textContent = (idx + 1) + ' / ' + total;
    if (idx <= 0)             document.getElementById('btn-prev').disabled = true;
    if (idx >= total - 1)     document.getElementById('btn-next').disabled = true;
}

function navegarCLP(direcao) {
    const idx = CLP_IPS_ORDERED.indexOf(CLP_IP);
    const novoIdx = idx + direcao;
    if (novoIdx < 0 || novoIdx >= CLP_IPS_ORDERED.length) return;
    const basePath = window.location.pathname.includes(APP_CONFIG.routesPrefix + '/')
        ? APP_CONFIG.routesPrefix + '/' : '/';
    window.location.href = basePath + 'clp/' + CLP_IPS_ORDERED[novoIdx];
}

// ---------- TABELAS (mesmo motor do /clps) ----------
function isCellEmpty(v) {
    if (v == null) return true;
    const s = String(v).trim();
    return s === '' || s === '-' || s === '—' || s === '–';
}
function isTotalRow(p) {
    return String(p.numero || '').trim().toUpperCase() === 'TOTAL';
}
function countCircuitos(v) {
    if (isCellEmpty(v)) return 0;
    const s = String(v).trim()
        .replace(/\s+e\s+/gi, ',')
        .replace(/\s*[\/;]\s*/g, ',');
    return s.split(',')
        .map(x => x.trim())
        .filter(x => x.length && !['-', '—', '–'].includes(x))
        .length;
}
const SKIP_TOTAL = { numero: 1, numero_2: 1, descricao: 1, tipo: 1, porta_io: 1 };

function escapeHtml(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function renderizarTabelas() {
    const container = document.getElementById('clp-tables-container');
    container.innerHTML = '';
    totalPontosCount = 0;

    if (CLP_DATA.secoes && CLP_DATA.secoes.length) {
        CLP_DATA.secoes.forEach((secao, idx) => {
            const heading = document.createElement('h3');
            heading.style.cssText = 'margin: 0.5rem 0 0.5rem; font-size: 0.95rem; color: #2d3748; font-weight: 600;';
            heading.innerHTML = '<i class="fas fa-layer-group" style="color: var(--primary); margin-right: 0.4rem;"></i>' +
                                escapeHtml(secao.nome || secao.titulo || 'Seção');
            container.appendChild(heading);
            container.appendChild(criarTabelaWrapper(secao.colunas_display, secao.colunas_keys, secao.pontos, idx));
        });
    } else {
        container.appendChild(criarTabelaWrapper(CLP_DATA.colunas_display, CLP_DATA.colunas_keys, CLP_DATA.pontos, 0));
    }

    document.getElementById('total-pontos').textContent = totalPontosCount;
    document.getElementById('total-filtrados').textContent = totalPontosCount;
}

function criarTabelaWrapper(displays, keys, pontos, secaoIdx) {
    const wrapper = document.createElement('div');
    wrapper.style.marginBottom = '1.25rem';

    displays = displays || [];
    keys = keys || [];

    // Mantemos o índice original (necessário para PUT/DELETE no backend)
    const pontosComIdx = (pontos || [])
        .map((p, i) => ({ p, idx: i }))
        .filter(x => {
            if (isTotalRow(x.p)) return false;
            return !keys.every(k => isCellEmpty(x.p[k]));
        });
    totalPontosCount += pontosComIdx.length;

    // Toolbar: + Adicionar ponto
    const toolbar = document.createElement('div');
    toolbar.style.cssText = 'display:flex; justify-content:flex-end; margin-bottom:0.5rem;';
    toolbar.innerHTML = '<button class="btn-clear-filter" onclick="abrirNovoPonto(' + secaoIdx + ')" title="Adicionar ponto I/O">' +
        '<i class="fas fa-plus"></i> Adicionar ponto</button>';
    wrapper.appendChild(toolbar);

    const tableContainer = document.createElement('div');
    tableContainer.className = 'table-container';
    wrapper.appendChild(tableContainer);

    if (!pontosComIdx.length) {
        tableContainer.innerHTML = '<div style="padding:1rem; color:var(--text-light); font-style:italic;">Sem pontos I/O cadastrados.</div>';
        return wrapper;
    }

    // TOTAL
    const pontosLimpos = pontosComIdx.map(x => x.p);
    const totalCounts = {};
    keys.forEach(k => {
        if (SKIP_TOTAL[k]) totalCounts[k] = null;
        else if (k === 'num_circuito') {
            totalCounts[k] = pontosLimpos.reduce((acc, p) => acc + countCircuitos(p[k]), 0);
        } else {
            totalCounts[k] = pontosLimpos.reduce((acc, p) => acc + (isCellEmpty(p[k]) ? 0 : 1), 0);
        }
    });

    const table = document.createElement('table');
    table.dataset.secao = secaoIdx;

    // Thead com headers ordenáveis + coluna "Ações"
    let theadHtml = '<thead><tr>';
    displays.forEach((d, colIdx) => {
        theadHtml += '<th class="sortable" data-col="' + colIdx + '" data-secao="' + secaoIdx + '">' +
                     escapeHtml(d) + ' <i class="fas fa-sort"></i></th>';
    });
    theadHtml += '<th class="col-acoes" style="text-align:center;">Ações</th>';
    theadHtml += '</tr></thead>';

    let tbodyHtml = '<tbody>';
    pontosComIdx.forEach(({ p, idx }) => {
        tbodyHtml += '<tr data-idx="' + idx + '">';
        keys.forEach(k => {
            let v = p[k];
            if (v == null || v === '') v = '—';
            tbodyHtml += '<td>' + escapeHtml(String(v)) + '</td>';
        });
        tbodyHtml += '<td class="col-acoes" style="text-align:center; white-space:nowrap;">' +
            '<button class="btn-cmd" title="Editar" onclick="abrirEditarPonto(' + secaoIdx + ',' + idx + ')">' +
                '<i class="fas fa-edit"></i></button> ' +
            '<button class="btn-cmd btn-danger-cmd" title="Excluir" onclick="excluirPonto(' + secaoIdx + ',' + idx + ')">' +
                '<i class="fas fa-trash"></i></button>' +
            '</td>';
        tbodyHtml += '</tr>';
    });
    // Linha TOTAL automática
    tbodyHtml += '<tr class="row-total" style="background:#f8fafc; border-top:2px solid #cbd5e0; font-weight:600;">';
    keys.forEach((k, idx) => {
        let content = '';
        if (idx === 0) {
            content = '<strong style="color:#1a202c;">TOTAL</strong>';
        } else if (!SKIP_TOTAL[k]) {
            const n = totalCounts[k];
            if (n > 0) content = '<span style="color:#2c5282;">' + n + '</span>';
        }
        tbodyHtml += '<td>' + content + '</td>';
    });
    tbodyHtml += '<td></td></tr>';
    tbodyHtml += '</tbody>';

    table.innerHTML = theadHtml + tbodyHtml;
    tableContainer.appendChild(table);

    // Sort handlers
    table.querySelectorAll('th.sortable').forEach(th => {
        th.addEventListener('click', () => ordenarTabela(table, parseInt(th.dataset.col, 10), keys, parseInt(th.dataset.secao, 10)));
    });

    return wrapper;
}

// ---------- FILTRO ----------
function filtrarTabela() {
    const busca = (document.getElementById('filtro-busca').value || '').toLowerCase();
    let visiveis = 0;
    document.querySelectorAll('#clp-tables-container table').forEach(table => {
        const rows = Array.from(table.querySelectorAll('tbody tr'));
        // separa linhas reais da linha TOTAL (última)
        rows.forEach((row, i) => {
            const isTotal = (i === rows.length - 1);
            if (isTotal) return; // mantém TOTAL sempre visível
            const txt = row.textContent.toLowerCase();
            if (!busca || txt.includes(busca)) {
                row.style.display = '';
                visiveis++;
            } else {
                row.style.display = 'none';
            }
        });
    });
    document.getElementById('total-filtrados').textContent = visiveis;
}

// ---------- SORT ----------
function ordenarTabela(table, colIdx, keys, secaoIdx) {
    const key = secaoIdx + '-' + colIdx;
    const asc = sortState[key] === undefined ? true : !sortState[key];
    sortState[key] = asc;

    const tbody = table.querySelector('tbody');
    const allRows = Array.from(tbody.querySelectorAll('tr'));
    const totalRow = allRows[allRows.length - 1];
    const rows = allRows.slice(0, -1);  // exclui linha TOTAL

    rows.sort((a, b) => {
        const av = (a.cells[colIdx] ? a.cells[colIdx].textContent : '').trim();
        const bv = (b.cells[colIdx] ? b.cells[colIdx].textContent : '').trim();
        const an = parseFloat(av), bn = parseFloat(bv);
        if (!isNaN(an) && !isNaN(bn)) return asc ? an - bn : bn - an;
        return asc ? av.localeCompare(bv, 'pt-BR') : bv.localeCompare(av, 'pt-BR');
    });

    rows.forEach(r => tbody.appendChild(r));
    if (totalRow) tbody.appendChild(totalRow);

    // Atualiza setas
    table.querySelectorAll('th.sortable').forEach(th => th.classList.remove('asc', 'desc'));
    const activeTh = table.querySelectorAll('th.sortable')[colIdx];
    if (activeTh) activeTh.classList.add(asc ? 'asc' : 'desc');
}

// ============================================================
// CRUD: adicionar / editar / excluir pontos I/O
// ============================================================
let pontoEditState = { secaoIdx: 0, idx: null };  // idx=null → criar; senão editar

function getApiBase() {
    return window.location.hostname.includes('tce.go.gov.br')
        ? APP_CONFIG.routesPrefix : '';
}

function getColunasAtuais(secaoIdx) {
    if (CLP_DATA.secoes && CLP_DATA.secoes.length) {
        const s = CLP_DATA.secoes[secaoIdx] || CLP_DATA.secoes[0];
        return { display: s.colunas_display || [], keys: s.colunas_keys || [] };
    }
    return {
        display: CLP_DATA.colunas_display || [],
        keys: CLP_DATA.colunas_keys || []
    };
}

function getPontoFromData(secaoIdx, idx) {
    if (CLP_DATA.secoes && CLP_DATA.secoes.length) {
        return (CLP_DATA.secoes[secaoIdx] || {}).pontos[idx];
    }
    return CLP_DATA.pontos[idx];
}

function popularFormPonto(ponto) {
    const { display, keys } = getColunasAtuais(pontoEditState.secaoIdx);
    const container = document.getElementById('pontoFormFields');
    container.innerHTML = '';
    keys.forEach((k, i) => {
        const value = (ponto && ponto[k] != null) ? String(ponto[k]) : '';
        const grupo = document.createElement('div');
        grupo.className = 'form-group';
        grupo.innerHTML =
            '<label for="ponto-fld-' + i + '">' + escapeHtml(display[i] || k) + '</label>' +
            '<input type="text" id="ponto-fld-' + i + '" data-key="' + k + '" value="' + escapeHtml(value) + '">';
        container.appendChild(grupo);
    });
    // Foco no primeiro campo
    setTimeout(() => {
        const first = container.querySelector('input');
        if (first) first.focus();
    }, 60);
}

function abrirNovoPonto(secaoIdx) {
    pontoEditState = { secaoIdx: secaoIdx, idx: null };
    document.getElementById('pontoModalTitle').innerHTML = '<i class="fas fa-plus"></i> Adicionar Ponto I/O';
    popularFormPonto({});
    document.getElementById('pontoModal').style.display = 'block';
}

function abrirEditarPonto(secaoIdx, idx) {
    pontoEditState = { secaoIdx: secaoIdx, idx: idx };
    document.getElementById('pontoModalTitle').innerHTML = '<i class="fas fa-edit"></i> Editar Ponto I/O';
    popularFormPonto(getPontoFromData(secaoIdx, idx) || {});
    document.getElementById('pontoModal').style.display = 'block';
}

function closePontoModal() {
    document.getElementById('pontoModal').style.display = 'none';
}

function lerFormPonto() {
    const ponto = {};
    document.querySelectorAll('#pontoFormFields input').forEach(inp => {
        ponto[inp.dataset.key] = inp.value.trim();
    });
    return ponto;
}

async function salvarPonto() {
    const ponto = lerFormPonto();
    const { secaoIdx, idx } = pontoEditState;
    const temSecoes = !!(CLP_DATA.secoes && CLP_DATA.secoes.length);
    const baseUrl = getApiBase();
    const isNovo = idx == null;
    const url = baseUrl + '/api/clp/' + CLP_IP + '/pontos' + (isNovo ? '' : '/' + idx);
    const payload = { ponto: ponto };
    if (temSecoes) payload.secao_idx = secaoIdx;

    try {
        const r = await fetch(url, {
            method: isNovo ? 'POST' : 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const j = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
        closePontoModal();
        await recarregarDados();
        toast(isNovo ? 'Ponto adicionado' : 'Ponto atualizado');
    } catch (e) {
        alert('Erro ao salvar: ' + e.message);
    }
}

async function excluirPonto(secaoIdx, idx) {
    if (!confirm('Excluir este ponto I/O?')) return;
    const temSecoes = !!(CLP_DATA.secoes && CLP_DATA.secoes.length);
    const baseUrl = getApiBase();
    let url = baseUrl + '/api/clp/' + CLP_IP + '/pontos/' + idx;
    if (temSecoes) url += '?secao_idx=' + secaoIdx;
    try {
        const r = await fetch(url, { method: 'DELETE' });
        const j = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
        await recarregarDados();
        toast('Ponto removido');
    } catch (e) {
        alert('Erro ao excluir: ' + e.message);
    }
}

async function recarregarDados() {
    const baseUrl = getApiBase();
    const r = await fetch(baseUrl + '/api/clp/' + CLP_IP);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const fresh = await r.json();
    // Substitui chaves relevantes do CLP_DATA in-place (mantém referências)
    Object.keys(CLP_DATA).forEach(k => { delete CLP_DATA[k]; });
    Object.assign(CLP_DATA, fresh);
    renderizarTabelas();
}

function toast(msg) {
    const old = document.querySelector('.clp-toast');
    if (old) old.remove();
    const t = document.createElement('div');
    t.className = 'clp-toast';
    t.style.cssText = 'position:fixed; bottom:20px; right:20px; background:#2d3748; color:white; padding:0.7rem 1.1rem; border-radius:8px; box-shadow:0 4px 12px rgba(0,0,0,0.2); z-index:9999; font-size:0.9rem;';
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 2500);
}

// Fecha modal por Esc / clique fora
document.addEventListener('DOMContentLoaded', () => {
    const modal = document.getElementById('pontoModal');
    if (modal) {
        modal.addEventListener('click', e => { if (e.target === modal) closePontoModal(); });
    }
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape') closePontoModal();
    });
});

// ---------- STATUS online/offline ----------
function verificarStatus() {
    const statusEl = document.getElementById('clp-status-pill');
    if (!statusEl) return;
    const baseUrl = window.location.hostname.includes('tce.go.gov.br')
        ? APP_CONFIG.routesPrefix : '';

    fetch(baseUrl + '/api/start-check/85')
        .then(r => r.status === 200 ? r.json() : null)
        .then(data => {
            if (!data) { statusEl.textContent = '—'; return; }
            const dev = data.find(d => d.ip === CLP_IP);
            if (!dev) { statusEl.textContent = 'N/M'; return; }
            statusEl.textContent = dev.status === 'on' ? 'Online' : 'Offline';
            statusEl.style.color = dev.status === 'on' ? '#2f855a' : '#c53030';
        })
        .catch(() => { statusEl.textContent = '—'; });
}
