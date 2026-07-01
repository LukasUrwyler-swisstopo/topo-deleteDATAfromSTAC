"""
stac_api.py  –  STAC API Hilfsfunktionen für ch.swisstopo.spezialbefliegungen

Importiert von 0_GUI_stac_gdwh_delete_Data.py.
Direkt nutzbar: python stac_api.py  (gibt Kurzinfo aus)
"""

import re
import requests
from urllib.parse import urljoin
from typing import Dict, List, Optional, Tuple


# Firmenproxy für externe Verbindungen (data.geo.admin.ch / sys-data.int.bgdi.ch)
_PROXY = {
    "http":  "http://proxy-bvcol.admin.ch:8080",
    "https": "http://proxy-bvcol.admin.ch:8080",
}

COLLECTION_ID = "ch.swisstopo.spezialbefliegungen"

ENVIRONMENTS = {
    "INT":  "https://sys-data.int.bgdi.ch/api/stac/v0.9/",
    "PROD": "https://data.geo.admin.ch/api/stac/v0.9/",
}

# Hash-Routing-Basis des STAC-Browsers je Umgebung (für Kunden-Weitergabe).
# INT läuft direkt auf der Domain-Root, PROD unter /browser/index.html.
_BROWSER_BASE = {
    "INT":  "https://sys-data.int.bgdi.ch/#/collections/{cid}",
    "PROD": "https://data.geo.admin.ch/browser/index.html#/collections/{cid}",
}


def browser_url(env: str, item_id: Optional[str] = None) -> str:
    """Liefert den STAC-Browser-Link zur Collection, optional zu einem Item."""
    url = _BROWSER_BASE[env].format(cid=COLLECTION_ID)
    if item_id:
        url += f"/items/{item_id}"
    return url + "?.language=en"


AUFTRAGSTYPEN: Dict[str, str] = {
    "KRY (Kryosphäre)":   "kry",
    "RAM (Rapidmapping)": "ram",
    "Alle":               "",
}

EXT_PRESETS: List[Tuple[str, List[str]]] = [
    ("tif / tiff",      [".tif", ".tiff"]),
    ("copc.laz / laz",  [".copc.laz", ".laz"]),
    ("jpg / jpeg",      [".jpg", ".jpeg"]),
    ("png",             [".png"]),
    ("json",            [".json"]),
]


# ─── Interne Session-Funktionen ───────────────────────────────────────────────

# None = noch nicht getestet, True/False = Ergebnis des ersten Verbindungsversuchs.
# Ausserhalb des Bundesnetz (z.B. privater Rechner) ist proxy-bvcol.admin.ch nicht
# auflösbar -> nach einmaligem ProxyError auf Direktverbindung umschalten.
_USE_PROXY: Optional[bool] = None


def _request(method, url: str, **kwargs) -> requests.Response:
    global _USE_PROXY
    if _USE_PROXY is False:
        return method(url, proxies=None, **kwargs)
    try:
        r = method(url, proxies=_PROXY, **kwargs)
        _USE_PROXY = True
        return r
    except requests.exceptions.ProxyError:
        _USE_PROXY = False
        return method(url, proxies=None, **kwargs)


def _session_get(url: str, auth: Tuple, params: dict = None) -> requests.Response:
    return _request(requests.get, url, auth=auth, params=params,
                    verify=False, timeout=(30, 60))


def _session_delete(url: str, auth: Tuple) -> requests.Response:
    return _request(requests.delete, url, auth=auth,
                    verify=False, timeout=(30, 60))


# ─── Öffentliche API-Funktionen ───────────────────────────────────────────────

def get_item_direct(base_url: str, auth: Tuple, item_id: str) -> Optional[Dict]:
    """Holt ein einzelnes Item per exakter ID. Gibt None bei 404 zurück."""
    url = urljoin(base_url, f"collections/{COLLECTION_ID}/items/{item_id.strip()}")
    r = _session_get(url, auth)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def get_collection_items(base_url: str, auth: Tuple, log_fn=print) -> List[Dict]:
    """Holt alle Items der Collection mit Paginierung."""
    all_items = []
    url    = urljoin(base_url, f"collections/{COLLECTION_ID}/items")
    params = {"limit": 1000}
    while url:
        r = _session_get(url, auth, params)
        r.raise_for_status()
        data = r.json()
        all_items.extend(data.get("features", []))
        nxt = next((lk for lk in data.get("links", []) if lk.get("rel") == "next"), None)
        if nxt:
            url    = nxt["href"]
            params = None
            log_fn(f"  Paginierung … bisher {len(all_items)} Items geladen\n")
        else:
            url = None
    return all_items


def filter_items(items: List[Dict], search_term: str = "") -> List[Dict]:
    """Filtert Items nach Teilstring in der ID (case-insensitive)."""
    if not search_term:
        return items
    term = search_term.lower()
    return [item for item in items if term in item.get("id", "").lower()]


