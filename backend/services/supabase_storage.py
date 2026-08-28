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