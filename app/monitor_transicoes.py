"""
Monitor de hosts OFFLINE → digest SEMANAL por e-mail (modelo 24h/semana)
=======================================================================

Modelo (decidido pelo usuário, jun/2026):
    - **Canal: e-mail apenas** (sem WhatsApp).
    - **Limiar 24h contínuas**: um host só é "reportável" quando está offline
      há MAIS de 24h ininterruptas. Rastreamos `offline_desde` (timestamp da
      queda) por IP no estado persistido.
    - **Acumulativo, 1×/semana**: em vez de uma notificação por transição,
      montamos **UM e-mail digest** listando TODOS os hosts atualmente
      offline >24h (nome/ip + há quanto tempo). No máximo 1 e-mail por semana
      (rate-limit por timestamp persistido + `dedup_key` semanal).
    - **Sem disparo imediato** UP→DOWN/DOWN→UP, **sem WhatsApp**, **sem
      notificação de recuperação** — o digest reflete o estado atual.
    - Se nenhum host estiver offline >24h naquela semana, NÃO envia nada.

O monitor de IPs (`ip_operations.verificar_ips`) é STATELESS: a cada ciclo
devolve a lista completa de IPs com status "on"/"off". Aqui mantemos, por IP
registrado, o status atual e (quando off) o instante `offline_desde` em que a
queda começou. Ao recuperar (off→on), zeramos `offline_desde`. Assim, a
duração contínua de offline é `agora - offline_desde`.

Características:
    - **Só dispositivos REGISTRADOS** (com descrição/tipo): IPs sem cadastro
      (descricao '-' / vazia) são dark hosts da varredura /1-254 — ignorados.
    - **Estado persistido** em JSON no volume (DATA_ROOT) — sobrevive a
      restart/rebuild. Ao semear pela 1ª vez NÃO inventa `offline_desde`:
      só registra a queda quando REALMENTE observa o host off.
    - **Best-effort**: qualquer falha aqui é capturada e logada; jamais
      derruba o loop de monitoramento.

Destinos: CATEGORIA 'HOST_MONITOR' no helpdesk, com fallback explícito para o
técnico responsável de infra/rede (`pedro`).
"""

import os
import json
import logging
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from app.settings import PROJECT_DATA, VLANS
from app import notificacoes_helpdesk as notif

logger = logging.getLogger("MonitorTransicoes")

# Categoria de roteamento no núcleo do helpdesk.
CATEGORIA = "HOST_MONITOR"

# Destino-fallback (responsável de infra/rede). O helpdesk também roteia pela
# CATEGORIA; este destino garante entrega mesmo sem responsável de categoria.
DESTINOS_PADRAO = [{"tipo": "tecnico", "valor": "pedro"}]

# Limiar de offline contínuo (em horas) para um host entrar no digest.
LIMIAR_HORAS = int(os.environ.get("OFFLINE_LIMIAR_HORAS", "24"))

# Janela mínima entre dois digests (em dias). Semanal = 7.
DIGEST_INTERVALO_DIAS = int(os.environ.get("OFFLINE_DIGEST_INTERVALO_DIAS", "7"))

# Arquivo de estado persistido. Esquema novo (por IP):
#   { ip: {"status": "on"|"off", "offline_desde": "<iso8601>"|null} }
# Compatível com o esquema antigo ({ip: "on"|"off"}), migrado ao carregar.
_ESTADO_PATH = os.path.join(PROJECT_DATA, "estado_hosts.json")
# Timestamp ISO do último digest enviado (rate-limit semanal).
_DIGEST_META_PATH = os.path.join(PROJECT_DATA, "offline_digest_meta.json")

# Mapa em memória. Carregado do disco na 1ª chamada.
_estado: Optional[Dict[str, dict]] = None
_lock = threading.Lock()

_ISO = "%Y-%m-%dT%H:%M:%S"


def _agora() -> datetime:
    return datetime.now()


