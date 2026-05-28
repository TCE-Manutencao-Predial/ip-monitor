// ====================================
// CLPs Listing Page - Tabela
// ====================================

let statusData = {};
let sortKey = 'ip';
let sortDir = 'asc';
const detalheCache = {};  // {ip: {data, html}} — cache local p/ expand instantâneo
const ipExpandido = new Set();  // IPs com linha expandida

document.addEventListener('DOMContentLoaded', function () {
    setupSortHeaders();
    renderTabela();
    carregarStatus();
});

function getApiBaseUrl() {
    if (window.location.hostname.includes(APP_CONFIG.domainBase)) {
        return APP_CONFIG.routesPrefix;
    }
    return '';
}

function setupSortHeaders() {
    document.querySelectorAll('#clpsTable th.sortable').forEach(function (th) {
        th.addEventListener('click', function () {
            var key = th.dataset.sort;
            if (sortKey === key) {
                sortDir = (sortDir === 'asc') ? 'desc' : 'asc';
            } else {
                sortKey = key;
                sortDir = 'asc';
            }
            atualizarHeaderSort();
            renderTabela();
        });
    });
    atualizarHeaderSort();
}

function atualizarHeaderSort() {
    document.querySelectorAll('#clpsTable th.sortable').forEach(function (th) {
        th.classList.remove('asc', 'desc');
        if (th.dataset.sort === sortKey) {
            th.classList.add(sortDir);
        }
    });
}

function compararIPs(a, b) {
    var pa = a.split('.').map(Number);
    var pb = b.split('.').map(Number);
    for (var i = 0; i < 4; i++) {
        if (pa[i] !== pb[i]) return pa[i] - pb[i];
    }
    return 0;
}

function rankStatus(status) {
    if (status === 'on')  return 0;
    if (status === 'off') return 1;
    return 2; // desconhecido por último
}

function ordenarLista(ips) {
    var arr = ips.slice();
    arr.sort(function (a, b) {
        var ca = CLPS_DATA[a] || {};
        var cb = CLPS_DATA[b] || {};
        var va, vb;
        switch (sortKey) {
            case 'status':
                va = rankStatus(statusData[a]);
                vb = rankStatus(statusData[b]);
                break;
            case 'titulo':
                va = (ca.titulo || '').toLowerCase();
                vb = (cb.titulo || '').toLowerCase();
                break;
            case 'modelo':
                va = (ca.modelo || '').toLowerCase();
                vb = (cb.modelo || '').toLowerCase();
                break;
            case 'aba':
                va = (ca.aba_nome || '').toLowerCase();
                vb = (cb.aba_nome || '').toLowerCase();
                break;
            case 'pontos':
                va = ca.total_pontos || 0;
                vb = cb.total_pontos || 0;
                break;
            case 'ip':
            default:
                return sortDir === 'asc' ? compararIPs(a, b) : compararIPs(b, a);
        }
        if (va < vb) return sortDir === 'asc' ? -1 : 1;
        if (va > vb) return sortDir === 'asc' ?  1 : -1;
        return compararIPs(a, b);
    });
    return arr;
}

