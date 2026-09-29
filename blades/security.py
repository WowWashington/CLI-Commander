"""Network boundary and browser request protection for a private terminal server.

Tailscale ACLs decide which tailnet devices may connect. We trust no forwarding
headers and never expose the service through Funnel or a LAN/wildcard listener.
"""
import ipaddress
import json
import subprocess
import threading
import time
from urllib.parse import urlsplit

from flask import abort, request
from . import config

_TS4 = ipaddress.ip_network('100.64.0.0/10')
_TS6 = ipaddress.ip_network('fd7a:115c:a1e0::/48')
_hosts = {'localhost', '127.0.0.1', '::1'}
_checked = 0
_lock = threading.Lock()


def private_peer(value):
    try:
        ip = ipaddress.ip_address(value)
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        return ip.is_loopback or ip in (_TS4 if ip.version == 4 else _TS6)
    except ValueError:
        return False


def allowed_hosts():
    global _hosts, _checked
    with _lock:
        if time.monotonic() - _checked < 30:
            return set(_hosts)
        hosts = {'localhost', '127.0.0.1', '::1'}
        ts = config.tailscale_ip()
        if ts:
            hosts.add(ts)
        for binary in ('tailscale', '/Applications/Tailscale.app/Contents/MacOS/Tailscale'):
            try:
                result = subprocess.run([binary, 'status', '--json'], capture_output=True, text=True, timeout=3,
                                        env=config.tailscale_cli_environment())
                own = json.loads(result.stdout).get('Self') or {}
                dns = own.get('DNSName', '').rstrip('.').lower()
                if dns:
                    hosts.update((dns, dns.split('.')[0]))
                break
            except (OSError, ValueError, subprocess.TimeoutExpired):
                continue
        _hosts, _checked = hosts, time.monotonic()
        return set(hosts)


def _origin_tuple(url, allow_root_path=True):
    try:
        u = urlsplit(url)
        if u.scheme not in ('http', 'https') or u.username or u.password or not u.hostname:
            return None
        if u.path not in (('', '/') if allow_root_path else ('',)) or u.query or u.fragment:
            return None
        return (u.scheme, u.hostname.lower(), u.port if u.port is not None else (443 if u.scheme == 'https' else 80))
    except ValueError:
        return None


def install(app):
    app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024
    app.config['SOCK_SERVER_OPTIONS'] = {'ping_interval': 25, 'max_message_size': 1024 * 1024}

    @app.before_request
    def guard():
        if request.content_length and request.content_length > app.config['MAX_CONTENT_LENGTH']:
            abort(413, 'Request body too large')
        if not private_peer(request.remote_addr or ''):
            abort(403, 'Only local and Tailscale connections are allowed')
        origin = _origin_tuple(request.host_url)
        if not origin or origin[1] not in allowed_hosts():
            abort(403, 'Unrecognized host')
        supplied = request.headers.get('Origin')
        if supplied and _origin_tuple(supplied, allow_root_path=False) != origin:
            abort(403, 'Cross-origin requests are disabled')
        if request.headers.get('Sec-Fetch-Site') == 'cross-site':
            abort(403, 'Cross-site requests are disabled')
        # WebSockets carry no custom headers. A matching Origin is mandatory.
        if request.path.startswith('/ws/') and not supplied:
            abort(403, 'WebSocket Origin required')
        if request.path.startswith('/hook'):
            try:
                local = ipaddress.ip_address(request.remote_addr).is_loopback
            except ValueError:
                local = False
            if not local or supplied:
                abort(403, 'Hooks are local process callbacks only')
        elif request.method not in ('GET', 'HEAD', 'OPTIONS'):
            if request.headers.get('X-Blades-Request') != '1':
                abort(403, 'X-Blades-Request header required')
            if request.content_length and not request.is_json:
                abort(415, 'JSON required')

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if not request.path.startswith('/static/'):
            response.headers['Cache-Control'] = 'no-store'
        return response
