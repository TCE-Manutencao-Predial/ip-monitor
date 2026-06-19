"""Autorização do ip-monitor via matriz R/W/A do helpdesk-monitor.

Grupo deste microserviço: `automacao` (movido de público → protegido em
2026-06-19). Padrão idêntico ao banco-precos/rfid: consulta o X-Remote-User
(injetado pelo nginx via auth_request) contra a matriz central; bypass de
HTTP_PROXY via opener; cache 5 min; fail-secure.

Aplicado por um `before_request` global (ver routes.py::_autz_guard), porque o
routes.py é um arquivo plano com ~30 @app.route (com e sem prefixo RAIZ) — um
hook central é mais seguro que decorar função por função.

Whitelist (sem exigir grupo), para NÃO quebrar quem já consome o serviço:
  • `GET /` (raiz local) — healthcheck do Docker (urlopen localhost:5000/).
  • `GET /ipmonitor/api/ip-status`       — consumido por infra-docs.
  • `GET /ipmonitor/api/devices/<vlan>`  — consumido por tcego-ia.
Esses são inter-container (chamados direto na :5000, sem passar pelo nginx →
sem X-Remote-User). A porta 5000 não é exposta ao host, então requisição sem
X-Remote-User só pode ser interna; qualquer outra sem identidade é negada.
"""
import json
import logging
import re
import urllib.request
from datetime import datetime, timedelta
from typing import Tuple

from flask import jsonify, request

from app.settings import ROUTES_PREFIX as RAIZ

logger = logging.getLogger('ip-monitor')

_GRUPO = 'automacao'
_HELPDESK_URL = 'http://helpdesk-monitor:5000/helpdeskmonitor/api/permissoes/usuario'
_HIERARQUIA = {'A': 3, 'W': 2, 'R': 1}
_NIVEIS_TTL = timedelta(minutes=5)
_cache: dict = {}
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# Endpoints liberados sem grupo (ver docstring). Casados por path exato/regex.
_RE_DEVICES = re.compile(r'^' + re.escape(RAIZ) + r'/api/devices/\d+$')


def _consultar_niveis(usuario: str) -> dict:
    if not usuario:
        return {'super_admin': False, 'niveis': {}}
    c = _cache.get(usuario)
    if c and datetime.now() - c['ts'] < _NIVEIS_TTL:
        return c
    try:
        req = urllib.request.Request(f'{_HELPDESK_URL}/{usuario}')
        with _opener.open(req, timeout=4) as r:
            if r.status == 200:
                d = json.loads(r.read())
                entrada = {'super_admin': bool(d.get('super_admin')),
                           'niveis':      d.get('niveis') or {},
                           'ts':          datetime.now()}
                _cache[usuario] = entrada
                return entrada
    except Exception as e:
        logger.warning(f'[autz] falha consultando permissoes p/ {usuario}: {e}')
    return {'super_admin': False, 'niveis': {}, 'ts': datetime.now()}


def _autorizado(usuario: str, nivel_min: str) -> Tuple[bool, dict]:
    dados = _consultar_niveis(usuario)
    if dados['super_admin']:
        return True, dados
    nivel_atual = (dados['niveis'] or {}).get(_GRUPO)
    if not nivel_atual:
        return False, dados
    return _HIERARQUIA.get(nivel_atual, 0) >= _HIERARQUIA.get(nivel_min, 999), dados


def _negar(usuario, nivel_min: str, atual=None):
    logger.warning(
        f'[autz] Negado {usuario or "(sem identidade)"} → {request.method} {request.path}: '
        f'nivel={atual!r} precisa={_GRUPO}_{nivel_min}'
    )
    return jsonify({
        'success': False,
        'erro': f'Sem permissão {_GRUPO}_{nivel_min}. Solicite acesso ao administrador.',
        'sistema': _GRUPO, 'nivel_min': nivel_min,
    }), 403


def _liberado_sem_grupo() -> bool:
    """True p/ healthcheck e os 2 GETs inter-container consumidos por outros sistemas."""
    p, m = request.path, request.method
    if p == '/':                       # healthcheck Docker (GET localhost:5000/)
        return True
    if m == 'GET' and (p == RAIZ + '/api/ip-status' or _RE_DEVICES.match(p)):
        return True
    return False


def guard():
    """before_request global. None = segue; resposta = bloqueia."""
    if request.method == 'OPTIONS' or request.endpoint == 'static':
        return None
    if _liberado_sem_grupo():
        return None
    usuario = (request.headers.get('X-Remote-User') or '').strip().lower() or None
    if usuario is None:
        # Sem identidade e fora da whitelist → nega (porta 5000 não é exposta;
        # tráfego externo sempre chega pelo nginx com X-Remote-User).
        return _negar(None, 'R')
    nivel = 'R' if request.method in ('GET', 'HEAD') else 'W'
    ok, dados = _autorizado(usuario, nivel)
    if ok:
        return None
    return _negar(usuario, nivel, (dados.get('niveis') or {}).get(_GRUPO))
