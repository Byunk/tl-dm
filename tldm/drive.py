import logging
import re
from pathlib import Path

import google.auth
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload, MediaIoBaseDownload

from tldm.config import Settings

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive"]

_DRIVE_URL_PATTERNS = [
    re.compile(r"drive\.google\.com/file/d/([a-zA-Z0-9_-]+)"),
    re.compile(r"drive\.google\.com/open\?id=([a-zA-Z0-9_-]+)"),
    re.compile(r"^([a-zA-Z0-9_-]{20,})$"),
]


def parse_drive_input(url_or_id: str) -> str:
    """Extract a Google Drive file ID from a URL or raw ID.

    Args:
        url_or_id: A Google Drive URL or bare file ID.

    Returns:
        The extracted file ID string.

    Raises:
        ValueError: If the input cannot be parsed as a valid Drive reference.
    """
    for pattern in _DRIVE_URL_PATTERNS:
        match = pattern.search(url_or_id)
        if match:
            return match.group(1)
    msg = f"Cannot parse Drive file ID from: {url_or_id}"
    raise ValueError(msg)


def resolve_credentials(settings: Settings) -> Credentials:
    """Resolve Google credentials using the best available strategy.

    Tries in order: service account key, Application Default Credentials (gcloud), then raises.

    Args:
        settings: Application settings with credential paths.

    Returns:
        Authenticated Google credentials.

    Raises:
        RuntimeError: If no valid credentials can be resolved.
    """
    if settings.service_account_path and settings.service_account_path.exists():
        logger.info("Using service account: %s", settings.service_account_path)
        return service_account.Credentials.from_service_account_file(
            str(settings.service_account_path),
            scopes=SCOPES,
        )

    try:
        creds, _ = google.auth.default(scopes=SCOPES)
        if hasattr(creds, "expired") and creds.expired and hasattr(creds, "refresh"):
            creds.refresh(Request())
        logger.info("Using Application Default Credentials")
        return creds
    except google.auth.exceptions.DefaultCredentialsError:
        pass

    msg = (
        "No Google credentials found. Either:\n"
        "  1. Run: gcloud auth application-default login"
        " --client-id-file=$HOME/.config/tldm/credentials.json"
        " --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/drive\n"
        "  2. Set TLDM_SERVICE_ACCOUNT_PATH to a service account key file"
    )
    raise RuntimeError(msg)


def download_file(file_id: str, credentials: Credentials, dest_dir: Path) -> Path:
    """Download a file from Google Drive.

    Args:
        file_id: The Google Drive file ID.
        credentials: Authenticated Google credentials.
        dest_dir: Directory to save the downloaded file.

    Returns:
        Path to the downloaded file.

    Raises:
        RuntimeError: If the download fails.
    """
    service = build("drive", "v3", credentials=credentials)

    metadata = service.files().get(fileId=file_id, fields="name,mimeType").execute()
    filename = metadata.get("name", f"{file_id}.mp4")
    safe_filename = filename.replace("/", "_")
    logger.info("Downloading: %s", filename)

    dest_path = dest_dir / safe_filename
    request = service.files().get_media(fileId=file_id)

    with dest_path.open("wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status:
                logger.info("Download progress: %d%%", int(status.progress() * 100))

    logger.info("Downloaded to %s", dest_path)
    return dest_path


def get_file_parents(file_id: str, credentials: Credentials) -> list[str]:
    """Get the parent folder IDs of a Drive file.

    Args:
        file_id: The Google Drive file ID.
        credentials: Authenticated Google credentials.

    Returns:
        List of parent folder IDs.
    """
    service = build("drive", "v3", credentials=credentials)
    metadata = service.files().get(fileId=file_id, fields="parents").execute()
    return metadata.get("parents", [])


def upload_file(
    content: str, filename: str, parent_id: str, credentials: Credentials, mime_type: str = "text/markdown"
) -> str:
    """Upload a text file to Google Drive.

    Args:
        content: The text content to upload.
        filename: Name for the uploaded file.
        parent_id: Drive folder ID to upload into.
        credentials: Authenticated Google credentials.
        mime_type: MIME type of the file.

    Returns:
        The file ID of the uploaded file.
    """
    service = build("drive", "v3", credentials=credentials)

    file_metadata = {"name": filename, "parents": [parent_id]}
    media = MediaInMemoryUpload(content.encode(), mimetype=mime_type)

    uploaded = service.files().create(body=file_metadata, media_body=media, fields="id").execute()
    file_id = uploaded["id"]
    logger.info("Uploaded %s to Drive (id: %s)", filename, file_id)
    return file_id
