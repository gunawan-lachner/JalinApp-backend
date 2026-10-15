# Core API Routes
from app.routes.core.connections import connection_bp
from app.routes.core.queries import queries_bp as core_queries_bp
from app.routes.core.tables import tables_bp

# Query Management Routes
from app.routes.queries import queries_bp

# Transaction API Routes (moved from rna, no /rna prefix)
from app.routes.transaction.rna_transactions import rna_transaction_bp

# Rules API Routes
from app.routes.rules.rules import rna_rules_bp

# Routing API Routes
from app.routes.gateway.rna_routing import routing_bp

# Participant API Routes
from app.routes.participant.participant import participant_bp

# RENJS API Routes
from app.routes.renjs.renjs import renjs_bp

# Gateway API Routes
from app.routes.gateway.gateway import gateway_bp
from app.routes.gateway.routing import routing_api_bp

# Transaction Utility Routes
from app.routes.transaction.transaction import transaction_bp
from app.routes.transaction.bin_routing import bin_routing_bp

# SSH API Routes
from app.routes.ssh.ssh_log_extractor import ssh_extractor_bp
from app.routes.ssh.ssh_client import ssh_client_bp
from app.routes.ssh.ssh_transfer import ssh_transfer_bp
from app.routes.ssh.file_manager import file_manager_bp

# Graylog API Routes
from app.routes.graylog.graylog_rules import graylog_rules_bp
from app.routes.graylog.graylog_data import graylog_data_bp
from app.routes.graylog.graylog_config import graylog_config_bp

# Redmine API Routes
from app.routes.redmine import redmine_bp

# Authentication routes (JWT only)
from app.routes.auth import auth_bp

# Network Message API Routes
from app.routes.network_message.network_message import network_message_bp

# Blueprint registration function
def register_blueprints(app):
    """Register all blueprints with proper URL prefixes"""
    
    # Core API routes
    app.register_blueprint(connection_bp, url_prefix='/api/core/connections')
    app.register_blueprint(core_queries_bp, url_prefix='/api/core/queries')
    # Tables API routes (moved to short prefix '/api/table')
    app.register_blueprint(tables_bp, url_prefix='/api/table')
    # Backward-compatible alias for legacy clients that call '/api/tables/...'
    app.register_blueprint(tables_bp, url_prefix='/api/tables', name='tables_legacy')
    
    # Query Management routes
    app.register_blueprint(queries_bp, url_prefix='/api/queries')
    
    # Transaction API routes (no /rna prefix)
    app.register_blueprint(rna_transaction_bp, url_prefix='/api/transactions')
    
    # Rules API routes
    app.register_blueprint(rna_rules_bp, url_prefix='/api/rules')
    
    # Routing API routes
    app.register_blueprint(routing_bp, url_prefix='/api/routing')
    
    # Participant API routes
    # Register participant under /api so endpoints are /api/participant[...] (not /api/rna/participant[...])
    app.register_blueprint(participant_bp, url_prefix='/api/participant')

    # RENJS API routes
    app.register_blueprint(renjs_bp, url_prefix='/api/renjs')
    
    # Gateway API routes
    app.register_blueprint(gateway_bp, url_prefix='/api/gateway')
    app.register_blueprint(routing_api_bp, url_prefix='/api/routing-sim')
    
    # Transaction Utility routes
    app.register_blueprint(transaction_bp, url_prefix='/api/transaction')
    app.register_blueprint(bin_routing_bp, url_prefix='/api/bin-routing')
    
    # SSH API routes
    app.register_blueprint(ssh_extractor_bp, url_prefix='/api/ssh/extractor')
    app.register_blueprint(ssh_client_bp, url_prefix='/api/ssh/client')
    app.register_blueprint(ssh_transfer_bp, url_prefix='/api/ssh/transfer')
    app.register_blueprint(file_manager_bp, url_prefix='/api/ssh/file-manager')
    
    # Graylog API routes
    app.register_blueprint(graylog_rules_bp, url_prefix='/api/graylog/rules')
    app.register_blueprint(graylog_data_bp, url_prefix='/api/graylog/data')
    app.register_blueprint(graylog_config_bp, url_prefix='/api/graylog/config')
    
    # Redmine API routes
    app.register_blueprint(redmine_bp, url_prefix='/api/redmine')
    
    # Authentication routes (JWT only)
    app.register_blueprint(auth_bp, url_prefix='/api/auth')
    
    # Network Message API routes
    app.register_blueprint(network_message_bp, url_prefix='/api/network-message')
