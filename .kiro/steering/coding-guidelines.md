# SRE Watchdog — Coding Guidelines

inclusion: auto

## Python Style
- **If a .venv folder is not present in the project directory**, in a Powershell terminal, create virtual environment using the bash command `python -m venv .venv`
- **Always activatethe virtual environment first** using the following command in a Powershell terminal: .venv\Scripts\Activate.ps1 
- Python 3.11+ features allowed (type unions with `|`, match statements)
- Max line length: 120 characters (configured in .flake8)
- Use type hints on all function signatures
- Prefer explicit imports over wildcard imports
- Use `Optional[T]` for nullable parameters for consistency with existing code

## FastAPI Patterns

- Use `APIRouter` for each endpoint group with appropriate prefix and tags
- Use `Depends(get_db)` for database session injection
- Use `Depends(get_settings)` for configuration injection
- Return Pydantic models from endpoints (use `response_model` parameter)
- Use `JSONResponse` for non-standard status codes (413, 503)
- Background work via `BackgroundTasks` parameter

## Database Patterns

- All ORM models inherit from `Base` in `app/database.py`
- Timestamps stored as ISO 8601 TEXT (SQLite compatibility)
- Always use indexes for frequently queried columns
- Use `SessionLocal()` for new sessions in BackgroundTasks (outside request lifecycle)
- Use `db.commit()` after writes, `db.rollback()` on errors

## Logging

- Use `logging.getLogger(__name__)` in every module
- Emit structured JSON via `json.dumps({...})` passed to `logger.info/warning/error`
- Every service operation should log: event name, relevant IDs, outcome

## Error Handling

- Bedrock failures → `analysis_failed` status, never crash the app
- Webhook failures → retry 3 times, then mark as `failed`
- Config errors → raise `ConfigurationError` at startup
- Never swallow exceptions silently — always log them

## Testing

- Unit tests in `tests/unit/`, integration tests in `tests/integration/`
- Use `test_db` fixture for in-memory SQLite
- Use `test_client` fixture for FastAPI TestClient with overrides
- Mock external boundaries only (Bedrock API, webhook targets)
- Use `freezegun` for time-sensitive tests
- Use `respx` for HTTP mocking