function renderTabela() {
    var tbody = document.getElementById('clpsTableBody');
    if (!tbody) return;
    var busca = (document.getElementById('search-clps').value || '').toLowerCase();
    tbody.innerHTML = '';

    var ips = Object.keys(CLPS_DATA);

    // Filtro de busca
    var filtrados = ips.filter(function (ip) {
        var clp = CLPS_DATA[ip] || {};
        var blob = ((clp.titulo || '') + ' ' + (clp.modelo || '') + ' ' + ip + ' ' + (clp.aba_nome || '')).toLowerCase();
        return !busca || blob.includes(busca);
    });

    // Atualiza totais
    document.getElementById('total-clps').textContent = ips.length;
    var totalPontos = ips.reduce(function (acc, ip) {
        return acc + (CLPS_DATA[ip].total_pontos || 0);
    }, 0);
    document.getElementById('total-pontos').textContent = totalPontos;

    // Estado vazio
    var emptyEl = document.getElementById('clps-empty');
    if (!filtrados.length) {
        if (emptyEl) emptyEl.style.display = 'block';
        return;
    }
    if (emptyEl) emptyEl.style.display = 'none';

    // Renderiza linhas
    var ordenados = ordenarLista(filtrados);
    var basePath = window.location.pathname.includes(APP_CONFIG.routesPrefix + '/')
        ? APP_CONFIG.routesPrefix + '/' : '/';

    ordenados.forEach(function (ip) {
        var clp = CLPS_DATA[ip] || {};
        var status = statusData[ip] || 'unknown';
        var dotCls = status === 'on' ? 'dot-on' : 'dot-off';
        var rowCls = status === 'off' ? 'row-off' : '';
        var expandido = ipExpandido.has(ip);

        var tr = document.createElement('tr');
        tr.className = 'clp-row ' + rowCls + (expandido ? ' is-expanded' : '');
        tr.dataset.ip = ip;

        tr.innerHTML =
            '<td class="col-status"><span class="dot ' + dotCls + '" title="' + (status === 'on' ? 'Online' : status === 'off' ? 'Offline' : 'Desconhecido') + '"></span></td>' +
            '<td class="col-ip"><span style="font-family: \'SF Mono\', Consolas, monospace; font-size: 0.85rem;">' + ip + '</span></td>' +
            '<td><strong>' + escapeHtml(clp.titulo || ('CLP ' + ip)) + '</strong>' +
                (clp.tem_secoes ? ' <small class="text-muted" style="opacity:0.7;">· múltiplas seções</small>' : '') +
            '</td>' +
            '<td>' + escapeHtml(clp.modelo || '—') + '</td>' +
            '<td>' + escapeHtml(clp.aba_nome || '—') + '</td>' +
            '<td style="text-align: right;"><strong>' + (clp.total_pontos || 0) + '</strong></td>' +
            '<td class="col-acoes">' +
                '<button class="btn btn-expand" data-action="toggle" data-ip="' + ip + '" title="Expandir detalhes">' +
                    '<i class="fas ' + (expandido ? 'fa-chevron-up' : 'fa-chevron-down') + '"></i>' +
                '</button> ' +
                '<button class="btn" data-action="page" data-ip="' + ip + '" title="Abrir página completa">' +
                    '<i class="fas fa-external-link-alt"></i>' +
                '</button>' +
            '</td>';

        tbody.appendChild(tr);

        // Linha de detalhes (vazia até expandir)
        if (expandido) {
            var detalheTR = criarLinhaDetalhe(ip);
            tbody.appendChild(detalheTR);
            // Renderiza conteúdo (cache ou carrega)
            preencherDetalhe(ip, detalheTR.querySelector('.detalhe-content'));
        }
    });

    // Click handler
    tbody.querySelectorAll('button[data-action="toggle"]').forEach(function (btn) {
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            toggleExpand(btn.dataset.ip);
        });
    });
    tbody.querySelectorAll('button[data-action="page"]').forEach(function (btn) {
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            window.location.href = basePath + 'clp/' + btn.dataset.ip;
        });
    });
    tbody.querySelectorAll('tr.clp-row').forEach(function (tr) {
        tr.style.cursor = 'pointer';
        tr.addEventListener('click', function (e) {
            // Evita disparar quando clicou em botão
            if (e.target.closest('button')) return;
            toggleExpand(tr.dataset.ip);
        });
    });
}

// ==================== EXPAND / COLLAPSE ====================

function criarLinhaDetalhe(ip) {
    var tr = document.createElement('tr');
    tr.className = 'clp-detalhe-row';
    tr.dataset.detalheIp = ip;
    tr.innerHTML =
        '<td colspan="7" style="padding: 0; background: #fafbfc; border-top: 0;">' +
            '<div class="detalhe-content" style="padding: 1rem 1.25rem;">' +
                '<div class="text-center" style="padding:1rem; color:#6b7280;">' +
                    '<i class="fas fa-spinner fa-spin"></i> Carregando detalhes…' +
                '</div>' +
            '</div>' +
        '</td>';
    return tr;
}

