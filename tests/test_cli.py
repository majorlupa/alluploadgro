import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from allegro.cli import build_offer, ensure_active, main, parser
from allegro.transport import Error
from allegro.client import Pending


class CliTests(unittest.TestCase):
    def args(self, *extra):
        return parser().parse_args(["sell", "--product", "123", "--price", "49.99", *extra])

    def test_minimal_active_offer(self):
        data = build_offer(self.args())
        self.assertEqual(data["productSet"], [{"product": {"id": "123"}}])
        self.assertEqual(data["sellingMode"]["price"], {"amount": "49.99", "currency": "PLN"})
        self.assertEqual(data["stock"]["available"], 1)
        self.assertEqual(data["publication"]["status"], "ACTIVE")

    def test_invalid_prices(self):
        for price in ["0", "-1", "NaN", "Infinity", "0.001", "abc", "1e999999", "1,20"]:
            with self.subTest(price=price), self.assertRaises(Error):
                build_offer(parser().parse_args(["sell", "--product", "123", "--price", price]))

    def test_optional_fields_and_escaped_description(self):
        data = build_offer(self.args("--draft", "--title", "Sample product",
                                     "--description", "A < B & C", "--image", "https://example.com/a.jpg",
                                     "--shipping-rate", "shipping", "--quantity", "2"))
        self.assertEqual(data["publication"]["status"], "INACTIVE")
        self.assertEqual(data["delivery"]["shippingRates"]["id"], "shipping")
        self.assertEqual(data["description"]["sections"][0]["items"][0]["content"],
                         "<p>A &lt; B &amp; C</p>")
        self.assertEqual(data["stock"]["available"], 2)

    def test_invalid_quantity(self):
        with self.assertRaises(Error):
            build_offer(self.args("--quantity", "0"))

    def test_json_preserves_custom_fields_and_sets_draft(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "offer.json"
            data = build_offer(self.args())
            data["productSet"][0]["safetyInformation"] = {"type": "NO_SAFETY_INFORMATION"}
            file.write_text(json.dumps(data))
            result = build_offer(parser().parse_args(["sell", "--file", str(file), "--draft"]))
            self.assertIn("safetyInformation", result["productSet"][0])
            self.assertEqual(result["publication"]["status"], "INACTIVE")

    def test_invalid_file_structure(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "offer.json"
            for value in ["[]", "{", '{"productSet": []}', '{"productSet": [{}], "sellingMode": null}']:
                file.write_text(value)
                with self.subTest(value=value), self.assertRaises(Error):
                    build_offer(parser().parse_args(["sell", "--file", str(file)]))

    def test_dry_run_needs_no_client(self):
        out = io.StringIO()
        with patch("allegro.cli.Client") as client, contextlib.redirect_stdout(out):
            result = main(["sell", "--product", "123", "--price", "10", "--dry-run"])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(out.getvalue())["sellingMode"]["price"]["amount"], "10.00")
        client.assert_not_called()

    def test_failed_publication_exits_nonzero_and_keeps_response(self):
        out, err = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as directory, patch("allegro.cli.Client") as client:
            client.return_value.complete.return_value = {
                "id": "123", "publication": {"status": "INACTIVE"},
                "validation": {"errors": [{"message": "Missing shipping"}]}}
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                result = main(["--state-dir", directory, "sell", "--product", "123", "--price", "10", "--json"])
        self.assertEqual(result, 1)
        self.assertEqual(json.loads(out.getvalue())["id"], "123")
        self.assertIn("Missing shipping", err.getvalue())

    def test_invalid_image_url_is_readable(self):
        with self.assertRaisesRegex(Error, "Invalid image URL"):
            build_offer(self.args("--image", "https://["))

    def test_malformed_publication_is_readable(self):
        with self.assertRaisesRegex(Error, "invalid publication"):
            ensure_active({"publication": ["ACTIVE"]})

    def test_activating_is_pending_not_republish(self):
        with self.assertRaisesRegex(Error, "Do not publish again.*status 123"):
            ensure_active({"id": "123", "publication": {"status": "ACTIVATING"}})

    def test_sell_by_ean_avoids_product_uuid(self):
        data = build_offer(parser().parse_args(["sell", "--ean", "195950051186", "--price", "2499"]))
        self.assertEqual(data["productSet"], [{"product": {"id": "195950051186", "idType": "GTIN"}}])

    def test_invalid_ean_rejected_before_network(self):
        for value in ["iphone", "123", "1" * 15]:
            with self.subTest(value=value), self.assertRaises(Error):
                build_offer(parser().parse_args(["sell", "--ean", value, "--price", "2499"]))

    def search(self, response, *flags):
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, patch("allegro.cli.Client") as client:
            client.return_value.complete.return_value = response
            with contextlib.redirect_stdout(out):
                result = main(["--state-dir", directory, "search", "195950051186", *flags])
            call = client.return_value.request.call_args
        return result, out.getvalue(), call

    def test_barcode_search_automatically_uses_gtin_and_compact_output(self):
        result, output, call = self.search({"products": [{
            "id": "product-id", "name": "Apple iPhone 16e SMARTFON", "parameters": [
                {"name": "EAN (GTIN)", "values": ["195950051186"], "options": {"isGTIN": True}}],
            "images": [{"url": "https://example/image.jpg"}]}], "filters": [], "nextPage": None})
        self.assertEqual(result, 0)
        self.assertEqual(call.kwargs["params"]["mode"], "GTIN")
        self.assertIn("Apple iPhone 16e SMARTFON", output)
        self.assertIn("sell --product product-id", output)
        self.assertNotIn('"filters"', output)
        self.assertNotIn("https://example/image.jpg", output)

    def test_search_json_preserves_response(self):
        response = {"products": [], "filters": ["raw"], "nextPage": None}
        result, output, _ = self.search(response, "--json")
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output), response)

    def test_empty_search_is_actionable(self):
        _, output, _ = self.search({"products": [], "nextPage": None})
        self.assertIn("No products found", output)
        self.assertIn("Sandbox", output)

    def test_search_limit_and_pagination_do_not_hide_more_results(self):
        products = [{"id": str(i), "name": "Phone " + str(i)} for i in range(3)]
        _, output, call = self.search({"products": products, "nextPage": {"id": "cursor"}},
                                      "--limit", "2", "--page", "previous")
        self.assertIn("Showing 2 of 3", output)
        self.assertNotIn("Phone 2", output)
        self.assertIn("--page cursor", output)
        self.assertEqual(call.kwargs["params"]["page.id"], "previous")

    def test_search_missing_product_id_has_readable_error(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            result, _, _ = self.search({"products": [{"name": "Phone"}]})
        self.assertEqual(result, 1)
        self.assertIn("invalid product", err.getvalue())

    def test_optional_malformed_fields_do_not_crash_summary(self):
        from allegro.display import display
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            display({"products": [{"id": "phone", "name": "Phone", "category": {"path": ["leaf"]},
                                   "parameters": [{"options": ["product"], "values": 123}]}]},
                    parser().parse_args(["search", "Phone"]))
            display({"id": "123", "validation": {"errors": 123, "warnings": 123}},
                    parser().parse_args(["status", "123"]))
            display({"parameters": [{"id": "1", "options": ["product"]}]},
                    parser().parse_args(["parameters", "165"]))
        self.assertIn("Phone", out.getvalue())
        self.assertIn("Offer 123", out.getvalue())

    def test_pending_sale_has_clear_status_and_distinct_exit(self):
        out, err = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as directory, patch("allegro.cli.Client") as client:
            client.return_value.complete.side_effect = Pending(
                "/sale/product-offers/123/operations/op", "/sale/product-offers/123",
                {"id": "123", "publication": {"status": "ACTIVE"}})
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                result = main(["--state-dir", directory, "sell", "--product", "123", "--price", "10",
                               "--wait-seconds", "0"])
        self.assertEqual(result, 2)
        self.assertIn("ACTIVE", out.getvalue())
        self.assertIn("still processing", err.getvalue())
        self.assertNotIn("Error:", err.getvalue())
        self.assertIn("docker compose run --rm allegro operation", err.getvalue())
        self.assertEqual(client.return_value.request.call_count, 1)
        self.assertEqual(client.return_value.complete.call_args.kwargs["timeout"], 0)

    def test_pending_json_is_machine_readable_and_not_completed(self):
        out, err = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as directory, patch("allegro.cli.Client") as client:
            client.return_value.complete.side_effect = Pending(
                "/sale/product-offers/123/operations/op", "/sale/product-offers/123", None)
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                result = main(["--state-dir", directory, "operation", "/sale/product-offers/123/operations/op", "--json"])
        self.assertEqual(result, 2)
        self.assertEqual(json.loads(out.getvalue())["processing"], "PENDING")
