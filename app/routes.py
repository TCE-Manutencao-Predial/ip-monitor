from flask import Flask, render_template, jsonify, make_response, request, send_file, abort  # Importa as funções necessárias do Flask.
from app import ip_operations  # Importa o módulo 'ip_operations' da aplicação, que contém a função 'verificar_ips'.
import time  # Módulo para manipulação de tempo (usado para pausas e delays).
import threading  # Módulo para rodar threads em paralelo (execução simultânea).
import os  # Sistema de arquivos (fotos dos locais de instalação).
import re  # Validação de IP/id (segurança de path).
import glob  # Busca de arquivos de foto.
import uuid  # IDs de foto.
import io  # Buffers em memória (zip).
import zipfile  # Upload de .zip de fotos.
from app import app  # Importa a instância 'app' da aplicação Flask.
import concurrent.futures  # Para execução concorrente de múltiplas tarefas.
import logging  # Adicionar logging
import json  # Para serialização de dados em logs
from app.config_manager import config_manager  # Importa o gerenciador de configurações.
from app.device_manager import device_manager  # Importa o gerenciador de dispositivos.
from app.clp_manager import clp_manager  # Importa o gerenciador de dados CLP.
from app import audit as audit_mod  # Trilha de auditoria (SQLite local).
from app import monitor_transicoes  # Detecção de transições UP↔DOWN + notificações (Onda 3).
from app.settings import ROUTES_PREFIX, NETWORK_BASE, DATA_ROOT  # Importa configurações centralizadas

# Configurar logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Dicionário global que armazenará o status dos IPs verificados, por VLAN.
check_ip = {}

# Constante que define o caminho raiz para os endpoints da API.
RAIZ = ROUTES_PREFIX  # Usa configuração centralizada do settings.py

# Autorização central (grupo `automacao` da matriz do helpdesk). Movido de
# público → protegido em 2026-06-19. Whitelist preserva healthcheck + os GETs
# inter-container consumidos por infra-docs/tcego-ia (ver app/autz.py).
from app import autz as _autz  # noqa: E402


@app.before_request
def _autz_guard():
    return _autz.guard()

# Variável global para controlar o loop de verificação
background_thread = None
should_stop = False

# Função que verifica os IPs em uma determinada VLAN em segundo plano.
# Esta função é chamada pelas threads para rodar verificações assíncronas.
def background_ip_check(vlan):
    global check_ip
    rede_base = NETWORK_BASE.format(vlan=vlan)  # Usa configuração centralizada do settings.py
    
    logging.info(f"[BACKGROUND] Verificando em background a VLAN {vlan} e rede_base {rede_base}")

    # Chama a função 'verificar_ips' do módulo 'ip_operations' e armazena o resultado no dicionário 'check_ip'.
    result = ip_operations.verificar_ips(rede_base)
    
    # Log dos resultados antes de armazenar
    items_com_tipo = [item for item in result if item.get('tipo') and item['tipo'].strip()]
    logging.info(f"[BACKGROUND] VLAN {vlan} - Resultado: {len(result)} itens, {len(items_com_tipo)} com tipo")

    # Atualiza o estado de offline (status + offline_desde) por host. NÃO
    # notifica aqui — o digest semanal é disparado 1× por ciclo no check_loop.
    # Best-effort: nunca derruba o monitoramento.
    monitor_transicoes.processar_resultado_vlan(vlan, result)

    check_ip[vlan] = result


'''API ENDPOINTS'''        

# Define o endpoint principal para a página inicial.
@app.route('/')  # Rota para rodar localmente.
@app.route(RAIZ + '/')  # Rota que inclui o prefixo 'RAIZ' para ambiente de produção.
def index():
    # Passa lista de IPs com dados CLP para o template
    return render_template('index.html', clp_ips=clp_manager.get_clp_ips())

# Define o endpoint principal para a página de configurações.
@app.route('/configuracoes')  # Rota para rodar localmente.
@app.route(RAIZ + '/configuracoes')  # Rota que inclui o prefixo 'RAIZ' para ambiente de produção.
def configuracoes():
    # Obtém as configurações atuais
    config = config_manager.get_config()
    # Renderiza o arquivo HTML 'configuracoes.html' com as configurações
    return render_template('configuracoes.html', config=config)

