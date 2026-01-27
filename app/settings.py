"""
Configurações Centralizadas do IP Monitor
==========================================

Este arquivo centraliza TODAS as configurações e variáveis globais do projeto.
Objetivo: Padronizar e facilitar migração futura, SEM quebrar código existente.

IMPORTANTE: 
- Configurações de DEPLOY são lidas de .env.deploy (produção)
- Configurações de DESENVOLVIMENTO usam valores padrão ou .env local

Data de criação: 2025-11-14
Versão: 2.0.0 - Migrado para .env.deploy
"""

import os
import platform
from pathlib import Path
from dotenv import load_dotenv


# ============================================================================
# CARREGAR CONFIGURAÇÕES DE .env.deploy
# ============================================================================
# Em produção: usa .env.deploy (versionado com valores padrão)
# Sobrescritas locais: usa .env.local (opcional, não versionado)
# Em desenvolvimento: usa .env (opcional) ou valores padrão

# Localizar arquivo .env apropriado
BASE_DIR = Path(__file__).parent.parent
env_file_deploy = BASE_DIR / '.env.deploy'
env_file_local = BASE_DIR / '.env.local'
env_file_dev = BASE_DIR / '.env'

# Carregar em ordem de preferência (cada um sobrescreve o anterior)
if env_file_deploy.exists():
    load_dotenv(env_file_deploy)
    print(f"[SETTINGS] Configurações base carregadas de: {env_file_deploy}")
    
    # Carregar .env.local se existir (sobrescreve .env.deploy)
    if env_file_local.exists():
        load_dotenv(env_file_local, override=True)
        print(f"[SETTINGS] Configurações locais carregadas de: {env_file_local}")
elif env_file_dev.exists():
    load_dotenv(env_file_dev)
    print(f"[SETTINGS] Configurações de desenvolvimento carregadas de: {env_file_dev}")
else:
    print("[SETTINGS] Usando valores padrão (nenhum arquivo .env encontrado)")


# ============================================================================
# SEÇÃO 1: IDENTIFICAÇÃO DO PROJETO
# ============================================================================
# Valores lidos de .env.deploy ou fallback para desenvolvimento

PROJECT_NAME = os.getenv('PROJECT_NAME', 'ipmonitor')
PROJECT_NAME_DISPLAY = os.getenv('PROJECT_NAME_DISPLAY', 'IP Monitor')
PROJECT_NAME_GIT = os.getenv('PROJECT_NAME_GIT', 'ip-monitor')
SERVICE_NAME = os.getenv('SERVICE_NAME', 'ipmonitor')

# Constantes de aplicação
MODULE_NAME = "config"
PORT_DEFAULT = int(os.getenv('PORT', '8000'))


# ============================================================================
# SEÇÃO 2: ROTAS E URLs
# ============================================================================
# Define como o serviço é acessado via web

# Prefixo das rotas (lido de .env.deploy)
ROUTES_PREFIX = os.getenv('ROUTES_PREFIX', '/ipmonitor')

# Domínio base do sistema
DOMAIN_BASE = os.getenv('DOMAIN_BASE', 'automacao.tce.go.gov.br')

# URLs completas
BASE_URL_PRODUCTION = f"https://{DOMAIN_BASE}{ROUTES_PREFIX}"
API_BASE_URL_PRODUCTION = f"{BASE_URL_PRODUCTION}/api"


# ============================================================================
# SEÇÃO 3: ESTRUTURA DE PASTAS - PRODUÇÃO (Linux)
# ============================================================================
# Organização padronizada de diretórios no servidor de produção

# Raiz dos softwares TCE (lido de .env.deploy)
BACKEND_ROOT = os.getenv('BACKEND_ROOT', '/var/softwaresTCE')
FRONTEND_ROOT = os.getenv('FRONTEND_ROOT', f'/var/www/{DOMAIN_BASE}')
# DATA_ROOT aponta para o diretório de dados no volume Docker
# Definido em .env.deploy como /var/softwaresTCE/ip-monitor/dados
DATA_ROOT = os.getenv('DATA_ROOT', '/var/softwaresTCE/ip-monitor/dados')
LOGS_ROOT = os.getenv('LOGS_ROOT', '/var/softwaresTCE/ip-monitor/logs')

# Caminhos derivados (calculados a partir das raízes)
PROJECT_BACKEND_DEPLOY = os.path.join(BACKEND_ROOT, PROJECT_NAME)
PROJECT_FRONTEND_DEPLOY = os.path.join(FRONTEND_ROOT, PROJECT_NAME)
# DATA_ROOT já é o diretório final de dados (sem subpasta PROJECT_NAME)
PROJECT_DATA_DEPLOY = DATA_ROOT
PROJECT_LOGS_DEPLOY = LOGS_ROOT


# ============================================================================
# SEÇÃO 4: ESTRUTURA DE PASTAS - DESENVOLVIMENTO (Windows/Mac/Linux Local)
# ============================================================================
# Caminhos para ambiente de desenvolvimento local

if platform.system() == "Linux":
    # Em produção Linux, usa os caminhos do .env.deploy
    PROJECT_BACKEND = PROJECT_BACKEND_DEPLOY
    PROJECT_FRONTEND = PROJECT_FRONTEND_DEPLOY
    PROJECT_DATA = PROJECT_DATA_DEPLOY
    PROJECT_LOGS = PROJECT_LOGS_DEPLOY
    PROJECT_STATIC = os.path.join(PROJECT_BACKEND, "app", "static")
