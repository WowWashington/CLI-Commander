"""Private-network and browser-boundary regression tests; no live services."""
import json
import os
import tempfile
import unittest
from unittest import mock

from flask import Flask

# config creates its directories on import. Keep even discovery off real state.
_IMPORT_HOME = tempfile.TemporaryDirectory(prefix="blades-security-import-")
with mock.patch.dict(os.environ, {"BLADES_HOME": _IMPORT_HOME.name}):
    from blades import config, security


class BindPolicyTests(unittest.TestCase):
    def test_launchd_discovery_uses_cli_mode_without_changing_parent_environment(self):
        def cli(args, **kwargs):
            self.assertEqual(kwargs['env']['TERM'], 'dumb')
            if args[0] == 'tailscale':
                raise FileNotFoundError()
            return mock.Mock(stdout='100.100.20.30\n', returncode=0)
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch('subprocess.run', side_effect=cli):
            self.assertEqual(config.tailscale_ip(), '100.100.20.30')
            self.assertNotIn('TERM', os.environ)

    def test_default_exposes_only_loopback_and_own_tailnet_ip(self):
        with mock.patch.object(config, "HOST", ""), mock.patch.object(
                config, "tailscale_ip", return_value="100.100.20.30"):
            self.assertEqual(config.bind_addresses(), ["127.0.0.1", "100.100.20.30"])

    def test_missing_tailscale_fails_back_to_loopback(self):
        with mock.patch.object(config, "HOST", ""), mock.patch.object(
                config, "tailscale_ip", return_value=""):
            self.assertEqual(config.bind_addresses(), ["127.0.0.1"])

    def test_explicit_lan_wildcard_public_or_other_tailnet_bind_is_rejected(self):
        for host in ("0.0.0.0", "::", "192.168.1.5", "10.0.0.5", "172.16.1.5",
                     "8.8.8.8", "100.100.20.31", "example.com"):
            with self.subTest(host=host), mock.patch.object(config, "HOST", host), \
                    mock.patch.object(config, "tailscale_ip", return_value="100.100.20.30"):
                with self.assertRaises(ValueError):
                    config.bind_addresses()

    def test_explicit_loopback_or_own_tailnet_bind_is_allowed(self):
        for host in ("127.0.0.1", "::1", "localhost", "100.100.20.30"):
            with self.subTest(host=host), mock.patch.object(config, "HOST", host), \
                    mock.patch.object(config, "tailscale_ip", return_value="100.100.20.30"):
                self.assertEqual(config.bind_addresses(), [host])


class RequestBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.hosts = mock.patch.object(security, "allowed_hosts", return_value={
            "localhost", "127.0.0.1", "::1", "100.100.20.30", "blades.example.ts.net"})
        self.hosts.start()
        self.addCleanup(self.hosts.stop)
        self.app = Flask(__name__)
        self.app.config["TESTING"] = True
        security.install(self.app)
        for path in ("/", "/mutate", "/ws/test", "/hook", "/hook/codex"):
            self.app.add_url_rule(path, path, lambda: {"ok": True}, methods=["GET", "POST"])
        self.client = self.app.test_client()

    def request(self, path="/", method="GET", peer="127.0.0.1", host="localhost",
                headers=None, **kwargs):
        return self.client.open(path, method=method, base_url="http://" + host,
                                environ_overrides={"REMOTE_ADDR": peer},
                                headers=headers or {}, **kwargs)

    def test_peer_allowlist_ipv4_ipv6_and_mapped_ipv4(self):
        for peer in ("127.0.0.1", "::1", "::ffff:127.0.0.1", "100.64.0.1",
                     "100.127.255.254", "fd7a:115c:a1e0::1234", "::ffff:100.90.1.2"):
            with self.subTest(peer=peer):
                self.assertEqual(self.request(peer=peer).status_code, 200)
        for peer in ("", "garbage", "192.168.0.1", "10.0.0.1", "172.20.0.1",
                     "100.63.255.255", "100.128.0.1", "8.8.8.8", "fd00::1", "2001:db8::1"):
            with self.subTest(peer=peer):
                self.assertEqual(self.request(peer=peer).status_code, 403)

    def test_forwarding_headers_do_not_override_an_untrusted_peer(self):
        response = self.request(peer="192.168.1.25", headers={
            "X-Forwarded-For": "100.90.1.2", "X-Real-IP": "127.0.0.1",
            "Tailscale-User-Login": "owner@example.test"})
        self.assertEqual(response.status_code, 403)

    def test_host_must_be_an_exact_known_host(self):
        for host in ("evil.test", "localhost.evil.test", "blades.example.ts.net.evil.test",
                     "192.168.1.5", "100.100.20.31"):
            with self.subTest(host=host):
                self.assertEqual(self.request(host=host).status_code, 403)
        self.assertEqual(self.request(host="blades.example.ts.net:5090").status_code, 200)
        self.assertEqual(self.request(headers={"Host": "localhost:bad"}).status_code, 403)

    def test_origin_matches_scheme_host_and_port(self):
        good = self.request(host="localhost:5090", headers={"Origin": "http://localhost:5090"})
        self.assertEqual(good.status_code, 200)
        for origin in ("http://evil.test:5090", "https://localhost:5090", "http://localhost",
                       "http://127.0.0.1:5090", "null", "http://user@localhost:5090"):
            with self.subTest(origin=origin):
                self.assertEqual(self.request(host="localhost:5090", headers={"Origin": origin}).status_code, 403)

    def test_tailscale_serve_https_origin_only_via_loopback_proxy(self):
        # `tailscale serve` connects from loopback over HTTP; the browser is on https://<node>.ts.net.
        ts = {"Origin": "https://blades.example.ts.net", "X-Blades-Request": "1"}
        self.assertEqual(self.request("/mutate", "POST", host="blades.example.ts.net", headers=ts, json={}).status_code, 200)
        self.assertEqual(self.request("/ws/test", host="blades.example.ts.net", headers={"Origin": "https://blades.example.ts.net"}).status_code, 200)
        for peer, host, origin in (
                ("100.90.1.2", "blades.example.ts.net", "https://blades.example.ts.net"),   # not via the local proxy
                ("127.0.0.1", "blades.example.ts.net", "https://evil.example.ts.net"),     # another name
                ("127.0.0.1", "blades.example.ts.net", "https://blades.example.ts.net:8443"),
                ("127.0.0.1", "localhost", "https://localhost"),                           # only Tailscale names
                ("127.0.0.1", "blades.example.ts.net", "http://evil.test")):
            with self.subTest(peer=peer, origin=origin):
                self.assertEqual(self.request("/mutate", "POST", peer=peer, host=host,
                                              headers={"Origin": origin, "X-Blades-Request": "1"}, json={}).status_code, 403)

    def test_malformed_origin_is_rejected_instead_of_normalized(self):
        for origin in ("http://localhost/extra", "http://localhost?extra=1",
                       "http://localhost#fragment", "http://localhost:0", "http://[invalid"):
            with self.subTest(origin=origin):
                self.assertEqual(self.request(headers={"Origin": origin}).status_code, 403)

    def test_mutations_require_non_simple_header_and_json_body(self):
        self.assertEqual(self.request("/mutate", "POST", json={}).status_code, 403)
        self.assertEqual(self.request("/mutate", "POST", headers={"X-Blades-Request": "1"},
                                      data="name=x", content_type="application/x-www-form-urlencoded").status_code, 415)
        self.assertEqual(self.request("/mutate", "POST", headers={"X-Blades-Request": "1"},
                                      json={"name": "x"}).status_code, 200)
        self.assertEqual(self.request("/mutate", "POST", headers={"X-Blades-Request": "1",
                                      "Sec-Fetch-Site": "cross-site"}, json={}).status_code, 403)

    def test_cross_site_allows_only_top_level_page_navigation(self):
        nav = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
        self.assertEqual(self.request("/", headers=nav).status_code, 200)
        for path, method, headers in (
                ("/", "GET", {**nav, "Sec-Fetch-Dest": "iframe"}),
                ("/", "GET", {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"}),
                ("/mutate", "POST", {**nav, "X-Blades-Request": "1"}),
                ("/ws/test", "GET", {**nav, "Origin": "http://localhost"})):
            with self.subTest(path=path, headers=headers):
                self.assertEqual(self.request(path, method, headers=headers, **({"json": {}} if method == "POST" else {})).status_code, 403)

    def test_websocket_requires_same_origin_even_from_tailnet(self):
        self.assertEqual(self.request("/ws/test", peer="100.90.1.2").status_code, 403)
        self.assertEqual(self.request("/ws/test", peer="100.90.1.2",
                                      headers={"Origin": "http://evil.test"}).status_code, 403)
        self.assertEqual(self.request("/ws/test", peer="100.90.1.2",
                                      headers={"Origin": "http://localhost"}).status_code, 200)

    def test_hook_callbacks_allow_only_local_process_requests(self):
        for endpoint in ("/hook", "/hook/codex"):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.request(endpoint, "POST", json={}).status_code, 200)
                self.assertEqual(self.request(endpoint, "POST", peer="::1", json={}).status_code, 200)
                self.assertEqual(self.request(endpoint, "POST", peer="100.90.1.2", json={}).status_code, 403)
                self.assertEqual(self.request(endpoint, "POST", headers={"Origin": "http://localhost"},
                                              json={}).status_code, 403)

    def test_security_headers_and_body_size_limit(self):
        response = self.request()
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        oversized = self.request("/mutate", "POST", headers={"X-Blades-Request": "1"},
                                 data=b"x" * (1024 * 1024 + 1), content_type="application/json")
        self.assertEqual(oversized.status_code, 413)


if __name__ == "__main__":
    unittest.main()


class HttpsUrlTests(unittest.TestCase):
    def status(self, web):
        return mock.Mock(stdout=json.dumps({"Web": web}))

    def setUp(self):
        security._https.update(checked=0.0, url="")
        self.addCleanup(security._https.update, checked=0.0, url="")

    def test_https_url_only_for_this_port_on_443(self):
        port = security.config.PORT
        cases = [
            ({"node.example.ts.net:443": {"Handlers": {"/": {"Proxy": f"http://127.0.0.1:{port}"}}}}, "https://node.example.ts.net"),
            ({"node.example.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:1"}}}}, ""),
            ({"node.example.ts.net:8443": {"Handlers": {"/": {"Proxy": f"http://127.0.0.1:{port}"}}}}, ""),
            ({}, ""),
        ]
        for web, expected in cases:
            with self.subTest(expected=expected), mock.patch.object(security.subprocess, "run", return_value=self.status(web)):
                security._https.update(checked=0.0, url="")
                self.assertEqual(security.https_url(), expected)
