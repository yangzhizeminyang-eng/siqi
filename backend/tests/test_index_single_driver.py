"""Celery 模式下 API 不得与 Worker 同时驱动仓库索引。"""

from __future__ import annotations


def _patch_settings(monkeypatch, *, use_celery: bool, broker: str):
    from app import main
    from app.core import runtime_config

    class Settings:
        pass

    settings = Settings()
    settings.use_celery = use_celery
    settings.celery_broker_url = broker
    monkeypatch.setattr(runtime_config, "load_runtime_settings", lambda: settings)
    return main


def test_api_does_not_drive_index_when_celery_broker_configured(monkeypatch):
    main = _patch_settings(monkeypatch, use_celery=True, broker="redis://broker:6379/0")
    calls = []
    monkeypatch.setattr(main, "REPOSITORY_INDEXES", {"idx_test": object()})
    monkeypatch.setattr(
        main, "progress_single_repository_index",
        lambda record, force=False: calls.append(record))

    main.progress_repository_index_jobs()

    assert calls == []


def test_api_still_drives_index_in_memory_mode(monkeypatch):
    main = _patch_settings(monkeypatch, use_celery=False, broker="")
    record = object()
    calls = []
    monkeypatch.setattr(main, "REPOSITORY_INDEXES", {"idx_test": record})
    monkeypatch.setattr(
        main, "progress_single_repository_index",
        lambda item, force=False: calls.append(item))

    main.progress_repository_index_jobs()

    assert calls == [record]
