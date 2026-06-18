"""
Cliente fino para o núcleo de notificações do HelpDesk Monitor (Onda 3)
=======================================================================

Emite notificações de *transição* de estado de host (UP→DOWN / DOWN→UP)
detectadas pelo monitor de IPs para a API unificada do helpdesk-monitor:

    POST {base}/notificacoes   (roteamento central: push/whatsapp/email)

Diferente de disparar WhatsApp direto, este cliente PRODUZ uma notificação
na matriz de responsabilidades do helpdesk-monitor — o helpdesk decide
quem recebe (por categoria/técnico/grupo/área) e por quais canais.

Design (padrão de pred-analytics `app/utils/notificacoes_unificadas.py`,
mas sobre `urllib` stdlib — o ip-monitor não embarca `requests`, e o build
Docker não alcança o PyPI; os decorators R/W/A do projeto usam a mesma
abordagem urllib+ProxyHandler({}) por isso):
    - **Best-effort**: NUNCA levanta exceção para o chamador; timeout curto.
      Uma falha do núcleo JAMAIS pode derrubar o monitoramento de IPs.
    - Inter-container: opener com `ProxyHandler({})` (ignora HTTP_PROXY /
      proxy-hub — equivalente ao `trust_env=False` do requests).
    - Credencial reaproveitada das envs já existentes no serviço:
        HELPDESK_API_BASE_URL  (ex.: http://helpdesk-monitor:5000/helpdeskmonitor/api)
        WHATSAPP_API_TOKEN     (Bearer compartilhado do helpdesk)
      Overrides opcionais: HELPDESK_NOTIF_URL e HELPDESK_NOTIF_TOKEN.
      NENHUM segredo é hardcoded.
    - Default explícito de canais: ['whatsapp'].

Anti-flood: o chamador só invoca isto na BORDA de transição (mudança de
estado), nunca a cada verificação, e passa um `dedup_key`.
"""

import os
import json
import logging
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional

logger = logging.getLogger("NotificacoesHelpdesk")

PRIORIDADES_VALIDAS = ("info", "aviso", "alarme", "emergencia")

# Origem identificadora deste produtor no núcleo.
ORIGEM = "ip-monitor"


def _resolver_url() -> Optional[str]:
    """URL completa de POST /notificacoes (best-effort, sem exceção)."""
    url = os.getenv("HELPDESK_NOTIF_URL")
    if url:
        return url.rstrip("/")
    base = os.getenv("HELPDESK_API_BASE_URL")
    if base:
        # base já termina em '/helpdeskmonitor/api'
        return f"{base.rstrip('/')}/notificacoes"
    # Fallback inter-container (DNS docker). Sem segredo embutido.
    return "http://helpdesk-monitor:5000/helpdeskmonitor/api/notificacoes"


def _resolver_token() -> Optional[str]:
    return os.getenv("HELPDESK_NOTIF_TOKEN") or os.getenv("WHATSAPP_API_TOKEN")


# Opener sem proxy (ignora HTTP_PROXY do ambiente Docker — equivalente ao
# trust_env=False do requests). Inter-container não passa pelo proxy-hub.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def enviar_notificacao(
    titulo: str,
    corpo: str = "",
    *,
    categoria: str = "HOST_MONITOR",
    prioridade: str = "alarme",
    destinos: Optional[List[Dict[str, str]]] = None,
    canais: Optional[List[str]] = None,  # None → default explícito ['whatsapp']
    dedup_key: Optional[str] = None,
    dados: Optional[Dict[str, Any]] = None,
    emergencial: bool = False,
    ttl_seg: Optional[int] = None,
    timeout: float = 6.0,
) -> Dict[str, Any]:
    """Emite uma notificação no núcleo unificado. Best-effort.

    Args:
        titulo: Título curto (obrigatório).
        corpo: Texto descritivo.
        categoria: categoria de roteamento no helpdesk (default HOST_MONITOR).
        prioridade: info|aviso|alarme|emergencia (default 'alarme').
        destinos: lista de {'tipo':'tecnico'|'grupo'|'competencia'|'area_operacional',
                  'valor': <chave>}. Se None/[], o helpdesk roteia pela CATEGORIA.
        canais: ['push','whatsapp','email'] (None/[] → default ['whatsapp']).
        dedup_key: chave de deduplicação (anti-flood enquanto a condição
                   persiste). Ex.: 'hostdown:<ip>:<YYYYMMDD>'.
        dados: dict de payload extra.
        emergencial: entrega imediata (ignora jornada).
        ttl_seg: expira a notificação após N segundos.
        timeout: timeout HTTP curto (best-effort).

    Returns:
        Dict best-effort: {'ok': bool, 'id': int|None, 'entregas': int,
                           'status': int|None, 'erro': str|None}.
        NUNCA levanta exceção.
    """
    titulo = (titulo or "").strip()
    if not titulo:
        logger.warning("[notif-helpdesk] título vazio — notificação ignorada")
        return {"ok": False, "id": None, "entregas": 0, "status": None,
                "erro": "titulo vazio"}

    if prioridade not in PRIORIDADES_VALIDAS:
        prioridade = "alarme"

    # Default explícito de canais (Onda 2/3): whatsapp.
    if not canais:
        canais = ["whatsapp"]

    url = _resolver_url()
    token = _resolver_token()
    if not url or not token:
        logger.warning(
            "[notif-helpdesk] URL ou token ausente (HELPDESK_API_BASE_URL/"
            "WHATSAPP_API_TOKEN) — notificação não enviada")
        return {"ok": False, "id": None, "entregas": 0, "status": None,
                "erro": "config ausente"}

    payload: Dict[str, Any] = {
        "categoria": categoria,
        "titulo": titulo,
        "corpo": corpo or "",
        "prioridade": prioridade,
        "emergencial": bool(emergencial),
        "origem": ORIGEM,
        "canais": canais,
    }
    if destinos:
        payload["destinos"] = destinos
    if ttl_seg:
        payload["ttl_seg"] = int(ttl_seg)
    if dedup_key:
        payload["dedup_key"] = dedup_key
    if dados:
        payload["dados"] = dados

    body_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body_bytes, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("X-Origem", ORIGEM)

    try:
        with _opener.open(req, timeout=timeout) as resp:
            status = resp.getcode()
            raw = resp.read().decode("utf-8", "replace")
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {}
        notif_id = data.get("id")
        entregas = data.get("entregas", 0) or 0
        logger.info(
            f"[notif-helpdesk] enviada cat={categoria} prio={prioridade} "
            f"id={notif_id} entregas={entregas} dedup={dedup_key}")
        return {"ok": True, "id": notif_id, "entregas": entregas,
                "status": status, "erro": None}
    except urllib.error.HTTPError as e:
        try:
            texto = e.read().decode("utf-8", "replace")[:200]
        except Exception:  # noqa: BLE001
            texto = ""
        logger.warning(
            f"[notif-helpdesk] status {e.code} ao notificar "
            f"cat={categoria}: {texto}")
        return {"ok": False, "id": None, "entregas": 0, "status": e.code,
                "erro": f"http {e.code}"}
    except urllib.error.URLError as e:
        # Best-effort: loga e segue. Não propaga.
        logger.warning(f"[notif-helpdesk] falha de rede ao notificar: {e}")
        return {"ok": False, "id": None, "entregas": 0, "status": None,
                "erro": str(e)}
    except Exception as e:  # noqa: BLE001 — blindagem total
        logger.error(f"[notif-helpdesk] erro inesperado ao notificar: {e}")
        return {"ok": False, "id": None, "entregas": 0, "status": None,
                "erro": str(e)}
