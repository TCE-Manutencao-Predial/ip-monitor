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
import urllib.request
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
                            # `offline_desde` que já entrou num digest. Enquanto
                            # igual ao offline_desde atual, o host NÃO é re-notificado
                            # (supressão de permanentemente-offline). Só volta a
                            # notificar se recuperar e cair de novo (offline_desde muda).
                            "notificado_desde": v.get("notificado_desde") or None,
                        }
                    else:
                        # Esquema antigo: só o status. offline_desde desconhecido.
                        out[str(k)] = {"status": str(v), "offline_desde": None,
                                       "label": "", "notificado_desde": None}
                _seed_notificados_uma_vez(out)
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


# VLAN (3º octeto do IP) → descrição amigável p/ agrupar o digest.
_VLAN_DESC = {
    70: "Câmeras", 80: "Alarme", 85: "Automação Ethernet",
    86: "Automação WiFi", 200: "Telefonia IP Fixa", 204: "Telefonia IP Móvel",
}


def _vlan_de_ip(ip: str):
    """3º octeto do IP (= número da VLAN no padrão 172.17.<vlan>.x). None se não casar."""
    try:
        p = ip.split(".")
        return int(p[2]) if len(p) >= 3 else None
    except (ValueError, IndexError, AttributeError):
        return None


def _vlan_rotulo(ip: str) -> str:
    v = _vlan_de_ip(ip)
    if v is None:
        return "Outros"
    desc = _VLAN_DESC.get(v)
    return f"VLAN {v} — {desc}" if desc else f"VLAN {v}"


def _seed_notificados_uma_vez(estado: Dict[str, dict]) -> None:
    """Migração ÚNICA: marca como já-notificados os hosts que JÁ entraram no
    último digest (offline >24h no momento daquele envio). Sem isto, hosts que
    estão permanentemente offline desde antes desta feature seriam re-notificados
    uma última vez. Guarda flag em digest_meta p/ rodar só 1×; assim restarts
    futuros NÃO suprimem hosts que caíram e ainda não foram avisados."""
    try:
        meta = _carregar_digest_meta()
        if meta.get("seed_notificados_v1"):
            return
        ult = meta.get("ultimo_envio_iso")
        if ult:  # só semeia se já houve um digest (esses hosts já foram avisados)
            ult_dt = datetime.strptime(ult, _ISO)
            limiar = timedelta(hours=LIMIAR_HORAS)
            for reg in estado.values():
                od = reg.get("offline_desde")
                if reg.get("status") == "off" and od and not reg.get("notificado_desde"):
                    try:
                        if ult_dt - datetime.strptime(od, _ISO) > limiar:
                            reg["notificado_desde"] = od
                    except (ValueError, TypeError):
                        pass
        # Persiste o estado JÁ com notificado_desde (não espera o próximo scan):
        # garante que a marcação sobreviva a um restart imediato.
        _salvar_estado(estado)
        meta["seed_notificados_v1"] = True
        _salvar_digest_meta(meta)
        logger.info("[offline] seed único de notificados aplicado (supressão de permanentes)")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[offline] falha no seed de notificados: {e}")


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
                        "notificado_desde": None,
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


# Idade máxima do último dado gravado para o sensor contar como PRODUZINDO.
# Generoso de propósito: há sensores de baixa cadência, e o objetivo é separar
# "mudo" de "vivo", não policiar atraso de minutos.
ARDUINOS_DEVICES_URL = os.getenv(
    'IPMON_ARDUINOS_DEVICES_URL', 'http://arduinos:5000/arduinos/api/devices')
FRESCOR_TTL_S = float(os.getenv('IPMON_FRESCOR_TTL_S', '120'))
_cache_frescor: dict = {'dados': {}, 'quando': None}
_avisos_dado: Dict[str, str] = {}


def _hoje_str() -> str:
    return _agora().strftime('%Y-%m-%d')


