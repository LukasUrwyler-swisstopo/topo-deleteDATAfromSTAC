"""
test_functions.py  –  pytest Tests für stac_api.py und gdwh_api.py

Ausführen:
    pytest test_functions.py -v
    pytest test_functions.py -v --tb=short   # kompakter Fehler-Output

Keine echten HTTP-Requests – alle Netzwerkaufrufe werden gemockt.
"""

import pytest
from unittest.mock import MagicMock, patch

import requests as req_module

from stac_api import (
    COLLECTION_ID, ENVIRONMENTS, AUFTRAGSTYPEN, EXT_PRESETS,
    filter_items,
    get_item_direct, get_collection_items,
    delete_asset, delete_item,
    check_asset_info,
    stac_item_year, stac_item_area,
)
from gdwh_api import (
    GDWH_ENVIRONMENTS,
    gdwh_get_imports, gdwh_delete_import,
    gdwh_import_id, gdwh_import_name, gdwh_import_date, gdwh_import_status,
    gdwh_import_footprint_bbox, gdwh_estimate_area,
    gdwh_bucket_path,
    _lv95, _extract_year_from_folder, _area_from_folder_name, _parse_iso_dt,
)

AUTH      = ("testuser", "testpass")
BASE      = "https://sys-data.int.bgdi.ch/api/stac/v0.9/"
GDWH_BASE = "https://ltgdwhi.adr.admin.ch/gdwh-api/v2/"


def _mock_response(status: int = 200, json_data=None, raise_on_status=False):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_data if json_data is not None else {}
    if raise_on_status:
        r.raise_for_status.side_effect = req_module.HTTPError(response=r)
    else:
        r.raise_for_status = MagicMock()
    return r


# ═══════════════════════════════════════════════════════════════════════════════
# Konstanten
# ═══════════════════════════════════════════════════════════════════════════════

class TestKonstanten:

    def test_collection_id(self):
        assert COLLECTION_ID == "ch.swisstopo.spezialbefliegungen"

    def test_environments_schluessel(self):
        assert "INT"  in ENVIRONMENTS
        assert "PROD" in ENVIRONMENTS

    def test_gdwh_environments_schluessel(self):
        assert "INT"  in GDWH_ENVIRONMENTS
        assert "PROD" in GDWH_ENVIRONMENTS

    def test_auftragstypen_vorhanden(self):
        assert "KRY (Kryosphäre)"   in AUFTRAGSTYPEN
        assert "RAM (Rapidmapping)" in AUFTRAGSTYPEN
        assert "Alle"               in AUFTRAGSTYPEN

    def test_ext_presets_nicht_leer(self):
        assert len(EXT_PRESETS) > 0
        for label, exts in EXT_PRESETS:
            assert isinstance(label, str)
            assert all(e.startswith(".") for e in exts)


# ═══════════════════════════════════════════════════════════════════════════════
# filter_items
# ═══════════════════════════════════════════════════════════════════════════════

class TestFilterItems:

    ITEMS = [
        {"id": "ch.swisstopo.spezialbefliegungen_kry_2024-08-20"},
        {"id": "ch.swisstopo.spezialbefliegungen_kry_2024-09-15"},
        {"id": "ch.swisstopo.spezialbefliegungen_ram_2024-07-01"},
    ]

    def test_kein_suchbegriff_gibt_alle_zurueck(self):
        assert filter_items(self.ITEMS, "") == self.ITEMS

    def test_teilstring_trifft_mehrere(self):
        result = filter_items(self.ITEMS, "kry")
        assert len(result) == 2
        assert all("kry" in i["id"] for i in result)

    def test_teilstring_trifft_einen(self):
        result = filter_items(self.ITEMS, "ram")
        assert len(result) == 1
        assert result[0]["id"].endswith("ram_2024-07-01")

    def test_datum_als_filter(self):
        result = filter_items(self.ITEMS, "2024-08-20")
        assert len(result) == 1

    def test_case_insensitive(self):
        assert filter_items(self.ITEMS, "KRY") == filter_items(self.ITEMS, "kry")

    def test_kein_treffer(self):
        assert filter_items(self.ITEMS, "xyz_nicht_vorhanden") == []

    def test_leere_liste(self):
        assert filter_items([], "kry") == []

    def test_item_ohne_id_feld(self):
        items = [{"id": "kry-001"}, {"properties": {}}]
        result = filter_items(items, "kry")
        assert len(result) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# check_asset_info
