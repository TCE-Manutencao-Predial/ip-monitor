// Estado global
let currentDevicesData = [];
let currentVlan = '85';
let sortKey = 'ip';
let sortAsc = true;
let arduinosByIp = {};  // { '172.17.86.x': {rssi, uptime, heap, ...} }

// Carrega diagnóstico do arduinos (status online + uptime por IP)
async function loadArduinosDiag() {
    try {
        const r = await fetch('/arduinos/api/diagnostico');
        if (!r.ok) return;
        const data = await r.json();
        arduinosByIp = {};
        data.forEach(d => {
            if (d.ip) arduinosByIp[d.ip] = {
                diag: d.diag || null,
                is_online: !!d.is_online
            };
        });
    } catch (e) {
        console.warn('Falha ao buscar /arduinos/api/diagnostico:', e);
    }
}

function getApiBaseUrl() {
    return window.location.hostname.includes('tce.go.gov.br') ? APP_CONFIG.routesPrefix : '';
}

// IP em ordem numérica
function ipKey(ip) {
    return ip.split('.').map(n => parseInt(n, 10).toString().padStart(3, '0')).join('.');
}

async function searchByVlan() {
    const vlanSelect = document.getElementById('filtroVLAN');
    const vlan = encodeURIComponent(vlanSelect.value);

    // Cor do select conforme VLAN
    vlanSelect.className = '';
    vlanSelect.classList.add('vlan-' + vlan);

    updateGateway(vlan);
    document.getElementById('vlanBadge').textContent = 'VLAN ' + vlan;

    const baseUrl = getApiBaseUrl();
    let response;
    try {
        response = await fetch(`${baseUrl}/api/start-check/${vlan}`);
    } catch (e) {
        console.error('Falha de rede:', e);
        return;
    }

    const msgEl = document.getElementById('mensagem_preliminar');
    if (response.status !== 200) {
        msgEl.style.display = 'block';
        msgEl.innerHTML = `Falha ao obter dados (cód. ${response.status}). Aguarde…`;
        return;
    }
    msgEl.style.display = 'none';

    const data = await response.json();
    currentDevicesData = data;
    currentVlan = vlan;

    populateFilters(data);
    await loadArduinosDiag();
    sortAndRender();
}

function updateGateway(vlan) {
    const el = document.getElementById('gateway-value');
    if (el) el.textContent = `172.17.${vlan}.254`;
}

function populateFilters(data) {
    const tipos = new Set();
    const sensores = new Set();
    const tabelas = new Set();
    data.forEach(d => {
        if (d.tipo) tipos.add(d.tipo);
        (d.sensores || []).forEach(s => sensores.add(s.codigo || s));
        (d.tabelas_sql || []).forEach(t => { if (t.tabela) tabelas.add(t.tabela); });
    });
    fillSelect('filter-tipo', tipos, 'Todos os tipos');
    fillSelect('filter-sensor', sensores, 'Todos os sensores');
    fillSelect('filter-tabela', tabelas, 'Todas as tabelas');
}

function fillSelect(id, valuesSet, defaultLabel) {
    const sel = document.getElementById(id);
    const previous = sel.value;
    sel.innerHTML = '';
    const opt0 = document.createElement('option');
    opt0.value = ''; opt0.textContent = defaultLabel;
    sel.appendChild(opt0);
    [...valuesSet].sort().forEach(v => {
        const o = document.createElement('option');
        o.value = v; o.textContent = v;
        sel.appendChild(o);
    });
    if (previous) sel.value = previous;
}

function getActiveFilters() {
    return {
        q: (document.getElementById('search-devices').value || '').toLowerCase().trim(),
        status: document.getElementById('filter-status').value,
        tipo: document.getElementById('filter-tipo').value,
        sensor: document.getElementById('filter-sensor').value,
        tabela: document.getElementById('filter-tabela').value,
        hideEmpty: document.getElementById('hide-empty').checked,
    };
}

// Considera "sem dados" quando descricao é '-' ou vazia (IP não cadastrado)
function isEmpty(d) {
    const desc = (d.descricao || '').trim();
    return !desc || desc === '-';
}

