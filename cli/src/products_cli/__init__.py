from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from products_cli.auth import Session, login, normalize_base_url
from products_cli.client import ProductsClient
from products_cli.config import Credentials, load_credentials, save_credentials
from products_cli.errors import CliError

_UPDATE_FIELDS = ("name", "section", "description", "discount", "price")


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    try:
        args.handler(args)
    except CliError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        raise SystemExit(130)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="products-cli", description="CLI for the Products API"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    login_parser = commands.add_parser("login", help="authenticate and store the session")
    login_parser.add_argument("--base-url", required=True)
    login_parser.add_argument("--username", required=True)
    login_parser.add_argument("--password", required=True)
    login_parser.set_defaults(handler=_cmd_login)

    products_parser = commands.add_parser("products", help="work with products")
    products = products_parser.add_subparsers(dest="products_command", required=True)

    list_parser = products.add_parser("list", help="list products")
    _add_base_url_override(list_parser)
    list_parser.add_argument("--section")
    list_parser.add_argument("--name")
    list_parser.add_argument("--min-price", type=float)
    list_parser.add_argument("--max-price", type=float)
    discount_group = list_parser.add_mutually_exclusive_group()
    discount_group.add_argument(
        "--has-discount", dest="has_discount", action="store_true", default=None
    )
    discount_group.add_argument(
        "--no-discount", dest="has_discount", action="store_false", default=None
    )
    list_parser.add_argument("--limit", type=int)
    list_parser.add_argument("--offset", type=int)
    list_parser.set_defaults(handler=_cmd_list)

    get_parser = products.add_parser("get", help="get a single product")
    _add_base_url_override(get_parser)
    get_parser.add_argument("--id", type=int, required=True)
    get_parser.set_defaults(handler=_cmd_get)

    update_parser = products.add_parser("update", help="update fields of a product")
    _add_base_url_override(update_parser)
    update_parser.add_argument("--id", type=int, required=True)
    update_parser.add_argument("--name")
    update_parser.add_argument("--section")
    update_parser.add_argument("--description")
    update_parser.add_argument("--discount", type=float)
    update_parser.add_argument("--price", type=float)
    update_parser.set_defaults(handler=_cmd_update)

    create_parser = products.add_parser("create", help="create a product")
    _add_base_url_override(create_parser)
    create_parser.add_argument("--name", required=True)
    create_parser.add_argument("--section", required=True)
    create_parser.add_argument("--price", type=float, required=True)
    create_parser.add_argument("--description")
    create_parser.add_argument("--discount", type=float)
    create_parser.set_defaults(handler=_cmd_create)

    delete_parser = products.add_parser("delete", help="delete a product")
    _add_base_url_override(delete_parser)
    delete_parser.add_argument("--id", type=int, required=True)
    delete_parser.set_defaults(handler=_cmd_delete)

    batch_parser = products.add_parser(
        "batch-update", help="set the discount for every product in a section"
    )
    _add_base_url_override(batch_parser)
    batch_parser.add_argument("--section", required=True)
    batch_parser.add_argument("--discount", type=float, required=True)
    batch_parser.set_defaults(handler=_cmd_batch_update)

    return parser


def _add_base_url_override(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--base-url",
        default=None,
        help="override the base URL stored by `login` for this call",
    )


def _cmd_login(args: argparse.Namespace) -> None:
    credentials = login(args.base_url, args.username, args.password)
    save_credentials(credentials)
    _emit({"status": "ok"})


def _cmd_list(args: argparse.Namespace) -> None:
    filters = {
        "section": args.section,
        "name": args.name,
        "min_price": args.min_price,
        "max_price": args.max_price,
        "has_discount": args.has_discount,
        "limit": args.limit,
        "offset": args.offset,
    }
    with _client(args) as client:
        _emit(client.list_products(filters))


def _cmd_get(args: argparse.Namespace) -> None:
    with _client(args) as client:
        _emit(client.get_product(args.id))


def _cmd_update(args: argparse.Namespace) -> None:
    body = {
        field: getattr(args, field)
        for field in _UPDATE_FIELDS
        if getattr(args, field) is not None
    }
    with _client(args) as client:
        _emit(client.update_product(args.id, body))


def _cmd_create(args: argparse.Namespace) -> None:
    body: dict[str, Any] = {
        "name": args.name,
        "section": args.section,
        "price": args.price,
    }
    if args.description is not None:
        body["description"] = args.description
    if args.discount is not None:
        body["discount"] = args.discount
    with _client(args) as client:
        _emit(client.create_product(body))


def _cmd_delete(args: argparse.Namespace) -> None:
    with _client(args) as client:
        client.delete_product(args.id)
    _emit({"status": "ok"})


def _cmd_batch_update(args: argparse.Namespace) -> None:
    updated = 0
    with _client(args) as client:
        products = client.list_all_in_section(args.section)
        for product in products:
            product_id = product.get("id") if isinstance(product, dict) else None
            if not isinstance(product_id, int):
                raise CliError("GET /products returned an item without an integer id")
            client.update_product(product_id, {"discount": args.discount})
            updated += 1
    _emit({"updated": updated})


class _ClientContext:
    def __init__(self, credentials: Credentials) -> None:
        self._session = Session(credentials)

    def __enter__(self) -> ProductsClient:
        return ProductsClient(self._session)

    def __exit__(self, *_exc: object) -> None:
        self._session.close()


def _client(args: argparse.Namespace) -> _ClientContext:
    credentials = load_credentials()
    override = getattr(args, "base_url", None)
    if override:
        credentials.base_url = normalize_base_url(override)
    return _ClientContext(credentials)


def _emit(payload: Any) -> None:
    print(json.dumps(payload))
