import json
import os
import logging
from threading import RLock
from app.migration import get_data_file_path

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class CLPManager:
    """Gerenciador de dados de CLPs (Controladores Logicos Programaveis)."""

    def __init__(self, data_file='clp_data.json'):
        self.data_file = get_data_file_path(data_file)
        self.data_lock = RLock()
        self.clp_data = self._load_data()

    def _load_data(self):
        """Carrega dados CLP do arquivo JSON."""
        try:
            full_path = os.path.abspath(self.data_file)
            logging.info(f"[CLP_MANAGER] Tentando carregar: {full_path}")

            if os.path.exists(self.data_file):
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                logging.info(f"[CLP_MANAGER] Carregados dados de {len(data)} CLPs")
                return data
            else:
                logging.warning(f"[CLP_MANAGER] Arquivo nao encontrado: {full_path}")
                return {}
        except Exception as e:
            logging.error(f"[CLP_MANAGER] Erro ao carregar dados CLP: {e}")
            return {}

    def get_clp_data(self, ip):
        """Retorna dados de um CLP especifico pelo IP."""
        with self.data_lock:
            data = self.clp_data.get(ip)
            if data:
                return dict(data)
            return None

    def get_all_clps(self):
        """Retorna lista resumida de todos os CLPs disponiveis."""
        with self.data_lock:
            result = {}
            for ip, data in self.clp_data.items():
                result[ip] = {
                    'titulo': data.get('titulo', ''),
                    'modelo': data.get('modelo', ''),
                    'aba_nome': data.get('aba_nome', ''),
                    'total_pontos': data.get('total_pontos', 0),
                    'tem_secoes': 'secoes' in data
                }
            return result

    def has_clp_data(self, ip):
        """Verifica se existe dados CLP para um IP."""
        with self.data_lock:
            return ip in self.clp_data

    def get_clp_ips(self):
        """Retorna lista de IPs que possuem dados CLP."""
        with self.data_lock:
            return list(self.clp_data.keys())

    def reload(self):
        """Recarrega dados do arquivo JSON."""
        with self.data_lock:
            self.clp_data = self._load_data()

    # ============================================================
    # ESCRITA: CRUD de pontos I/O
    # ============================================================

    def _save_data(self):
        """Grava o JSON em disco. Escreve em arquivo temporário + rename
        para evitar arquivo corrompido em caso de falha."""
        tmp = self.data_file + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.clp_data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.data_file)

    def _get_pontos_list(self, clp, secao_idx):
        """Retorna a lista de pontos da seção (ou root) onde escrever."""
        if 'secoes' in clp:
            secoes = clp['secoes']
            if not isinstance(secao_idx, int) or secao_idx < 0 or secao_idx >= len(secoes):
                raise IndexError(f"secao_idx inválido: {secao_idx}")
            return secoes[secao_idx].setdefault('pontos', [])
        return clp.setdefault('pontos', [])

    def _recalcular_total(self, clp):
        if 'secoes' in clp:
            clp['total_pontos'] = sum(len(s.get('pontos', [])) for s in clp['secoes'])
        else:
            clp['total_pontos'] = len(clp.get('pontos', []))

    def add_ponto(self, ip, ponto, secao_idx=None):
        """Adiciona um ponto. Retorna (sucesso, mensagem)."""
        with self.data_lock:
            clp = self.clp_data.get(ip)
            if not clp:
                return False, 'CLP não encontrado'
            try:
                pontos = self._get_pontos_list(clp, secao_idx if secao_idx is not None else 0)
            except IndexError as e:
                return False, str(e)
            # Sanitiza valores: string trim, sem None
            ponto_limpo = {k: ('' if v is None else str(v).strip()) for k, v in ponto.items()}
            pontos.append(ponto_limpo)
            self._recalcular_total(clp)
            self._save_data()
            return True, 'Ponto adicionado'

    def update_ponto(self, ip, idx, ponto, secao_idx=None):
        """Atualiza ponto[idx]. Retorna (sucesso, mensagem)."""
        with self.data_lock:
            clp = self.clp_data.get(ip)
            if not clp:
                return False, 'CLP não encontrado'
            try:
                pontos = self._get_pontos_list(clp, secao_idx if secao_idx is not None else 0)
            except IndexError as e:
                return False, str(e)
            if not isinstance(idx, int) or idx < 0 or idx >= len(pontos):
                return False, 'Índice inválido'
            ponto_limpo = {k: ('' if v is None else str(v).strip()) for k, v in ponto.items()}
            pontos[idx] = ponto_limpo
            self._save_data()
            return True, 'Ponto atualizado'

    def delete_ponto(self, ip, idx, secao_idx=None):
        """Remove ponto[idx]. Retorna (sucesso, mensagem)."""
        with self.data_lock:
            clp = self.clp_data.get(ip)
            if not clp:
                return False, 'CLP não encontrado'
            try:
                pontos = self._get_pontos_list(clp, secao_idx if secao_idx is not None else 0)
            except IndexError as e:
                return False, str(e)
            if not isinstance(idx, int) or idx < 0 or idx >= len(pontos):
                return False, 'Índice inválido'
            pontos.pop(idx)
            self._recalcular_total(clp)
            self._save_data()
            return True, 'Ponto removido'


# Instancia global
clp_manager = CLPManager()
