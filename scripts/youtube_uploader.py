"""
AutoClip-AI YouTube Studio Connector & Direct Uploader
Handles Google OAuth 2.0 authentication, channel status verification,
metadata generation, and resumable 60 FPS YouTube Shorts upload.
"""

import os
import json
import logging
import threading
from typing import Dict, Any, Optional, List, Callable

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

logger = logging.getLogger("YouTubeUploader")

CREDENTIALS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "credentials"))
CLIENT_SECRETS_FILE = os.path.join(CREDENTIALS_DIR, "client_secrets.json")
TOKEN_FILE = os.path.join(CREDENTIALS_DIR, "youtube_token.json")

# Standard YouTube Data API v3 scopes for uploading videos and reading channel info
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly"
]

_auth_flow_instance = None
_auth_flow_lock = threading.Lock()
_active_auth_flow = None
_active_auth_lock = threading.Lock()


def ensure_credentials_dir():
    os.makedirs(CREDENTIALS_DIR, exist_ok=True)


def get_credentials() -> Optional[Credentials]:
    """Load valid YouTube credentials from disk, refreshing if expired."""
    ensure_credentials_dir()
    if not os.path.exists(TOKEN_FILE):
        return None

    try:
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Save refreshed credentials
            with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
        if creds and creds.valid:
            return creds
    except Exception as e:
        logger.error(f"Error loading credentials: {e}")
    return None


def get_channel_info(creds: Credentials) -> Dict[str, Any]:
    """Fetch authenticated channel details."""
    try:
        service = build("youtube", "v3", credentials=creds)
        response = service.channels().list(
            part="snippet,statistics",
            mine=True
        ).execute()

        items = response.get("items", [])
        if not items:
            return {"error": "No YouTube channel found for this Google account"}

        item = items[0]
        snippet = item.get("snippet", {})
        statistics = item.get("statistics", {})

        avatar = (
            snippet.get("thumbnails", {}).get("default", {}).get("url")
            or snippet.get("thumbnails", {}).get("medium", {}).get("url")
            or ""
        )

        return {
            "id": item.get("id"),
            "title": snippet.get("title", "Unknown Channel"),
            "custom_url": snippet.get("customUrl", ""),
            "avatar": avatar,
            "subscribers": statistics.get("subscriberCount", "0"),
            "videos": statistics.get("videoCount", "0"),
            "views": statistics.get("viewCount", "0"),
            "created_at": snippet.get("publishedAt", "")
        }
    except Exception as e:
        logger.error(f"Failed to fetch channel info: {e}")
        return {"error": str(e)}


def get_status() -> Dict[str, Any]:
    """Returns the current connection status of YouTube Studio."""
    ensure_credentials_dir()
    has_secrets = os.path.exists(CLIENT_SECRETS_FILE)
    has_token = os.path.exists(TOKEN_FILE)

    if not has_token:
        return {
            "connected": False,
            "has_client_secrets": has_secrets,
            "message": "YouTube Studio not connected. Client secrets configured: " + ("Yes" if has_secrets else "No")
        }

    creds = get_credentials()
    if not creds:
        return {
            "connected": False,
            "has_client_secrets": has_secrets,
            "error": "Saved credentials expired or invalid. Please re-authenticate."
        }

    info = get_channel_info(creds)
    if "error" in info:
        return {
            "connected": False,
            "has_client_secrets": has_secrets,
            "error": info["error"]
        }

    return {
        "connected": True,
        "has_client_secrets": has_secrets,
        "channel": info
    }


