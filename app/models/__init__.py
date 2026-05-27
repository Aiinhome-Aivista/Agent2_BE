"""Domain models.

This project uses raw SQL via mysql-connector for clarity and to keep
the data layer fully transparent. ORM mappings are intentionally omitted —
the Pydantic schemas in `app.schemas` serve as the canonical model
representations consumed by the API.

Add SQLAlchemy or other ORM mappings here later if you outgrow raw SQL.
"""
