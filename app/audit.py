"""Trilha de auditoria de modificações em ip_devices.json.

Sem migrar o storage primário — o JSON continua sendo fonte da verdade.
Esse módulo grava cada add/update/remove num SQLite local pra preservar
histórico (quem · quando · de quê · para quê) e permitir rollback manual.

Uso típico no app:
    from app.audit import get_audit, record_diff
    audit = get_audit()
    # ANTES de salvar mudanças:
    record_diff(old_devices, new_devices, user='admin', source='api/devices/86')
"""
import json
import os
import sqlite3
import threading
from datetime import datetime
from typing import Optional, List, Dict, Any

from app.migration import get_data_file_path

_DB_PATH = None
_lock = threading.RLock()


def _db_file() -> str:
    global _DB_PATH
    if _DB_PATH is None:
        _DB_PATH = get_data_file_path('audit.db')
    return _DB_PATH


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_db_file(), timeout=10, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('PRAGMA foreign_keys=ON')
    return c


def init_schema() -> None:
    """Cria a tabela e índices na primeira execução. Idempotente."""
    with _lock, _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS device_audit (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT    NOT NULL,
                action      TEXT    NOT NULL CHECK(action IN ('add','update','remove')),
                vlan        TEXT    NOT NULL,
                ip          TEXT    NOT NULL,
                before_json TEXT,           -- estado antes (NULL p/ add)
                after_json  TEXT,           -- estado depois (NULL p/ remove)
                fields      TEXT,           -- lista CSV dos campos alterados (só update)
                user        TEXT,
                source      TEXT            -- ex.: 'api/devices/86', 'cli', 'sync'
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS ix_audit_ts   ON device_audit (timestamp DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_audit_ip   ON device_audit (ip)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_audit_vlan ON device_audit (vlan)")


def get_audit() -> sqlite3.Connection:
    """Conexão para queries. Cada chamador fecha quando termina."""
    return _conn()


# ----------------------------- gravação --------------------------------------

def _record(action: str, vlan: str, ip: str,
            before: Optional[Dict], after: Optional[Dict],
            fields: Optional[List[str]] = None,
            user: Optional[str] = None, source: Optional[str] = None) -> None:
    with _lock, _conn() as c:
        c.execute("""
            INSERT INTO device_audit
                (timestamp, action, vlan, ip, before_json, after_json, fields, user, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now().isoformat(timespec='seconds'),
            action, str(vlan), ip,
            json.dumps(before, ensure_ascii=False) if before is not None else None,
            json.dumps(after,  ensure_ascii=False) if after  is not None else None,
            ','.join(fields) if fields else None,
            user, source,
        ))


def record_diff(old_data: Dict, new_data: Dict,
                user: Optional[str] = None, source: Optional[str] = None) -> int:
    """Compara duas estruturas {'vlans': {'86': [...], ...}} e grava cada
    diferença (add/update/remove) como uma linha. Retorna nº de entradas gravadas.

    `update` é detectado quando o mesmo IP tem campos com valores diferentes
    (ignorando created_at/updated_at).
    """
    init_schema()
    IGNORE = {'created_at', 'updated_at'}
    n = 0
    old_vlans = (old_data or {}).get('vlans', {}) or {}
    new_vlans = (new_data or {}).get('vlans', {}) or {}
    all_vlans = set(old_vlans.keys()) | set(new_vlans.keys())

    for vlan in all_vlans:
        old_by_ip = {d.get('ip'): d for d in (old_vlans.get(vlan) or []) if d.get('ip')}
        new_by_ip = {d.get('ip'): d for d in (new_vlans.get(vlan) or []) if d.get('ip')}
        all_ips = set(old_by_ip.keys()) | set(new_by_ip.keys())
        for ip in all_ips:
            a, b = old_by_ip.get(ip), new_by_ip.get(ip)
            if a is None and b is not None:
                _record('add', vlan, ip, None, b, None, user, source); n += 1
            elif b is None and a is not None:
                _record('remove', vlan, ip, a, None, None, user, source); n += 1
            else:
                # compara campos relevantes
                changed = []
                keys = set((a or {}).keys()) | set((b or {}).keys())
                for k in keys:
                    if k in IGNORE: continue
                    if (a or {}).get(k) != (b or {}).get(k):
                        changed.append(k)
                if changed:
                    _record('update', vlan, ip, a, b, sorted(changed), user, source); n += 1
    return n


# ----------------------------- consulta --------------------------------------

def list_entries(limit: int = 200, offset: int = 0,
                 vlan: Optional[str] = None, ip: Optional[str] = None,
                 action: Optional[str] = None,
                 since: Optional[str] = None) -> List[Dict[str, Any]]:
    """Lista entradas mais recentes primeiro, com filtros opcionais."""
    init_schema()
    sql = "SELECT * FROM device_audit WHERE 1=1"
    args: List[Any] = []
    if vlan:   sql += " AND vlan = ?";   args.append(str(vlan))
    if ip:     sql += " AND ip = ?";     args.append(ip)
    if action: sql += " AND action = ?"; args.append(action)
    if since:  sql += " AND timestamp >= ?"; args.append(since)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    args.extend([int(limit), int(offset)])
    with _conn() as c:
        rows = c.execute(sql, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for jk in ('before_json', 'after_json'):
            if d.get(jk):
                try: d[jk] = json.loads(d[jk])
                except Exception: pass
        d['fields'] = d['fields'].split(',') if d.get('fields') else []
        out.append(d)
    return out


def count_entries(vlan: Optional[str] = None, ip: Optional[str] = None,
                  action: Optional[str] = None,
                  since: Optional[str] = None) -> int:
    init_schema()
    sql = "SELECT COUNT(*) FROM device_audit WHERE 1=1"
    args: List[Any] = []
    if vlan:   sql += " AND vlan = ?";   args.append(str(vlan))
    if ip:     sql += " AND ip = ?";     args.append(ip)
    if action: sql += " AND action = ?"; args.append(action)
    if since:  sql += " AND timestamp >= ?"; args.append(since)
    with _conn() as c:
        return int(c.execute(sql, args).fetchone()[0])


def stats(days: int = 7) -> Dict[str, Any]:
    """Resumo das últimas N dias: total + por ação."""
    init_schema()
    since = (datetime.now().replace(microsecond=0)).isoformat()  # fallback
    from datetime import timedelta
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec='seconds')
    with _conn() as c:
        total = int(c.execute(
            "SELECT COUNT(*) FROM device_audit WHERE timestamp >= ?",
            (since,)
        ).fetchone()[0])
        by_action = {r[0]: r[1] for r in c.execute(
            "SELECT action, COUNT(*) FROM device_audit WHERE timestamp >= ? GROUP BY action",
            (since,)
        ).fetchall()}
        last = c.execute(
            "SELECT timestamp, action, vlan, ip FROM device_audit ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return {
        'days': days, 'since': since,
        'total': total, 'by_action': by_action,
        'last': dict(last) if last else None,
    }