def _frescor_dos_devices() -> Dict[str, bool]:
    """{ip: está chegando dado?} — perguntado ao `arduinos`, cacheado.

    POR QUE NÃO CONSULTAR O MySQL DAQUI
    -----------------------------------
    O ip-monitor não tem — e não deveria ter — credencial do banco. Copiar a
    senha para cá espalharia o segredo por mais um serviço só para responder
    uma pergunta que OUTRO serviço já responde: o `arduinos` é o dono da relação
    dispositivo↔coluna e mantém `is_online` por device, que significa
    "chegou dado recentemente" — não "respondeu ping". É esse o sinal que falta.

    `trust_env=False` equivalente (ProxyHandler vazio): chamada entre
    containers não passa pelo proxy-hub.
    """
    agora = _agora()
    if _cache_frescor['quando'] and \
            (agora - _cache_frescor['quando']).total_seconds() < FRESCOR_TTL_S:
        return _cache_frescor['dados']
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with op.open(ARDUINOS_DEVICES_URL, timeout=10) as r:
            devs = json.loads(r.read().decode('utf-8'))
        dados = {str(d.get('ip')): bool(d.get('is_online'))
                 for d in devs if d.get('ip')}
        _cache_frescor['dados'] = dados
        _cache_frescor['quando'] = agora
        return dados
    except Exception as e:                                    # noqa: BLE001
        # UMA vez por motivo por dia, não por dispositivo: com ~60 hosts, o
        # arduinos fora do ar encheria o log com 60 linhas iguais por ciclo — e
        # log cheio de repetição é onde a linha que importa se esconde.
        motivo = type(e).__name__ + ':' + str(e)[:60]
        if _avisos_dado.get(motivo) != _hoje_str():
            _avisos_dado[motivo] = _hoje_str()
            logger.warning('[transicoes] não consigo consultar o frescor dos sensores '
                           '(%s) — a distinção "host fora × sensor mudo" fica '
                           'indisponível neste ciclo', e)
        # Devolve o cache VELHO se houver: dado de 10 min atrás ainda separa
        # host fora de sensor mudo melhor do que não separar nada.
        return _cache_frescor['dados'] or {}


def _sensor_produzindo(ip: str):
    """True/False/None — chega dado deste dispositivo?

    `None` = não dá para saber (não é device do arduinos, ou a consulta falhou).
    É diferente de False, e a mensagem precisa dizer qual dos dois: afirmar
    "sensor mudo" sem ter conseguido olhar seria inventar diagnóstico.
    """
    return _frescor_dos_devices().get(ip)


def _classificar(ip: str) -> tuple:
    """(rotulo, explicacao) a partir de PING × DADO.

    O digest dizia "offline" para tudo que não responde ping, e ficava calado
    sobre o sensor que morreu com o host no ar — que é justamente o caso em que
    alguém precisa subir no quadro. Dois sinais independentes, quatro
    desfechos, e cada um pede uma ação diferente:

        ping ok  + dado ok    saudável (não entra no digest)
        ping ok  + sem dado   HOST NO AR, SENSOR MUDO -> trocar/reassentar sensor
        ping off + sem dado   offline de verdade -> energia, rede, IP trocado
        ping off + dado ok    não responde ICMP mas grava (power-save) -> ignorar
    """
    frescor = _frescor_dos_devices()
    prod = frescor.get(ip)
    if prod is None:
        # Distinção que evita 19 linhas de ruído por digest: a maioria dos
        # offline (câmera, CLP, telefone) NÃO é dispositivo do arduinos e nunca
        # teve dado a conferir. Dizer "não foi possível conferir" sobre eles
        # sugere uma pendência que não existe. Só quando a CONSULTA falhou —
        # dicionário vazio — é que a ressalva é verdadeira.
        if frescor:
            return ('offline', 'sem ping (não é sensor do arduinos).')
        return ('offline-sem-conferir',
                'sem ping; não foi possível conferir se ainda envia dado.')
    if prod is True:
        return ('grava-sem-ping',
                'não responde ao ping, mas CONTINUA GRAVANDO dado — típico de '
                'ESP32 em power-save. Não é queda.')
    return ('offline', 'sem ping e sem dado chegando.')