def save_client_secrets(secrets_data: Any) -> bool:
    """Save client secrets from dict or raw JSON string."""
    ensure_credentials_dir()
    try:
        if isinstance(secrets_data, str):
            clean_str = secrets_data.strip()
            # If multiple JSON objects were pasted or trailing characters exist, extract first JSON
            if clean_str.startswith("{"):
                depth = 0
                end_pos = -1
                for idx, ch in enumerate(clean_str):
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            end_pos = idx + 1
                            break
                if end_pos != -1:
                    clean_str = clean_str[:end_pos]
            secrets_data = json.loads(clean_str)

        # Ensure correct structure
        if "installed" not in secrets_data and "web" not in secrets_data:
            # If user provided {"client_id": "...", "client_secret": "..."}
            client_id = secrets_data.get("client_id")
            client_secret = secrets_data.get("client_secret")
            if not client_id or not client_secret:
                raise ValueError("JSON must contain 'installed', 'web', or 'client_id' and 'client_secret'")
            secrets_data = {
                "installed": {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                    "redirect_uris": ["http://localhost", "http://127.0.0.1"]
                }
            }

        with open(CLIENT_SECRETS_FILE, "w", encoding="utf-8") as f:
            json.dump(secrets_data, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Error saving client secrets: {e}")
        return False


def save_token_json(token_data: Any) -> bool:
    """Directly save token JSON if user pastes token credentials, or route to client_secrets if that was pasted."""
    ensure_credentials_dir()
    try:
        if isinstance(token_data, str):
            clean_str = token_data.strip()
            if clean_str.startswith("{"):
                depth = 0
                end_pos = -1
                for idx, ch in enumerate(clean_str):
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            end_pos = idx + 1
                            break
                if end_pos != -1:
                    clean_str = clean_str[:end_pos]
            token_data = json.loads(clean_str)

        # Smart fallback: if user pasted client_secrets.json here by mistake
        if isinstance(token_data, dict) and ("installed" in token_data or "web" in token_data or ("client_secret" in token_data and "refresh_token" not in token_data)):
            logger.info("Detected client_secrets in token paste input. Routing to save_client_secrets.")
            return save_client_secrets(token_data)

        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            json.dump(token_data, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Error saving token JSON: {e}")
        return False


def disconnect() -> bool:
    """Disconnect YouTube Studio by removing stored token."""
    try:
        if os.path.exists(TOKEN_FILE):
            os.remove(TOKEN_FILE)
        return True
    except Exception as e:
        logger.error(f"Error disconnecting YouTube: {e}")
        return False


def start_local_auth(port: int = 8089, timeout_sec: int = 120, on_complete: Optional[Callable] = None) -> Dict[str, Any]:
    """
    Launches a local server flow on `port` (e.g. 8089), generates auth_url immediately,
    and listens on localhost for the Google OAuth redirect.
    """
    global _active_auth_flow
    if not os.path.exists(CLIENT_SECRETS_FILE):
        return {"error": "client_secrets.json not found. Please upload or configure your Google Cloud OAuth Client credentials first."}

    try:
        flow = InstalledAppFlow.from_client_secrets_file(
            CLIENT_SECRETS_FILE,
            scopes=SCOPES,
            redirect_uri=f"http://localhost:{port}/"
        )
        auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")

        with _active_auth_lock:
            _active_auth_flow = flow

        def _run_flow():
            try:
                creds = flow.run_local_server(
                    host="localhost",
                    port=port,
                    authorization_prompt_message="Open this link in your browser to authorize AutoClip-AI: {url}",
                    success_message="AutoClip-AI is now connected to your YouTube Studio! You can close this tab and return to the app.",
                    open_browser=False
                )
                with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                    f.write(creds.to_json())
                logger.info("YouTube Studio connected successfully via local redirect!")
                if on_complete:
                    on_complete(True, None)
            except Exception as e:
                logger.error(f"Local auth flow failed or finished: {e}")
                if on_complete:
                    on_complete(False, str(e))

        thread = threading.Thread(target=_run_flow, daemon=True)
        thread.start()

        return {
            "status": "started",
            "port": port,
            "auth_url": auth_url,
            "message": "Authentication server started. Open the link to authorize."
        }
    except Exception as e:
        logger.error(f"Failed to start OAuth flow: {e}")
        return {"error": str(e)}


def exchange_auth_code(code_or_url: str) -> Dict[str, Any]:
    """Exchanges an authorization code or full redirect URL for tokens."""
    global _active_auth_flow
    ensure_credentials_dir()
    clean_code = code_or_url.strip()

    if "code=" in clean_code:
        import urllib.parse
        parsed = urllib.parse.urlparse(clean_code)
        qs = urllib.parse.parse_qs(parsed.query)
        extracted = qs.get("code")
        if extracted:
            clean_code = extracted[0]

    with _active_auth_lock:
        flow = _active_auth_flow

    if not flow and os.path.exists(CLIENT_SECRETS_FILE):
        flow = InstalledAppFlow.from_client_secrets_file(
            CLIENT_SECRETS_FILE,
            scopes=SCOPES,
            redirect_uri="http://localhost:8089/"
        )

    if not flow:
        return {"error": "No active authentication flow or client_secrets.json"}

    try:
        flow.fetch_token(code=clean_code)
        creds = flow.credentials
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
        info = get_channel_info(creds)
        return {"success": True, "channel": info}
    except Exception as e:
        logger.error(f"Error exchanging code: {e}")
        return {"error": f"Failed to exchange authorization code: {e}"}


def upload_short(
    video_path: str,
    title: str,
    description: str,
    tags: Optional[List[str]] = None,
    category_id: str = "22",  # 22 = People & Blogs, 20 = Gaming, 24 = Entertainment
    privacy_status: str = "public",  # 'public', 'unlisted', 'private'
    progress_callback: Optional[Callable[[float, str], None]] = None
) -> Dict[str, Any]:
    """
    Uploads a 9:16 vertical video to YouTube as a Short with resumable upload.
    Includes #Shorts in title & description automatically if not present.
    """
    if not os.path.exists(video_path):
        return {"error": f"Video file not found: {video_path}"}

    creds = get_credentials()
    if not creds:
        return {"error": "YouTube Studio is not authenticated. Please connect your YouTube account in the top bar."}

    # Format title & description for Shorts
    clean_title = (title or "Viral Highlight").strip()
    if "#Shorts" not in clean_title and "#shorts" not in clean_title:
        # YouTube title limit is 100 characters
        if len(clean_title) <= 90:
            clean_title = f"{clean_title} #Shorts"
        else:
            clean_title = clean_title[:90] + " #Shorts"
    clean_title = clean_title[:100]

    clean_description = (description or "").strip()
    if "#Shorts" not in clean_description and "#shorts" not in clean_description:
        clean_description += "\n\n#Shorts #Viral #Clips"

    clean_tags = list(tags) if tags else []
    if "shorts" not in [t.lower() for t in clean_tags]:
        clean_tags.append("shorts")
    if "viral" not in [t.lower() for t in clean_tags]:
        clean_tags.append("viral")

    body = {
        "snippet": {
            "title": clean_title,
            "description": clean_description[:5000],
            "tags": clean_tags[:50],
            "categoryId": category_id
        },
        "status": {
            "privacyStatus": privacy_status.lower() if privacy_status.lower() in ("public", "unlisted", "private") else "private",
            "selfDeclaredMadeForKids": False
        }
    }

    try:
        service = build("youtube", "v3", credentials=creds)
        # Resumable chunk size: 5MB
        media = MediaFileUpload(
            video_path,
            mimetype="video/mp4",
            chunksize=5 * 1024 * 1024,
            resumable=True
        )

        request = service.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status and progress_callback:
                pct = status.progress() * 100.0
                progress_callback(pct, f"Uploading to YouTube: {pct:.1f}%")

        if response and "id" in response:
            video_id = response["id"]
            short_url = f"https://youtube.com/shorts/{video_id}"
            watch_url = f"https://www.youtube.com/watch?v={video_id}"
            if progress_callback:
                progress_callback(100.0, "Upload completed successfully!")
            return {
                "success": True,
                "video_id": video_id,
                "url": short_url,
                "watch_url": watch_url,
                "title": clean_title,
                "privacy": privacy_status
            }
        else:
            return {"error": "Upload completed but no video ID returned", "raw": response}

    except HttpError as e:
        logger.error(f"YouTube API HttpError: {e}")
        try:
            error_details = json.loads(e.content.decode("utf-8"))
            msg = error_details.get("error", {}).get("message", str(e))
        except Exception:
            msg = str(e)
        return {"error": f"YouTube API Error: {msg}"}
    except Exception as e:
        logger.error(f"Unexpected upload error: {e}")
        return {"error": str(e)}
