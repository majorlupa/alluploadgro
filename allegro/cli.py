"""CLI commands and the minimal catalog-product offer builder."""

import argparse
from decimal import Decimal, InvalidOperation
import html
import json
import math
import os
from pathlib import Path
import sys
import shlex
from urllib.parse import quote, urlsplit
from typing import Any

from .client import Client, Pending, locked_state
from .display import display
from .transport import Error


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Post products on Allegro Sandbox.")
    result.add_argument("--state-dir", type=Path,
                        default=Path(os.environ.get("ALLEGRO_STATE_DIR", ".allegro")))
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("login", help="Authorize via a browser link; save tokens")
    commands.add_parser("logout", help="Remove locally saved tokens")
    search = commands.add_parser("search", help="Search the Sandbox product catalog")
    search.add_argument("phrase")
    search.add_argument("--gtin", action="store_true", help="Search by barcode")
    search.add_argument("--limit", type=int, default=10, help="Maximum displayed results per page (default: 10)")
    search.add_argument("--page", help="Next-page cursor printed by a previous search")
    search.add_argument("--category", help="Filter name search by category ID")
    product = commands.add_parser("product", help="Show catalog product details")
    product.add_argument("id")
    categories = commands.add_parser("categories", help="List categories (select a leaf for custom products)")
    categories.add_argument("--parent")
    parameters = commands.add_parser("parameters", help="Show category parameters and required values")
    parameters.add_argument("category_id")
    commands.add_parser("settings", help="List shipping rates, return policies and complaint policies")
    sell = commands.add_parser("sell", help="Create and publish an offer (or a draft)")
    source = sell.add_mutually_exclusive_group(required=True)
    source.add_argument("--product", help="Sandbox catalog product ID")
    source.add_argument("--ean", help="Barcode (EAN/UPC/GTIN); no product UUID needed")
    source.add_argument("--file", type=Path, help="Full Allegro product-offer JSON file")
    sell.add_argument("--price", help="Positive price in PLN, e.g. 49.99")
    sell.add_argument("--quantity", type=int, help="Stock quantity; defaults to 1")
    sell.add_argument("--title")
    sell.add_argument("--description", help="Plain text description")
    sell.add_argument("--image", action="append", help="Public HTTPS image URL; repeat for more")
    sell.add_argument("--shipping-rate", help="Shipping rate ID; otherwise Allegro uses account defaults")
    sell.add_argument("--draft", action="store_true", help="Create INACTIVE instead of ACTIVE")
    sell.add_argument("--dry-run", action="store_true", help="Print payload without login or network calls")
    for name, help_text in [("status", "Show an offer's current status"),
                            ("publish", "Activate an existing draft")]:
        command = commands.add_parser(name, help=help_text)
        command.add_argument("id")
    operation = commands.add_parser("operation", help="Resume polling an asynchronous operation")
    operation.add_argument("path", help="Operation path or Sandbox API URL from the timeout message")
    for command in commands.choices.values():
        command.add_argument("--json", action="store_true", help="Print the full API response as JSON")
    for name in ("sell", "publish", "operation"):
        commands.choices[name].add_argument("--wait-seconds", type=float, default=15,
                                            help="Wait for processing before returning pending (default: 15)")
    return result


def is_gtin(value: str) -> bool:
    return value.isascii() and value.isdigit() and len(value) in {8, 12, 13, 14}


def price(value: Any) -> str:
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount <= 0 or amount > Decimal("999999999.99"):
            raise InvalidOperation
        if amount != amount.quantize(Decimal("0.01")):
            raise InvalidOperation
        return format(amount, ".2f")
    except (InvalidOperation, ValueError) as exc:
        raise Error("Price must be positive, at most 999999999.99, and have at most two decimal places.") from exc


