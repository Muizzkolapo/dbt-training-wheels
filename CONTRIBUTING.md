# Contributing to DBT Training Wheels

## Development Setup

1. **Install uv:**
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. **Install dependencies** (creates `.venv` and installs the dev group):
   ```bash
   uv sync
   ```

3. **Install pre-commit hooks:**
   ```bash
   uv run pre-commit install
   uv run pre-commit install --hook-type pre-push
   ```

## Code Style

The following tools run automatically via pre-commit hooks:

- **Linting:** ruff (auto-fixes on commit)
- **Formatting:** ruff-format
- **Dead code:** vulture
- **Secrets:** detect-secrets

Run all checks manually:
```bash
uv run pre-commit run --all-files
```

## Testing

```bash
uv run pytest tests/ -v
```

## Regenerating the Tailwind stylesheet

`static/css/tailwind.css` is generated and committed. It replaced the
`cdn.tailwindcss.com` script, which shipped the JIT compiler to the browser and made a
network request on every page load of a tool that otherwise only talks to localhost.

Because it is pre-built, it contains only the utility classes present in the source at
generation time. **If you add a Tailwind class the file doesn't already carry, it will
have no effect until you regenerate:**

```bash
cd "$(mktemp -d)"
cat > tailwind.config.js <<'EOF'
module.exports = {
  content: [
    "<repo>/dbt_training_wheels/templates/**/*.html",
    "<repo>/dbt_training_wheels/static/js/**/*.js",
  ],
}
EOF
printf '@tailwind base;\n@tailwind components;\n@tailwind utilities;\n' > input.css
npx -y tailwindcss@3.4.17 -c tailwind.config.js -i input.css -o out.css --minify
```

Then copy `out.css` into `dbt_training_wheels/static/css/tailwind.css`, keeping the
header comment at the top of the existing file.

## Project Structure

```
dbt_training_wheels/
├── app.py              # Flask app entry point
├── config.py           # Configuration loading
├── routes/api/         # REST API endpoints
├── services/           # Business logic
├── parsers/            # SQL parsing (Strategy pattern)
├── repositories/       # Data access (Repository pattern)
├── static/js/          # Frontend JavaScript
└── templates/          # Jinja2 templates
```

For detailed architecture, see [DOCS.md](DOCS.md#architecture).

## PR Process

1. Create a feature branch from `main`
2. Make your changes
3. Ensure pre-commit hooks pass
4. Write or update tests as needed
5. Submit a PR with a clear description

## Commit Messages

Use clear, descriptive commit messages:
- `feat: Add new feature`
- `fix: Fix bug in X`
- `docs: Update documentation`
- `refactor: Refactor X for clarity`
- `test: Add tests for X`

## Questions?

Open an issue for questions or suggestions.