# ═══════════════════════════════════════════════════════════════════════════════

class TestCheckAssetInfo:

    URL = "https://example.com/file.tif"

    def _resp(self, status=200, headers=None):
        r = _mock_response(status)
        r.headers = headers or {}
        return r

    def test_leere_url_gibt_minus_1(self):
        assert check_asset_info("", AUTH)["status"] == -1

    def test_200_ok(self):
        with patch("stac_api.requests.head", return_value=self._resp(200)):
            assert check_asset_info(self.URL, AUTH)["status"] == 200

    def test_404_nicht_gefunden(self):
        with patch("stac_api.requests.head", return_value=self._resp(404)):
            assert check_asset_info(self.URL, AUTH)["status"] == 404

    def test_403_wird_mit_auth_wiederholt(self):
        """Bei 403 soll ein zweiter HEAD-Request mit Auth gesendet werden."""
        with patch("stac_api.requests.head",
                   side_effect=[self._resp(403), self._resp(200)]) as mock_head:
            result = check_asset_info(self.URL, AUTH)
        assert result["status"] == 200
        assert mock_head.call_count == 2
        _, kwargs = mock_head.call_args
        assert kwargs.get("auth") == AUTH

    def test_timeout_gibt_minus_2(self):
        with patch("stac_api.requests.head",
                   side_effect=req_module.exceptions.Timeout):
            assert check_asset_info(self.URL, AUTH)["status"] == -2

    def test_netzwerkfehler_gibt_minus_3(self):
        with patch("stac_api.requests.head",
                   side_effect=ConnectionError("no route")):
            assert check_asset_info(self.URL, AUTH)["status"] == -3

    def test_groesse_und_datum_werden_gelesen(self):
        headers = {"Content-Length": "12345", "Last-Modified": "Wed, 20 Aug 2024 10:00:00 GMT"}
        with patch("stac_api.requests.head", return_value=self._resp(200, headers)):
            result = check_asset_info(self.URL, AUTH)
        assert result["size_bytes"] == 12345
        assert result["last_modified"] == headers["Last-Modified"]


# ═══════════════════════════════════════════════════════════════════════════════
# get_item_direct
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetItemDirect:

    ITEM = {
        "id": "test-item-001",
        "assets": {"nrgb_cog": {"href": "https://example.com/file.tif"}},
    }

    def test_item_gefunden(self):
        with patch("stac_api._session_get", return_value=_mock_response(200, self.ITEM)):
            result = get_item_direct(BASE, AUTH, "test-item-001")
        assert result == self.ITEM

    def test_item_nicht_gefunden_404(self):
        with patch("stac_api._session_get", return_value=_mock_response(404)):
            result = get_item_direct(BASE, AUTH, "existiert-nicht")
        assert result is None

    def test_item_id_wird_getrimmt(self):
        with patch("stac_api._session_get",
                   return_value=_mock_response(200, self.ITEM)) as mock_get:
            get_item_direct(BASE, AUTH, "  test-item-001  ")
        url = mock_get.call_args[0][0]
        assert "test-item-001" in url
        assert "  " not in url

    def test_url_enthaelt_collection_und_item(self):
        with patch("stac_api._session_get",
                   return_value=_mock_response(200, self.ITEM)) as mock_get:
            get_item_direct(BASE, AUTH, "item-abc")
        url = mock_get.call_args[0][0]
        assert f"collections/{COLLECTION_ID}/items/item-abc" in url


