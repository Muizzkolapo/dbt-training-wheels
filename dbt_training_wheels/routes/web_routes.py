"""Web routes for DBT Training Wheels."""

from flask import Blueprint, render_template

from dbt_training_wheels.config import get_conversion_steps, get_org_config
from dbt_training_wheels.services.query_service import load_conversions, load_queries_from_directory

web_bp = Blueprint("web", __name__)


@web_bp.route("/")
def index():
    """
    Render the main interface page.

    Returns:
        Rendered HTML template
    """
    config = get_org_config()
    scheduled_queries = load_queries_from_directory(config)
    # The sidebar lists conversions - one uploaded folder is one entry
    conversions = load_conversions(config)

    # Prepare config info for template
    config_info = None
    if config:
        config_info = {
            "org_name": config.org_name,
            "dialect": config.database.dialect,
            "materialization": config.output.default_materialization,
            "naming": {
                # 2-layer architecture naming
                "staging_model_prefix": config.naming.staging_model_prefix,
                "intermediate_model_prefix": config.naming.intermediate_model_prefix,
                "mart_model_prefix": config.naming.mart_model_prefix,
                "layer_folder_names": config.naming.layer_folder_names,
            },
        }

    # Get conversion steps (from config or Python fallback)
    conversion_steps = get_conversion_steps(config)

    return render_template(
        "index.html",
        scheduled_queries=scheduled_queries,
        conversions=conversions,
        conversion_steps=conversion_steps,
        org_config=config_info,
    )


@web_bp.route("/troubleshooting")
def troubleshooting():
    """
    Render the troubleshooting guide page.

    Returns:
        Rendered HTML template
    """
    return render_template("troubleshooting.html")
