"""Fixed Sandbox destinations and readable transport errors."""

from urllib.parse import urljoin, urlsplit
from typing import Any

import requests

API_URL = "https://api.allegro.pl.allegrosandbox.pl"
AUTH_URL = "https://allegro.pl.allegrosandbox.pl/auth/oauth"
MEDIA_TYPE = "application/vnd.allegro.public.v1+json"


class Error(Exception):
    """An actionable failure suitable for CLI output."""


def api_url(path: str) -> str:
    try:
        url = urljoin(API_URL + "/", path)
        parsed = urlsplit(url)
    except ValueError as exc:
        raise Error("Invalid API URL. Use the operation path printed by Allegro.") from exc
    if (parsed.scheme != "https" or parsed.netloc != urlsplit(API_URL).netloc
            or parsed.username or parsed.password or parsed.fragment):
        raise Error("Refusing API destination outside Allegro Sandbox.")
    return url


def send(session: requests.Session, method: str, url: str, **kwargs: Any) -> requests.Response:
    try:
        return session.request(method, url, timeout=30, allow_redirects=False, **kwargs)
    except requests.RequestException as exc:
        raise Error(
            "Network request failed. For sell, do not blindly repeat: check your "
            "Sandbox offers first; the request may have reached Allegro."
        ) from exc


def body(response: requests.Response) -> dict[str, Any]:
    try:
        result = response.json()
    except ValueError as exc:
        raise Error(f"Allegro returned non-JSON data (HTTP {response.status_code}).") from exc
    if not isinstance(result, dict):
        raise Error("Allegro returned an unexpected JSON structure.")
    return result


def check(response: requests.Response) -> requests.Response:
    if 200 <= response.status_code < 300:
        return response
    data = body(response)
    messages = []
    errors = data.get("errors")
    if errors is not None and not isinstance(errors, list):
        raise Error(f"Allegro HTTP {response.status_code}: malformed errors field.")
    for item in errors or []:
        if isinstance(item, dict):
            location = item.get("path") or (item.get("details") or "")
            message = item.get("userMessage") or item.get("message") or item.get("code")
            messages.append(f"{location}: {message}" if location else str(message))
    if not messages:
        messages.append(str(data.get("error_description") or data.get("error") or "Request rejected"))
    raise Error(f"Allegro HTTP {response.status_code}: " + "; ".join(messages))