# ═══════════════════════════════════════════════════════════════════════════════
# get_collection_items
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetCollectionItems:

    def test_einzelne_seite(self):
        data = {"features": [{"id": "item-1"}, {"id": "item-2"}], "links": []}
        with patch("stac_api._session_get", return_value=_mock_response(200, data)):
            result = get_collection_items(BASE, AUTH)
        assert len(result) == 2

    def test_paginierung_zwei_seiten(self):
        page1 = {
            "features": [{"id": "item-1"}],
            "links": [{"rel": "next", "href": "https://example.com/page2"}],
        }
        page2 = {"features": [{"id": "item-2"}, {"id": "item-3"}], "links": []}

        responses = iter([_mock_response(200, page1), _mock_response(200, page2)])
        with patch("stac_api._session_get", side_effect=lambda *a, **kw: next(responses)):
            result = get_collection_items(BASE, AUTH)

        assert len(result) == 3
        assert result[0]["id"] == "item-1"
        assert result[2]["id"] == "item-3"

    def test_leere_collection(self):
        data = {"features": [], "links": []}
        with patch("stac_api._session_get", return_value=_mock_response(200, data)):
            result = get_collection_items(BASE, AUTH)
        assert result == []

    def test_log_fn_wird_bei_paginierung_aufgerufen(self):
        page1 = {
            "features": [{"id": "item-1"}],
            "links": [{"rel": "next", "href": "https://example.com/page2"}],
        }
        page2 = {"features": [{"id": "item-2"}], "links": []}
        log_calls = []
        responses = iter([_mock_response(200, page1), _mock_response(200, page2)])
        with patch("stac_api._session_get", side_effect=lambda *a, **kw: next(responses)):
            get_collection_items(BASE, AUTH, log_fn=lambda msg: log_calls.append(msg))
        assert len(log_calls) == 1
        assert "Paginierung" in log_calls[0]


# ═══════════════════════════════════════════════════════════════════════════════
# delete_asset
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeleteAsset:

    def test_success_200(self):
        with patch("stac_api._session_delete", return_value=_mock_response(200)):
            ok, code = delete_asset(BASE, AUTH, "item-001", "nrgb_cog")
        assert ok is True
        assert code == 200

    def test_success_204(self):
        with patch("stac_api._session_delete", return_value=_mock_response(204)):
            ok, code = delete_asset(BASE, AUTH, "item-001", "nrgb_cog")
        assert ok is True
        assert code == 204

    def test_fail_403(self):
        with patch("stac_api._session_delete", return_value=_mock_response(403)):
            ok, code = delete_asset(BASE, AUTH, "item-001", "nrgb_cog")
        assert ok is False
        assert code == 403

    def test_fail_404(self):
        with patch("stac_api._session_delete", return_value=_mock_response(404)):
            ok, code = delete_asset(BASE, AUTH, "item-001", "nrgb_cog")
        assert ok is False

    def test_url_korrekt_aufgebaut(self):
        with patch("stac_api._session_delete",
                   return_value=_mock_response(200)) as mock_del:
            delete_asset(BASE, AUTH, "item-abc", "my_asset_key")
        url = mock_del.call_args[0][0]
        assert f"collections/{COLLECTION_ID}/items/item-abc/assets/my_asset_key" in url


# ═══════════════════════════════════════════════════════════════════════════════
# delete_item
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeleteItem:

    def test_success_200(self):
        with patch("stac_api._session_delete", return_value=_mock_response(200)):
            ok, code = delete_item(BASE, AUTH, "item-001")
        assert ok is True
        assert code == 200

    def test_success_204(self):
        with patch("stac_api._session_delete", return_value=_mock_response(204)):
            ok, _ = delete_item(BASE, AUTH, "item-001")
        assert ok is True

    def test_fail_404(self):
        with patch("stac_api._session_delete", return_value=_mock_response(404)):
            ok, code = delete_item(BASE, AUTH, "item-999")
        assert ok is False
        assert code == 404

    def test_url_korrekt_aufgebaut(self):
        with patch("stac_api._session_delete",
                   return_value=_mock_response(200)) as mock_del:
            delete_item(BASE, AUTH, "item-xyz")
        url = mock_del.call_args[0][0]
        assert f"collections/{COLLECTION_ID}/items/item-xyz" in url
        assert "/assets/" not in url


