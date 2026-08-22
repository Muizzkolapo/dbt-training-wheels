"""
DBT Training Wheels - SQL to dbt Conversion Tool

A Flask application for converting scheduled queries to dbt models.
"""

import logging
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask
from flask_wtf.csrf import CSRFProtect

# Load environment variables from .env file in project root
# Get the project root directory (parent of the dbt_training_wheels package directory)
PACKAGE_DIR = Path(__file__).parent
PROJECT_ROOT = PACKAGE_DIR.parent
dotenv_path = PROJECT_ROOT / ".env"

# Load .env file with debug info
if dotenv_path.exists():
    load_dotenv(dotenv_path=dotenv_path, override=True)
    # Log at startup (before Flask logger is configured)
    print(f"[DBT Training Wheels] Loaded .env from: {dotenv_path}")
    print("[DBT Training Wheels] Using SSH keys for GitHub authentication")
else:
    print(f"[DBT Training Wheels] Warning: .env file not found at {dotenv_path}")

# Imports after loading .env to ensure environment variables are available
from dbt_training_wheels.config import (  # noqa: E402
    DEBUG,
    HOST,
    PORT,
    STATIC_DIRECTORY,
    TEMPLATE_DIRECTORY,
    load_organization_config,
)
from dbt_training_wheels.routes.api import api_bp  # noqa: E402
from dbt_training_wheels.routes.web_routes import web_bp  # noqa: E402

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Initialize Flask application
app = Flask(__name__, template_folder=TEMPLATE_DIRECTORY, static_folder=STATIC_DIRECTORY)

# Configure secret key for CSRF protection (use environment variable in production)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", secrets.token_hex(32))

# Limit request body size to prevent memory exhaustion (default 2MB)
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_CONTENT_LENGTH", 2 * 1024 * 1024))

# Initialize CSRF protection.
#
# This covers the API blueprints too. The app binds to a local port with no
# authentication, so without CSRF any page in the user's browser could POST to
# it and drive the tool -- deleting queries or pushing to GitHub -- using the
# user's own SSH keys. The browser sends no credentials we could check instead,
# so the token is the only thing distinguishing a real request from a forged one.
#
# static/js/csrf.js reads the token from the meta tag in the page and attaches it
# as X-CSRFToken on every state-changing fetch.
csrf = CSRFProtect(app)

# Load and store organization configuration
with app.app_context():
    org_config = load_organization_config()
    app.org_config = org_config
    if org_config.org_name:
        logger.info(f"DBT Training Wheels configured for organization: {org_config.org_name}")
    logger.info(f"Database dialect: {org_config.database.dialect}")
    logger.info(f"Intermediate model prefix: {org_config.naming.intermediate_model_prefix}")

# Register blueprints
app.register_blueprint(web_bp)
app.register_blueprint(api_bp)


if __name__ == "__main__":
    app.run(debug=DEBUG, host=HOST, port=PORT)