function toggleExpand(ip) {
    if (ipExpandido.has(ip)) {
        ipExpandido.delete(ip);
    } else {
        ipExpandido.add(ip);
    }
    renderTabela();
}

function preencherDetalhe(ip, container) {
    if (detalheCache[ip]) {
        container.innerHTML = detalheCache[ip].html;
        return;
    }
    var baseUrl = getApiBaseUrl();
    fetch(baseUrl + '/api/clp/' + ip)
        .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
        .then(function (data) {
            var html = renderDetalheHtml(ip, data);
            detalheCache[ip] = { data: data, html: html };
            container.innerHTML = html;
        })
        .catch(function (err) {
            container.innerHTML =
                '<div style="padding:1rem; color:#c53030;">' +
                    '<i class="fas fa-exclamation-triangle"></i> Falha ao carregar detalhes (' + err + ')' +
                '</div>';
        });
}

function renderDetalheHtml(ip, clp) {
    var blocos = [];

    // Metadados resumidos
    var meta = '<div style="display:flex; gap:1.25rem; flex-wrap:wrap; margin-bottom:0.75rem; font-size:0.85rem; color:#4a5568;">';
    if (clp.descricao) meta += '<span><i class="fas fa-info-circle" style="color:var(--primary);"></i> ' + escapeHtml(clp.descricao) + '</span>';
    var modulos = clp.modulos_expansao || [];
    if (modulos.length) {
        meta += '<span><i class="fas fa-puzzle-piece" style="color:var(--primary);"></i> ' + modulos.length + ' módulo' + (modulos.length > 1 ? 's' : '') + ' de expansão</span>';
    }
    meta += '<span><i class="fas fa-plug" style="color:var(--primary);"></i> ' + (clp.total_pontos || 0) + ' pontos I/O</span>';
    meta += '</div>';
    blocos.push(meta);

    // Seções múltiplas ou tabela única
    if (clp.secoes && clp.secoes.length) {
        clp.secoes.forEach(function (secao) {
            blocos.push('<h4 style="margin:0.75rem 0 0.4rem; font-size:0.92rem; color:#2d3748; font-weight:600;">' +
                '<i class="fas fa-layer-group" style="color:var(--primary);"></i> ' +
                escapeHtml(secao.nome || secao.titulo || 'Seção') +
            '</h4>');
            blocos.push(renderTabelaPontos(secao.colunas_display, secao.colunas_keys, secao.pontos || []));
        });
    } else {
        blocos.push(renderTabelaPontos(clp.colunas_display, clp.colunas_keys, clp.pontos || []));
    }

    return blocos.join('');
}

