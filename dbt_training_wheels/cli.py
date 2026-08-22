"""CLI entry point for DBT Training Wheels."""

import argparse

# Load environment variables from .env file in project root
# Get the project root directory (parent of the dbt_training_wheels package directory)
from pathlib import Path

from dotenv import load_dotenv

PACKAGE_DIR = Path(__file__).parent
PROJECT_ROOT = PACKAGE_DIR.parent
dotenv_path = PROJECT_ROOT / ".env"

# Load .env file with debug info
if dotenv_path.exists():
    load_dotenv(dotenv_path=dotenv_path, override=True)
    print(f"[DBT Training Wheels CLI] Loaded .env from: {dotenv_path}")
    print("[DBT Training Wheels CLI] Using SSH keys for GitHub authentication")
else:
    print(f"[DBT Training Wheels CLI] Warning: .env file not found at {dotenv_path}")


def main():
    """Main entry point for the dbt_training_wheels command."""
    parser = argparse.ArgumentParser(prog="dbt_training_wheels", description="DBT Training Wheels - SQL to dbt Conversion Tool")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind the server to (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Port to run the server on (default: 8000)")
    parser.add_argument("--debug", action="store_true", help="Run in debug mode")
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")

    args = parser.parse_args()

    # Import app here to avoid circular imports
    from dbt_training_wheels.app import app

    print(f"""
    ╔═══════════════════════════════════════════════════════════╗
    ║                                                           ║
    ║   DBT Training Wheels - SQL to dbt Conversion Tool                      ║
    ║                                                           ║
    ║   Server running at: http://{args.host}:{args.port}                 ║
    ║   Press Ctrl+C to stop                                    ║
    ║                                                           ║
    ╚═══════════════════════════════════════════════════════════╝
    """)

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