def _carregar_estado() -> Dict[str, dict]:
    """Carrega o estado persistido (best-effort). Migra o esquema antigo
    ({ip: 'on'|'off'}) para o novo ({ip: {status, offline_desde}}) sem
    inventar `offline_desde` (só sabemos a hora da queda quando a observamos)."""
    try:
        if os.path.exists(_ESTADO_PATH):
            with open(_ESTADO_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                out: Dict[str, dict] = {}
                for k, v in data.items():
                    if isinstance(v, dict):
                        out[str(k)] = {
                            "status": str(v.get("status", "on")),
                            "offline_desde": v.get("offline_desde") or None,
                            "label": v.get("label") or "",
                        }
                    else:
                        # Esquema antigo: só o status. offline_desde desconhecido.
                        out[str(k)] = {"status": str(v), "offline_desde": None,
                                       "label": ""}
                return out
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[offline] falha ao carregar estado: {e}")
    return {}


def _salvar_estado(estado: Dict[str, dict]) -> None:
    """Persiste o estado (best-effort, escrita atômica)."""
    try:
        os.makedirs(os.path.dirname(_ESTADO_PATH), exist_ok=True)
        tmp = _ESTADO_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(estado, f, ensure_ascii=False)
        os.replace(tmp, _ESTADO_PATH)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[offline] falha ao salvar estado: {e}")


def _carregar_digest_meta() -> dict:
    try:
        if os.path.exists(_DIGEST_META_PATH):
            with open(_DIGEST_META_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                return d
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[offline] falha ao carregar digest meta: {e}")
    return {}


def _salvar_digest_meta(meta: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_DIGEST_META_PATH), exist_ok=True)
        tmp = _DIGEST_META_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
        os.replace(tmp, _DIGEST_META_PATH)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[offline] falha ao salvar digest meta: {e}")


def _is_registrado(item: dict) -> bool:
    """True só para dispositivos com cadastro (descrição/tipo) — ignora os
    dark hosts da varredura completa /1-254."""
    desc = (item.get("descricao") or "").strip()
    tipo = (item.get("tipo") or "").strip()
    return bool(tipo) or (bool(desc) and desc != "-")


def _rotulo(item: dict) -> str:
    """Nome amigável: 'descrição (ip)' quando há descrição; senão só o ip."""
    ip = item.get("ip", "?")
    desc = (item.get("descricao") or "").strip()
    if desc and desc != "-":
        return f"{desc} ({ip})"
    return ip


def _fmt_duracao(delta: timedelta) -> str:
    segs = int(delta.total_seconds())
    if segs < 0:
        segs = 0
    dias = segs // 86400
    horas = (segs % 86400) // 3600
    mins = (segs % 3600) // 60
    partes = []
    if dias:
        partes.append(f"{dias}d")
    if horas:
        partes.append(f"{horas}h")
    if not dias:
        partes.append(f"{mins}min")
    return " ".join(partes) or "0min"


def processar_resultado_vlan(vlan, ip_status_list: List[dict]) -> None:
    """Atualiza o estado (status + offline_desde) para uma VLAN. NÃO envia
    nada — o disparo do digest é feito por `talvez_enviar_digest()`, 1× por
    ciclo de check_loop.

    Best-effort: jamais levanta exceção.
    """
    global _estado
    try:
        with _lock:
            if _estado is None:
                _estado = _carregar_estado()

            agora_iso = _agora().strftime(_ISO)
            mudou = False

            for item in ip_status_list:
                if not _is_registrado(item):
                    continue
                ip = item.get("ip")
                if not ip:
                    continue
                novo = "on" if item.get("status") == "on" else "off"
                rotulo = _rotulo(item)
                reg = _estado.get(ip)

                if reg is None:
                    # 1ª observação deste host registrado: semeia o status.
                    # Se já está off agora, marca offline_desde = agora (não
                    # inventa passado; conta a partir de quando o vimos off).
                    _estado[ip] = {
                        "status": novo,
                        "offline_desde": agora_iso if novo == "off" else None,
                        "label": rotulo,
                    }
                    mudou = True
                    continue

                # Mantém o rótulo atualizado (cadastro pode ganhar descrição).
                if rotulo and reg.get("label") != rotulo:
                    reg["label"] = rotulo
                    mudou = True

                anterior = reg.get("status", "on")
                if novo == anterior:
                    # Sem transição. Garante que um host off tenha offline_desde
                    # (caso tenha vindo do esquema antigo sem o campo).
                    if novo == "off" and not reg.get("offline_desde"):
                        reg["offline_desde"] = agora_iso
                        mudou = True
                    continue

                # --- TRANSIÇÃO (apenas atualiza estado; NÃO notifica) ---
                if novo == "off":
                    reg["status"] = "off"
                    reg["offline_desde"] = agora_iso
                else:  # on (recuperou)
                    reg["status"] = "on"
                    reg["offline_desde"] = None
                mudou = True

            if mudou:
                _salvar_estado(_estado)
    except Exception as e:  # noqa: BLE001 — blindagem total
        logger.error(f"[offline] erro ao processar VLAN {vlan}: {e}")


def _hosts_offline_24h(agora: datetime):
    """Lista [(ip, offline_desde_dt, duracao)] de hosts offline > LIMIAR_HORAS."""
    limiar = timedelta(hours=LIMIAR_HORAS)
    out = []
    for ip, reg in (_estado or {}).items():
        if reg.get("status") != "off":
            continue
        od = reg.get("offline_desde")
        if not od:
            continue
        try:
            od_dt = datetime.strptime(od, _ISO)
        except (ValueError, TypeError):
            continue
        dur = agora - od_dt
        if dur > limiar:
            out.append((ip, od_dt, dur))
    out.sort(key=lambda t: t[1])  # mais antigo primeiro
    return out


def _descricao_por_ip(ip: str) -> str:
    """Rótulo amigável do host (descrição (ip)) gravado no estado; só o ip se
    não houver descrição cadastrada."""
    reg = (_estado or {}).get(ip) or {}
    return reg.get("label") or ip


def talvez_enviar_digest() -> Optional[dict]:
    """Verifica o estado atual; se houver hosts offline > LIMIAR_HORAS E já
    passou >= DIGEST_INTERVALO_DIAS desde o último digest, envia UM e-mail
    digest. Caso contrário, não faz nada.

    Best-effort: nunca levanta. Retorna o dict do núcleo quando enviou, ou
    None quando suprimido (sem hosts ou rate-limit).
    """
    global _estado
    try:
        with _lock:
            if _estado is None:
                _estado = _carregar_estado()
            agora = _agora()
            hosts = _hosts_offline_24h(agora)
            if not hosts:
                return None  # nada offline >24h → não envia

            meta = _carregar_digest_meta()
            ultimo = meta.get("ultimo_envio_iso")
            if ultimo:
                try:
                    ult_dt = datetime.strptime(ultimo, _ISO)
                    if agora - ult_dt < timedelta(days=DIGEST_INTERVALO_DIAS):
                        logger.info(
                            f"[offline] digest suprimido (último em {ultimo}; "
                            f"intervalo < {DIGEST_INTERVALO_DIAS}d)")
                        return None
                except (ValueError, TypeError):
                    pass

            # Monta o corpo do digest.
            iso_semana = agora.isocalendar()
            dedup_key = f"offline-weekly:ip-monitor:{iso_semana[0]}-W{iso_semana[1]:02d}"
            linhas = []
            for ip, od_dt, dur in hosts:
                linhas.append(
                    f"- {_descricao_por_ip(ip)} — offline há {_fmt_duracao(dur)} "
                    f"(desde {od_dt.strftime('%d/%m/%Y %H:%M')})")
            corpo = (
                f"Hosts de rede offline há mais de {LIMIAR_HORAS}h "
                f"(total: {len(hosts)}):\n\n" + "\n".join(linhas) +
                "\n\nDigest semanal automático do monitor de IPs.")
            titulo = (f"[Rede] {len(hosts)} host(s) offline há +{LIMIAR_HORAS}h "
                      f"— digest semanal")

            res = notif.enviar_notificacao(
                titulo=titulo,
                corpo=corpo,
                categoria=CATEGORIA,
                prioridade="aviso",
                canais=["email"],
                destinos=DESTINOS_PADRAO,
                dedup_key=dedup_key,
                dados={"total": len(hosts),
                       "ips": [ip for ip, _, _ in hosts],
                       "limiar_horas": LIMIAR_HORAS,
                       "evento": "digest_semanal_offline"},
            )
            # Só grava o rate-limit se o núcleo aceitou (ok). Se falhou, tenta
            # de novo no próximo ciclo (não perde o digest da semana por um
            # blip de rede).
            if res.get("ok"):
                _salvar_digest_meta({
                    "ultimo_envio_iso": agora.strftime(_ISO),
                    "ultimo_dedup_key": dedup_key,
                    "ultimo_total": len(hosts),
                })
            logger.info(
                f"[offline] digest semanal: {len(hosts)} host(s) >+{LIMIAR_HORAS}h "
                f"ok={res.get('ok')} entregas={res.get('entregas')} dedup={dedup_key}")
            return res
    except Exception as e:  # noqa: BLE001 — blindagem total
        logger.error(f"[offline] erro ao montar/enviar digest: {e}")
        return None