def delete_asset(base_url: str, auth: Tuple,
                 item_id: str, asset_key: str) -> Tuple[bool, int]:
    """Löscht einen einzelnen Asset. Gibt (Erfolg, HTTP-Statuscode) zurück."""
    url = urljoin(base_url,
                  f"collections/{COLLECTION_ID}/items/{item_id}/assets/{asset_key}")
    r = _session_delete(url, auth)
    return r.status_code in (200, 204), r.status_code


def delete_item(base_url: str, auth: Tuple, item_id: str) -> Tuple[bool, int]:
    """Löscht ein Item vollständig (nur wenn leer). Gibt (Erfolg, HTTP-Statuscode) zurück."""
    url = urljoin(base_url, f"collections/{COLLECTION_ID}/items/{item_id}")
    r = _session_delete(url, auth)
    return r.status_code in (200, 204), r.status_code


def check_asset_info(href: str, auth: Tuple) -> Dict:
    """HEAD-Request auf Asset-URL.
    Gibt dict mit status, size_bytes und last_modified zurück.
    status: HTTP-Code oder negativ (-1=kein href, -2=timeout, -3=exception)."""
    result: Dict = {"status": -1, "size_bytes": None, "last_modified": None}
    if not href:
        return result
    try:
        r = _request(requests.head, href, verify=False,
                    timeout=(5, 15), allow_redirects=True)
        if r.status_code in (401, 403):
            r = _request(requests.head, href, auth=auth, verify=False,
                        timeout=(5, 15), allow_redirects=True)
        result["status"] = r.status_code
        cl = r.headers.get("Content-Length", "")
        if cl.isdigit():
            result["size_bytes"] = int(cl)
        lm = r.headers.get("Last-Modified")
        if lm:
            result["last_modified"] = lm
    except requests.exceptions.Timeout:
        result["status"] = -2
    except Exception:
        result["status"] = -3
    return result


def stac_item_acq_date(item: Dict) -> str:
    """Gibt das Aufnahmedatum aus der Item-ID zurück (Format: YYYY-MM-DD).
    Priorität: Item-ID (enthält immer das Befliegungsdatum), dann properties.datetime."""
    m = re.search(r"(20\d{2}-\d{2}-\d{2})", item.get("id", ""))
    if m:
        return m.group(1)
    return item.get("properties", {}).get("datetime", "")


def stac_item_year(item: Dict) -> str:
    """Extrahiert das Aufnahmejahr aus der Item-ID (Befliegungsdatum, nicht Importdatum)."""
    m = re.search(r"\b(20\d{2})\b", item.get("id", ""))
    if m:
        return m.group(1)
    # Fallback: properties.datetime (kann Importdatum sein – nur wenn ID kein Jahr hat)
    m = re.search(r"\b(20\d{2})\b", item.get("properties", {}).get("datetime", ""))
    return m.group(1) if m else ""


def stac_item_area(item: Dict) -> str:
    """Gibt den AOI-Namen zurück: zuerst aus Item-Properties (falls vorhanden),
    sonst aus der Description des ersten passenden Assets."""
    props = item.get("properties", {})
    for key in ("area", "aoi", "area_name", "region"):
        val = str(props.get(key, "")).strip()
        if val:
            return val.upper()
    for asset in item.get("assets", {}).values():
        area = asset_area(asset)
        if area:
            return area.upper()
    return ""


# Bekannte Schlüssel im "Key: Value, Key: Value, ..."-Format der Asset-Description
# (z.B. "Area: RANDA, TerrainModel: ..., Acquisition time: t1,t2,t3, LineId: ...").
# Einzelne Werte (Acquisition time, LineId) enthalten selbst Kommas – ein Split
# ausschliesslich anhand dieser bekannten Schlüssel verhindert falsches Zerteilen.
_ASSET_DESC_KEYS = [
    "Area", "TerrainModel", "SourceReferenceSystem", "CameraSystem",
    "Acquisition time", "LineId", "Commentary",
]


def parse_asset_description(description: str) -> Dict[str, str]:
    """Zerlegt die Asset-Description in ein Dict der bekannten Schlüssel."""
    if not description:
        return {}
    pattern = r"(" + "|".join(re.escape(k) for k in _ASSET_DESC_KEYS) + r"):\s*"
    matches = list(re.finditer(pattern, description))
    result: Dict[str, str] = {}
    for i, m in enumerate(matches):
        start = m.end()
        end   = matches[i + 1].start() if i + 1 < len(matches) else len(description)
        result[m.group(1)] = description[start:end].rstrip(", ").strip()
    return result


def asset_area(asset: Dict) -> str:
    """Extrahiert den 'Area'-Wert aus der Asset-Description, falls vorhanden."""
    return parse_asset_description(asset.get("description", "")).get("Area", "")


if __name__ == "__main__":
    print("stac_api.py – STAC API Modul")
    print(f"  Collection:  {COLLECTION_ID}")
    print(f"  Umgebungen:  {list(ENVIRONMENTS.keys())}")
    print(f"  Endpunkte:   DELETE asset, DELETE item, GET items")
