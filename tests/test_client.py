import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import requests

from allegro.client import Client, Pending, locked_state
from allegro.transport import API_URL, Error, api_url


def response(status=200, data=None, **headers):
    result = Mock(status_code=status, headers=headers)
    result.json.return_value = {} if data is None else data
    return result


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {
            "ALLEGRO_CLIENT_ID": "client", "ALLEGRO_CLIENT_SECRET": "secret"
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = Client(Path(self.tmp.name))
        self.client.session = Mock()

    def saved(self, expires=9999999999):
        self.client.save_tokens({"access_token": "old", "refresh_token": "refresh",
                                 "expires_in": 3600})
        data = json.loads(self.client.token_path.read_text())
        data["expires_at"] = expires
        self.client.token_path.write_text(json.dumps(data))

    def test_device_pending_slowdown_then_success(self):
        self.client.session.request.side_effect = [
            response(data={"device_code": "d", "user_code": "u", "expires_in": 60,
                           "interval": 1, "verification_uri_complete": "https://example"}),
            response(400, {"error": "authorization_pending"}),
            response(400, {"error": "slow_down"}),
            response(data={"access_token": "token", "refresh_token": "rotated", "expires_in": 100}),
        ]
        with patch("allegro.client.time.sleep") as sleep:
            self.client.login()
        self.assertEqual([x.args[0] for x in sleep.call_args_list], [1, 1, 6])
        self.assertEqual(self.client.access_token(), "token")
        self.assertEqual(self.client.token_path.stat().st_mode & 0o777, 0o600)
        self.assertFalse(list(Path(self.tmp.name).glob("*.tmp")))

    def test_device_denied(self):
        self.client.session.request.side_effect = [
            response(data={"device_code": "d", "expires_in": 60, "interval": 1,
                           "verification_uri_complete": "https://example"}),
            response(400, {"error": "access_denied"}),
        ]
        with patch("allegro.client.time.sleep"), self.assertRaisesRegex(Error, "access_denied"):
            self.client.login()
        self.assertFalse(self.client.token_path.exists())

    def test_expired_token_refresh_rotates_and_persists(self):
        self.saved(expires=0)
        self.client.session.request.return_value = response(data={
            "access_token": "new", "refresh_token": "new-refresh", "expires_in": 3600})
        self.assertEqual(self.client.access_token(), "new")
        self.assertEqual(json.loads(self.client.token_path.read_text())["refresh_token"], "new-refresh")
        call = self.client.session.request.call_args
        self.assertEqual(call.kwargs["data"]["grant_type"], "refresh_token")
        self.assertFalse(call.kwargs["allow_redirects"])

    def test_401_refresh_only_once(self):
        self.saved()
        self.client.session.request.side_effect = [
            response(401), response(data={"access_token": "new", "refresh_token": "r", "expires_in": 100}),
            response(401, {"errors": [{"message": "Unauthorized"}]}),
        ]
        with self.assertRaisesRegex(Error, "401"):
            self.client.request("GET", "/me")
        self.assertEqual(self.client.session.request.call_count, 3)

    def test_completed_operation_follows_303(self):
        self.saved()
        self.client.session.request.side_effect = [
            response(202, **{"Retry-After": "0"}),
            response(303, Location="/sale/product-offers/123"),
            response(data={"id": "123", "publication": {"status": "ACTIVE"}}),
        ]
        first = response(202, Location="/sale/product-offers/123/operations/op", **{"Retry-After": "0"})
        with patch("allegro.client.time.sleep"):
            result = self.client.complete(first)
        self.assertEqual(result["publication"]["status"], "ACTIVE")

    def test_completed_redirect_is_followed_even_at_wait_deadline(self):
        self.saved()
        self.client.session.request.return_value = response(data={"id": "123", "publication": {"status": "ACTIVE"}})
        self.assertEqual(self.client.complete(response(303, Location="/sale/product-offers/123"), timeout=0)["id"], "123")

    def test_completed_redirect_needs_its_own_location(self):
        with self.assertRaisesRegex(Error, "no Location"):
            self.client.complete(response(303))

    def test_timeout_reports_recovery_path(self):
        first = response(202, Location="/sale/product-offers/123/operations/op")
        with self.assertRaisesRegex(Error, "operations/op"):
            self.client.complete(first, timeout=0)

    def test_pending_timeout_checks_actual_offer_without_reposting(self):
        self.saved()
        offer = {"id": "123", "publication": {"status": "ACTIVE"}}
        self.client.session.request.return_value = response(data=offer)
        first = response(202, Location="/sale/product-offers/123/operations/op")
        with self.assertRaises(Pending) as pending:
            self.client.complete(first, timeout=0)
        self.assertEqual(pending.exception.offer, offer)
        call = self.client.session.request.call_args
        self.assertEqual(call.args, ("GET", API_URL + "/sale/product-offers/123"))
        self.assertEqual(self.client.session.request.call_count, 1)

    def test_pending_preserves_recovery_if_offer_read_fails(self):
        self.saved()
        self.client.session.request.return_value = response(404, {"errors": [{"message": "Not visible yet"}]})
        first = response(202, Location="/sale/product-offers/123/operations/op")
        with self.assertRaises(Pending) as pending:
            self.client.complete(first, timeout=0)
        self.assertIsNone(pending.exception.offer)
        self.assertEqual(pending.exception.location, "/sale/product-offers/123/operations/op")

    def test_operation_error_is_not_replaced_by_offer_snapshot(self):
        self.saved()
        self.client.session.request.return_value = response(422, {"errors": [{"message": "Validation failed"}]})
        first = response(202, Location="/sale/product-offers/123/operations/op", **{"Retry-After": "0"})
        with patch("allegro.client.time.sleep"), self.assertRaisesRegex(Error, "Validation failed"):
            self.client.complete(first)
        self.assertEqual(self.client.session.request.call_count, 1)

    def test_unsafe_locations_never_sent(self):
        for url in ["https://evil.test/path", "//evil.test/path", "http://" + API_URL[8:],
                    API_URL + "@evil.test/path", API_URL + ":444/path"]:
            with self.subTest(url=url), self.assertRaises(Error):
                api_url(url)
        first = response(202, Location="https://evil.test/path")
        with self.assertRaises(Error):
            self.client.complete(first)
        self.client.session.request.assert_not_called()

    def test_state_lock_excludes_other_command(self):
        with locked_state(Path(self.tmp.name)):
            with self.assertRaisesRegex(Error, "Another"):
                with locked_state(Path(self.tmp.name)):
                    pass

    def test_error_retains_field_path(self):
        self.saved()
        self.client.session.request.return_value = response(422, {"errors": [
            {"path": "delivery.shippingRates", "userMessage": "Choose a shipping rate"}]})
        with self.assertRaisesRegex(Error, "delivery.shippingRates.*Choose"):
            self.client.request("POST", "/sale/product-offers", json={})

    def test_malformed_response(self):
        self.saved()
        self.client.session.request.return_value = response(data=[])
        with self.assertRaisesRegex(Error, "unexpected JSON"):
            self.client.complete(self.client.request("GET", "/me"))

    def test_missing_login(self):
        with self.assertRaisesRegex(Error, "login"):
            self.client.access_token()

    def test_credentials_do_not_match_saved_login(self):
        self.saved()
        with patch.dict(os.environ, {"ALLEGRO_CLIENT_ID": "other"}):
            with self.assertRaisesRegex(Error, "different"):
                Client(Path(self.tmp.name)).access_token()

    def test_invalid_url_is_readable(self):
        with self.assertRaisesRegex(Error, "Invalid API URL"):
            api_url("https://[")

    def test_malformed_error_field_is_readable(self):
        self.saved()
        self.client.session.request.return_value = response(422, {"errors": None})
        with self.assertRaisesRegex(Error, "422"):
            self.client.request("GET", "/me")

    def test_invalid_saved_token_types_and_expiry(self):
        for key, value in [("access_token", 123), ("refresh_token", []), ("expires_at", "NaN")]:
            self.saved()
            saved = json.loads(self.client.token_path.read_text())
            saved[key] = value
            self.client.token_path.write_text(json.dumps(saved))
            with self.subTest(key=key), self.assertRaisesRegex(Error, "Invalid saved"):
                self.client.access_token()

    def test_bad_oauth_expiry_is_rejected(self):
        for expires in ["NaN", "Infinity", -1]:
            with self.subTest(expires=expires), self.assertRaisesRegex(Error, "expires_in"):
                self.client.save_tokens({"access_token": "a", "refresh_token": "r", "expires_in": expires})

    def test_network_post_is_never_replayed(self):
        self.saved()
        self.client.session.request.side_effect = requests.Timeout()
        with self.assertRaisesRegex(Error, "may have reached Allegro"):
            self.client.request("POST", "/sale/product-offers", json={})
        self.assertEqual(self.client.session.request.call_count, 1)

    def test_redirect_cannot_leak_bearer_token(self):
        self.saved()
        self.client.session.request.return_value = response(303, Location="https://evil.test/path")
        with self.assertRaises(Error):
            self.client.complete(self.client.request("GET", "/sale/product-offers/123"))
        self.assertEqual(self.client.session.request.call_count, 1)
        self.assertFalse(self.client.session.request.call_args.kwargs["allow_redirects"])