# ═══════════════════════════════════════════════════════════════════════════════
# GDWH Hilfsfunktionen – gdwh_import_id
# ═══════════════════════════════════════════════════════════════════════════════

class TestGdwhImportId:

    def test_feld_uuid_hat_prioritaet(self):
        assert gdwh_import_id({"uuid": "abc-123", "id": "other"}) == "abc-123"

    def test_feld_uuid(self):
        assert gdwh_import_id({"uuid": "964dba08-12ee-4884-a4ec-958db29f0e4c"}) == \
               "964dba08-12ee-4884-a4ec-958db29f0e4c"

    def test_fallback_id(self):
        assert gdwh_import_id({"id": "pkg-001"}) == "pkg-001"

    def test_fallback_datapackageId(self):
        assert gdwh_import_id({"datapackageId": "pkg-002"}) == "pkg-002"

    def test_fallback_package_id(self):
        assert gdwh_import_id({"package_id": "pkg-003"}) == "pkg-003"

    def test_kein_feld_gibt_fragezeichen(self):
        assert gdwh_import_id({}) == "?"


# ═══════════════════════════════════════════════════════════════════════════════
# GDWH Hilfsfunktionen – gdwh_import_name
# ═══════════════════════════════════════════════════════════════════════════════

class TestGdwhImportName:

    def test_feld_name(self):
        assert gdwh_import_name({"name": "mein_paket"}) == "mein_paket"

    def test_fallback_datapackageName(self):
        assert gdwh_import_name({"datapackageName": "paket_xyz"}) == "paket_xyz"

    def test_fallback_description(self):
        assert gdwh_import_name({"description": "Beschreibung"}) == "Beschreibung"

    def test_fallback_uuid_gekuerzt(self):
        """Wenn kein Namensfeld: UUID wird auf 8 Zeichen + … gekürzt."""
        result = gdwh_import_name({"uuid": "abcdef12-0000-0000-0000-000000000000"})
        assert result == "abcdef12…"

    def test_fallback_id_gekuerzt(self):
        """Wenn weder Name noch uuid: id wird auf 8 Zeichen + … gekürzt."""
        result = gdwh_import_name({"id": "123456789abc"})
        assert result == "12345678…"

    def test_kein_feld_gibt_fragezeichen(self):
        """Leeres Dict: id-Fallback gibt '?', das kürzer als 8 ist → '?' bleibt."""
        assert gdwh_import_name({}) == "?"

    def test_prioritaet_name_vor_description(self):
        assert gdwh_import_name({"name": "A", "description": "B"}) == "A"


# ═══════════════════════════════════════════════════════════════════════════════
# GDWH Hilfsfunktionen – gdwh_import_date
# ═══════════════════════════════════════════════════════════════════════════════

class TestGdwhImportDate:

    def test_importDate_hat_prioritaet(self):
        result = gdwh_import_date({"importDate": "2024-08-20T10:30:00",
                                   "date": "2023-01-01T00:00:00"})
        assert result.startswith("2024-08-20")

    def test_iso_datum_mit_t(self):
        assert gdwh_import_date({"importDate": "2024-08-20T10:30:00Z"}) == "2024-08-20 10:30"

    def test_datum_wird_auf_16_zeichen_gekuerzt(self):
        result = gdwh_import_date({"importDate": "2024-08-20T10:30:45.123Z"})
        assert result == "2024-08-20 10:30"

    def test_fallback_date(self):
        assert gdwh_import_date({"date": "2024-09-01T08:00:00"}) == "2024-09-01 08:00"

    def test_fallback_created_at(self):
        assert gdwh_import_date({"created_at": "2024-01-15T12:00:00"}) == "2024-01-15 12:00"

    def test_kein_feld_gibt_strich(self):
        assert gdwh_import_date({}) == "–"