else:
    # Desenvolvimento local - usa diretório relativo ao projeto
    PROJECT_BACKEND = str(BASE_DIR)
    PROJECT_FRONTEND = str(BASE_DIR / "app" / "templates")
    PROJECT_DATA = str(BASE_DIR / "data")
    PROJECT_LOGS = str(BASE_DIR / "logs")
    PROJECT_STATIC = str(BASE_DIR / "app" / "static")


# ============================================================================
# SEÇÃO 5: ARQUIVOS DE DADOS
# ============================================================================
# Localização de arquivos JSON e bancos de dados

# Arquivos de configuração
APP_CONFIG_FILE = "app_config.json"
APP_CONFIG_PATH = os.path.join(PROJECT_DATA, APP_CONFIG_FILE)

# Arquivos de dados
DEVICES_FILE = "ip_devices.json"
DEVICES_PATH = os.path.join(PROJECT_DATA, DEVICES_FILE)

IPS_LIST_FILE = "ips_list.json"
IPS_LIST_PATH = os.path.join(PROJECT_DATA, IPS_LIST_FILE)


# ============================================================================
# SEÇÃO 6: CONFIGURAÇÕES DO SYSTEMD SERVICE
# ============================================================================
# Configurações para o serviço systemd no servidor

SERVICE_FILE = f"{SERVICE_NAME}.service"
SERVICE_PATH = f"/usr/lib/systemd/system/{SERVICE_FILE}"
SERVICE_DESCRIPTION = f"TCE {PROJECT_NAME_DISPLAY} - Sistema de Monitoramento de Dispositivos de Rede"


# ============================================================================
# SEÇÃO 7: CONFIGURAÇÕES DO GIT
# ============================================================================
# Informações do repositório (lido de .env.deploy)

GIT_REPO_NAME = os.getenv('GIT_REPO_NAME', PROJECT_NAME_GIT)
GIT_REPO_OWNER = os.getenv('GIT_REPO_OWNER', 'TCE-Manutencao-Predial')
GIT_REPO_URL = f"https://github.com/{GIT_REPO_OWNER}/{GIT_REPO_NAME}.git"


# ============================================================================
# SEÇÃO 8: CONFIGURAÇÕES DE REDE E MONITORAMENTO
# ============================================================================
# Parâmetros técnicos do sistema de monitoramento

# VLANs monitoradas
VLANS = {
    70: "Câmeras",
    80: "Alarme",
    85: "Automação Ethernet",
    86: "Automação WiFi",
    200: "Telefonia IP Fixa",
    204: "Telefonia IP Móvel"
}

# Base de rede para cada VLAN (lido de .env.deploy)
NETWORK_BASE = os.getenv('NETWORK_BASE', '172.17.{vlan}.')

# Configurações padrão de ping
PING_TIMEOUT_DEFAULT = 2
MAX_CONCURRENT_PINGS_DEFAULT = 3
RETRY_ATTEMPTS_DEFAULT = 2


# ============================================================================
# SEÇÃO 9: FUNÇÕES UTILITÁRIAS
# ============================================================================

def ensure_directories():
    """
    Garante que todos os diretórios necessários existem.
    Esta função deve ser chamada na inicialização da aplicação.
    """
    directories = [
        PROJECT_DATA,
        PROJECT_LOGS,
        PROJECT_STATIC
    ]
    
    for directory in directories:
        if not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
            

def get_environment():
    """
    Retorna o ambiente atual (production ou development).
    """
    return "production" if platform.system() == "Linux" else "development"


def get_routes_prefix():
    """
    Retorna o prefixo de rotas apropriado para o ambiente.
    Em produção retorna '/ipmonitor', em desenvolvimento retorna ''.
    """
    return ROUTES_PREFIX if get_environment() == "production" else ""


def get_api_base_url():
    """
    Retorna a URL base da API para o ambiente atual.
    """
    if get_environment() == "production":
        return f"{ROUTES_PREFIX}/api"
    else:
        return "/api"


# ============================================================================
# SEÇÃO 10: CONFIGURAÇÕES DE LOGGING
# ============================================================================

LOG_FILE = os.path.join(PROJECT_LOGS, "app.log")
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_LEVEL = "INFO"
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10MB
LOG_BACKUP_COUNT = 5


# ============================================================================
# SEÇÃO 11: COMPATIBILIDADE COM CÓDIGO EXISTENTE
# ============================================================================
# Aliases para manter compatibilidade com variáveis usadas no código atual

# Para deploy.sh
ROOT_FRONTEND = PROJECT_FRONTEND
ROOT_BACKEND = PROJECT_BACKEND
ROOT_DATA = PROJECT_DATA
ROOT_SOFTWARES = BACKEND_ROOT

# Para routes.py
RAIZ = ROUTES_PREFIX

# Para config_manager.py (já usa get_data_file_path)
# Sem mudanças necessárias


# ============================================================================
# INFORMAÇÕES DO ARQUIVO
# ============================================================================

__version__ = "1.0.0"
__author__ = "TCE Manutenção Predial"
__date__ = "2025-11-14"
__description__ = "Configurações centralizadas do IP Monitor"


# ============================================================================
# EXEMPLO DE USO
# ============================================================================
"""
# No código Python, importe as configurações:
from app.settings import (
    PROJECT_NAME,
    ROUTES_PREFIX,
    PROJECT_DATA,
    ensure_directories
)

# Garanta que os diretórios existem:
ensure_directories()

# Use as constantes:
print(f"Projeto: {PROJECT_NAME}")
print(f"Dados em: {PROJECT_DATA}")
"""