function matchDevice(d, f) {
    if (f.hideEmpty && isEmpty(d)) return false;
    if (f.status === 'online' && d.status !== 'on') return false;
    if (f.status === 'offline' && d.status !== 'off') return false;
    if (f.tipo && d.tipo !== f.tipo) return false;
    if (f.sensor) {
        const list = (d.sensores || []).map(s => s.codigo || s);
        if (!list.includes(f.sensor)) return false;
    }
    if (f.tabela) {
        const list = (d.tabelas_sql || []).map(t => t.tabela);
        if (!list.includes(f.tabela)) return false;
    }
    if (f.q) {
        const haystack = [
            d.ip,
            d.descricao || '',
            d.tipo || '',
            ...(d.sensores || []).map(s => (s.codigo || '') + ' ' + (s.categoria || '')),
            ...(d.tabelas_sql || []).map(t => (t.tabela || '') + '.' + (t.coluna || '')),
        ].join(' ').toLowerCase();
        if (!haystack.includes(f.q)) return false;
    }
    return true;
}

function filtrarDispositivos() { sortAndRender(); }

function limparFiltros() {
    document.getElementById('search-devices').value = '';
    document.getElementById('filter-status').value = '';
    document.getElementById('filter-tipo').value = '';
    document.getElementById('filter-sensor').value = '';
    document.getElementById('filter-tabela').value = '';
    document.getElementById('hide-empty').checked = true;
    sortAndRender();
}

function ordenar(key) {
    if (sortKey === key) sortAsc = !sortAsc;
    else { sortKey = key; sortAsc = true; }
    sortAndRender();
}

function sortAndRender() {
    const f = getActiveFilters();
    const filtered = currentDevicesData.filter(d => matchDevice(d, f));

    filtered.sort((a, b) => {
        let av, bv;
        if (sortKey === 'ip') { av = ipKey(a.ip); bv = ipKey(b.ip); }
        else if (sortKey === 'status') { av = a.status; bv = b.status; }
        else { av = (a[sortKey] || '').toString().toLowerCase(); bv = (b[sortKey] || '').toString().toLowerCase(); }
        if (av < bv) return sortAsc ? -1 : 1;
        if (av > bv) return sortAsc ? 1 : -1;
        return 0;
    });

    renderTable(filtered);

    // Sumário (online/offline considera só os com dados se hide-empty estiver ativo)
    const base = f.hideEmpty ? currentDevicesData.filter(d => !isEmpty(d)) : currentDevicesData;
    const online = base.filter(d => d.status === 'on').length;
    const offline = base.length - online;
    setText('total-online', online);
    setText('total-offline', offline);
    setText('total-devices', base.length);
    setText('total-visible', filtered.length);

    // Indicador de ordenação nos headers
    document.querySelectorAll('th.sortable').forEach(th => th.classList.remove('asc', 'desc'));
    const headerMap = {
        'status': 0, 'ip': 1, 'descricao': 2, 'tipo': 3,
    };
    if (headerMap.hasOwnProperty(sortKey)) {
        const ths = document.querySelectorAll('table thead th');
        const th = ths[headerMap[sortKey]];
        if (th && th.classList.contains('sortable')) th.classList.add(sortAsc ? 'asc' : 'desc');
    }
    // Atualiza ícone fa-sort para fa-sort-up/down
    document.querySelectorAll('th.sortable i').forEach(i => i.className = 'fas fa-sort');
    document.querySelectorAll('th.sortable.asc i').forEach(i => i.className = 'fas fa-sort-up');
    document.querySelectorAll('th.sortable.desc i').forEach(i => i.className = 'fas fa-sort-down');
}

function setText(id, v) { const el = document.getElementById(id); if (el) el.textContent = v; }