# ═══════════════════════════════════════════════════════════════════════════════
# GDWH Hilfsfunktionen – gdwh_import_status
# ═══════════════════════════════════════════════════════════════════════════════

class TestGdwhImportStatus:

    def test_feld_status(self):
        assert gdwh_import_status({"status": "completed"}) == "completed"

    def test_fallback_state(self):
        assert gdwh_import_status({"state": "running"}) == "running"

    def test_fallback_importStatus(self):
        assert gdwh_import_status({"importStatus": "failed"}) == "failed"

    def test_kein_feld_gibt_leerstring(self):
        assert gdwh_import_status({}) == ""


# ═══════════════════════════════════════════════════════════════════════════════
# GDWH Hilfsfunktionen – gdwh_import_footprint_bbox
# ═══════════════════════════════════════════════════════════════════════════════

class TestGdwhImportFootprintBbox:

    WKT = ("POLYGON ((2652172 1151242.5,2652172 1155998,"
           "2663100 1155998,2663100 1151242.5,2652172 1151242.5))")

    def test_zentroid_format_lv95(self):
        result = gdwh_import_footprint_bbox({"footprint": self.WKT})
        assert "LV95" in result
        assert "E" in result
        assert "N" in result

    def test_apostroph_als_tausendertrennzeichen(self):
        result = gdwh_import_footprint_bbox({"footprint": self.WKT})
        assert "'" in result

    def test_kein_footprint_gibt_leerstring(self):
        assert gdwh_import_footprint_bbox({}) == ""
        assert gdwh_import_footprint_bbox({"footprint": ""}) == ""

    def test_zentroid_plausibel(self):
        result = gdwh_import_footprint_bbox({"footprint": self.WKT})
        # Zentroid X ≈ 2'657'636, Y ≈ 1'153'620
        assert "2'657" in result
        assert "1'153" in result


# ═══════════════════════════════════════════════════════════════════════════════
# GDWH Hilfsfunktionen – gdwh_estimate_area
# ═══════════════════════════════════════════════════════════════════════════════

class TestGdwhEstimateArea:

    # Footprint nahe OBERAAR (X≈2657000, Y≈1160000)
    WKT_OBERAAR = ("POLYGON ((2654000 1158000,2654000 1162000,"
                   "2660000 1162000,2660000 1158000,2654000 1158000))")

    # Footprint nahe GORNER (X≈2621000, Y≈1094000)
    WKT_GORNER  = ("POLYGON ((2618000 1091000,2618000 1097000,"
                   "2624000 1097000,2624000 1091000,2618000 1091000))")

    def test_gibt_geschaetzt_suffix(self):
        result = gdwh_estimate_area({"footprint": self.WKT_OBERAAR})
        assert "(geschätzt)" in result

    def test_kein_footprint_gibt_leerstring(self):
        assert gdwh_estimate_area({}) == ""
        assert gdwh_estimate_area({"footprint": ""}) == ""

    def test_oberaar_wird_erkannt(self):
        result = gdwh_estimate_area({"footprint": self.WKT_OBERAAR})
        assert "OBERAAR" in result

    def test_gorner_wird_erkannt(self):
        result = gdwh_estimate_area({"footprint": self.WKT_GORNER})
        assert "GORNER" in result


# ═══════════════════════════════════════════════════════════════════════════════
# GDWH Bucket-Pfad
# ═══════════════════════════════════════════════════════════════════════════════

