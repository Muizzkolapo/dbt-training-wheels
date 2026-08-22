"""API routes package for DBT Training Wheels.

This package organizes API endpoints into domain-specific modules:
- analysis: Query analysis and file generation
- models: Source preview and model management
- upload: File upload and listing
- github: GitHub integration
- config: Configuration endpoints

All modules register their blueprints here for the main app to consume.
"""

from flask import Blueprint

from dbt_training_wheels.routes.api.analysis import analysis_bp
from dbt_training_wheels.routes.api.config import config_bp
from dbt_training_wheels.routes.api.cross_project_refs import cross_project_refs_bp
from dbt_training_wheels.routes.api.github import github_bp
from dbt_training_wheels.routes.api.models import models_bp
from dbt_training_wheels.routes.api.query_config import query_config_bp
from dbt_training_wheels.routes.api.upload import upload_bp

# Create main API blueprint
api_bp = Blueprint("api", __name__, url_prefix="/api")

# Register all sub-blueprints
api_bp.register_blueprint(analysis_bp)
api_bp.register_blueprint(models_bp)
api_bp.register_blueprint(upload_bp)
api_bp.register_blueprint(github_bp)
api_bp.register_blueprint(config_bp)
api_bp.register_blueprint(cross_project_refs_bp)
api_bp.register_blueprint(query_config_bp)

__all__ = ["api_bp"]
