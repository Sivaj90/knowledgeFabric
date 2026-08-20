from kb_fabric.config import get_settings


def test_settings_load_from_env():
    settings = get_settings()
    assert settings.postgres_db == "kb_fabric"
    assert settings.embedding_model == "landmark-text-embedding-3-large"
    assert "psycopg" in settings.sqlalchemy_database_url


def test_db_connectivity():
    from sqlalchemy import text
    from kb_fabric.db import get_engine

    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1")).scalar()
        assert result == 1
