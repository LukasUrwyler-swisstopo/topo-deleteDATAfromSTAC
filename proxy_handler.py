"""
proxy_handler.py  –  Automatische Proxy-Erkennung und -Konfiguration.

Reihenfolge (detect_proxy_requirement):
  1. Direkte Verbindung
  2. System-Proxy (Windows-Registrierung / Umgebungsvariablen)
  3. Proxies aus secrets/proxy_config.json

Kerberos/SSPI wird automatisch erkannt – kein Benutzername/Passwort nötig.
"""

import json
import logging
import threading as _threading
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
import urllib3

logger = logging.getLogger(__name__)

# Pfad relativ zum Script (nicht zum CWD)
PROXY_CONFIG_PATH = Path(__file__).parent / "secrets" / "proxy_config.json"

PROXY_CONFIG: Dict = {
    'enabled': False,
    'proxies': None,
    'session': None,
    'verify_ssl': True,
    'active_proxy': None,
    'initialized': False,
    'is_vpn': False,
    'auth_method': None,
}

_PROXY_INIT_LOCK = _threading.Lock()

DEFAULT_PROXY_CONFIG = {
    'proxies': [
        {'name': 'BVCOL', 'url': 'http://proxy-bvcol.admin.ch:8080', 'enabled': True}
    ],
    'test_url': 'https://data.geo.admin.ch/browser/index.html',
    'timeout': 5,
    'disable_ssl_warnings': True,
}


def load_proxy_config() -> Dict:
    if not PROXY_CONFIG_PATH.exists():
        logger.info(f"  Keine proxy_config.json gefunden – verwende Default (BVCOL)")
        return DEFAULT_PROXY_CONFIG
    try:
        with open(PROXY_CONFIG_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"  Fehler beim Laden der Proxy-Config: {e} – verwende Default")
        return DEFAULT_PROXY_CONFIG


def get_enabled_proxies(config: Dict) -> List[Dict]:
    return [p for p in config.get('proxies', []) if p.get('enabled') and p.get('url')]


