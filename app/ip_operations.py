import concurrent.futures  # Importa o módulo para execução paralela de tarefas.
from ping3 import ping  # Importa a função ping do módulo ping3 para verificar conectividade com IPs.
from collections import deque  # Importa deque, uma estrutura de dados de fila, que será usada para o histórico de status dos IPs.
import json  # Importa o módulo JSON para manipulação de arquivos JSON.
import logging  # Importa logging para diagnóstico
from app.config_manager import config_manager  # Importa o gerenciador de configurações.
from app.device_manager import device_manager  # Importa o gerenciador de dispositivos.

# Configurar logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Função principal que verifica os IPs em uma determinada rede base.
def verificar_ips(rede_base: str):
    # Obtém configurações atuais do sistema
    network_config = config_manager.get_config('network_settings')
    ping_timeout = network_config.get('ping_timeout', 2)
    max_workers = network_config.get('max_concurrent_pings', 3) * 20  # Multiplica para ter mais threads para IPs
    retry_attempts = network_config.get('retry_attempts', 2)
    
    # Cria uma lista de IPs na rede base, variando de 1 a 254.
    ip_list = [rede_base + str(i) for i in range(1, 255)]
    
    # Cria uma fila (deque) com limite de 10 elementos para armazenar o histórico do status dos IPs.
    ip_history = deque(maxlen=10)
    
    print(f"Verificando rede base n° {rede_base} (timeout: {ping_timeout}s, workers: {max_workers}, retry: {retry_attempts})")
    
    # Inicializa o histórico com "off" (sem conectividade) para cada IP.
    for i in range(0, 10):
        ip_history.append("off")
    
    # Cria um dicionário onde cada IP terá um deque de 10 posições para armazenar seu histórico de status (on ou off).
    ip_status_dict = {ip: deque(["off"] * 10, maxlen=10) for ip in ip_list}

    # Outro dicionário para armazenar o status final ("on" ou "off") de cada IP após a verificação.
    ip_checked = {ip: "on" for ip in ip_list}

    # Função auxiliar que verifica o status de um IP (ping).
    def verificar_ip(ip):
        # Tenta pingar o IP com configurações dinâmicas
        success = False
        
        # Implementa retry attempts
        for attempt in range(retry_attempts + 1):
            if ping(ip, timeout=ping_timeout): 
                success = True
                break
        
        if success:
            ip_status_dict[ip].append("on")
        else:
            ip_status_dict[ip].append("off")

    # Usa um executor de pool de threads para verificar os IPs simultaneamente (concorrência).
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        executor.map(verificar_ip, ip_list)  # Aplica a função verificar_ip para cada IP da lista.

    # Após a verificação, atualiza o status final de cada IP.
    for ip in ip_list:
        # Se não houve nenhum "on" no histórico, marca o IP como "off". Caso contrário, "on".
        if ip_status_dict[ip].count("on") == 0:
            ip_checked[ip] = "off"
        else:
            ip_checked[ip] = "on"

    # Extrai o número da VLAN da rede base (assumindo que está no terceiro octeto do IP).
    vlan = rede_base.split('.')[2]
    
    logging.info(f"[IP_OPERATIONS] Processando VLAN {vlan}")
    
    # Obtém a lista de dispositivos da VLAN usando o device_manager
    vlan_devices = device_manager.get_devices_by_vlan(int(vlan))
    
    logging.info(f"[IP_OPERATIONS] Dispositivos encontrados na VLAN {vlan}: {len(vlan_devices)}")
    if vlan_devices:
        for device in vlan_devices[:3]:  # Log apenas os primeiros 3 para não poluir
            logging.info(f"[IP_OPERATIONS] Dispositivo: IP={device.get('ip')}, Desc={device.get('descricao')}, Tipo={device.get('tipo', 'VAZIO')}")
                
    # Cria uma lista de dicionários com o status de cada IP (IP e se está "on" ou "off").
    ip_status_list = [{"ip": ip, "status": status} for ip, status in ip_checked.items()]
    
    # Se existir uma lista de dispositivos correspondente à VLAN atual, adiciona descrições e tipos aos IPs.
    if vlan_devices:
        dispositivos_com_tipo = 0
        for item in ip_status_list:
            for device in vlan_devices:
                if item['ip'] == device['ip']:  # Se o IP do dispositivo corresponder ao IP verificado.
                    item['descricao'] = device['descricao']  # Adiciona a descrição associada ao IP.
                    item['tipo'] = device.get('tipo', '')  # Adiciona o tipo do dispositivo.
                    item['sensores'] = device.get('sensores', [])
                    item['tabelas_sql'] = device.get('tabelas_sql', [])
                    if item['tipo']:
                        dispositivos_com_tipo += 1
                    break
            else:
                item['descricao'] = '-'  # Se não houver correspondência, adiciona "-" como descrição.
                item['tipo'] = ''  # Se não houver correspondência, tipo vazio.
                item['sensores'] = []
                item['tabelas_sql'] = []

        logging.info(f"[IP_OPERATIONS] Dispositivos com tipo definido: {dispositivos_com_tipo}")
    else:
        # Se não houver uma lista de dispositivos para a VLAN, adiciona "-" para todos os IPs.
        for item in ip_status_list:
            item['descricao'] = '-'
            item['tipo'] = ''
            item['sensores'] = []
            item['tabelas_sql'] = []
        logging.info(f"[IP_OPERATIONS] Nenhum dispositivo encontrado para VLAN {vlan}")

    # Log de algumas amostras do resultado final
    amostras_com_tipo = [item for item in ip_status_list if item.get('tipo') and item['descricao'] != '-']
    logging.info(f"[IP_OPERATIONS] Amostras com tipo no resultado final: {len(amostras_com_tipo)}")
    for amostra in amostras_com_tipo[:2]:
        logging.info(f"[IP_OPERATIONS] Resultado final: IP={amostra['ip']}, Desc={amostra['descricao']}, Tipo={amostra['tipo']}")

    return ip_status_list  # Retorna a lista de status de todos os IPs.

# Função que carrega as VLANs de um arquivo JSON (mantida para compatibilidade, mas não é mais usada).
def vlan_loader():
    file_path = 'ips_list.json'  # Define o caminho do arquivo JSON.
    # Abre o arquivo JSON e carrega os dados.
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            data = json.load(file)
        return data  # Retorna os dados carregados.
    except FileNotFoundError:
        # Se o arquivo não existir, retorna estrutura vazia
        return {"vlans": {}}
