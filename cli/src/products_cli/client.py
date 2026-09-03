from __future__ import annotations

from typing import Any

import httpx

from products_cli.auth import Session, describe_http_error
from products_cli.errors import CliError

PAGE_SIZE = 200


class ProductsClient:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_products(self, filters: dict[str, Any]) -> list[Any]:
        page = self._request_page(filters)
        return page[0]

    def get_product(self, product_id: int) -> Any:
        response = self._session.request("GET", f"/products/{product_id}")
        return self._payload(response, dict)

    def create_product(self, body: dict[str, Any]) -> Any:
        response = self._session.request("POST", "/products", json_body=body)
        return self._payload(response, dict)

    def update_product(self, product_id: int, body: dict[str, Any]) -> Any:
        response = self._session.request(
            "PATCH", f"/products/{product_id}", json_body=body
        )
        return self._payload(response, dict)

    def delete_product(self, product_id: int) -> None:
        response = self._session.request("DELETE", f"/products/{product_id}")
        if response.status_code >= 400:
            raise CliError(describe_http_error(response))

    def list_all_in_section(self, section: str) -> list[Any]:
        collected: list[Any] = []
        offset = 0
        while True:
            items, total = self._request_page(
                {"section": section, "limit": PAGE_SIZE, "offset": offset}
            )
            if not items:
                break
            collected.extend(items)
            if len(items) < PAGE_SIZE:
                break
            if isinstance(total, int) and len(collected) >= total:
                break
            offset += len(items)
        return collected

    def _request_page(self, filters: dict[str, Any]) -> tuple[list[Any], int | None]:
        params = {key: value for key, value in filters.items() if value is not None}
        response = self._session.request("GET", "/products", params=params)
        payload = self._payload(response, dict)
        items = payload.get("items")
        if not isinstance(items, list):
            raise CliError("GET /products did not return an 'items' array")
        pagination = payload.get("pagination")
        total = pagination.get("total") if isinstance(pagination, dict) else None
        return items, total if isinstance(total, int) else None

    @staticmethod
    def _payload(response: httpx.Response, expected: type) -> Any:
        if response.status_code >= 400:
            raise CliError(describe_http_error(response))
        try:
            payload: Any = response.json()
        except ValueError as exc:
            request = response.request
            raise CliError(
                f"{request.method} {request.url.path} returned a non-JSON response"
            ) from exc
        if not isinstance(payload, expected):
            request = response.request
            raise CliError(
                f"{request.method} {request.url.path} returned an unexpected payload"
            )
        return payload
