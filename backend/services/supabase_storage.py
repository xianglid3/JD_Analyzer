import os
from functools import lru_cache

from supabase import Client, create_client


@lru_cache(maxsize=1)
def get_storage_client() -> Client:
    return create_client(
        os.environ["SUPABASE_API_URL"],
        os.environ["SUPABASE_SECRET_KEY"],
    )


def get_resume_bucket():
    bucket_name = os.environ["SUPABASE_STORAGE_BUCKET"]
    return get_storage_client().storage.from_(bucket_name)


def upload_resume_file(path: str, file_bytes: bytes, content_type: str):
    return get_resume_bucket().upload(
        path=path,
        file=file_bytes,
        file_options={
            "content-type": content_type,
            "upsert": "false",
        },
    )


def create_resume_download_url(path: str, expires_in: int = 60) -> str:
    response = get_resume_bucket().create_signed_url(
        path,
        expires_in,
        {"download": True},
    )
    url = response.get("signedURL") or response.get("signedUrl")
    if not url:
        raise RuntimeError("Supabase did not return a signed URL")
    return url


def delete_resume_file(path: str):
    return get_resume_bucket().remove([path])


def delete_resume_files(paths: list[str]):
    if not paths:
        return None
    return get_resume_bucket().remove(paths)


def list_resume_files(page_size: int = 100) -> list[dict]:
    """List every file in the bucket, including files inside user UUID folders."""
    bucket = get_resume_bucket()
    files = []
    pending_folders = [""]
    visited_folders = set()

    while pending_folders:
        folder = pending_folders.pop()
        if folder in visited_folders:
            continue
        visited_folders.add(folder)

        offset = 0
        while True:
            entries = bucket.list(
                path=folder or None,
                options={
                    "limit": page_size,
                    "offset": offset,
                    "sortBy": {"column": "name", "order": "asc"},
                },
            )

            for entry in entries:
                name = entry.get("name")
                if not isinstance(name, str) or not name or name in {".", ".."}:
                    continue
                path = f"{folder}/{name}" if folder else name
                if entry.get("id") is None:
                    pending_folders.append(path)
                else:
                    files.append({
                        "path": path,
                        "created_at": entry.get("created_at"),
                        "updated_at": entry.get("updated_at"),
                    })

            if len(entries) < page_size:
                break
            offset += page_size

    return files