def _sensores_mudos():
    """[(ip, rotulo)] de hosts ONLINE cujo sensor parou de gravar.

    Este é o caso que não existia no digest e é o que manda alguém subir no
    quadro: o host responde, a rede está boa, a energia está boa — e o sensor
    morreu. Dizer "offline" sobre ele mandaria o técnico procurar a coisa
    errada; não dizer nada o deixa invisível até alguém sentir falta do dado.
    """
    out = []
    for ip, reg in (_estado or {}).items():
        if reg.get('status') != 'on':
            continue
        if _sensor_produzindo(ip) is False:
            out.append((ip, reg.get('label') or ip))
    out.sort(key=lambda t: t[0])
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
            mudos = _sensores_mudos()
            if not hosts and not mudos:
                return None  # nada offline >24h e nenhum sensor mudo → não envia

            # Reportáveis: hosts cujo `offline_desde` ainda NÃO foi notificado —
            # caíram agora pela 1ª vez, OU recuperaram e voltaram a cair (o
            # offline_desde muda). Hosts permanentemente offline desde um aviso
            # anterior têm offline_desde == notificado_desde → suprimidos.
            reportaveis = []
            for ip, od_dt, dur in hosts:
                reg = (_estado or {}).get(ip) or {}
                if reg.get("offline_desde") != reg.get("notificado_desde"):
                    reportaveis.append((ip, od_dt, dur))
            if not reportaveis and not mudos:
                logger.info(
                    f"[offline] digest suprimido: {len(hosts)} offline >{LIMIAR_HORAS}h, "
                    f"nenhum novo/reincidente e nenhum sensor mudo")
                return None

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

            # Monta o corpo do digest, agrupado por VLAN.
            iso_semana = agora.isocalendar()
            dedup_key = f"offline-weekly:ip-monitor:{iso_semana[0]}-W{iso_semana[1]:02d}"
            grupos: Dict[str, list] = {}
            for ip, od_dt, dur in reportaveis:
                grupos.setdefault(_vlan_rotulo(ip), []).append((ip, od_dt, dur))
            blocos = []
            for vlan_lbl in sorted(grupos):
                itens = sorted(grupos[vlan_lbl], key=lambda t: t[1])  # mais antigo 1º
                linhas = []
                for ip, od_dt, dur in itens:
                    rotulo, expl = _classificar(ip)
                    if rotulo == 'grava-sem-ping':
                        # Não é queda: some do bloco de offline para não mandar
                        # o técnico atrás de um aparelho que está trabalhando.
                        linhas.append(
                            f"   • {_descricao_por_ip(ip)} — sem ping há "
                            f"{_fmt_duracao(dur)}, MAS continua gravando dado "
                            f"(power-save; não é queda)")
                        continue
                    sufixo = ('' if rotulo == 'offline'
                              else '  [não foi possível conferir o dado]')
                    linhas.append(
                        f"   • {_descricao_por_ip(ip)} — offline há {_fmt_duracao(dur)} "
                        f"(desde {od_dt.strftime('%d/%m %H:%M')}){sufixo}")
                blocos.append(f"▸ {vlan_lbl}  ({len(itens)})\n" + "\n".join(linhas))
            if mudos:
                # Bloco SEPARADO, e não mais uma linha de "offline": a ação é
                # outra. Host no ar com sensor morto é sensor/fiação; host fora
                # é energia, rede ou IP trocado.
                linhas = [f"   • {rot} — responde ao ping, mas parou de ENVIAR DADO"
                          for _, rot in mudos]
                blocos.append("▸ HOST NO AR, SENSOR MUDO  (%d)\n" % len(mudos)
                              + "\n".join(linhas))
            partes = []
            if reportaveis:
                partes.append(
                    f"{len(reportaveis)} host(s) entraram em offline prolongado "
                    f"(> {LIMIAR_HORAS}h) desde o último aviso.")
            if mudos:
                partes.append(
                    f"{len(mudos)} dispositivo(s) RESPONDEM ao ping mas pararam de "
                    f"gravar dado — o host está no ar e o sensor, não.")
            corpo = (
                "\n".join(partes) + "\n"
                f"Hosts que seguem offline desde um aviso anterior NÃO são repetidos — "
                f"só reaparecem se recuperarem e caírem de novo.\n\n"
                + "\n\n".join(blocos)
                + "\n\n— Monitor de IPs · digest semanal automático."
            )
            # O título diz os DOIS casos. "N host(s) offline" com o digest
            # cheio de sensor mudo faria quem lê o assunto procurar problema de
            # rede — e o problema é de sensor.
            pedacos = []
            if reportaveis:
                pedacos.append(f"{len(reportaveis)} offline +{LIMIAR_HORAS}h")
            if mudos:
                pedacos.append(f"{len(mudos)} com sensor mudo")
            titulo = "[Rede] " + " · ".join(pedacos) + " — digest semanal"

            res = notif.enviar_notificacao(
                titulo=titulo,
                corpo=corpo,
                categoria=CATEGORIA,
                prioridade="aviso",
                canais=["email"],
                destinos=DESTINOS_PADRAO,
                dedup_key=dedup_key,
                dados={"total": len(reportaveis),
                       "total_offline_24h": len(hosts),
                       "ips": [ip for ip, _, _ in reportaveis],
                       "limiar_horas": LIMIAR_HORAS,
                       "evento": "digest_semanal_offline"},
            )
            # Só grava (rate-limit + marca notificados) se o núcleo aceitou. Se
            # falhou, tenta de novo no próximo ciclo (não perde o digest da semana).
            if res.get("ok"):
                meta.update({
                    "ultimo_envio_iso": agora.strftime(_ISO),
                    "ultimo_dedup_key": dedup_key,
                    "ultimo_total": len(reportaveis),
                })
                _salvar_digest_meta(meta)   # preserva seed_notificados_v1
                # Registra os IPs notificados: enquanto seguirem offline (mesmo
                # offline_desde) não voltam ao digest; só reaparecem se recaírem.
                for ip, _od, _dur in reportaveis:
                    reg = (_estado or {}).get(ip)
                    if reg:
                        reg["notificado_desde"] = reg.get("offline_desde")
                _salvar_estado(_estado)
            logger.info(
                f"[offline] digest semanal: {len(reportaveis)} reportável(is) de "
                f"{len(hosts)} offline >{LIMIAR_HORAS}h ok={res.get('ok')} "
                f"entregas={res.get('entregas')} dedup={dedup_key}")
            return res
    except Exception as e:  # noqa: BLE001 — blindagem total
        logger.error(f"[offline] erro ao montar/enviar digest: {e}")
        return None
