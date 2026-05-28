import json
import os
import logging
from threading import RLock
from datetime import datetime
from app.config_manager import config_manager
from app.migration import get_data_file_path

# Configurar logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class IPDeviceManager:
    """Gerenciador de dispositivos IP com tipos"""
    
    def __init__(self, devices_file='ip_devices.json'):
        self.devices_file = get_data_file_path(devices_file)
        self.devices_lock = RLock()  # RLock permite reentrância (mesma thread pode adquirir múltiplas vezes)
        self.devices = self._load_devices()
    
    def _load_devices(self):
        """Carrega dispositivos do arquivo JSON"""
        try:
            full_path = os.path.abspath(self.devices_file)
            logging.info(f"[DEVICE_MANAGER] Tentando carregar arquivo: {full_path}")
            
            if os.path.exists(self.devices_file):
                logging.info(f"[DEVICE_MANAGER] Arquivo encontrado, carregando dados...")
                with open(self.devices_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    total_vlans = len(data.get('vlans', {}))
                    logging.info(f"[DEVICE_MANAGER] Carregados dados de {total_vlans} VLANs")
                    return data
            else:
                logging.warning(f"[DEVICE_MANAGER] Arquivo não encontrado: {full_path}")
                # Se não existe, criar estrutura baseada no ips_list.json existente
                return self._migrate_from_ips_list()
        except Exception as e:
            logging.error(f"[DEVICE_MANAGER] Erro ao carregar dispositivos: {e}")
            return {"vlans": {}}
    
    def _migrate_from_ips_list(self):
        """Migra dados do ips_list.json existente"""
        try:
            ips_list_path = get_data_file_path('ips_list.json')
            if os.path.exists(ips_list_path):
                with open(ips_list_path, 'r', encoding='utf-8') as f:
                    old_data = json.load(f)
                
                new_structure = {"vlans": {}}
                
                for vlan, devices in old_data.get('vlans', {}).items():
                    new_structure['vlans'][vlan] = []
                    for device in devices:
                        new_device = {
                            "ip": device.get("ip", ""),
                            "descricao": device.get("descricao", ""),
                            "tipo": "",  # Novo campo vazio inicialmente
                            "created_at": datetime.now().isoformat(),
                            "updated_at": datetime.now().isoformat()
                        }
                        new_structure['vlans'][vlan].append(new_device)
                
                # Salvar a nova estrutura
                self._save_devices(new_structure)
                return new_structure
            else:
                return {"vlans": {}}
        except Exception as e:
            print(f"Erro na migração: {e}")
            return {"vlans": {}}
    
    def _save_devices(self, devices_data=None, audit_user=None, audit_source=None):
        """Salva dispositivos no arquivo JSON e registra diff no audit log.

        Se audit_user/audit_source não forem passados, tenta extrair da request
        Flask atual (X-Remote-User via nginx + path da rota). Assim qualquer
        endpoint que dispare um save herda automaticamente o contexto.
        """
        try:
            with self.devices_lock:
                data_to_save = devices_data if devices_data else self.devices
                # Snapshot do estado anterior (do disco) p/ diff de auditoria.
                old_data = None
                try:
                    if os.path.exists(self.devices_file):
                        with open(self.devices_file, 'r', encoding='utf-8') as f:
                            old_data = json.load(f)
                except Exception:
                    old_data = None
                with open(self.devices_file, 'w', encoding='utf-8') as f:
                    json.dump(data_to_save, f, indent=4, ensure_ascii=False)
                # Audit log — falha silenciosa (não bloqueia save se SQLite estiver indisponível).
                try:
                    # Extrai user/source automaticamente da request Flask atual
                    # (mesma convenção do scada-web: header X-Remote-User do nginx).
                    if audit_user is None or audit_source is None:
                        try:
                            from flask import request, has_request_context
                            if has_request_context():
                                if audit_user is None:
                                    audit_user = (request.headers.get('X-Remote-User')
                                                  or request.headers.get('Remote-User'))
                                if audit_source is None:
                                    audit_source = request.path
                        except Exception:
                            pass  # rodando fora de request (ex.: script CLI)
                    from app.audit import record_diff
                    record_diff(old_data or {}, data_to_save,
                                user=audit_user, source=audit_source)
                except Exception as e:
                    logging.warning(f"[DEVICE_MANAGER] audit log falhou: {e}")
                return True
        except Exception as e:
            print(f"Erro ao salvar dispositivos: {e}")
            return False
    
    def get_devices_by_vlan(self, vlan):
        """Obtém dispositivos de uma VLAN específica (retorna cópia para evitar deadlock)"""
        with self.devices_lock:
            devices = self.devices.get('vlans', {}).get(str(vlan), [])
            # Retorna uma cópia da lista para liberar o lock rapidamente
            devices_copy = [d.copy() for d in devices]
            logging.info(f"[DEVICE_MANAGER] get_devices_by_vlan({vlan}) - Encontrados {len(devices_copy)} dispositivos")
            dispositivos_com_tipo = [d for d in devices_copy if d.get('tipo') and d['tipo'].strip()]
            logging.info(f"[DEVICE_MANAGER] VLAN {vlan} - {len(dispositivos_com_tipo)} dispositivos com tipo")
            if dispositivos_com_tipo:
                exemplo = dispositivos_com_tipo[0]
                logging.info(f"[DEVICE_MANAGER] Exemplo: IP={exemplo.get('ip')}, Desc={exemplo.get('descricao')}, Tipo='{exemplo.get('tipo')}'")
            return devices_copy
    
    def add_device(self, vlan, ip, descricao, tipo=""):
        """Adiciona um novo dispositivo"""
        try:
            with self.devices_lock:
                vlan_str = str(vlan)
                
                if 'vlans' not in self.devices:
                    self.devices['vlans'] = {}
                
                if vlan_str not in self.devices['vlans']:
                    self.devices['vlans'][vlan_str] = []
                
                # Verificar se IP já existe
                for device in self.devices['vlans'][vlan_str]:
                    if device['ip'] == ip:
                        return False, "IP já existe nesta VLAN"
                
                new_device = {
                    "ip": ip,
                    "descricao": descricao,
                    "tipo": tipo,
                    "created_at": datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat()
                }
                
                self.devices['vlans'][vlan_str].append(new_device)
                
                if self._save_devices():
                    return True, "Dispositivo adicionado com sucesso"
                else:
                    return False, "Erro ao salvar dispositivo"
        
        except Exception as e:
            print(f"Erro ao adicionar dispositivo: {e}")
            return False, str(e)
    
    def update_device(self, vlan, ip, descricao=None, tipo=None, sensores=None, tabelas_sql=None):
        """Atualiza um dispositivo existente"""
        try:
            with self.devices_lock:
                vlan_str = str(vlan)

                if vlan_str not in self.devices.get('vlans', {}):
                    # Cria a VLAN se ela ainda não existir (caso de IP novo numa VLAN nova)
                    self.devices.setdefault('vlans', {})[vlan_str] = []

                for device in self.devices['vlans'][vlan_str]:
                    if device['ip'] == ip:
                        if descricao is not None:
                            device['descricao'] = descricao
                        if tipo is not None:
                            device['tipo'] = tipo
                        if sensores is not None:
                            device['sensores'] = sensores
                        if tabelas_sql is not None:
                            device['tabelas_sql'] = tabelas_sql
                        device['updated_at'] = datetime.now().isoformat()

                        if self._save_devices():
                            return True, "Dispositivo atualizado com sucesso"
                        else:
                            return False, "Erro ao salvar alterações"

                # IP não existe na VLAN — cria o registro
                novo = {
                    'ip': ip,
                    'descricao': descricao or '',
                    'tipo': tipo or '',
                    'sensores': sensores if sensores is not None else [],
                    'tabelas_sql': tabelas_sql if tabelas_sql is not None else [],
                    'created_at': datetime.now().isoformat(),
                    'updated_at': datetime.now().isoformat(),
                }
                self.devices['vlans'][vlan_str].append(novo)
                if self._save_devices():
                    return True, "Dispositivo criado com sucesso"
                return False, "Erro ao salvar novo dispositivo"

        except Exception as e:
            print(f"Erro ao atualizar dispositivo: {e}")
            return False, str(e)
    
    def delete_device(self, vlan, ip):
        """Remove um dispositivo"""
        try:
            with self.devices_lock:
                vlan_str = str(vlan)
                
                if vlan_str not in self.devices.get('vlans', {}):
                    return False, "VLAN não encontrada"
                
                devices_list = self.devices['vlans'][vlan_str]
                for i, device in enumerate(devices_list):
                    if device['ip'] == ip:
                        del devices_list[i]
                        
                        if self._save_devices():
                            return True, "Dispositivo removido com sucesso"
                        else:
                            return False, "Erro ao salvar alterações"
                
                return False, "Dispositivo não encontrado"
        
        except Exception as e:
            print(f"Erro ao remover dispositivo: {e}")
            return False, str(e)
    
    def get_device_types_by_vlan(self, vlan):
        """Obtém tipos únicos de dispositivos em uma VLAN"""
        with self.devices_lock:
            devices = self.devices.get('vlans', {}).get(str(vlan), [])
            types = set()
            for device in devices:
                if device.get('tipo'):
                    types.add(device['tipo'])
            return list(types)
    
    def search_devices(self, query, vlan=None):
        """Busca dispositivos por descrição, IP ou tipo"""
        results = []
        query_lower = query.lower()
        
        with self.devices_lock:
            vlans_to_search = [str(vlan)] if vlan else self.devices.get('vlans', {}).keys()
            
            for vlan_id in vlans_to_search:
                for device in self.devices.get('vlans', {}).get(vlan_id, []):
                    if (query_lower in device.get('descricao', '').lower() or
                        query_lower in device.get('ip', '').lower() or
                        query_lower in device.get('tipo', '').lower()):
                        
                        result = device.copy()
                        result['vlan'] = vlan_id
                        results.append(result)
        
        return results
    
    def get_statistics(self):
        """Obtém estatísticas dos dispositivos"""
        stats = {
            'total_devices': 0,
            'devices_by_vlan': {},
            'devices_by_type': {},
            'devices_with_type': 0,
            'devices_without_type': 0
        }
        
        with self.devices_lock:
            for vlan, devices in self.devices.get('vlans', {}).items():
                stats['devices_by_vlan'][vlan] = len(devices)
                stats['total_devices'] += len(devices)
                
                for device in devices:
                    device_type = device.get('tipo', '')
                    if device_type:
                        stats['devices_with_type'] += 1
                        if device_type not in stats['devices_by_type']:
                            stats['devices_by_type'][device_type] = 0
                        stats['devices_by_type'][device_type] += 1
                    else:
                        stats['devices_without_type'] += 1
        
        return stats

# Instância global do gerenciador de dispositivos
device_manager = IPDeviceManager()