class TestGdwhBucketPath:

    def test_int_raster_sb_dsm(self):
        path = gdwh_bucket_path("INT", "SB_DSM")
        assert "BUCKET_INT" in path
        assert "RASTER" in path
        assert "SB_DSM" in path

    def test_prod_raster_sb_dop(self):
        path = gdwh_bucket_path("PROD", "SB_DOP")
        assert "BUCKET_INT" not in path
        assert "RASTER" in path
        assert "SB_DOP" in path

    def test_int_vector_sb_dsm_punktwolke(self):
        path = gdwh_bucket_path("INT", "SB_DSM_PUNKTWOLKE")
        assert "VECTOR" in path
        assert "SB_DSM_PUNKTWOLKE" in path

    def test_prod_vector_sb_dsm_punktwolke(self):
        path = gdwh_bucket_path("PROD", "SB_DSM_PUNKTWOLKE")
        assert "BUCKET_INT" not in path
        assert "VECTOR" in path


# ═══════════════════════════════════════════════════════════════════════════════
# Interne Hilfsfunktionen
# ═══════════════════════════════════════════════════════════════════════════════

class TestLv95Format:

    def test_apostroph_als_trennzeichen(self):
        assert _lv95(2657636) == "2'657'636"

    def test_kleine_zahl(self):
        assert _lv95(999) == "999"

    def test_millionen(self):
        assert _lv95(1153620) == "1'153'620"


class TestExtractYearFromFolder:

    def test_jahr_am_anfang(self):
        assert _extract_year_from_folder("2023_OBERAAR_DSM") == "2023"

    def test_jahr_mit_bindestrich(self):
        assert _extract_year_from_folder("2024-GORNER-DOP") == "2024"

    def test_kein_jahr(self):
        assert _extract_year_from_folder("OBERAAR_DSM") == ""

    def test_jahr_nicht_am_anfang_wird_ignoriert(self):
        assert _extract_year_from_folder("OBERAAR_2023_DSM") == ""


class TestAreaFromFolderName:

    def test_raster_dsm(self):
        assert _area_from_folder_name("2023_OBERAAR_DSM") == "OBERAAR"

    def test_raster_dop(self):
        assert _area_from_folder_name("2025_GUPPENFIRN_DOP") == "GUPPENFIRN"

    def test_vector_punktwolke(self):
        assert _area_from_folder_name("2023_OBERAAR_DSM_PointCloud") == "OBERAAR"

    def test_mehrteiliger_aoi(self):
        assert _area_from_folder_name("2024_MONT_ETOILE_DSM") == "MONT_ETOILE"

    def test_ohne_jahr(self):
        assert _area_from_folder_name("BIRCH_DSM") == "BIRCH"

    def test_gorner(self):
        assert _area_from_folder_name("2025_BIRCH_DSM") == "BIRCH"


class TestParseIsoDt:

    def test_mit_millisekunden(self):
        dt = _parse_iso_dt("2026-06-09T14:39:22.6049990Z")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 6
        assert dt.day == 9

    def test_ohne_millisekunden(self):
        dt = _parse_iso_dt("2024-08-20T10:30:00")
        assert dt is not None
        assert dt.year == 2024

    def test_nur_datum(self):
        dt = _parse_iso_dt("2023-01-15")
        assert dt is not None
        assert dt.year == 2023

    def test_ungueltig_gibt_none(self):
        assert _parse_iso_dt("kein-datum") is None


# ═══════════════════════════════════════════════════════════════════════════════
# gdwh_get_imports (gemockt)
# ═══════════════════════════════════════════════════════════════════════════════

