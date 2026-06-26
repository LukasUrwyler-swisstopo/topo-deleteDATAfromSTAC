# STAC Asset-Deleting-Tool

GUI-Tool zum gezielten Löschen von Assets aus der STAC-Collection  
**`ch.swisstopo.spezialbefliegungen`** (Kryosphäre / Rapidmapping).

Die **Items selbst bleiben erhalten** — es werden ausschliesslich einzelne Asset-Einträge gelöscht.  
Nach der Löschung kann ein korrektes Asset problemlos neu importiert werden.

---

## GUI

<img width="1377" height="1405" alt="grafik" src="https://github.com/user-attachments/assets/5ec79c0a-7a49-4155-8c06-64eb5c79c589" />


---

## Voraussetzungen

- Python 3.6+
- Paket: `requests` (tkinter ist in der Standardbibliothek enthalten)

```
pip install requests
```

Installation prüfen:
```bash
python -c "import requests; print(requests.__version__)"
```

---

## Ordnerstruktur

```
deleteDATA-STAC-del_assets/
├── 0_GUI_stac_delete_kry_assets.py   ← Hauptscript
├── secrets/
│   ├── stac_credentials.json         ← STAC-Zugangsdaten (nicht in Git!)
│   └── proxy_config.json             ← Proxy-Konfiguration (optional)
├── .gitignore                        ← secrets/ ist ausgeschlossen
└── README.md
```

### `secrets/stac_credentials.json`

```json
{
    "INT": {
        "username": "...",
        "password": "..."
    },
    "PROD": {
        "username": "...",
        "password": "..."
    }
}
```

---

## Starten

```bash
python 0_GUI_stac_delete_kry_assets.py
```

---

## Bedienung

### Schritt 1 — Umgebung & Credentials

- **INT** = Integrationsumgebung (`sys-data.int.bgdi.ch`) — zum Testen
- **PROD** = Produktionsumgebung (`data.geo.admin.ch`) — Live-Daten

`Credentials laden` liest die Zugangsdaten aus `secrets/stac_credentials.json`.  
Erst danach werden die Suchbuttons aktiviert.

---

### Schritt 2 — Auftragstyp, Item & Asset-Filter

#### Auftragstyp
Wähle den Typ, um den Item-ID-Filter vorzubelegen:

| Auftragstyp | Such-Vorschlag |
|---|---|
| KRY (Kryosphäre) | `kry` |
| RAM (Rapidmapping) | `ram` |
| Alle | *(leer)* |

Der Wert im Item-ID-Feld ist jederzeit überschreibbar.

#### Item-ID Suche

| Button | Verhalten |
|---|---|
| **Exakt abrufen (1 Item)** | Direkter API-Call mit der vollständigen Item-ID — sofort, 1 Request |
| **Alle suchen + filtern** | Lädt alle Items der Collection und filtert nach Teilstring — **langsam** bei 5000+ Items |

> **Teilstring-Beispiele:** `2024-08-20`, `kry-2024`, `t10270000`

#### Asset-Key Filter
Filtert Assets nach einem Teilstring im Key, z.B. `nrgb`, `16bit`, `thumbnail`.  
Leer lassen = alle Assets anzeigen.

#### Dateiendungs-Filter
Checkboxen für häufige Typen: `tif/tiff`, `copc.laz/laz`, `jpg/jpeg`, `png`, `json`.  
Zusätzlich Freitext für weitere Endungen (z.B. `gpkg pdf`).

> Filteränderungen wirken **sofort** auf die geladenen Daten — kein Neu-Abruf nötig.

---

### Schritt 3 — Assets auswählen

Nach dem Laden erscheinen alle gefilterten Assets als Checkboxen:

```
▸  kry-2024-08-20t10270000
─────────────────────────────────────────────────────────
☐  kry-2024-08-20t10270000-nrgb-16bit    .tif
☐  kry-2024-08-20t10270000-thumbnail     .jpg
```

**Standardmässig sind alle Assets abgewählt** — die Auswahl muss bewusst getroffen werden.

#### Auswahlsteuerung

| Button | Funktion |
|---|---|
| Alle auswählen | Alle sichtbaren Assets ankreuzen |
| Alle abwählen | Alle abwählen |
| **Assets prüfen (HEAD)** | HTTP-HEAD-Request je Asset → Statusanzeige |
| **Fehlerhafte auswählen** | Alle Assets mit Fehler-Status automatisch ankreuzen |

#### Asset-Prüfung (HEAD-Requests)

Prüft die Erreichbarkeit der Dateien direkt auf dem Server (6 parallele Requests).  
Der Status wird live neben dem Asset-Key angezeigt:

| Anzeige | Bedeutung |
|---|---|
| `⟳` | Wird gerade geprüft |
| `✓ 200` grün | Asset erreichbar und korrekt |
| `✗ 400` rot | **Korrupt / Bad Request** → Kandidat zum Löschen |
| `✗ 404` rot | Datei nicht vorhanden |
| `✗ timeout` orange | Netzwerk-Timeout |

Nach der Prüfung wird `Fehlerhafte auswählen` aktiviert — ein Klick markiert automatisch alle fehlerhaften Assets.

---

### Schritt 4 — Löschung ausführen

Der Lösch-Button zeigt immer die aktuelle Auswahl:

```
Asset Auswahl (3) löschen
```

Vor der Löschung erscheint ein **zweistufiger Sicherheitsdialog**:
1. Checkbox bestätigen: *"Ich verstehe, dass die Assets permanent gelöscht werden"*
2. Umgebungsname eintippen (`INT` oder `PROD`)

Das Log protokolliert jeden gelöschten Asset mit Status `[OK]` oder `[FAIL]`.

---

## Typischer Workflow: Korrupte Assets bereinigen

```
1.  Umgebung wählen (INT zum Testen, PROD für Live-Daten)
2.  Credentials laden
3.  Auftragstyp wählen (KRY / RAM)
4.  Item-ID oder Datum eingeben  →  "Alle suchen + filtern"
       Beispiel: "2024-08-20"
5.  [Assets prüfen (HEAD)]
       → fehlerhafte Assets werden rot markiert (✗ 400 / ✗ 404)
6.  [Fehlerhafte auswählen]
7.  [Asset Auswahl (n) löschen]  →  Sicherheitsdialog bestätigen
8.  Korrektes Asset über den normalen Importprozess neu eintragen
       → Item bleibt vollständig erhalten (Geometrie, Datum, alle anderen Assets)
```

---

## Hinweise

- **Items werden nie gelöscht** — nur einzelne Asset-Einträge werden entfernt.
- Nach dem Löschen kann das Asset erneut importiert werden. Alle Metadaten des Items (Geometrie, Zeitstempel, Properties, übrige Assets) bleiben unverändert.
- `secrets/` ist über `.gitignore` vom Git-Tracking ausgeschlossen — Credentials nie committen.
- Für Einsatz hinter einem Proxy: `PROXY_AVAILABLE = True` setzen und `secrets/proxy_config.json` anpassen.
