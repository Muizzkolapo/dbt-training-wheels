# Source Code Structure

This directory contains the modularized source code for DBT Training Wheels.

## Directory Layout

```
src/
├── __init__.py              # Package initialization
├── config.py                # Application configuration
├── utils/                   # Utility modules
│   ├── __init__.py
│   └── sql_parser.py       # SQL parsing and extraction logic
├── services/               # Business logic layer
│   ├── __init__.py
│   ├── query_service.py    # Query loading and management
│   ├── analysis_service.py # SQL analysis logic
│   └── file_generator.py   # dbt file generation
└── routes/                 # Flask route handlers
    ├── __init__.py
    ├── web_routes.py       # Web page routes
    └── api_routes.py       # API endpoint routes
```

## Module Descriptions

### config.py
- Application configuration settings
- Directory paths
- Migration workflow definitions
- Constants and configuration values

### utils/sql_parser.py
- `parse_sql_file()` - Parse SQL files and extract metadata
- `extract_sql_for_table()` - Extract SQL logic for specific tables
- `analyze_sql_content()` - Analyze SQL for CREATE statements and dependencies

### services/query_service.py
- `load_queries_from_directory()` - Load all SQL files from source_sql_file/
- `get_query_by_id()` - Retrieve a specific query by ID

### services/analysis_service.py
- `analyze_query()` - Perform complete query analysis
- Returns analysis results including CTEs, hardcoded tables, and SQL previews

### services/file_generator.py
- `generate_prep_model_content()` - Generate prep model SQL content
- `generate_final_model_content()` - Generate final model SQL content
- `generate_files_for_query()` - Generate all files for a query

### routes/web_routes.py
- `index()` - Main page route handler
- Returns rendered HTML template with queries and migration steps

### routes/api_routes.py
- `/api/analyze/<query_id>` - Analyze query endpoint
- `/api/generate-files/<query_id>` - Generate files endpoint
- Returns JSON responses

## Design Principles

### Separation of Concerns
- **Routes**: Handle HTTP requests/responses
- **Services**: Business logic and orchestration
- **Utils**: Reusable utility functions
- **Config**: Centralized configuration

### Modularity
- Each module has a single responsibility
- Easy to test individual components
- Clear dependencies between modules

### Type Hints
- All functions include type hints for parameters and return values
- Improves code documentation and IDE support

### Error Handling
- Services handle errors gracefully
- Routes return appropriate HTTP status codes

## Usage in app.py

```python
from flask import Flask
from src.routes.web_routes import web_bp
from src.routes.api_routes import api_bp
from src.config import DEBUG, HOST, PORT

app = Flask(__name__)
app.register_blueprint(web_bp)
app.register_blueprint(api_bp)

if __name__ == '__main__':
    app.run(debug=DEBUG, host=HOST, port=PORT)
```

## Benefits of This Structure

1. **Maintainability**: Easy to find and modify specific functionality
2. **Testability**: Each module can be tested independently
3. **Scalability**: Easy to add new features without affecting existing code
4. **Readability**: Clear organization makes code easier to understand
5. **Reusability**: Utility functions can be used across different parts of the app

## Adding New Features

### To add a new API endpoint:
1. Add function to appropriate service module
2. Create route handler in `routes/api_routes.py`
3. Register endpoint in blueprint

### To add new analysis logic:
1. Add utility function to `utils/sql_parser.py` if needed
2. Update `services/analysis_service.py` to use new logic
3. Update route handler to return new data

### To modify configuration:
1. Update values in `src/config.py`
2. No code changes needed elsewhere (configuration is centralized)
