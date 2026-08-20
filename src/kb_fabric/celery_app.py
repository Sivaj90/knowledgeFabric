"""Celery application, wired to Redis via kb_fabric.config settings."""

from celery import Celery

from kb_fabric.config import get_settings


def make_celery_app() -> Celery:
    settings = get_settings()
    app = Celery(
        "kb_fabric",
        broker=settings.celery_broker_url,
        backend=settings.celery_result_backend,
        include=["kb_fabric.tasks"],
    )
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
    )
    return app


celery_app = make_celery_app()