# Define o endpoint para retornar o status dos IPs verificados em formato JSON.
@app.route('/api/ip-status')  # Rota para rodar localmente.
@app.route(RAIZ + '/api/ip-status')  # Rota com prefixo 'RAIZ' para produção.
def ip_status():
    # Retorna o conteúdo do dicionário 'check_ip' (status dos IPs) como um JSON.
    return jsonify(check_ip)
    
# Endpoint para iniciar a verificação de uma VLAN específica.
@app.route('/api/start-check/<string:vlan>', methods=['GET'])  # Rota local.
@app.route(RAIZ + '/api/start-check/<string:vlan>', methods=['GET'])  # Rota com prefixo 'RAIZ'.
def check(vlan):
    try:
        # Tenta retornar o status da VLAN solicitada em formato JSON.
        result = check_ip[int(vlan)]
        
        # Log para diagnóstico
        logging.info(f"[ROUTES] API /api/start-check/{vlan} - Retornando {len(result)} itens")
        items_com_tipo = [item for item in result if item.get('tipo') and item['tipo'].strip()]
        logging.info(f"[ROUTES] Items com tipo na resposta: {len(items_com_tipo)}")
        if items_com_tipo:
            logging.info(f"[ROUTES] Exemplo com tipo: {items_com_tipo[0]}")
        
        return jsonify(result)
    except KeyError:
        # Caso a VLAN ainda não tenha sido verificada, retorna status 204 (No Content).
        logging.warning(f"[ROUTES] VLAN {vlan} não encontrada em check_ip por enquanto.")
        return '', 204  # Resposta vazia com status 204.

# Endpoint para salvar configurações
@app.route('/api/config/save', methods=['POST'])
@app.route(RAIZ + '/api/config/save', methods=['POST'])
def save_config():
    logging.info('[CONFIG] ========== INÍCIO SALVAMENTO DE CONFIGURAÇÕES ==========')
    try:
        # Log da requisição recebida
        logging.info(f'[CONFIG] Método: {request.method}')
        logging.info(f'[CONFIG] Content-Type: {request.content_type}')
        
        data = request.get_json()
        logging.info(f'[CONFIG] Dados recebidos (JSON): {json.dumps(data, indent=2, ensure_ascii=False)}')
        
        # Validar dados antes de salvar
        logging.info('[CONFIG] Validando dados de configuração...')
        if not validate_config_data(data):
            logging.error('[CONFIG] ❌ Validação falhou - dados inválidos')
            return jsonify({'error': 'Dados de configuração inválidos'}), 400
        
        logging.info('[CONFIG] ✅ Validação passou')
        
        # Atualizar configurações em memória (SEM salvar ainda)
        logging.info('[CONFIG] Atualizando configurações em memória...')
        with config_manager.config_lock:
            for section, values in data.items():
                logging.info(f'[CONFIG] Atualizando seção em memória: {section}')
                if section in config_manager.config:
                    config_manager.config[section].update(values)
                else:
                    config_manager.config[section] = values
        
        # Salvar arquivo UMA ÚNICA VEZ no final
        logging.info('[CONFIG] Salvando arquivo de configuração...')
        if not config_manager.save_config():
            logging.error('[CONFIG] ❌ Erro ao salvar arquivo de configuração')
            return jsonify({'error': 'Erro ao salvar configurações no arquivo'}), 500
        
        logging.info('[CONFIG] ✅ Arquivo salvo com sucesso')
        
        # Reiniciar serviço de background com novas configurações
        logging.info('[CONFIG] Reiniciando serviço de background...')
        restart_background_service()
        logging.info('[CONFIG] ✅ Serviço de background reiniciado')
        
        logging.info('[CONFIG] ========== CONFIGURAÇÕES SALVAS COM SUCESSO ==========')
        return jsonify({
            'success': True,
            'message': 'Configurações salvas com sucesso'
        })
    
    except Exception as e:
        logging.error(f'[CONFIG] ❌ EXCEÇÃO ao salvar configurações: {e}')
        logging.error(f'[CONFIG] Stack trace:', exc_info=True)
        return jsonify({'error': str(e)}), 500