def test_connection(test_url: str, proxies: Optional[Dict] = None,
                   verify_ssl: bool = True, timeout: int = 5) -> bool:
    try:
        r = requests.get(test_url, proxies=proxies, verify=verify_ssl, timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def _get_system_proxy_urls() -> List[str]:
    import os
    raw = urllib.request.getproxies()
    env_keys = ('https_proxy', 'http_proxy', 'all_proxy',
                'HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY')
    seen: set = set()
    result: List[str] = []
    for key in ('https', 'http'):
        url = raw.get(key)
        if url and url not in seen:
            seen.add(url)
            result.append(url)
    for key in env_keys:
        url = os.environ.get(key)
        if url and url not in seen:
            seen.add(url)
            result.append(url)
    return result


def _make_kerberos_session(proxies_dict: Optional[Dict],
                           verify_ssl: bool) -> Tuple[Optional[requests.Session], Optional[str]]:
    """Session mit Kerberos/SSPI – versucht pyspnego, dann requests-negotiate-sspi."""
    _proxy_host = ''
    if proxies_dict:
        from urllib.parse import urlparse
        _proxy_host = urlparse(next(iter(proxies_dict.values()), '')).hostname or ''

    # Option 1: pyspnego
    try:
        import spnego as _spnego
        import base64 as _b64

        class _SpnegoAuth(requests.auth.AuthBase):
            def __call__(self, r):
                ctx = _spnego.client(hostname=_proxy_host, service='http', protocol='negotiate')
                out = ctx.step()
                if out:
                    r.headers['Proxy-Authorization'] = f'Negotiate {_b64.b64encode(out).decode()}'
                return r

        s = requests.Session()
        if proxies_dict:
            s.proxies.update(proxies_dict)
        s.verify = verify_ssl
        s.auth = _SpnegoAuth()
        return s, "spnego"
    except ImportError:
        pass

    # Option 2: requests-negotiate-sspi
    try:
        from requests_negotiate_sspi import HttpNegotiateAuth
        s = requests.Session()
        if proxies_dict:
            s.proxies.update(proxies_dict)
        s.verify = verify_ssl
        s.auth = HttpNegotiateAuth()
        return s, "negotiate-sspi"
    except ImportError:
        pass

    return None, None


def _probe_proxy(proxy_url: str, test_url: str, timeout: int) -> dict:
    proxies = {"http": proxy_url, "https": proxy_url}
    for verify in (True, False):
        try:
            r = requests.get(test_url, proxies=proxies, verify=verify, timeout=timeout)
            if r.status_code == 200:
                return {'status': 'ok', 'verify_ssl': verify}
        except requests.exceptions.ProxyError as e:
            if '407' in str(e):
                return {'status': 'needs_kerberos'}
        except Exception:
            pass
    return {'status': 'fail'}


def _probe_proxy_kerberos(proxy_url: str, test_url: str, timeout: int) -> dict:
    proxies = {"http": proxy_url, "https": proxy_url}
    for verify in (True, False):
        session, method = _make_kerberos_session(proxies, verify)
        if session is None:
            return {'status': 'no_lib'}
        try:
            r = session.get(test_url, timeout=timeout)
            if r.status_code == 200:
                return {'status': 'ok', 'verify_ssl': verify,
                        'session': session, 'method': method}
        except Exception:
            pass
    return {'status': 'fail'}


def _connect_named_proxy(proxy_name, proxy_url, test_url, timeout, _ok):
    probe = _probe_proxy(proxy_url, test_url, timeout)
    if probe['status'] == 'ok':
        verify = probe['verify_ssl']
        s = requests.Session()
        s.proxies.update({"http": proxy_url, "https": proxy_url})
        s.verify = verify
        logger.info(f"  ✓ Proxy '{proxy_name}' OK" +
                    (" (SSL deaktiviert)" if not verify else ""))
        return _ok(proxy_name, {"http": proxy_url, "https": proxy_url}, verify, s, not verify)

    if probe['status'] == 'needs_kerberos':
        logger.info(f"  Proxy '{proxy_name}' verlangt Kerberos – versuche SSPI ...")
        kprobe = _probe_proxy_kerberos(proxy_url, test_url, timeout)
        if kprobe['status'] == 'ok':
            logger.info(f"  ✓ Proxy '{proxy_name}' OK (Kerberos/{kprobe['method']})")
            return _ok(f"{proxy_name} (kerberos/{kprobe['method']})",
                       {"http": proxy_url, "https": proxy_url},
                       kprobe['verify_ssl'], kprobe['session'],
                       auth_method=f"kerberos/{kprobe['method']}")
        if kprobe['status'] == 'no_lib':
            logger.warning(f"  Proxy '{proxy_name}' braucht Kerberos, Bibliothek fehlt.\n"
                           "  pip install requests-negotiate-sspi")
    else:
        logger.info(f"  ✗ Proxy '{proxy_name}' nicht erreichbar")
    return None


def detect_proxy_requirement() -> Dict:
    """Autodetect: direkt → System-Proxy → proxy_config.json"""
    config   = load_proxy_config()
    test_url = config.get('test_url', DEFAULT_PROXY_CONFIG['test_url'])
    timeout  = config.get('timeout',  DEFAULT_PROXY_CONFIG['timeout'])

    if config.get('disable_ssl_warnings', True):
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def _ok(name, proxies_dict, verify, session, is_vpn=False, auth_method=None):
        return {'enabled': proxies_dict is not None, 'proxies': proxies_dict,
                'session': session, 'verify_ssl': verify, 'active_proxy': name,
                'initialized': True, 'is_vpn': is_vpn, 'auth_method': auth_method}

    # 1. Direkte Verbindung
    logger.info(f"[1] Direkte Verbindung → {test_url} ...")
    if test_connection(test_url, timeout=timeout):
        logger.info("  ✓ Direkte Verbindung OK")
        s = requests.Session()
        s.verify = True
        return _ok(None, None, True, s)
    logger.info("  ✗ Direkte Verbindung fehlgeschlagen")

    # 2. System-Proxy
    system_proxies = _get_system_proxy_urls()
    if system_proxies:
        logger.info(f"[2] System-Proxy: {system_proxies}")
        for proxy_url in system_proxies:
            result = _connect_named_proxy(f"system:{proxy_url}", proxy_url, test_url, timeout, _ok)
            if result:
                return result

    # 3. proxy_config.json (inkl. BVCOL-Default)
    for idx, p in enumerate(get_enabled_proxies(config), 1):
        logger.info(f"[3.{idx}] Konfigurierter Proxy '{p['name']}': {p['url']}")
        result = _connect_named_proxy(p['name'], p['url'], test_url, timeout, _ok)
        if result:
            return result

    raise ConnectionError(
        f"Keine Verbindung möglich (getestet: direkt, System-Proxy, {len(get_enabled_proxies(config))} Proxy(s)).\n"
        f"Test-URL: {test_url}"
    )


def initialize_proxy():
    """Thread-sicher: Proxy einmalig initialisieren."""
    global PROXY_CONFIG
    if PROXY_CONFIG.get('initialized'):
        return
    with _PROXY_INIT_LOCK:
        if PROXY_CONFIG.get('initialized'):
            return
        PROXY_CONFIG.update(detect_proxy_requirement())
        status = f"Proxy: {PROXY_CONFIG['active_proxy']}" if PROXY_CONFIG['enabled'] else "Direkte Verbindung"
        logger.info(status)


def get_session() -> requests.Session:
    if not PROXY_CONFIG.get('initialized'):
        initialize_proxy()
    if PROXY_CONFIG['session'] is None:
        raise RuntimeError("Session konnte nicht erstellt werden.")
    return PROXY_CONFIG['session']


def get_proxies_dict() -> Optional[Dict]:
    if not PROXY_CONFIG.get('initialized'):
        initialize_proxy()
    return PROXY_CONFIG.get('proxies')


def get_verify_ssl() -> bool:
    if not PROXY_CONFIG.get('initialized'):
        initialize_proxy()
    return PROXY_CONFIG.get('verify_ssl', True)