function renderTable(devices) {
    const tbody = document.getElementById('devicesTableBody');
    const empty = document.getElementById('empty-state');
    if (!devices.length) {
        tbody.innerHTML = '';
        empty.style.display = '';
        return;
    }
    empty.style.display = 'none';

    const MAX_SQL_VISIBLE = 4;
    let html = '';
    for (const d of devices) {
        const isOn = d.status === 'on';
        const sensors = d.sensores || [];
        const sqls = d.tabelas_sql || [];

        const sensorBadges = sensors.length
            ? sensors.map(s => `<span class="badge badge-sensor" title="${esc(s.categoria||'')}">${esc(s.codigo||s)}</span>`).join('')
            : '<span class="muted">—</span>';

        // Cada coluna como badge própria: TABELA.COLUNA (com ênfase na coluna)
        const sqlBadges = sqls.length
            ? sqls.slice(0, MAX_SQL_VISIBLE).map(t => {
                const fullPath = (t.tabela || '') + (t.coluna ? '.' + t.coluna : '');
                return `<span class="badge badge-sql" title="${esc(fullPath)}" onclick="showSqlDetails('${esc(d.ip)}')"><span class="sql-tab">${esc(t.tabela||'')}.</span><span class="sql-col">${esc(t.coluna||'—')}</span></span>`;
              }).join('') +
              (sqls.length > MAX_SQL_VISIBLE ? `<span class="badge badge-more" title="Ver todas (${sqls.length})" onclick="showSqlDetails('${esc(d.ip)}')">+${sqls.length - MAX_SQL_VISIBLE}</span>` : '')
            : '<span class="muted">—</span>';

        const isClp = currentVlan === '85' && CLP_IPS && CLP_IPS.includes(d.ip);
        const hasCadastro = d.descricao && d.descricao !== '-';
        const desc = hasCadastro ? d.descricao : '<span class="muted">— sem cadastro —</span>';
        const tipo = d.tipo ? `<span class="badge ${tipoColorClass(d.tipo)}">${esc(d.tipo)}</span>` : '<span class="muted">—</span>';
        const arduinosBadge = renderArduinosBadge(d.ip);
        const devUrl = `http://${esc(d.ip)}/`;
        // Clicar na descrição abre o modal Editar (antes abria o IP em nova aba).
        const editArgs = `'${esc(d.ip)}','${escAttr(d.descricao===' -' ? '' : (d.descricao||''))}','${escAttr(d.tipo||'')}','${currentVlan}'`;
        const descInner = hasCadastro ? esc(desc) : desc;
        const descCell = `<span class="desc-edit" title="Clique para editar este dispositivo" onclick="openEditModal(${editArgs})">${descInner}</span>`;

        html += `<tr class="${isOn ? 'row-on' : 'row-off'}" data-ip="${esc(d.ip)}">
            <td class="col-status"><span class="dot ${isOn ? 'dot-on' : 'dot-off'}" title="${isOn ? 'Online' : 'Offline'}"></span></td>
            <td class="col-ip mono"><a href="${devUrl}" target="_blank" class="link-plain" title="Abrir página web do dispositivo">${esc(d.ip)}</a></td>
            <td>${descCell}</td>
            <td class="col-tipo">${tipo}</td>
            <td class="col-sensores">${sensorBadges}</td>
            <td class="col-sql">${sqlBadges}</td>
            <td class="col-arduinos">${arduinosBadge}</td>
            <td class="col-acoes">
                <button class="btn btn-icon" title="Editar" onclick="openEditModal('${esc(d.ip)}','${escAttr(d.descricao===' -' ? '' : (d.descricao||''))}','${escAttr(d.tipo||'')}','${currentVlan}')"><i class="fas fa-edit"></i></button>
                ${isClp ? `<button class="btn btn-icon" title="Ver CLP" onclick="goToClp('${esc(d.ip)}')"><i class="fas fa-microchip"></i></button>` : ''}
            </td>
        </tr>`;
    }
    tbody.innerHTML = html;
}