class TestGdwhGetImports:

    GDS_KEY = "SB_DSM"

    def test_direkte_liste_als_antwort(self):
        data = [{"uuid": "pkg-1"}, {"uuid": "pkg-2"}]
        with patch("gdwh_api.requests.get", return_value=_mock_response(200, data)):
            result = gdwh_get_imports(GDWH_BASE, self.GDS_KEY)
        assert result == data

    def test_wrapper_objekt_items(self):
        data = {"items": [{"uuid": "pkg-1"}], "total": 1}
        with patch("gdwh_api.requests.get", return_value=_mock_response(200, data)):
            result = gdwh_get_imports(GDWH_BASE, self.GDS_KEY)
        assert result == [{"uuid": "pkg-1"}]

    def test_wrapper_objekt_imports(self):
        data = {"imports": [{"uuid": "pkg-1"}, {"uuid": "pkg-2"}]}
        with patch("gdwh_api.requests.get", return_value=_mock_response(200, data)):
            result = gdwh_get_imports(GDWH_BASE, self.GDS_KEY)
        assert len(result) == 2

    def test_wrapper_objekt_data(self):
        data = {"data": [{"uuid": "pkg-1"}]}
        with patch("gdwh_api.requests.get", return_value=_mock_response(200, data)):
            result = gdwh_get_imports(GDWH_BASE, self.GDS_KEY)
        assert result == [{"uuid": "pkg-1"}]

    def test_leere_liste(self):
        with patch("gdwh_api.requests.get", return_value=_mock_response(200, [])):
            result = gdwh_get_imports(GDWH_BASE, self.GDS_KEY)
        assert result == []

    def test_url_korrekt_aufgebaut(self):
        with patch("gdwh_api.requests.get",
                   return_value=_mock_response(200, [])) as mock_get:
            gdwh_get_imports(GDWH_BASE, self.GDS_KEY)
        url = mock_get.call_args[0][0]
        assert f"api/geodatasets/{self.GDS_KEY}/data/imports" in url

    def test_sspi_auth_wird_verwendet(self):
        """GET nutzt Windows SSPI (HttpNegotiateAuth), kein explizites Passwort."""
        from requests_negotiate_sspi import HttpNegotiateAuth
        with patch("gdwh_api.requests.get",
                   return_value=_mock_response(200, [])) as mock_get:
            gdwh_get_imports(GDWH_BASE, self.GDS_KEY)
        _, kwargs = mock_get.call_args
        assert isinstance(kwargs.get("auth"), HttpNegotiateAuth)

    def test_http_fehler_wird_weitergegeben(self):
        with patch("gdwh_api.requests.get",
                   return_value=_mock_response(500, raise_on_status=True)):
            with pytest.raises(req_module.HTTPError):
                gdwh_get_imports(GDWH_BASE, self.GDS_KEY)


# ═══════════════════════════════════════════════════════════════════════════════
# gdwh_delete_import (gemockt)
# ═══════════════════════════════════════════════════════════════════════════════

class TestGdwhDeleteImport:

    GDS_KEY = "SB_DSM"
    PKG_ID  = "964dba08-12ee-4884-a4ec-958db29f0e4c"

    def test_job_objekt_wird_zurueckgegeben(self):
        job = {"id": "job-001", "status": "running", "progress": 0}
        with patch("gdwh_api.requests.delete", return_value=_mock_response(200, job)):
            result = gdwh_delete_import(GDWH_BASE, self.GDS_KEY, self.PKG_ID)
        assert result == job

    def test_mit_email_parameter(self):
        with patch("gdwh_api.requests.delete",
                   return_value=_mock_response(200, {})) as mock_del:
            gdwh_delete_import(GDWH_BASE, self.GDS_KEY, self.PKG_ID,
                               email="lukas@example.com")
        _, kwargs = mock_del.call_args
        assert kwargs["params"] == {"email": "lukas@example.com"}

    def test_ohne_email_kein_params(self):
        with patch("gdwh_api.requests.delete",
                   return_value=_mock_response(200, {})) as mock_del:
            gdwh_delete_import(GDWH_BASE, self.GDS_KEY, self.PKG_ID)
        _, kwargs = mock_del.call_args
        assert kwargs["params"] is None

    def test_sspi_auth_wird_verwendet(self):
        """DELETE nutzt Windows SSPI (HttpNegotiateAuth)."""
        from requests_negotiate_sspi import HttpNegotiateAuth
        with patch("gdwh_api.requests.delete",
                   return_value=_mock_response(200, {})) as mock_del:
            gdwh_delete_import(GDWH_BASE, self.GDS_KEY, self.PKG_ID)
        _, kwargs = mock_del.call_args
        assert isinstance(kwargs.get("auth"), HttpNegotiateAuth)

    def test_url_korrekt_aufgebaut(self):
        with patch("gdwh_api.requests.delete",
                   return_value=_mock_response(200, {})) as mock_del:
            gdwh_delete_import(GDWH_BASE, self.GDS_KEY, self.PKG_ID)
        url = mock_del.call_args[0][0]
        assert f"api/geodatasets/{self.GDS_KEY}/data/imports/{self.PKG_ID}" in url

    def test_nicht_json_antwort_gibt_status_dict(self):
        r = _mock_response(200)
        r.json.side_effect = ValueError("no json")
        with patch("gdwh_api.requests.delete", return_value=r):
            result = gdwh_delete_import(GDWH_BASE, self.GDS_KEY, self.PKG_ID)
        assert result == {"status": "200"}

    def test_http_fehler_401_wird_weitergegeben(self):
        with patch("gdwh_api.requests.delete",
                   return_value=_mock_response(401, raise_on_status=True)):
            with pytest.raises(req_module.HTTPError):
                gdwh_delete_import(GDWH_BASE, self.GDS_KEY, self.PKG_ID)