# Endpoint para resetar configurações
@app.route('/api/config/reset', methods=['POST'])
@app.route(RAIZ + '/api/config/reset', methods=['POST'])
def reset_config():
    try:
        if config_manager.reset_to_defaults():
            restart_background_service()
            return jsonify({
                'success': True,
                'message': 'Configurações restauradas para os valores padrão'
            })
        else:
            return jsonify({'error': 'Erro ao resetar configurações'}), 500
    
    except Exception as e:
        print(f"Erro ao resetar configurações: {e}")
        return jsonify({'error': str(e)}), 500

# Endpoint de teste para diagnóstico de tipos
@app.route('/api/debug/devices/<int:vlan>')
@app.route(RAIZ + '/api/debug/devices/<int:vlan>')
def debug_devices(vlan):
    try:
        devices = device_manager.get_devices_by_vlan(vlan)
        return jsonify({
            'vlan': vlan,
            'total_devices': len(devices),
            'devices_with_type': [d for d in devices if d.get('tipo')],
            'all_devices': devices
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Endpoint para testar configurações
@app.route('/api/config/test', methods=['POST'])
@app.route(RAIZ + '/api/config/test', methods=['POST'])
def test_config():
    try:
        data = request.get_json()
        
        # Simular teste das configurações
        test_results = {
            'ping_tests': 0,
            'network_connectivity': True,
            'config_validity': True
        }
        
        # Testar algumas configurações básicas
        if 'network_settings' in data:
            timeout = data['network_settings'].get('ping_timeout', 2)
            if timeout < 1 or timeout > 10:
                test_results['config_validity'] = False
        
        if 'ping_intervals' in data:
            for vlan, interval in data['ping_intervals'].items():
                if interval < 5 or interval > 300:
                    test_results['config_validity'] = False
                test_results['ping_tests'] += 1
        
        message = f"Teste concluído. {test_results['ping_tests']} intervalos testados."
        if not test_results['config_validity']:
            message += " Alguns valores estão fora dos limites recomendados."
        
        return jsonify({
            'success': True,
            'message': message,
            'details': test_results
        })
    
    except Exception as e:
        print(f"Erro ao testar configurações: {e}")
        return jsonify({'error': str(e)}), 500

# Endpoints para gerenciar dispositivos
@app.route('/api/devices/<int:vlan>', methods=['GET'])
@app.route(RAIZ + '/api/devices/<int:vlan>', methods=['GET'])
def get_devices(vlan):
    try:
        devices = device_manager.get_devices_by_vlan(vlan)
        return jsonify({'success': True, 'devices': devices})
    except Exception as e:
        print(f"Erro ao obter dispositivos: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/devices/<int:vlan>', methods=['POST'])
@app.route(RAIZ + '/api/devices/<int:vlan>', methods=['POST'])
def add_device(vlan):
    try:
        data = request.get_json()
        ip = data.get('ip')
        descricao = data.get('descricao')  # Corrigido para usar 'descricao'
        tipo = data.get('tipo', '')
        
        if not ip or not descricao:
            return jsonify({'error': 'IP e descrição são obrigatórios'}), 400
        
        success, message = device_manager.add_device(vlan, ip, descricao, tipo)
        
        if success:
            return jsonify({'success': True, 'message': message})
        else:
            return jsonify({'error': message}), 400
    
    except Exception as e:
        print(f"Erro ao adicionar dispositivo: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/devices/<int:vlan>/<string:ip>', methods=['PUT'])
@app.route(RAIZ + '/api/devices/<int:vlan>/<string:ip>', methods=['PUT'])
def update_device(vlan, ip):
    try:
        data = request.get_json()
        descricao = data.get('descricao')
        tipo = data.get('tipo')
        sensores = data.get('sensores')          # lista de {codigo, categoria}
        tabelas_sql = data.get('tabelas_sql')    # lista de {tabela, coluna}

        # Sanitização leve: garantir que são listas se foram enviadas
        if sensores is not None and not isinstance(sensores, list):
            return jsonify({'error': 'sensores deve ser uma lista'}), 400
        if tabelas_sql is not None and not isinstance(tabelas_sql, list):
            return jsonify({'error': 'tabelas_sql deve ser uma lista'}), 400

        success, message = device_manager.update_device(
            vlan, ip,
            descricao=descricao,
            tipo=tipo,
            sensores=sensores,
            tabelas_sql=tabelas_sql,
        )

        if success:
            return jsonify({'success': True, 'message': message})
        else:
            return jsonify({'error': message}), 400

    except Exception as e:
        print(f"Erro ao atualizar dispositivo: {e}")
        return jsonify({'error': str(e)}), 500


# =====================================================================
# Fotos do LOCAL DE INSTALAÇÃO do dispositivo (até 5 por IP).
# Armazenadas no volume: DATA_ROOT/fotos/<ip>/<id>.<ext> (persiste em rebuild).
# =====================================================================
_FOTOS_ROOT = os.path.join(DATA_ROOT, 'fotos')
_IP_RE = re.compile(r'^\d{1,3}(?:\.\d{1,3}){3}$')   # evita path traversal
_ID_RE = re.compile(r'^[A-Za-z0-9]{1,32}$')
_FOTO_MAX = 5
_FOTO_EXT = {'jpg', 'jpeg', 'png', 'webp', 'gif', 'heic', 'heif'}
_FOTO_CT = {'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp',
            'image/gif': 'gif', 'image/heic': 'heic', 'image/heif': 'heif'}


def _heic_para_jpeg(data, ext):
    """HEIC/HEIF do iPhone → JPEG (navegadores não exibem HEIC). Outros: inalterado."""
    if ext not in ('heic', 'heif'):
        return data, ext
    try:
        import io as _io
        from PIL import Image
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
        except Exception:
            pass
        im = Image.open(_io.BytesIO(data)).convert('RGB')
        out = _io.BytesIO()
        im.save(out, 'JPEG', quality=90)
        return out.getvalue(), 'jpg'
    except Exception as e:
        logging.warning(f"[fotos] conversão HEIC→JPEG falhou: {e}")
        return data, ext


def _foto_dir(ip):
    if not _IP_RE.match(ip or ''):
        return None
    return os.path.join(_FOTOS_ROOT, ip)


def _foto_listar(ip):
    d = _foto_dir(ip)
    if not d or not os.path.isdir(d):
        return []
    ids = []
    for fn in sorted(os.listdir(d)):
        stem, _, ext = fn.partition('.')
        if ext.lower() in _FOTO_EXT:
            ids.append(stem)
    return ids


@app.route('/api/devices/<string:ip>/fotos', methods=['GET'])
@app.route(RAIZ + '/api/devices/<string:ip>/fotos', methods=['GET'])
def listar_fotos(ip):
    if not _foto_dir(ip):
        return jsonify({'error': 'IP inválido'}), 400
    return jsonify({'success': True, 'fotos': _foto_listar(ip), 'max': _FOTO_MAX})


def _img_ext(nome):
    ext = nome.rsplit('.', 1)[-1].lower() if '.' in (nome or '') else ''
    return 'jpg' if ext == 'jpeg' else ext


def _imagens_do_zip(data):
    """(nome, bytes) das imagens dentro do zip; ignora o resto e entradas perigosas."""
    out = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for zi in z.infolist():
                if zi.is_dir():
                    continue
                nome = zi.filename
                if '..' in nome or nome.startswith('/') or nome.startswith('\\'):
                    continue
                if zi.file_size > 25 * 1024 * 1024:        # 25 MB por imagem (anti zip-bomb)
                    continue
                if _img_ext(nome) not in _FOTO_EXT:
                    continue
                with z.open(zi) as fh:
                    out.append((os.path.basename(nome), fh.read()))
                if len(out) >= 50:                          # teto de segurança
                    break
    except Exception as e:
        logging.warning(f"[fotos] zip inválido: {e}")
    return out


@app.route('/api/devices/<string:ip>/fotos', methods=['POST'])
@app.route(RAIZ + '/api/devices/<string:ip>/fotos', methods=['POST'])
def upload_foto(ip):
    """Aceita VÁRIAS imagens (multi-seleção) e/ou um .zip de fotos. Salva até o
    limite por dispositivo; converte HEIC→JPEG. Resposta inclui quantas entraram
    e quantas foram ignoradas (limite/formato)."""
    d = _foto_dir(ip)
    if not d:
        return jsonify({'error': 'IP inválido'}), 400

    arquivos = [a for a in request.files.getlist('foto') if a and a.filename]
    if not arquivos:
        return jsonify({'error': 'Nenhuma imagem enviada'}), 400

    # Monta candidatos (nome, bytes) a partir de imagens soltas e de zips
    candidatos = []
    for a in arquivos:
        raw = a.read()
        if a.filename.lower().endswith('.zip') or (a.mimetype or '') in (
                'application/zip', 'application/x-zip-compressed', 'multipart/x-zip'):
            candidatos.extend(_imagens_do_zip(raw))
        else:
            candidatos.append((a.filename, raw))

    existentes = len(_foto_listar(ip))
    livres = _FOTO_MAX - existentes
    if livres <= 0:
        return jsonify({'error': f'Limite de {_FOTO_MAX} fotos por dispositivo atingido',
                        'adicionadas': 0, 'ignoradas': len(candidatos), 'total': existentes,
                        'max': _FOTO_MAX}), 400

    os.makedirs(d, exist_ok=True)
    adicionadas, ignoradas = 0, 0
    for nome, raw in candidatos:
        if adicionadas >= livres:
            ignoradas += 1
            continue
        ext = _img_ext(nome)
        if ext not in _FOTO_EXT:
            ignoradas += 1
            continue
        data, ext = _heic_para_jpeg(raw, ext)
        fid = uuid.uuid4().hex[:10]
        with open(os.path.join(d, f'{fid}.{ext}'), 'wb') as fh:
            fh.write(data)
        adicionadas += 1
    return jsonify({'success': True, 'adicionadas': adicionadas, 'ignoradas': ignoradas,
                    'total': existentes + adicionadas, 'max': _FOTO_MAX})


@app.route('/api/devices/<string:ip>/fotos/<string:foto_id>', methods=['GET'])
@app.route(RAIZ + '/api/devices/<string:ip>/fotos/<string:foto_id>', methods=['GET'])
def obter_foto(ip, foto_id):
    d = _foto_dir(ip)
    if not d or not _ID_RE.match(foto_id):
        abort(404)
    matches = glob.glob(os.path.join(d, foto_id + '.*'))
    if not matches:
        abort(404)
    return send_file(matches[0])


@app.route('/api/devices/<string:ip>/fotos/<string:foto_id>', methods=['DELETE'])
@app.route(RAIZ + '/api/devices/<string:ip>/fotos/<string:foto_id>', methods=['DELETE'])
def excluir_foto(ip, foto_id):
    d = _foto_dir(ip)
    if not d or not _ID_RE.match(foto_id):
        return jsonify({'error': 'parâmetro inválido'}), 400
    removidos = 0
    for m in glob.glob(os.path.join(d, foto_id + '.*')):
        os.remove(m)
        removidos += 1
    return jsonify({'success': removidos > 0})


@app.route('/api/devices/<int:vlan>', methods=['DELETE'])
@app.route(RAIZ + '/api/devices/<int:vlan>', methods=['DELETE'])
def delete_device(vlan):
    try:
        data = request.get_json()
        ip = data.get('ip')
        
        if not ip:
            return jsonify({'error': 'IP é obrigatório'}), 400
        
        success, message = device_manager.delete_device(vlan, ip)
        
        if success:
            return jsonify({'success': True, 'message': message})
        else:
            return jsonify({'error': message}), 400
    
    except Exception as e:
        print(f"Erro ao remover dispositivo: {e}")
        return jsonify({'error': str(e)}), 500

# Endpoints para gerenciar tipos de dispositivos
@app.route('/api/device-types/<int:vlan>', methods=['GET'])
@app.route(RAIZ + '/api/device-types/<int:vlan>', methods=['GET'])
def get_device_types(vlan):
    try:
        # Tipos configurados no sistema
        configured_types = config_manager.get_device_types(vlan)
        # Tipos únicos já usados na VLAN
        used_types = device_manager.get_device_types_by_vlan(vlan)
        
        # Combinar e remover duplicatas
        all_types = list(set(configured_types + used_types))
        all_types.sort()
        
        return jsonify({
            'success': True, 
            'types': all_types,
            'configured_types': configured_types,
            'used_types': used_types
        })
    except Exception as e:
        print(f"Erro ao obter tipos de dispositivos: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/device-types/<int:vlan>', methods=['POST'])
@app.route(RAIZ + '/api/device-types/<int:vlan>', methods=['POST'])
def add_device_type(vlan):
    try:
        data = request.get_json()
        device_type = data.get('type')
        
        if not device_type:
            return jsonify({'error': 'Tipo de dispositivo é obrigatório'}), 400
        
        success = config_manager.add_device_type(vlan, device_type)
        
        if success:
            return jsonify({'success': True, 'message': 'Tipo adicionado com sucesso'})
        else:
            return jsonify({'error': 'Erro ao adicionar tipo'}), 500
    
    except Exception as e:
        print(f"Erro ao adicionar tipo de dispositivo: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/device-types/<int:vlan>', methods=['DELETE'])
@app.route(RAIZ + '/api/device-types/<int:vlan>', methods=['DELETE'])
def delete_device_type(vlan):
    try:
        data = request.get_json()
        device_type = data.get('type')
        
        if not device_type:
            return jsonify({'error': 'Tipo é obrigatório'}), 400
        
        success = config_manager.remove_device_type(vlan, device_type)
        
        if success:
            return jsonify({'success': True, 'message': 'Tipo removido com sucesso'})
        else:
            return jsonify({'error': 'Erro ao remover tipo'}), 500
    
    except Exception as e:
        print(f"Erro ao remover tipo de dispositivo: {e}")
        return jsonify({'error': str(e)}), 500

# Pagina de listagem de todos os CLPs
@app.route('/clps')
@app.route(RAIZ + '/clps')
def clps_listagem():
    """Pagina com listagem de todos os CLPs."""
    all_clps = clp_manager.get_all_clps()
    return render_template('clps.html', clps_data=all_clps)

# Endpoints para dados de CLPs
@app.route('/clp/<ip>')
@app.route(RAIZ + '/clp/<ip>')
def clp_detalhes(ip):
    """Pagina de detalhes de um CLP especifico."""
    clp = clp_manager.get_clp_data(ip)
    if not clp:
        return jsonify({'error': f'CLP nao encontrado para IP {ip}'}), 404
    # Lista ordenada de IPs para navegacao prev/next
    all_ips = sorted(clp_manager.get_clp_ips(), key=lambda x: list(map(int, x.split('.'))))
    return render_template('clp_detalhes.html', clp_data=clp, clp_ip=ip, clp_ips_ordered=all_ips)

@app.route('/api/clp')
@app.route(RAIZ + '/api/clp')
def api_clp_list():
    """API - lista resumida de todos os CLPs disponiveis."""
    return jsonify(clp_manager.get_all_clps())

@app.route('/api/clp/<ip>')
@app.route(RAIZ + '/api/clp/<ip>')
def api_clp_data(ip):
    """API - dados completos de um CLP especifico."""
    clp = clp_manager.get_clp_data(ip)
    if not clp:
        return jsonify({'error': f'CLP nao encontrado para IP {ip}'}), 404
    return jsonify(clp)


def _parse_ponto_payload():
    """Lê JSON do request e devolve (ponto, secao_idx)."""
    payload = request.get_json(silent=True) or {}
    ponto = payload.get('ponto')
    if not isinstance(ponto, dict):
        return None, None, 'Body inválido: campo "ponto" obrigatório (objeto).'
    secao_idx = payload.get('secao_idx')
    if secao_idx is not None:
        try:
            secao_idx = int(secao_idx)
        except (TypeError, ValueError):
            return None, None, 'secao_idx inválido'
    return ponto, secao_idx, None


@app.route('/api/clp/<ip>/pontos', methods=['POST'])
@app.route(RAIZ + '/api/clp/<ip>/pontos', methods=['POST'])
def api_clp_add_ponto(ip):
    """Adiciona um ponto I/O ao CLP."""
    ponto, secao_idx, err = _parse_ponto_payload()
    if err:
        return jsonify({'error': err}), 400
    ok, msg = clp_manager.add_ponto(ip, ponto, secao_idx)
    return (jsonify({'sucesso': True, 'mensagem': msg}) if ok
            else (jsonify({'error': msg}), 400))


@app.route('/api/clp/<ip>/pontos/<int:idx>', methods=['PUT'])
@app.route(RAIZ + '/api/clp/<ip>/pontos/<int:idx>', methods=['PUT'])
def api_clp_update_ponto(ip, idx):
    """Atualiza ponto[idx] do CLP."""
    ponto, secao_idx, err = _parse_ponto_payload()
    if err:
        return jsonify({'error': err}), 400
    ok, msg = clp_manager.update_ponto(ip, idx, ponto, secao_idx)
    return (jsonify({'sucesso': True, 'mensagem': msg}) if ok
            else (jsonify({'error': msg}), 400))


@app.route('/api/clp/<ip>/pontos/<int:idx>', methods=['DELETE'])
@app.route(RAIZ + '/api/clp/<ip>/pontos/<int:idx>', methods=['DELETE'])
def api_clp_delete_ponto(ip, idx):
    """Remove ponto[idx] do CLP. secao_idx via querystring para CLPs com seções."""
    secao_idx = request.args.get('secao_idx')
    if secao_idx is not None:
        try:
            secao_idx = int(secao_idx)
        except (TypeError, ValueError):
            return jsonify({'error': 'secao_idx inválido'}), 400
    ok, msg = clp_manager.delete_ponto(ip, idx, secao_idx)
    return (jsonify({'sucesso': True, 'mensagem': msg}) if ok
            else (jsonify({'error': msg}), 400))

# Função para validar dados de configuração
def validate_config_data(data):
    """Valida se os dados de configuração estão corretos"""
    logging.info('[VALIDATION] Iniciando validação de dados...')
    logging.info(f'[VALIDATION] Dados a validar: {json.dumps(data, indent=2, ensure_ascii=False)}')
    try:
        # Validar intervalos de ping
        if 'ping_intervals' in data:
            logging.info('[VALIDATION] Validando ping_intervals...')
            for vlan, interval in data['ping_intervals'].items():
                logging.info(f'[VALIDATION] VLAN {vlan}: intervalo = {interval} (tipo: {type(interval).__name__})')
                if not isinstance(interval, (int, float)) or interval < 5 or interval > 300:
                    logging.error(f'[VALIDATION] ❌ Intervalo inválido para {vlan}: {interval}')
                    return False
            logging.info('[VALIDATION] ✅ ping_intervals válido')
        
        # Validar configurações de rede
        if 'network_settings' in data:
            logging.info('[VALIDATION] Validando network_settings...')
            network = data['network_settings']
            if 'ping_timeout' in network:
                logging.info(f'[VALIDATION] ping_timeout = {network["ping_timeout"]} (tipo: {type(network["ping_timeout"]).__name__})')
                if not isinstance(network['ping_timeout'], (int, float)) or network['ping_timeout'] < 1 or network['ping_timeout'] > 10:
                    logging.error(f'[VALIDATION] ❌ ping_timeout inválido: {network["ping_timeout"]}')
                    return False
            if 'max_concurrent_pings' in network:
                logging.info(f'[VALIDATION] max_concurrent_pings = {network["max_concurrent_pings"]} (tipo: {type(network["max_concurrent_pings"]).__name__})')
                if not isinstance(network['max_concurrent_pings'], int) or network['max_concurrent_pings'] < 1 or network['max_concurrent_pings'] > 10:
                    logging.error(f'[VALIDATION] ❌ max_concurrent_pings inválido: {network["max_concurrent_pings"]}')
                    return False
            if 'retry_attempts' in network:
                logging.info(f'[VALIDATION] retry_attempts = {network["retry_attempts"]} (tipo: {type(network["retry_attempts"]).__name__})')
                if not isinstance(network['retry_attempts'], int) or network['retry_attempts'] < 0 or network['retry_attempts'] > 5:
                    logging.error(f'[VALIDATION] ❌ retry_attempts inválido: {network["retry_attempts"]}')
                    return False
            logging.info('[VALIDATION] ✅ network_settings válido')
        
        logging.info('[VALIDATION] ✅ Todos os dados são válidos')
        return True
    except Exception as e:
        logging.error(f'[VALIDATION] ❌ Erro na validação: {e}')
        logging.error('[VALIDATION] Stack trace:', exc_info=True)
        return False

# Função para reiniciar o serviço de background
def restart_background_service():
    """Reinicia o serviço de background com as novas configurações"""
    global should_stop, background_thread
    
    logging.info('[CONFIG] Solicitando parada do serviço de background...')
    # Sinalizar para parar o thread atual
    should_stop = True
    
    # NÃO aguardar aqui - deixar o thread parar naturalmente
    # O novo thread será iniciado imediatamente
    logging.info('[CONFIG] Flag should_stop definida como True')
    
    # Resetar flag e iniciar novo serviço em uma thread separada
    # para não bloquear a resposta HTTP
    def restart_async():
        global should_stop
        logging.info('[CONFIG] Aguardando 1 segundo antes de reiniciar...')
        time.sleep(1)
        should_stop = False
        logging.info('[CONFIG] Iniciando novo serviço de background...')
        start_background_service()
        logging.info('[CONFIG] Serviço de background reiniciado')
    
    # Executar restart em thread separada
    restart_thread = threading.Thread(target=restart_async, daemon=True)
    restart_thread.start()
    logging.info('[CONFIG] Thread de restart iniciada')

# Função que inicia o serviço de verificação de IPs em segundo plano.
def start_background_service():
    global background_thread, should_stop
    
    print("Iniciando serviço de verificação em background.")
    
    # Função interna que define um loop de verificação das VLANs.
    def check_loop():
        while not should_stop:
            # Obter VLANs ativas das configurações
            vlan_list = config_manager.get_active_vlans()
            
            # Usar configurações de concurrent pings
            max_workers = config_manager.get_config('network_settings').get('max_concurrent_pings', 3)
            
            # Usar um pool de threads para verificar VLANs simultaneamente
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                executor.map(background_ip_check, vlan_list)

            # Depois de atualizar o estado de todas as VLANs deste ciclo,
            # tenta o digest semanal de hosts offline >24h (rate-limit semanal
            # interno). Best-effort: nunca derruba o loop.
            try:
                monitor_transicoes.talvez_enviar_digest()
            except Exception as e:  # noqa: BLE001
                logging.warning(f"[BACKGROUND] digest offline falhou: {e}")

            # Aguardar intervalo configurado (usar o menor intervalo como base)
            ping_intervals = config_manager.get_config('ping_intervals')
            min_interval = min(ping_intervals.values()) if ping_intervals else 10
            
            # Verificar se deve parar durante o sleep
            for _ in range(int(min_interval)):
                if should_stop:
                    break
                time.sleep(1)

    # Inicia a execução do loop de verificação em uma nova thread.
    background_thread = threading.Thread(target=check_loop, daemon=True)
    background_thread.start()
    
# ----------------------------------------------------------------------------
# Auditoria de modificações (trilha em SQLite local)
# ----------------------------------------------------------------------------
@app.route('/configuracoes/historico')
@app.route(RAIZ + '/configuracoes/historico')
def historico_alteracoes():
    """Página com histórico de modificações nos devices (audit log SQLite)."""
    return render_template('historico_alteracoes.html', RAIZ=RAIZ)


@app.route('/api/audit/historico')
@app.route(RAIZ + '/api/audit/historico')
def api_audit_historico():
    """Lista paginada da trilha de auditoria. Filtros: vlan, ip, action, since."""
    try:
        limit  = min(int(request.args.get('limit', 100)), 500)
        offset = int(request.args.get('offset', 0))
        vlan   = request.args.get('vlan') or None
        ip     = request.args.get('ip') or None
        action = request.args.get('action') or None
        since  = request.args.get('since') or None
        entries = audit_mod.list_entries(limit=limit, offset=offset,
                                          vlan=vlan, ip=ip,
                                          action=action, since=since)
        total = audit_mod.count_entries(vlan=vlan, ip=ip, action=action, since=since)
        return jsonify({'success': True, 'total': total,
                        'limit': limit, 'offset': offset,
                        'entries': entries})
    except Exception as e:
        return jsonify({'success': False, 'erro': str(e)}), 500


@app.route('/api/audit/stats')
@app.route(RAIZ + '/api/audit/stats')
def api_audit_stats():
    """Resumo dos últimos N dias."""
    try:
        days = int(request.args.get('days', 7))
        return jsonify({'success': True, **audit_mod.stats(days=days)})
    except Exception as e:
        return jsonify({'success': False, 'erro': str(e)}), 500


# Ponto de entrada da aplicação. Executa o Flask quando o script é rodado diretamente.
if __name__ == '__main__':
    app.run(debug=True)  # Inicia o servidor Flask em modo debug.
