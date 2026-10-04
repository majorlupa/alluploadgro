"""OAuth device login, persistent tokens, and operation polling."""

from contextlib import contextmanager
import fcntl
import json
import math
import os
import re
from pathlib import Path
import sys
import tempfile
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit

import requests

from .transport import AUTH_URL, MEDIA_TYPE, Error, api_url, body, check, send


class Pending(Error):
    """Accepted work whose operation has not yet confirmed completion."""

    def __init__(self, location: str, offer_path: str | None = None,
                 offer: dict[str, Any] | None = None) -> None:
        super().__init__(f"Allegro is still processing the accepted request: {location}")
        self.location = location
        self.offer_path = offer_path
        self.offer = offer


@contextmanager
def locked_state(directory: Path) -> Iterator[None]:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Error("Another command is using these credentials. Wait for it to finish.") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


class Client:
    def __init__(self, state_dir: Path) -> None:
        self.token_path = state_dir / "tokens.json"
        self.client_id = os.environ.get("ALLEGRO_CLIENT_ID", "").strip()
        self.client_secret = os.environ.get("ALLEGRO_CLIENT_SECRET", "").strip()
        self.session = requests.Session()

    def oauth(self, endpoint: str, data: dict[str, Any]) -> requests.Response:
        if not self.client_id or not self.client_secret:
            raise Error("Set ALLEGRO_CLIENT_ID and ALLEGRO_CLIENT_SECRET in .env first.")
        return send(self.session, "POST", f"{AUTH_URL}/{endpoint}",
                    auth=(self.client_id, self.client_secret), data=data)

    def save_tokens(self, data: dict[str, Any]) -> None:
        if any(not isinstance(data.get(key), str) or not data[key].strip()
               for key in ("access_token", "refresh_token")):
            raise Error("OAuth response lacks access/refresh tokens. Run login again.")
        try:
            expires = float(data["expires_in"])
            if not math.isfinite(expires) or expires <= 0:
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise Error("OAuth response lacks a valid expires_in.") from exc
        saved = {"access_token": data["access_token"], "refresh_token": data["refresh_token"],
                 "expires_at": time.time() + expires, "client_id": self.client_id}
        self.token_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(dir=self.token_path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as file:
                json.dump(saved, file)
                file.flush()
                os.fsync(file.fileno())
            os.replace(name, self.token_path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def login(self) -> dict[str, Any]:
        code = body(check(self.oauth("device", {"client_id": self.client_id})))
        try:
            interval = max(1, int(code["interval"]))
            deadline = time.monotonic() + int(code["expires_in"])
            device_code = code["device_code"]
            link = code["verification_uri_complete"]
        except (KeyError, TypeError, ValueError) as exc:
            raise Error("Malformed device authorization response.") from exc
        print(f"Open in your browser and authorize your SANDBOX seller account:\n{link}",
              file=sys.stderr, flush=True)
        while time.monotonic() + interval < deadline:
            time.sleep(interval)
            result = self.oauth("token", {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
            })
            data = body(result)
            if result.status_code == 200:
                self.save_tokens(data)
                return {"authenticated": True, "environment": "sandbox"}
            if result.status_code == 400 and data.get("error") == "authorization_pending":
                continue
            if result.status_code == 400 and data.get("error") == "slow_down":
                interval += 5
                continue
            check(result)
        raise Error("Login expired. Run login again and authorize the browser link sooner.")

    def access_token(self, force_refresh: bool = False) -> str:
        try:
            saved = json.loads(self.token_path.read_text())
        except FileNotFoundError as exc:
            raise Error("No saved login. Run login first.") from exc
        except (ValueError, OSError) as exc:
            raise Error("Cannot read saved credentials. Run login again.") from exc
        if not isinstance(saved, dict):
            raise Error("Invalid saved credentials. Run login again.")
        if saved.get("client_id") != self.client_id:
            raise Error("Saved login belongs to a different client ID. Run login again.")
        try:
            expired = float(saved["expires_at"]) <= time.time() + 60
            token = saved["access_token"]
            refresh_token = saved["refresh_token"]
            if (not math.isfinite(float(saved["expires_at"]))
                    or not isinstance(token, str) or not token.strip()
                    or not isinstance(refresh_token, str) or not refresh_token.strip()):
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise Error("Invalid saved credentials. Run login again.") from exc
        if force_refresh or expired:
            result = self.oauth("token", {"grant_type": "refresh_token", "refresh_token": refresh_token})
            try:
                data = body(check(result))
            except Error as exc:
                raise Error(f"Token refresh failed. Run login again. {exc}") from exc
            self.save_tokens(data)
            token = data["access_token"]
        return token

    def request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        url = api_url(path)  # Validate before reading or transmitting credentials.
        headers = {"Accept": MEDIA_TYPE, "Content-Type": MEDIA_TYPE, "Accept-Language": "en-US"}
        for attempt in range(2):
            headers["Authorization"] = "Bearer " + self.access_token(force_refresh=attempt == 1)
            result = send(self.session, method, url, headers=headers, **kwargs)
            if result.status_code == 401 and attempt == 0:
                continue
            if result.status_code == 303:
                return result
            return check(result)
        raise Error("Authorization failed. Run login again.")

    def pending(self, location: str) -> Pending:
        # A snapshot is informative only: it must never substitute for a successful
        # operation result, which may still reveal validation errors.
        path = urlsplit(api_url(location)).path
        match = re.fullmatch(r"(/sale/product-offers/[0-9]+)/operations/[^/]+", path)
        offer_path = match.group(1) if match else None
        offer = None
        if offer_path:
            try:
                response = self.request("GET", offer_path)
                if response.status_code == 200:
                    offer = body(response)
            except Error:
                pass  # Preserve the operation recovery path even if the snapshot fails.
        return Pending(location, offer_path, offer)

    def complete(self, response: requests.Response, timeout: float = 15) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        operation = response.headers.get("Location")
        if response.status_code == 202:
            print(f"Allegro accepted the request. Waiting up to {timeout:g}s for processing...",
                  file=sys.stderr, flush=True)
        last_notice = time.monotonic()
        redirects = 0
        while response.status_code in (202, 303):
            location = response.headers.get("Location")
            if response.status_code == 303:
                redirects += 1
                if redirects > 5:
                    raise Error("Too many operation redirects. Check your Sandbox offer before retrying.")
            else:
                location = location or operation
            if not location:
                raise Error("Pending operation has no Location. Check your Sandbox offers before retrying.")
            api_url(location)
            if response.status_code == 202 and time.monotonic() >= deadline:
                raise self.pending(location)
            if response.status_code == 202:
                operation = location
                try:
                    delay = max(0.1, float(response.headers.get("Retry-After", "1")))
                    if not math.isfinite(delay):
                        delay = 1
                except (TypeError, ValueError):
                    delay = 1
                time.sleep(min(delay, max(0, deadline - time.monotonic())))
                if time.monotonic() >= deadline:
                    raise self.pending(location)
            response = self.request("GET", location)
            if response.status_code == 202 and time.monotonic() - last_notice >= 5:
                print("Still processing on Allegro...", file=sys.stderr, flush=True)
                last_notice = time.monotonic()
        return body(response)