function renderTabelaPontos(displays, keys, pontos) {
    if (!pontos || !pontos.length) {
        return '<div style="padding:0.5rem 0; color:#9ca3af; font-size:0.85rem;"><em>Sem pontos I/O cadastrados.</em></div>';
    }
    displays = displays || [];
    keys = keys || [];

    function isCellEmpty(v) {
        if (v == null) return true;
        var s = String(v).trim();
        return s === '' || s === '-' || s === '—' || s === '–';
    }
    function isTotalRow(p) {
        return String(p.numero || '').trim().toUpperCase() === 'TOTAL';
    }

    // Defesa: mesmo após limpeza no JSON, ainda filtra linhas TOTAL manuais e vazias
    var pontosLimpos = pontos.filter(function (p) {
        if (isTotalRow(p)) return false;
        return !keys.every(function (k) { return isCellEmpty(p[k]); });
    });

    if (!pontosLimpos.length) {
        return '<div style="padding:0.5rem 0; color:#9ca3af; font-size:0.85rem;"><em>Sem pontos I/O cadastrados.</em></div>';
    }

    // Calcula TOTAL automaticamente.
    // Colunas que NÃO recebem contagem (são identificadores ou texto livre):
    var SKIP_TOTAL = { numero: 1, numero_2: 1, descricao: 1, tipo: 1, porta_io: 1 };

    // Conta elementos em "Nº Circ." — uma célula pode ter múltiplos circuitos:
    //   "8 e 9"           → 2
    //   "10, 11, 12"      → 3
    //   "1 e 2, 3"        → 3
    //   "10/11"           → 2
    function countCircuitos(v) {
        if (isCellEmpty(v)) return 0;
        var s = String(v).trim()
            .replace(/\s+e\s+/gi, ',')   // " e " → ","
            .replace(/\s*[\/;]\s*/g, ','); // "/" ou ";" → ","
        var parts = s.split(',')
            .map(function (x) { return x.trim(); })
            .filter(function (x) { return x.length && x !== '-' && x !== '—' && x !== '–'; });
        return parts.length;
    }

    var totalCounts = {};
    keys.forEach(function (k) {
        if (SKIP_TOTAL[k]) {
            totalCounts[k] = null;
        } else if (k === 'num_circuito') {
            totalCounts[k] = pontosLimpos.reduce(function (acc, p) {
                return acc + countCircuitos(p[k]);
            }, 0);
        } else {
            totalCounts[k] = pontosLimpos.reduce(function (acc, p) {
                return acc + (isCellEmpty(p[k]) ? 0 : 1);
            }, 0);
        }
    });

    var html = '<div style="overflow-x:auto; border:1px solid #e5e7eb; border-radius:6px; background:white;">';
    html += '<table style="width:100%; border-collapse:collapse; font-size:0.82rem;">';
    html += '<thead><tr>';
    displays.forEach(function (d) {
        html += '<th style="text-align:left; padding:0.45rem 0.7rem; background:#f3f4f6; font-weight:600; font-size:0.75rem; text-transform:uppercase; letter-spacing:0.3px; color:#4a5568; border-bottom:1px solid #e5e7eb;">' + escapeHtml(d) + '</th>';
    });
    html += '</tr></thead><tbody>';
    pontosLimpos.forEach(function (p) {
        html += '<tr style="border-bottom:1px solid #f3f4f6;">';
        keys.forEach(function (k) {
            var v = p[k];
            if (v == null || v === '') v = '—';
            html += '<td style="padding:0.4rem 0.7rem; color:#374151;">' + escapeHtml(String(v)) + '</td>';
        });
        html += '</tr>';
    });

    // Linha TOTAL calculada
    html += '<tr style="background:#f8fafc; border-top:2px solid #cbd5e0; font-weight:600;">';
    keys.forEach(function (k, idx) {
        var content = '';
        if (idx === 0) {
            content = '<strong style="color:#1a202c;">TOTAL</strong>';
        } else if (!SKIP_TOTAL[k]) {
            var n = totalCounts[k];
            if (n > 0) content = '<span style="color:#2c5282;">' + n + '</span>';
        }
        html += '<td style="padding:0.5rem 0.7rem; color:#374151;">' + content + '</td>';
    });
    html += '</tr>';

    html += '</tbody></table></div>';
    return html;
}

function escapeHtml(s) {
    if (s == null) return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function carregarStatus() {
    var baseUrl = getApiBaseUrl();
    fetch(baseUrl + '/api/start-check/85')
        .then(function (r) { return r.status === 200 ? r.json() : null; })
        .then(function (data) {
            if (!data) return;
            data.forEach(function (d) { statusData[d.ip] = d.status; });

            var online = 0, offline = 0;
            Object.keys(CLPS_DATA).forEach(function (ip) {
                var s = statusData[ip];
                if (s === 'on') online++;
                else if (s === 'off') offline++;
            });
            document.getElementById('total-online').textContent = online;
            document.getElementById('total-offline').textContent = offline;

            renderTabela();
        })
        .catch(function () {
            console.warn('Não foi possível carregar status dos CLPs');
        });
}

function filtrarClps() {
    renderTabela();
}
