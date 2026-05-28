// Popula o submenu "CLPs" do header com a lista de CLPs cadastrados.
// Funciona em todas as páginas que tiverem um elemento <ul id="clps-submenu">.

(function () {
    function getApiBase() {
        if (typeof APP_CONFIG === 'undefined') return '';
        return window.location.hostname.includes(APP_CONFIG.domainBase)
            ? APP_CONFIG.routesPrefix : '';
    }

    function getBasePath() {
        if (typeof APP_CONFIG === 'undefined') return '/';
        return window.location.pathname.includes(APP_CONFIG.routesPrefix + '/')
            ? APP_CONFIG.routesPrefix + '/' : '/';
    }

    function ipSort(a, b) {
        var pa = a.split('.').map(Number);
        var pb = b.split('.').map(Number);
        for (var i = 0; i < 4; i++) {
            if (pa[i] !== pb[i]) return pa[i] - pb[i];
        }
        return 0;
    }

    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function popular() {
        var menu = document.getElementById('clps-submenu');
        if (!menu) return;

        var basePath = getBasePath();
        menu.innerHTML = '<li class="nav-submenu-loading">Carregando…</li>';

        fetch(getApiBase() + '/api/clp')
            .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
            .then(function (clps) {
                var ips = Object.keys(clps).sort(ipSort);
                if (!ips.length) {
                    menu.innerHTML = '<li class="nav-submenu-empty">Sem CLPs cadastrados.</li>';
                    return;
                }
                var html = '<li class="nav-submenu-section"><a href="' + basePath + 'clps">' +
                    '<i class="fas fa-list"></i> Lista completa</a></li>' +
                    '<li class="nav-submenu-divider"></li>';
                html += ips.map(function (ip) {
                    var c = clps[ip] || {};
                    var titulo = (c.titulo || ('CLP ' + ip)).replace(/^Automação\s*-\s*/i, '');
                    return '<li><a href="' + basePath + 'clp/' + ip + '">' +
                        '<span class="nav-submenu-ip">.' + escapeHtml(ip.split('.').pop()) + '</span>' +
                        '<span class="nav-submenu-name">' + escapeHtml(titulo) + '</span>' +
                        '</a></li>';
                }).join('');
                menu.innerHTML = html;
            })
            .catch(function () {
                menu.innerHTML = '<li class="nav-submenu-empty">Erro ao carregar.</li>';
            });
    }

    // Carga lazy: popula só na primeira vez que o cursor entra no dropdown.
    document.addEventListener('DOMContentLoaded', function () {
        var trigger = document.querySelector('.nav-item-dropdown');
        if (!trigger) return;
        var loaded = false;
        function loadOnce() {
            if (loaded) return;
            loaded = true;
            popular();
        }
        trigger.addEventListener('mouseenter', loadOnce);
        trigger.addEventListener('focusin', loadOnce);
        // Toggle por clique também (mobile / acessibilidade)
        var link = trigger.querySelector('a[data-dropdown-toggle]');
        if (link) {
            link.addEventListener('click', function (e) {
                if (window.matchMedia('(hover: none)').matches) {
                    e.preventDefault();
                    loadOnce();
                    trigger.classList.toggle('is-open');
                }
            });
        }
    });
})();