function esc(s) { return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function escAttr(s) { return esc(s).replace(/'/g, '&#39;'); }

// Verde para qualquer device que está online no arduinos (visível em /arduinos/diagnostico)
// Mostra uptime no label como info adicional. Traço para offline ou não cadastrado.
function renderArduinosBadge(ip) {
    const entry = arduinosByIp[ip];
    if (!entry || !entry.is_online) return '<span class="muted">—</span>';
    const diag = entry.diag;
    if (!diag) {
        return `<span class="badge-arduinos ok" title="Online no arduinos (sem diagnóstico ainda)"><i class="fas fa-check-circle"></i> OK</span>`;
    }
    const upS = parseInt(diag.uptime, 10) || 0;
    const days = Math.floor(upS / 86400);
    const hrs = Math.floor((upS % 86400) / 3600);
    const label = days > 0 ? `${days}d ${hrs}h` : `${hrs}h`;
    return `<span class="badge-arduinos ok" title="Online no arduinos · uptime ${label}"><i class="fas fa-check-circle"></i> ${label}</span>`;
}

// Mapeia "tipo" → classe CSS de cor (categoria de equipamento)
function tipoColorClass(tipo) {
    if (!tipo) return 'tipo-default';
    const t = tipo.toLowerCase();
    if (t.includes('sensor') || t.includes('esp32') || t.includes('iot') || t.includes('estação meteo')) return 'tipo-sensor';
    if (t.includes('clp') || t.includes('compactlogix') || t.includes('micrologix') || t.includes('point i/o')) return 'tipo-clp';
    if (t.includes('alarme') || t.includes('incêndio') || t.includes('cerca')) return 'tipo-alarme';
    if (t.includes('gerador') || t.includes('gmg') || t.includes('ups') || t.includes('no-break') || t.includes('qta')) return 'tipo-gerador';
    if (t.includes('inversor') || t.includes('servo') || t.includes('kinetix')) return 'tipo-inversor';
    if (t.includes('ihm') || t.includes('panelview') || t.includes('display')) return 'tipo-ihm';
    if (t.includes('cancela') || t.includes('portão') || t.includes('rfid')) return 'tipo-acesso';
    if (t.includes('solar') || t.includes('hoymiles') || t.includes('microinversor')) return 'tipo-solar';
    if (t.includes('conversor') || t.includes('lantronix') || t.includes('serial')) return 'tipo-conversor';
    if (t.includes('iluminação') || t.includes('decodificador')) return 'tipo-iluminacao';
    if (t.includes('cubículo') || t.includes('blindado') || t.includes('subestação')) return 'tipo-cubiculo';
    if (t.includes('meteorol')) return 'tipo-meteo';
    return 'tipo-default';
}

function goToClp(ip) {
    const basePath = window.location.pathname.includes(APP_CONFIG.routesPrefix + '/')
        ? APP_CONFIG.routesPrefix + '/' : '/';
    window.location.href = basePath + 'clp/' + ip;
}

function showSqlDetails(ip) {
    const dev = currentDevicesData.find(d => d.ip === ip);
    if (!dev) return;
    document.getElementById('details-title').innerHTML = `<i class="fas fa-info-circle"></i> ${esc(ip)} — ${esc(dev.descricao || '')}`;
    let html = '';
    if ((dev.sensores || []).length) {
        html += '<h3>Sensores</h3><div class="chip-row">';
        dev.sensores.forEach(s => {
            html += `<span class="badge badge-sensor">${esc(s.codigo||s)}<small>${esc(s.categoria||'')}</small></span>`;
        });
        html += '</div>';
    }
    if ((dev.tabelas_sql || []).length) {
        html += '<h3>Tabelas SQL</h3><table class="details-sql-table"><thead><tr><th>Tabela</th><th>Coluna</th></tr></thead><tbody>';
        dev.tabelas_sql.forEach(t => {
            html += `<tr><td class="mono">${esc(t.tabela)}</td><td class="mono">${esc(t.coluna || '—')}</td></tr>`;
        });
        html += '</tbody></table>';
    }
    if (!html) html = '<p>Sem informações de sensor ou SQL.</p>';
    document.getElementById('details-body').innerHTML = html;
    document.getElementById('detailsModal').style.display = 'block';
}
function closeDetailsModal() { document.getElementById('detailsModal').style.display = 'none'; }

let currentEditDevice = null;

function openEditModal(ip, descricao, tipo, vlan) {
    document.getElementById('edit-ip').value = ip;
    document.getElementById('edit-descricao').value = descricao;
    document.getElementById('edit-tipo').value = tipo || '';

    // Preencher textareas de sensores e tabelas SQL a partir dos dados em memória
    const dev = currentDevicesData.find(x => x.ip === ip);
    const sensores = (dev && dev.sensores) ? dev.sensores : [];
    const tabelas = (dev && dev.tabelas_sql) ? dev.tabelas_sql : [];

    // Editor estruturado (uma linha por sensor/tabela) — substitui os textareas
    popularDatalistsEditor();
    const sCont = document.getElementById('edit-sensores-rows');
    sCont.innerHTML = '';
    sensores.forEach(s => addSensorRow(s.codigo, s.categoria));
    const tCont = document.getElementById('edit-tabelas-rows');
    tCont.innerHTML = '';
    tabelas.forEach(t => addTabelaRow(t.tabela, t.coluna));

    currentEditDevice = { ip, vlan };
    loadDeviceTypes(vlan);
    carregarFotosDispositivo(ip);
    document.getElementById('editModal').style.display = 'block';
    setTimeout(() => document.getElementById('edit-descricao').focus(), 100);
}

// ---- Fotos do local de instalação (até 5 por dispositivo, salvas no servidor) ----
async function carregarFotosDispositivo(ip) {
    const galeria = document.getElementById('edit-fotos-galeria');
    const addBtn = document.getElementById('edit-foto-add');
    if (!galeria) return;
    galeria.innerHTML = '<span class="muted">carregando…</span>';
    try {
        const baseUrl = getApiBaseUrl();
        const r = await fetch(`${baseUrl}/api/devices/${ip}/fotos`);
        const d = await r.json();
        const fotos = (d && d.fotos) || [];
        const max = (d && d.max) || 5;
        if (!fotos.length) {
            galeria.innerHTML = '<span class="muted">Nenhuma foto ainda.</span>';
        } else {
            galeria.innerHTML = fotos.map(id => {
                const url = `${baseUrl}/api/devices/${ip}/fotos/${id}`;
                return `<div class="foto-thumb-wrap">
                    <img class="foto-thumb" src="${url}" alt="foto do local" title="Clique para ampliar" onclick="abrirFotoLightbox('${url}')">
                    <button type="button" class="foto-del" title="Remover foto" onclick="removerFotoDispositivo('${esc(ip)}','${esc(id)}')"><i class="fas fa-times"></i></button>
                </div>`;
            }).join('');
        }
        if (addBtn) addBtn.style.display = (fotos.length >= max) ? 'none' : '';
    } catch (e) {
        galeria.innerHTML = '<span class="muted">Falha ao carregar fotos.</span>';
    }
}

async function uploadFotoDispositivo(input) {
    const files = input.files;
    input.value = '';
    if (!files || !files.length || !currentEditDevice) return;
    const addBtn = document.getElementById('edit-foto-add');
    const htmlBtn = addBtn ? addBtn.innerHTML : '';
    if (addBtn) { addBtn.disabled = true; addBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Enviando…'; }
    try {
        const baseUrl = getApiBaseUrl();
        const fd = new FormData();
        for (const f of files) fd.append('foto', f);   // várias imagens e/ou um .zip
        const r = await fetch(`${baseUrl}/api/devices/${currentEditDevice.ip}/fotos`, { method: 'POST', body: fd });
        const d = await r.json();
        if (!r.ok || !d.success) {
            alert(d.error || 'Não foi possível enviar.');
        } else if (d.ignoradas) {
            alert(`${d.adicionadas} foto(s) adicionada(s). ${d.ignoradas} ignorada(s) (limite de ${d.max} por dispositivo ou formato inválido).`);
        }
    } catch (e) {
        alert('Falha de conexão ao enviar.');
    } finally {
        if (addBtn) { addBtn.disabled = false; addBtn.innerHTML = htmlBtn; }
    }
    carregarFotosDispositivo(currentEditDevice.ip);
}

async function removerFotoDispositivo(ip, id) {
    if (!confirm('Remover esta foto?')) return;
    try {
        const baseUrl = getApiBaseUrl();
        await fetch(`${baseUrl}/api/devices/${ip}/fotos/${id}`, { method: 'DELETE' });
    } catch (e) { /* ignora */ }
    carregarFotosDispositivo(ip);
}

function abrirFotoLightbox(url) {
    const lb = document.getElementById('fotoLightbox');
    const img = document.getElementById('fotoLightboxImg');
    if (!lb || !img) return;
    img.src = url;
    lb.style.display = 'flex';
}

// Parser dos textareas → arrays estruturados
function parseSensoresText(text) {
    return (text || '').split('\n')
        .map(l => l.trim())
        .filter(Boolean)
        .map(line => {
            const parts = line.split('|').map(s => s.trim());
            return { codigo: parts[0], categoria: parts[1] || '' };
        })
        .filter(s => s.codigo);
}

function parseTabelasSqlText(text) {
    return (text || '').split('\n')
        .map(l => l.trim())
        .filter(Boolean)
        .map(line => {
            const dot = line.indexOf('.');
            if (dot === -1) return { tabela: line, coluna: '' };
            return { tabela: line.slice(0, dot).trim(), coluna: line.slice(dot + 1).trim() };
        })
        .filter(t => t.tabela);
}

// ---- Editor estruturado de sensores e tabelas SQL (linhas com campos) ----
function addSensorRow(codigo, categoria) {
    const cont = document.getElementById('edit-sensores-rows');
    const div = document.createElement('div');
    div.className = 'editor-row';
    div.innerHTML =
        `<input class="row-cod" list="dl-sensor-cod" placeholder="Código (ex.: BME680)" value="${escAttr(codigo || '')}">` +
        `<input class="row-cat" list="dl-sensor-cat" placeholder="categoria (opcional)" value="${escAttr(categoria || '')}">` +
        `<button type="button" class="row-del" title="Remover" onclick="this.closest('.editor-row').remove()"><i class="fas fa-times"></i></button>`;
    cont.appendChild(div);
}

function addTabelaRow(tabela, coluna) {
    const cont = document.getElementById('edit-tabelas-rows');
    const div = document.createElement('div');
    div.className = 'editor-row';
    div.innerHTML =
        `<input class="row-tab" list="dl-tabela" placeholder="Tabela (ex.: ILUMINACAO)" value="${escAttr(tabela || '')}">` +
        `<input class="row-col" list="dl-coluna" placeholder="Coluna (ex.: BH1750_3P_A)" value="${escAttr(coluna || '')}">` +
        `<button type="button" class="row-del" title="Remover" onclick="this.closest('.editor-row').remove()"><i class="fas fa-times"></i></button>`;
    cont.appendChild(div);
}

function coletarSensores() {
    return Array.from(document.querySelectorAll('#edit-sensores-rows .editor-row')).map(r => ({
        codigo: r.querySelector('.row-cod').value.trim(),
        categoria: r.querySelector('.row-cat').value.trim()
    })).filter(s => s.codigo);
}

function coletarTabelas() {
    return Array.from(document.querySelectorAll('#edit-tabelas-rows .editor-row')).map(r => ({
        tabela: r.querySelector('.row-tab').value.trim(),
        coluna: r.querySelector('.row-col').value.trim()
    })).filter(t => t.tabela);
}

// Autocomplete: sugere códigos/categorias/tabelas/colunas já usados (sem inventar)
function popularDatalistsEditor() {
    const cods = new Set(), cats = new Set(), tabs = new Set(), cols = new Set();
    (currentDevicesData || []).forEach(d => {
        (d.sensores || []).forEach(s => { if (s.codigo) cods.add(s.codigo); if (s.categoria) cats.add(s.categoria); });
        (d.tabelas_sql || []).forEach(t => { if (t.tabela) tabs.add(t.tabela); if (t.coluna) cols.add(t.coluna); });
    });
    const fill = (id, set) => {
        const dl = document.getElementById(id);
        if (!dl) return;
        dl.innerHTML = '';
        Array.from(set).sort().forEach(v => { const o = document.createElement('option'); o.value = v; dl.appendChild(o); });
    };
    fill('dl-sensor-cod', cods); fill('dl-sensor-cat', cats);
    fill('dl-tabela', tabs); fill('dl-coluna', cols);
}

function closeEditModal() {
    document.getElementById('editModal').style.display = 'none';
    currentEditDevice = null;
}

async function loadDeviceTypes(vlan) {
    try {
        const baseUrl = getApiBaseUrl();
        const r = await fetch(`${baseUrl}/api/device-types/${vlan}`);
        if (!r.ok) return;
        const d = await r.json();
        const dl = document.getElementById('device-types');
        dl.innerHTML = '';
        (d.types || []).forEach(t => {
            const o = document.createElement('option');
            o.value = t;
            dl.appendChild(o);
        });
    } catch (e) { console.error(e); }
}

async function clearDeviceData() {
    if (!currentEditDevice) return;
    if (!confirm(`Apagar descrição, tipo, sensores e tabelas SQL de ${currentEditDevice.ip}?\n\nO IP continuará válido (com traços), pronto para preenchimento futuro.`)) return;

    const btn = document.querySelector('.btn-clear-data');
    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Limpando…';

    try {
        const baseUrl = getApiBaseUrl();
        const r = await fetch(`${baseUrl}/api/devices/${currentEditDevice.vlan}/${currentEditDevice.ip}`, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ descricao: '', tipo: '', sensores: [], tabelas_sql: [] })
        });
        const d = await r.json();
        if (r.ok && d.success) {
            const dev = currentDevicesData.find(x => x.ip === currentEditDevice.ip);
            if (dev) {
                dev.descricao = '';
                dev.tipo = '';
                dev.sensores = [];
                dev.tabelas_sql = [];
            }
            populateFilters(currentDevicesData);
            sortAndRender();
            closeEditModal();
            showToast('🗑️ Dados removidos');
        } else {
            alert('Erro: ' + (d.error || ''));
        }
    } catch (e) {
        alert('Erro: ' + e.message);
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="fas fa-eraser"></i> Limpar dados';
    }
}

async function saveDevice() {
    if (!currentEditDevice) return;
    const descricao = document.getElementById('edit-descricao').value.trim();
    const tipo = document.getElementById('edit-tipo').value.trim();
    const sensores = coletarSensores();
    const tabelas_sql = coletarTabelas();
    if (!descricao) { alert('Descrição obrigatória'); return; }

    const btn = document.querySelector('.btn-save');
    btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Salvando...';

    try {
        const baseUrl = getApiBaseUrl();
        const r = await fetch(`${baseUrl}/api/devices/${currentEditDevice.vlan}/${currentEditDevice.ip}`, {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ descricao, tipo, sensores, tabelas_sql })
        });
        const d = await r.json();
        if (r.ok && d.success) {
            const dev = currentDevicesData.find(x => x.ip === currentEditDevice.ip);
            if (dev) {
                dev.descricao = descricao;
                dev.tipo = tipo;
                dev.sensores = sensores;
                dev.tabelas_sql = tabelas_sql;
            }
            populateFilters(currentDevicesData);
            sortAndRender();
            closeEditModal();
            showToast('✅ Atualizado');
        } else {
            alert('Erro: ' + (d.error || ''));
        }
    } catch (e) {
        alert('Erro: ' + e.message);
    } finally {
        btn.disabled = false; btn.innerHTML = '<i class="fas fa-save"></i> Salvar';
    }
}

