"""Compact human-readable output; raw API responses remain available as JSON."""

import argparse
import shlex
from typing import Any

from .transport import Error


def text(value: Any) -> str:
    # API text must not emit terminal control sequences or multiline table cells.
    return " ".join("".join(c if c.isprintable() else " " for c in str(value)).split())


def object_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def objects(value: Any) -> list[dict[str, Any]]:
    return [item for item in list_value(value) if isinstance(item, dict)]


def identifiers(product: dict[str, Any]) -> list[str]:
    values = []
    for parameter in objects(product.get("parameters")):
        options = object_value(parameter.get("options"))
        if parameter.get("id") == "225693" or options.get("isGTIN"):
            values.extend(list_value(parameter.get("values")) or list_value(parameter.get("valuesLabels")))
    return [text(value) for value in values]


def product_line(product: dict[str, Any], number: int | None = None) -> None:
    prefix = f"{number}. " if number is not None else ""
    print(prefix + text(product.get("name", "Unnamed product")))
    print("   Product ID: " + text(product.get("id", "?")))
    gtins = identifiers(product)
    if gtins:
        print("   EAN/GTIN: " + ", ".join(gtins))
    category = object_value(product.get("category"))
    path = objects(category.get("path"))
    if path:
        print("   Category: " + text(path[-1].get("name", "")))
    details = []
    for parameter in objects(product.get("parameters")):
        if parameter.get("id") in {"202869", "202865", "202685", "246737"}:
            values = list_value(parameter.get("valuesLabels")) or list_value(parameter.get("values"))
            if values:
                details.append(text(parameter.get("name", "")) + ": " + ", ".join(text(v) for v in values))
    if details:
        print("   " + "; ".join(details))


def display(data: dict[str, Any], args: argparse.Namespace) -> None:
    command = args.command
    if command == "search":
        products = data.get("products")
        if not isinstance(products, list) or any(
                not isinstance(p, dict) or not isinstance(p.get("id"), str) or not p["id"] for p in products):
            raise Error("Allegro returned an invalid product list.")
        if not products:
            print("No products found in the Sandbox catalog.")
            print("Try the exact EAN/GTIN or a shorter name such as 'iPhone 16e'.")
            print("Products available on the main Allegro site may be absent from Sandbox.")
            return
        print(f"Showing {min(len(products), args.limit)} of {len(products)} products on this page:")
        for number, product in enumerate(products[:args.limit], 1):
            product_line(product, number)
        if len(products) > args.limit:
            print(f"Use --limit {len(products)} to show the rest of this page.")
        next_page = data.get("nextPage")
        if isinstance(next_page, dict) and next_page.get("id"):
            print("More results: repeat search with --page " + shlex.quote(str(next_page["id"])))
        if len(products) == 1:
            product = products[0]
            option = "--product " + shlex.quote(str(product["id"]))
            print("\nTo create a draft (replace PRICE with your price in PLN):")
            print("docker compose run --rm allegro sell " + option + " --price PRICE --draft")
    elif command == "product":
        product_line(data)
        print("Use --json to view all product parameters and images.")
    elif command in {"sell", "publish", "status", "operation"}:
        snapshot_only = getattr(args, "pending_snapshot", False)
        publication = data.get("publication")
        status = publication.get("status", "unknown") if isinstance(publication, dict) else "unknown"
        print(f"Offer {text(data.get('id', '?'))}: {text(status)}")
        if isinstance(publication, dict) and publication.get("endedBy"):
            print("Ended reason: " + text(publication["endedBy"]))
        if data.get("name"):
            print(text(data["name"]))
        selling = object_value(data.get("sellingMode"))
        cost = object_value(selling.get("price"))
        stock = object_value(data.get("stock"))
        if cost.get("amount"):
            print(f"Price: {text(cost['amount'])} {text(cost.get('currency', ''))}")
        if "available" in stock:
            print(f"Quantity: {text(stock['available'])}")
        validation = data.get("validation")
        if isinstance(validation, dict):
            for item in objects(validation.get("errors")):
                print("Error: " + text(item.get("userMessage") or item.get("message") or item.get("code")))
            warnings = objects(validation.get("warnings"))
            if warnings:
                print(f"{len(warnings)} Allegro warning(s):")
                for item in warnings:
                    print("  " + text(item.get("path") or "offer") + ": " + text(item.get("code") or item.get("message")))
                print("Use --json for the full warning text; review these fields on Sandbox before publishing.")
        if status == "INACTIVE" and data.get("id") and not snapshot_only:
            print("Publish: docker compose run --rm allegro publish " + shlex.quote(str(data["id"])))
    elif command == "settings":
        for key, label in [("shippingRates", "Shipping rates"), ("returnPolicies", "Return policies"),
                           ("impliedWarranties", "Complaint policies")]:
            print(label + ":")
            group = object_value(data.get(key))
            items = objects(group.get(key))
            for item in items:
                if isinstance(item, dict):
                    print("  " + text(item.get("name", "")) + "  ID: " + text(item.get("id", "")))
            if not items:
                print("  None configured. Set these up in your Sandbox seller account.")
    elif command == "categories":
        for category in objects(data.get("categories")):
            print(text(category.get("id", "")) + "  " + text(category.get("name", ""))
                  + (" (leaf)" if category.get("leaf") else " (use categories --parent ID)"))
    elif command == "parameters":
        for parameter in objects(data.get("parameters")):
            options = object_value(parameter.get("options"))
            kind = "product" if options.get("describesProduct") else "offer"
            required = parameter.get("required") or parameter.get("requiredForProduct")
            print(f"{text(parameter.get('id', ''))}  {text(parameter.get('name', ''))}  "
                  f"[{kind}{', required' if required else ''}]")
        print("Use --json for allowed values and dictionary IDs.")
    elif command == "login":
        print("Logged in to Allegro Sandbox. You can now search and sell products.")
    elif command == "logout":
        print("Saved login removed.")