# ═══════════════════════════════════════════════════════════════════════════════
# stac_item_year
# ═══════════════════════════════════════════════════════════════════════════════

class TestStacItemYear:

    def test_aus_properties_datetime(self):
        item = {"id": "x", "properties": {"datetime": "2024-08-20T10:27:00Z"}}
        assert stac_item_year(item) == "2024"

    def test_aus_item_id_fallback(self):
        item = {"id": "ch.swisstopo.spezialbefliegungen_kry_2023-08-15t09850000",
                "properties": {}}
        assert stac_item_year(item) == "2023"

    def test_properties_hat_prioritaet_vor_id(self):
        item = {"id": "kry_2020-01-01",
                "properties": {"datetime": "2024-08-20T00:00:00Z"}}
        assert stac_item_year(item) == "2024"

    def test_kein_datum_gibt_leerstring(self):
        assert stac_item_year({"id": "kein-datum", "properties": {}}) == ""

    def test_leeres_item(self):
        assert stac_item_year({}) == ""


# ═══════════════════════════════════════════════════════════════════════════════
# stac_item_area
# ═══════════════════════════════════════════════════════════════════════════════

class TestStacItemArea:

    def test_aus_properties_area(self):
        item = {"properties": {"area": "oberaar"}, "bbox": []}
        assert stac_item_area(item) == "OBERAAR"

    def test_aus_properties_aoi(self):
        item = {"properties": {"aoi": "gorner"}, "bbox": []}
        assert stac_item_area(item) == "GORNER"

    def test_properties_hat_prioritaet_vor_bbox(self):
        item = {
            "properties": {"area": "RHONE"},
            "bbox": [7.5, 45.8, 7.9, 46.2],
        }
        assert stac_item_area(item) == "RHONE"

    def test_bbox_nahe_oberaar(self):
        """WGS84-Schwerpunkt ~8.21°E / 46.59°N → nächste AOI: OBERAAR."""
        item = {"properties": {}, "bbox": [8.0, 46.4, 8.4, 46.8]}
        assert stac_item_area(item) == "OBERAAR"

    def test_bbox_nahe_gorner(self):
        """WGS84-Schwerpunkt ~7.72°E / 46.0°N → nächste AOI: GORNER."""
        item = {"properties": {}, "bbox": [7.5, 45.8, 7.9, 46.2]}
        assert stac_item_area(item) == "GORNER"

    def test_kein_bbox_kein_property_gibt_leerstring(self):
        assert stac_item_area({"properties": {}}) == ""

    def test_leeres_item(self):
        assert stac_item_area({}) == ""