window.onload = function() {
    document.getElementById('filtroVLAN').value = '86';
    updateGateway('86');
    searchByVlan();
};

setInterval(searchByVlan, 20000);

document.addEventListener('DOMContentLoaded', function() {
    const editClose = document.querySelector('#editModal .close');
    if (editClose) editClose.onclick = closeEditModal;
    window.onclick = function(e) {
        const editM = document.getElementById('editModal');
        const detM = document.getElementById('detailsModal');
        const addM = document.getElementById('addDeviceModal');
        const typesM = document.getElementById('typesModal');
        if (e.target === editM) closeEditModal();
        if (e.target === detM) closeDetailsModal();
        if (e.target === addM) closeAddDeviceModal();
        if (e.target === typesM) closeTypesModal();
    };
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            closeEditModal();
            closeDetailsModal();
            closeAddDeviceModal();
            closeTypesModal();
        }
        if (e.key === 'Enter') {
            const m = document.getElementById('editModal');
            if (m.style.display === 'block' && document.activeElement.id !== 'edit-tipo') saveDevice();
            const addM = document.getElementById('addDeviceModal');
            if (addM && addM.style.display === 'block') addDevice();
        }
    });
});

// ============================================================
// CRUD: Adicionar Dispositivo + Gerenciar Tipos (VLAN atual)
// ============================================================