def build_offer(args: argparse.Namespace) -> dict[str, Any]:
    if args.file:
        if any(getattr(args, field) is not None for field in
               ("price", "quantity", "title", "description", "image", "shipping_rate")):
            raise Error("With --file, set offer fields in the JSON file; only --draft and --dry-run can override it.")
        try:
            data = json.loads(args.file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise Error(f"Cannot read offer JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise Error("Offer JSON must be an object.")
    else:
        if not args.price:
            raise Error("--price is required with --product or --ean.")
        if args.ean and not is_gtin(args.ean):
            raise Error("EAN/GTIN must contain 8, 12, 13 or 14 digits (no spaces or hyphens).")
        product = {"id": args.ean, "idType": "GTIN"} if args.ean else {"id": args.product}
        data = {
            "productSet": [{"product": product}],
            "sellingMode": {"format": "BUY_NOW", "price": {"amount": price(args.price), "currency": "PLN"}},
            "stock": {"available": args.quantity if args.quantity is not None else 1},
        }
        if args.title is not None:
            if not 12 <= len(args.title) <= 75:
                raise Error("Title must contain 12–75 characters.")
            data["name"] = args.title
        if args.description is not None:
            data["description"] = {"sections": [{"items": [
                {"type": "TEXT", "content": "<p>" + html.escape(args.description) + "</p>"}]}]}
        if args.image:
            try:
                invalid = any(urlsplit(url).scheme != "https" or not urlsplit(url).netloc for url in args.image)
            except ValueError as exc:
                raise Error("Invalid image URL. Supply a public HTTPS image URL.") from exc
            if invalid:
                raise Error("Images must be publicly accessible HTTPS URLs.")
            data["images"] = args.image
        if args.shipping_rate:
            data["delivery"] = {"shippingRates": {"id": args.shipping_rate}}
    products = data.get("productSet")
    if not isinstance(products, list) or not products or any(
            not isinstance(item, dict) or not isinstance(item.get("product"), dict) or not item["product"]
            for item in products):
        raise Error("productSet must be a nonempty list with a product object in each item.")
    try:
        selling = data["sellingMode"]
        selling["price"]["amount"] = price(selling["price"]["amount"])
        if selling["price"].get("currency") != "PLN":
            raise Error("This minimal CLI supports PLN offers only.")
        quantity = data["stock"]["available"]
        if type(quantity) is not int or quantity < 1:
            raise Error("Quantity must be a positive integer.")
        publication = data.setdefault("publication", {})
        if not isinstance(publication, dict):
            raise Error("publication must be an object.")
        publication["status"] = "INACTIVE" if args.draft else "ACTIVE"
        if publication.get("startingAt"):
            raise Error("Scheduled publication is outside this CLI's scope. Remove publication.startingAt.")
    except (KeyError, TypeError, AttributeError) as exc:
        raise Error("Offer needs sellingMode.price (amount/currency) and stock.available.") from exc
    return data


def show(data: dict[str, Any]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def output(data: dict[str, Any], args: argparse.Namespace) -> None:
    if args.json:
        show(data)
    else:
        display(data, args)


def ensure_active(data: dict[str, Any]) -> None:
    publication = data.get("publication")
    if not isinstance(publication, dict):
        raise Error("Allegro returned an invalid publication field. Check the offer on Sandbox before retrying.")
    status = publication.get("status")
    if status == "ACTIVATING":
        raise Error(f"Offer {data.get('id', '?')} is still ACTIVATING. Do not publish again. "
                    f"Check with: status {data.get('id', '?')}")
    if status != "ACTIVE":
        validation = data.get("validation")
        errors = validation.get("errors", []) if isinstance(validation, dict) else []
        raise Error(f"Offer {data.get('id', '?')} is {status or 'unknown'}, not ACTIVE. "
                    f"Check status before retrying; correct it and use publish. Details: {json.dumps(errors)}")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        payload = build_offer(args) if args.command == "sell" else None
        if hasattr(args, "wait_seconds") and (not math.isfinite(args.wait_seconds) or args.wait_seconds < 0):
            raise Error("--wait-seconds must be a finite number of zero or more seconds.")
        if args.command == "search" and args.limit < 1:
            raise Error("--limit must be a positive integer.")
        if args.command == "sell" and args.dry_run:
            show(payload)
            return 0
        with locked_state(args.state_dir):
            client = Client(args.state_dir)
            if args.command == "login":
                output(client.login(), args)
            elif args.command == "logout":
                client.token_path.unlink(missing_ok=True)
                output({"logged_out": True}, args)
            elif args.command == "settings":
                data = {}
                for name, path in [("shippingRates", "/sale/shipping-rates"),
                                   ("returnPolicies", "/after-sales-service-conditions/return-policies"),
                                   ("impliedWarranties", "/after-sales-service-conditions/implied-warranties")]:
                    data[name] = client.complete(client.request("GET", path))
                output(data, args)
            elif args.command == "sell":
                data = client.complete(client.request("POST", "/sale/product-offers", json=payload),
                                       timeout=args.wait_seconds)
                output(data, args)
                if not args.draft:
                    ensure_active(data)
            elif args.command == "publish":
                data = client.complete(client.request("PATCH", "/sale/product-offers/" + quote(args.id, safe=""),
                                                      json={"publication": {"status": "ACTIVE"}}),
                                       timeout=args.wait_seconds)
                output(data, args)
                ensure_active(data)
            else:
                params = {}
                if args.command == "search":
                    path = "/sale/products"
                    phrase = args.phrase.strip()
                    if not phrase:
                        raise Error("Search needs a barcode or product name.")
                    params = {"phrase": phrase, "language": "pl-PL"}
                    if args.gtin or is_gtin(phrase):
                        params["mode"] = "GTIN"
                    if args.page:
                        params["page.id"] = args.page
                    if args.category:
                        params["category.id"] = args.category
                elif args.command == "product":
                    path = "/sale/products/" + quote(args.id, safe="")
                elif args.command == "categories":
                    path = "/sale/categories"
                    if args.parent:
                        params["parent.id"] = args.parent
                elif args.command == "parameters":
                    path = "/sale/categories/" + quote(args.category_id, safe="") + "/parameters"
                elif args.command == "status":
                    path = "/sale/product-offers/" + quote(args.id, safe="")
                else:
                    path = args.path
                output(client.complete(client.request("GET", path, params=params),
                                       timeout=getattr(args, "wait_seconds", 15)), args)
        return 0
    except Pending as pending:
        if args.json:
            show({"processing": "PENDING", "operation": pending.location, "offer": pending.offer})
        elif pending.offer is not None:
            args.pending_snapshot = True
            output(pending.offer, args)
        print("Allegro accepted the request and is still processing it. "
              "The displayed offer state does not confirm the operation has finished. "
              "Do not repeat sell or publish.", file=sys.stderr)
        if pending.offer_path:
            print("Check offer: docker compose run --rm allegro status "
                  + pending.offer_path.rsplit("/", 1)[-1], file=sys.stderr)
        print("Resume: docker compose run --rm allegro operation "
              + shlex.quote(pending.location), file=sys.stderr)
        return 2
    except (Error, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted. If publishing, check your Sandbox offers before repeating sell.", file=sys.stderr)
        return 130
