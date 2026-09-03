from __future__ import annotations

import json
from typing import Any

import httpx

from products_cli.config import Credentials, save_credentials
from products_cli.errors import CliError

TIMEOUT = httpx.Timeout(30.0)


def normalize_base_url(base_url: str) -> str:
    stripped = base_url.strip().rstrip("/")
    if not stripped:
        raise CliError("--base-url must not be empty")
    if not stripped.startswith(("http://", "https://")):
        raise CliError(f"--base-url must start with http:// or https:// (got {base_url!r})")
    return stripped


def describe_http_error(response: httpx.Response) -> str:
    detail = ""
    try:
        body: Any = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and body.get("detail") is not None:
        raw = body["detail"]
        detail = raw if isinstance(raw, str) else json.dumps(raw)
    if not detail:
        detail = response.text.strip()
    request = response.request
    suffix = f": {detail}" if detail else ""
    return f"{request.method} {request.url.path} failed with HTTP {response.status_code}{suffix}"


def _read_token_pair(response: httpx.Response, source: str) -> tuple[str, str]:
    try:
        payload: Any = response.json()
    except ValueError as exc:
        raise CliError(f"{source} returned a non-JSON response") from exc
    if not isinstance(payload, dict):
        raise CliError(f"{source} returned an unexpected payload")
    access = payload.get("access_token")
    refresh = payload.get("refresh_token")
    if not isinstance(access, str) or not isinstance(refresh, str):
        raise CliError(f"{source} did not return an access_token/refresh_token pair")
    return access, refresh


def login(base_url: str, username: str, password: str) -> Credentials:
    url = normalize_base_url(base_url)
    with httpx.Client(base_url=url, timeout=TIMEOUT) as client:
        try:
            response = client.post(
                "/auth/login", json={"username": username, "password": password}
            )
        except httpx.HTTPError as exc:
            raise CliError(f"could not reach the API at {url}: {exc}") from exc
        if response.status_code != 200:
            raise CliError(describe_http_error(response))
        access, refresh = _read_token_pair(response, "/auth/login")
    return Credentials(base_url=url, access_token=access, refresh_token=refresh)


class Session:
    def __init__(self, credentials: Credentials) -> None:
        self._credentials = credentials
        self._client = httpx.Client(base_url=credentials.base_url, timeout=TIMEOUT)

    def __enter__(self) -> "Session":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
    ) -> httpx.Response:
        response = self._send(method, path, params, json_body)
        if response.status_code != 401:
            return response
        self._refresh()
        return self._send(method, path, params, json_body)

    def _send(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None,
        json_body: Any,
    ) -> httpx.Response:
        try:
            return self._client.request(
                method,
                path,
                params=params,
                json=json_body,
                headers={"Authorization": f"Bearer {self._credentials.access_token}"},
            )
        except httpx.HTTPError as exc:
            raise CliError(
                f"could not reach the API at {self._credentials.base_url}: {exc}"
            ) from exc

    def _refresh(self) -> None:
        try:
            response = self._client.post(
                "/auth/refresh",
                json={"refresh_token": self._credentials.refresh_token},
            )
        except httpx.HTTPError as exc:
            raise CliError(
                f"could not reach the API at {self._credentials.base_url}: {exc}"
            ) from exc
        if response.status_code != 200:
            raise CliError(
                "the stored session could not be refreshed "
                f"({describe_http_error(response)}); run `products-cli login` again"
            )
        access, refresh = _read_token_pair(response, "/auth/refresh")
        self._credentials.access_token = access
        self._credentials.refresh_token = refresh
        save_credentials(self._credentials)