let deviceTypesCache = [];

function openAddDeviceModal() {
    if (!currentVlan) {
        showToast('Selecione uma VLAN primeiro');
        return;
    }
    document.getElementById('add-ip').value = '172.17.' + currentVlan + '.';
    document.getElementById('add-descricao').value = '';
    document.getElementById('add-tipo').value = '';
    document.getElementById('addDeviceModal').style.display = 'block';
    setTimeout(() => document.getElementById('add-ip').focus(), 50);
}

function closeAddDeviceModal() {
    document.getElementById('addDeviceModal').style.display = 'none';
}

async function addDevice() {
    const ip = document.getElementById('add-ip').value.trim();
    const descricao = document.getElementById('add-descricao').value.trim();
    const tipo = document.getElementById('add-tipo').value.trim();

    if (!/^(\d{1,3}\.){3}\d{1,3}$/.test(ip)) {
        showToast('IP inválido. Use o formato 172.17.X.Y');
        return;
    }
    try {
        const baseUrl = getApiBaseUrl();
        const r = await fetch(`${baseUrl}/api/devices/${currentVlan}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ip, descricao, tipo })
        });
        if (!r.ok) {
            const err = await r.json().catch(() => ({}));
            throw new Error(err.error || `HTTP ${r.status}`);
        }
        closeAddDeviceModal();
        showToast(`Dispositivo ${ip} adicionado`);
        searchByVlan();
    } catch (e) {
        showToast('Erro ao adicionar: ' + e.message, 3500);
    }
}

async function openManageTypesModal() {
    if (!currentVlan) {
        showToast('Selecione uma VLAN primeiro');
        return;
    }
    document.getElementById('types-vlan-label').textContent = `(VLAN ${currentVlan})`;
    document.getElementById('new-type-input').value = '';
    document.getElementById('typesModal').style.display = 'block';
    await refreshTypesList();
}

function closeTypesModal() {
    document.getElementById('typesModal').style.display = 'none';
}

async function refreshTypesList() {
    const list = document.getElementById('types-list');
    list.innerHTML = '<span style="color:var(--text-light); font-size:0.85rem;">Carregando…</span>';
    try {
        const baseUrl = getApiBaseUrl();
        const r = await fetch(`${baseUrl}/api/device-types/${currentVlan}`);
        if (!r.ok) throw new Error('HTTP ' + r.status);
        const data = await r.json();
        deviceTypesCache = data.types || [];

        // Quais tipos estão em uso? Compara com currentDevicesData da VLAN atual
        const emUso = new Set((currentDevicesData || []).map(d => d.tipo).filter(Boolean));

        if (!deviceTypesCache.length) {
            list.innerHTML = '<span style="color:var(--text-light); font-size:0.85rem;"><em>Nenhum tipo cadastrado nesta VLAN.</em></span>';
            return;
        }

        list.innerHTML = deviceTypesCache.map(tipo => {
            const used = emUso.has(tipo);
            const bg = used ? '#d1fae5' : '#e5e7eb';
            const color = used ? '#065f46' : '#374151';
            const border = used ? '#a7f3d0' : '#d1d5db';
            return `
                <span class="badge" data-tipo="${escAttr(tipo)}" style="display:inline-flex; align-items:center; gap:0.4rem; background:${bg}; color:${color}; border:1px solid ${border}; padding:0.35rem 0.65rem; border-radius:14px; font-size:0.82rem;">
                    ${esc(tipo)}
                    <button onclick="removeType('${escAttr(tipo)}', ${used})"
                            title="Remover tipo"
                            style="background:transparent; border:0; cursor:pointer; color:inherit; padding:0; font-size:1rem; line-height:1;">&times;</button>
                </span>
            `;
        }).join('');
    } catch (e) {
        list.innerHTML = `<span style="color:var(--danger); font-size:0.85rem;">Erro: ${esc(e.message)}</span>`;
    }
}

async function addNewType() {
    const input = document.getElementById('new-type-input');
    const tipo = input.value.trim();
    if (!tipo) {
        showToast('Digite o nome do tipo');
        return;
    }
    if (deviceTypesCache.includes(tipo)) {
        showToast('Este tipo já existe');
        return;
    }
    try {
        const baseUrl = getApiBaseUrl();
        const r = await fetch(`${baseUrl}/api/device-types/${currentVlan}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type: tipo })
        });
        if (!r.ok) {
            const err = await r.json().catch(() => ({}));
            throw new Error(err.error || `HTTP ${r.status}`);
        }
        input.value = '';
        showToast(`Tipo "${tipo}" adicionado`);
        await refreshTypesList();
        // Atualiza datalist de tipos (compartilhado com editar/adicionar)
        searchByVlan();
    } catch (e) {
        showToast('Erro: ' + e.message, 3500);
    }
}

async function removeType(tipo, emUso) {
    const msg = emUso
        ? `O tipo "${tipo}" está em uso. Remover assim mesmo? Os dispositivos ficarão sem tipo.`
        : `Remover o tipo "${tipo}"?`;
    if (!confirm(msg)) return;
    try {
        const baseUrl = getApiBaseUrl();
        const r = await fetch(`${baseUrl}/api/device-types/${currentVlan}`, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type: tipo })
        });
        if (!r.ok) {
            const err = await r.json().catch(() => ({}));
            throw new Error(err.error || `HTTP ${r.status}`);
        }
        showToast(`Tipo "${tipo}" removido`);
        await refreshTypesList();
        if (emUso) searchByVlan();
    } catch (e) {
        showToast('Erro: ' + e.message, 3500);
    }
}

function showToast(msg, dur = 2500) {
    const old = document.querySelector('.toast-notification');
    if (old) old.remove();
    const t = document.createElement('div');
    t.className = 'toast-notification';
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), dur);
}
