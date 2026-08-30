from datetime import datetime, timezone

from services.resume_storage_reconciliation import (
    classify_resume_storage,
    reconcile_resume_storage,
)


NOW = datetime(2026, 8, 28, 16, 0, tzinfo=timezone.utc)


def storage_object(path, created_at):
    return {
        "path": path,
        "created_at": created_at,
        "updated_at": created_at,
    }


def test_classification_protects_recent_and_unknown_age_orphans():
    objects = [
        storage_object("user-1/saved.pdf", "2026-08-20T12:00:00Z"),
        storage_object("user-1/stale.pdf", "2026-08-20T12:00:00Z"),
        storage_object("user-2/recent.pdf", "2026-08-28T15:30:00Z"),
        storage_object("user-3/unknown.pdf", None),
    ]

    report = classify_resume_storage(
        objects,
        {"user-1/saved.pdf", "user-4/missing.pdf"},
        min_age_hours=24,
        now=NOW,
    )

    assert report["eligible_orphans"] == ["user-1/stale.pdf"]
    assert report["recent_orphans"] == ["user-2/recent.pdf"]
    assert report["unknown_age_orphans"] == ["user-3/unknown.pdf"]
    assert report["missing_storage_objects"] == ["user-4/missing.pdf"]


def test_reconciliation_is_read_only_by_default(monkeypatch):
    deleted_batches = []
    monkeypatch.setattr(
        "services.resume_storage_reconciliation.list_resume_files",
        lambda: [storage_object("user-1/orphan.pdf", "2026-08-20T12:00:00Z")],
    )
    monkeypatch.setattr(
        "services.resume_storage_reconciliation._get_referenced_paths",
        lambda: set(),
    )
    monkeypatch.setattr(
        "services.resume_storage_reconciliation.delete_resume_files",
        deleted_batches.append,
    )

    report = reconcile_resume_storage(min_age_hours=24, now=NOW)

    assert report["eligible_orphans"] == ["user-1/orphan.pdf"]
    assert report["deleted"] == []
    assert deleted_batches == []


def test_delete_rechecks_database_before_removing_orphans(monkeypatch):
    orphan = "user-1/orphan.pdf"
    became_referenced = "user-2/just-committed.pdf"
    references = iter([set(), {became_referenced}])
    deleted_batches = []
    monkeypatch.setattr(
        "services.resume_storage_reconciliation.list_resume_files",
        lambda: [
            storage_object(orphan, "2026-08-20T12:00:00Z"),
            storage_object(became_referenced, "2026-08-20T12:00:00Z"),
        ],
    )
    monkeypatch.setattr(
        "services.resume_storage_reconciliation._get_referenced_paths",
        lambda: next(references),
    )
    monkeypatch.setattr(
        "services.resume_storage_reconciliation.delete_resume_files",
        deleted_batches.append,
    )

    report = reconcile_resume_storage(delete=True, min_age_hours=24, now=NOW)

    assert deleted_batches == [[orphan]]
    assert report["deleted"] == [orphan]
    assert report["protected_by_recheck"] == [became_referenced]


def test_storage_listing_recurses_into_user_folders(monkeypatch):
    from services.supabase_storage import list_resume_files

    class FakeBucket:
        def list(self, path=None, options=None):
            assert options["limit"] == 100
            if path is None:
                return [{"name": "user-1", "id": None}]
            if path == "user-1":
                return [{
                    "name": "resume.pdf",
                    "id": "object-id",
                    "created_at": "2026-08-20T12:00:00Z",
                    "updated_at": "2026-08-21T12:00:00Z",
                }]
            raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(
        "services.supabase_storage.get_resume_bucket",
        lambda: FakeBucket(),
    )

    assert list_resume_files() == [{
        "path": "user-1/resume.pdf",
        "created_at": "2026-08-20T12:00:00Z",
        "updated_at": "2026-08-21T12:00:00Z",
    }]
