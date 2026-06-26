"""
0_GUI_stac_delete_kry_assets.py  –  STAC Asset-Deleting-Tool

Löscht Assets von Items aus der Collection "ch.swisstopo.spezialbefliegungen".
Unterstützt Auftragstypen KRY (Kryosphäre) und RAM (Rapidmapping).
Die Items selbst bleiben erhalten.

Filterung nach Asset-Key (Teilstring) und Dateiendung (lokal, ohne Neu-Abruf).

Autor: (basierend auf util_stac_delete_ram.py von David Oesch)
Datum: 2025-12
Lizenz: MIT
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import ctypes
import threading
import concurrent.futures
import requests
import json
import logging
from pathlib import Path
from urllib.parse import urljoin
from typing import Dict, List, Optional, Tuple
from datetime import datetime

# Credentials aus: <script-ordner>\secrets\stac_credentials.json
# Format: {"INT": {"username": "...", "password": "..."}, "PROD": {...}}

# ─── Konstanten ───────────────────────────────────────────────────────────────
PROXY_AVAILABLE = False

COLLECTION_ID = "ch.swisstopo.spezialbefliegungen"

ENVIRONMENTS = {
    "INT":  "https://sys-data.int.bgdi.ch/api/stac/v0.9/",
    "PROD": "https://data.geo.admin.ch/api/stac/v0.9/",
}

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

# ─── Farbpaletten ─────────────────────────────────────────────────────────────
LIGHT = {
    "root":      "#f0f0f0",
    "panel":     "#f5f5f5",
    "input":     "#ffffff",
    "fg":        "#1a1a1a",
    "fg_dim":    "#666666",
    "accent":    "#0063b1",
    "hdr_bg":    "#1a3a5c",
    "hdr_fg":    "#ffffff",
    "btn":       "#e1e1e1",
    "btn_hover": "#c8c8c8",
    "list":      "#ffffff",
    "log_bg":    "#1e1e1e",
    "log_fg":    "#d4d4d4",
    "sep":       "#c0c0c0",
    "sel_bg":    "#0078d4",
    "sel_fg":    "#ffffff",
    "ok":        "#2e7d32",
    "err":       "#c62828",
    "hint":      "#8a6f2e",
    "chk_item":  "#0063b1",
    "chk_bg":    "#ffffff",
    "chk_row":   "#f9f9f9",
}

DARK = {
    "root":      "#1e1e1e",
    "panel":     "#252526",
    "input":     "#3c3c3c",
    "fg":        "#cccccc",
    "fg_dim":    "#7a7a7a",
    "accent":    "#4fc3f7",
    "hdr_bg":    "#1a1a1a",
    "hdr_fg":    "#cccccc",
    "btn":       "#3c3c3c",
    "btn_hover": "#505050",
    "list":      "#2d2d30",
    "log_bg":    "#1e1e1e",
    "log_fg":    "#d4d4d4",
    "sep":       "#3c3c3c",
    "sel_bg":    "#094771",
    "sel_fg":    "#cccccc",
    "ok":        "#66bb6a",
    "err":       "#ef5350",
    "hint":      "#c9a84c",
    "chk_item":  "#4fc3f7",
    "chk_bg":    "#2d2d30",
    "chk_row":   "#303030",
}


# ─── STAC API Funktionen ──────────────────────────────────────────────────────

def _session_get(url: str, auth: Tuple, params: dict = None) -> requests.Response:
    if PROXY_AVAILABLE:
        return get_session().get(url, auth=auth, params=params, timeout=(30, 60))
    return requests.get(url, auth=auth, params=params, timeout=(30, 60))


def _session_delete(url: str, auth: Tuple) -> requests.Response:
    if PROXY_AVAILABLE:
        return get_session().delete(url, auth=auth, timeout=(30, 60))
    return requests.delete(url, auth=auth, timeout=(30, 60))


def get_item_direct(base_url: str, auth: Tuple, item_id: str) -> Optional[Dict]:
    """Holt ein einzelnes Item per exakter ID. Gibt None bei 404 zurück."""
    url = urljoin(base_url, f"collections/{COLLECTION_ID}/items/{item_id.strip()}")
    r = _session_get(url, auth)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def get_collection_items(base_url: str, auth: Tuple, log_fn=print) -> List[Dict]:
    """Holt alle Items der Collection mit Paginierung (langsam)."""
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


def delete_asset(base_url: str, auth: Tuple, item_id: str, asset_key: str) -> bool:
    """Löscht einen einzelnen Asset. Gibt True bei Erfolg zurück."""
    url = urljoin(base_url, f"collections/{COLLECTION_ID}/items/{item_id}/assets/{asset_key}")
    r   = _session_delete(url, auth)
    return r.status_code in (200, 204)


def check_asset_status(href: str, auth: Tuple) -> int:
    """HEAD-Request auf Asset-URL. Gibt HTTP-Statuscode zurück, negativ bei Netzwerkfehler."""
    if not href:
        return -1
    try:
        r = requests.head(href, timeout=(5, 15), allow_redirects=True)
        if r.status_code in (401, 403):
            r = requests.head(href, auth=auth, timeout=(5, 15), allow_redirects=True)
        return r.status_code
    except requests.exceptions.Timeout:
        return -2
    except Exception:
        return -3


# ─── Bestätigungs-Dialog ─────────────────────────────────────────────────────

class ConfirmDialog(tk.Toplevel):
    def __init__(self, parent, environment: str, item_count: int,
                 asset_count: int, dark: bool):
        super().__init__(parent)
        self.result       = False
        self._environment = environment

        T = DARK if dark else LIGHT
        self.title("Löschung bestätigen")
        self.resizable(False, False)
        self.configure(bg=T["root"])
        self.grab_set()
        self.focus_set()
        self._build(T, environment, item_count, asset_count)
        self.transient(parent)
        self.wait_window(self)

    def _build(self, T, env, items, assets):
        hdr = tk.Frame(self, bg=T["err"], pady=6)
        hdr.pack(fill="x")
        tk.Label(hdr, text="  WARNUNG – DIESE AKTION IST NICHT UMKEHRBAR  ",
                 bg=T["err"], fg="#ffffff", font=("Segoe UI", 11, "bold")).pack()

        body = tk.Frame(self, bg=T["root"], padx=20, pady=10)
        body.pack(fill="both")

        info = (f"Umgebung:             {env}\n"
                f"Collection:           {COLLECTION_ID}\n"
                f"Betroffene Items:     {items}\n"
                f"Assets zum Löschen:  {assets}\n\n"
                "Die Items selbst bleiben erhalten.\n"
                "Nur die Assets werden permanent gelöscht.")
        tk.Label(body, text=info, bg=T["root"], fg=T["fg"],
                 font=("Segoe UI", 10), justify="left").pack(anchor="w", pady=(6, 10))

        tk.Frame(body, bg=T["sep"], height=1).pack(fill="x", pady=6)

        self._check_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            body,
            text="Ich verstehe, dass die Assets permanent und unwiderruflich gelöscht werden.",
            variable=self._check_var, command=self._update_state,
            bg=T["root"], fg=T["fg"], selectcolor=T["input"],
            activebackground=T["root"], activeforeground=T["fg"],
            font=("Segoe UI", 9),
        ).pack(anchor="w", padx=4)

        tk.Label(body, text=f'\nZur Bestätigung den Umgebungsnamen tippen ("{env}"):',
                 bg=T["root"], fg=T["hint"],
                 font=("Segoe UI", 9, "italic")).pack(anchor="w", padx=4)

        self._env_var   = tk.StringVar()
        self._env_entry = tk.Entry(
            body, textvariable=self._env_var, width=16, state="disabled",
            bg=T["input"], fg=T["fg"], insertbackground=T["fg"],
            disabledbackground=T["sep"], disabledforeground=T["fg_dim"],
            font=("Segoe UI", 10),
        )
        self._env_entry.pack(anchor="w", padx=4, pady=(3, 12))
        self._env_var.trace_add("write", lambda *_: self._update_state())

        btn_row = tk.Frame(body, bg=T["root"])
        btn_row.pack(fill="x", pady=(4, 6))
        tk.Button(btn_row, text="Abbrechen",
                  bg=T["btn"], fg=T["fg"], activebackground=T["btn_hover"],
                  activeforeground=T["fg"], font=("Segoe UI", 10), relief="flat",
                  padx=14, pady=6, command=self.destroy).pack(side="right", padx=(8, 0))
        self._ok_btn = tk.Button(
            btn_row, text="JETZT LÖSCHEN",
            bg=T["err"], fg="#ffffff", activebackground="#b71c1c",
            activeforeground="#ffffff", font=("Segoe UI", 10, "bold"),
            relief="flat", padx=14, pady=6, state="disabled", command=self._confirm,
        )
        self._ok_btn.pack(side="right")

    def _update_state(self):
        checked = self._check_var.get()
        self._env_entry.config(state="normal" if checked else "disabled")
        env_ok = self._env_var.get().strip().upper() == self._environment.upper()
        self._ok_btn.config(state="normal" if (checked and env_ok) else "disabled")

    def _confirm(self):
        self.result = True
        self.destroy()


# ─── Haupt-GUI ────────────────────────────────────────────────────────────────

class KryDeleteApp(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("STAC Asset-Lösch-Tool  —  KRY / RAM")
        self.minsize(920, 720)

        self._dark: bool = True
        self._auth: Optional[Tuple] = None
        self._base_url: str = ""

        self._items_preview: List[Dict] = []
        self._items_asset_hrefs: Dict[str, Dict[str, str]] = {}
        self._items_assets:      Dict[str, List[str]] = {}
        # Wert ist jetzt BooleanVar (echte Checkbox-Variable)
        self._asset_selection:     Dict[str, Dict[str, tk.BooleanVar]] = {}
        self._asset_status_labels: Dict[str, Dict[str, tk.Label]]      = {}

        self._build_ui()
        self._apply_theme(True)

    # ── UI aufbauen ───────────────────────────────────────────────────────────

    def _build_ui(self):
        self._hdr = tk.Frame(self, height=52)
        self._hdr.pack(fill="x")
        self._hdr.pack_propagate(False)
        self._hdr_lbl = tk.Label(
            self._hdr,
            text="  STAC Asset-Lösch-Tool  —  ch.swisstopo.spezialbefliegungen",
            font=("Segoe UI", 13, "bold"),
        )
        self._hdr_lbl.pack(side="left", padx=16, pady=10)
        self._theme_btn = tk.Button(
            self._hdr, text="Hell", relief="flat", borderwidth=0,
            font=("Segoe UI", 9), cursor="hand2", padx=10, pady=4,
            command=self._toggle_theme,
        )
        self._theme_btn.pack(side="right", padx=12)

        outer = ttk.Frame(self)
        outer.pack(fill="both", expand=True, padx=12, pady=8)

        self._canvas = tk.Canvas(outer, highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        self._sf   = ttk.Frame(self._canvas)
        win_id     = self._canvas.create_window((0, 0), window=self._sf, anchor="nw")
        self._sf.bind("<Configure>",
                      lambda _: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>",
                          lambda e: self._canvas.itemconfig(win_id, width=e.width))
        # Mausrad scrollt äusseren Canvas (wird bei Hover über Checkbox-Canvas überschrieben)
        self._canvas.bind_all("<MouseWheel>",
                              lambda e: self._canvas.yview_scroll(-1*(e.delta//120), "units"))

        self._build_step1(self._sf)
        self._build_step2(self._sf)
        self._build_step3(self._sf)
        self._build_step4(self._sf)

    def _build_step1(self, parent):
        sec = ttk.LabelFrame(parent, text="1   Umgebung & Credentials",
                             padding=10, style="Section.TLabelframe")
        sec.pack(fill="x", pady=(0, 6))
        sec.columnconfigure(4, weight=1)

        ttk.Label(sec, text="Umgebung:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._env_var = tk.StringVar(value="INT")
        for col, env in enumerate(("INT", "PROD"), 1):
            ttk.Radiobutton(sec, text=env, variable=self._env_var, value=env,
                            command=self._on_env_change).grid(
                row=0, column=col, sticky="w", padx=4)

        self._url_lbl = ttk.Label(sec, text=ENVIRONMENTS["INT"],
                                   font=("Segoe UI", 8), style="Dim.TLabel")
        self._url_lbl.grid(row=0, column=3, sticky="w", padx=12)

        self._cred_btn = ttk.Button(sec, text="Credentials laden",
                                     command=self._load_credentials)
        self._cred_btn.grid(row=0, column=5, padx=(12, 0))

        self._cred_status = ttk.Label(sec, text="nicht geladen",
                                       font=("Segoe UI", 9, "italic"), style="Dim.TLabel")
        self._cred_status.grid(row=0, column=6, padx=8)

    def _build_step2(self, parent):
        sec = ttk.LabelFrame(parent, text="2   Auftragstyp, Item & Asset-Filter",
                             padding=10, style="Section.TLabelframe")
        sec.pack(fill="x", pady=(0, 6))
        sec.columnconfigure(1, weight=1)

        # Zeile 0: Auftragstyp
        ttk.Label(sec, text="Auftragstyp:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._auftragstyp_var = tk.StringVar(value=list(AUFTRAGSTYPEN.keys())[0])
        typ_frame = ttk.Frame(sec)
        typ_frame.grid(row=0, column=1, columnspan=3, sticky="w")
        for typ in AUFTRAGSTYPEN:
            ttk.Radiobutton(typ_frame, text=typ, variable=self._auftragstyp_var, value=typ,
                            command=self._on_auftragstyp_change).pack(side="left", padx=(0, 14))

        # Zeile 1: Item-ID Suche
        ttk.Label(sec, text="Item-ID:").grid(row=1, column=0, sticky="w",
                                              padx=(0, 8), pady=(6, 0))
        self._item_id_var = tk.StringVar(value=list(AUFTRAGSTYPEN.values())[0])
        ttk.Entry(sec, textvariable=self._item_id_var, width=46).grid(
            row=1, column=1, sticky="ew", padx=(0, 10), pady=(6, 0))

        self._fetch_direct_btn = ttk.Button(
            sec, text="Exakt abrufen (1 Item)",
            command=self._fetch_direct, state="disabled",
        )
        self._fetch_direct_btn.grid(row=1, column=2, padx=(0, 4), pady=(6, 0))

        self._fetch_all_btn = ttk.Button(
            sec, text="Alle suchen + filtern",
            command=self._fetch_all, state="disabled",
        )
        self._fetch_all_btn.grid(row=1, column=3, pady=(6, 0))

        # Hinweis-Zeile zur Suche
        ttk.Label(
            sec,
            text='„Exakt" = vollständige Item-ID nötig  ·  '
                 '„Alle suchen" = Teilstring genügt, z.B. "2024-08-20" oder "kry" (langsam)',
            font=("Segoe UI", 8, "italic"), style="Dim.TLabel",
        ).grid(row=2, column=1, columnspan=3, sticky="w", pady=(2, 0))

        # Zeile 3: Asset-Key Filter
        ttk.Label(sec, text="Asset-Key:").grid(row=3, column=0, sticky="w",
                                                padx=(0, 8), pady=(6, 0))
        self._asset_filter_var = tk.StringVar()
        self._asset_filter_var.trace_add("write", lambda *_: self._apply_filters())
        ttk.Entry(sec, textvariable=self._asset_filter_var, width=30).grid(
            row=3, column=1, sticky="w", padx=(0, 10), pady=(6, 0))
        ttk.Label(
            sec, text='Teilstring, z.B. "nrgb" oder "16bit"  —  Leer = alle Assets',
            font=("Segoe UI", 8, "italic"), style="Dim.TLabel",
        ).grid(row=3, column=2, columnspan=2, sticky="w", pady=(6, 0))

        # Zeile 4: Dateiendungs-Filter
        ttk.Label(sec, text="Dateiendung:").grid(row=4, column=0, sticky="w",
                                                  padx=(0, 8), pady=(6, 0))
        ext_frame = ttk.Frame(sec)
        ext_frame.grid(row=4, column=1, columnspan=3, sticky="w", pady=(6, 0))

        self._ext_vars: List[Tuple[tk.BooleanVar, List[str]]] = []
        for label, exts in EXT_PRESETS:
            var = tk.BooleanVar(value=False)
            var.trace_add("write", lambda *_: self._apply_filters())
            self._ext_vars.append((var, exts))
            ttk.Checkbutton(ext_frame, text=label, variable=var).pack(
                side="left", padx=(0, 10))

        ttk.Label(ext_frame, text="Frei:").pack(side="left", padx=(6, 4))
        self._ext_custom_var = tk.StringVar()
        self._ext_custom_var.trace_add("write", lambda *_: self._apply_filters())
        ttk.Entry(ext_frame, textvariable=self._ext_custom_var, width=16).pack(side="left")
        ttk.Label(ext_frame, text="z.B. gpkg pdf",
                  font=("Segoe UI", 8, "italic"), style="Dim.TLabel").pack(
                      side="left", padx=(4, 0))

    def _build_step3(self, parent):
        sec = ttk.LabelFrame(parent, text="3   Assets auswählen zum Löschen",
                             padding=10, style="Section.TLabelframe")
        sec.pack(fill="x", pady=(0, 6))
        sec.columnconfigure(0, weight=1)

        # Auswahlsteuerung
        sel_row = ttk.Frame(sec)
        sel_row.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        self._sel_all_btn = ttk.Button(
            sel_row, text="Alle auswählen",
            command=self._select_all_assets, state="disabled",
        )
        self._sel_all_btn.pack(side="left", padx=(0, 4))

        self._sel_none_btn = ttk.Button(
            sel_row, text="Alle abwählen",
            command=self._deselect_all_assets, state="disabled",
        )
        self._sel_none_btn.pack(side="left", padx=(0, 16))

        ttk.Separator(sel_row, orient="vertical").pack(side="left", fill="y", padx=(0, 16))

        self._check_btn = ttk.Button(
            sel_row, text="Assets prüfen (HEAD)",
            command=self._check_assets, state="disabled",
        )
        self._check_btn.pack(side="left", padx=(0, 4))

        self._sel_faulty_btn = ttk.Button(
            sel_row, text="Fehlerhafte auswählen",
            command=self._select_faulty_assets, state="disabled",
        )
        self._sel_faulty_btn.pack(side="left")

        # Scrollbarer Checkbox-Bereich
        chk_outer = tk.Frame(sec, bd=1, relief="sunken")
        chk_outer.grid(row=1, column=0, sticky="nsew", pady=(0, 4))
        sec.rowconfigure(1, weight=1)

        self._chk_canvas = tk.Canvas(chk_outer, height=220, highlightthickness=0)
        vsb = ttk.Scrollbar(chk_outer, orient="vertical", command=self._chk_canvas.yview)
        self._chk_canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._chk_canvas.pack(side="left", fill="both", expand=True)

        self._chk_frame = tk.Frame(self._chk_canvas)
        chk_win = self._chk_canvas.create_window((0, 0), window=self._chk_frame, anchor="nw")
        self._chk_frame.bind(
            "<Configure>",
            lambda _: self._chk_canvas.configure(
                scrollregion=self._chk_canvas.bbox("all")))
        self._chk_canvas.bind(
            "<Configure>",
            lambda e: self._chk_canvas.itemconfig(chk_win, width=e.width))

        # Mausrad-Fokus: scrollt inneren Canvas beim Hovern
        self._chk_canvas.bind("<Enter>", lambda _: self._chk_canvas.bind_all(
            "<MouseWheel>",
            lambda e: self._chk_canvas.yview_scroll(-1*(e.delta//120), "units")))
        self._chk_canvas.bind("<Leave>", lambda _: self._canvas.bind_all(
            "<MouseWheel>",
            lambda e: self._canvas.yview_scroll(-1*(e.delta//120), "units")))

        # Statuszeile
        self._preview_lbl = ttk.Label(
            sec, text="Noch keine Vorschau geladen.",
            font=("Segoe UI", 9, "italic"), style="Dim.TLabel",
        )
        self._preview_lbl.grid(row=2, column=0, sticky="w", pady=(2, 0))

    def _build_step4(self, parent):
        sec = ttk.LabelFrame(parent, text="4   Löschung ausführen",
                             padding=10, style="Section.TLabelframe")
        sec.pack(fill="both", expand=True, pady=(0, 4))
        sec.columnconfigure(0, weight=1)
        sec.rowconfigure(0, weight=1)

        self._log = scrolledtext.ScrolledText(
            sec, height=10, state="disabled",
            font=("Cascadia Mono", 9), wrap="word",
        )
        self._log.grid(row=0, column=0, sticky="nsew", pady=(0, 8))

        btn_row = ttk.Frame(sec)
        btn_row.grid(row=1, column=0, sticky="ew")

        self._del_btn = tk.Button(
            btn_row, text="Ausgewählte Assets löschen …",
            relief="flat", padx=14, pady=6,
            font=("Segoe UI", 10, "bold"), state="disabled",
            command=self._start_deletion,
        )
        self._del_btn.pack(side="left")

        self._progress = ttk.Progressbar(btn_row, mode="determinate", length=280)
        self._progress.pack(side="left", padx=16)

        self._status_lbl = ttk.Label(btn_row, text="", font=("Segoe UI", 9))
        self._status_lbl.pack(side="left")

    # ── Theme ─────────────────────────────────────────────────────────────────

    def _toggle_theme(self):
        self._apply_theme(not self._dark)

    def _apply_theme(self, dark: bool):
        self._dark = dark
        T = DARK if dark else LIGHT

        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".",
            background=T["panel"], foreground=T["fg"],
            fieldbackground=T["input"],
            selectbackground=T["sel_bg"], selectforeground=T["sel_fg"],
            bordercolor=T["sep"], lightcolor=T["panel"], darkcolor=T["sep"],
            insertcolor=T["fg"], troughcolor=T["root"],
        )
        s.configure("TFrame",     background=T["panel"])
        s.configure("TLabelframe",
            background=T["panel"], bordercolor=T["sep"])
        s.configure("TLabelframe.Label",
            background=T["panel"], foreground=T["fg"],
            font=("Segoe UI", 9, "bold"))
        s.configure("Section.TLabelframe",
            background=T["panel"], bordercolor=T["sep"])
        s.configure("Section.TLabelframe.Label",
            background=T["panel"], foreground=T["accent"],
            font=("Segoe UI", 10, "bold"))
        s.configure("TLabel",     background=T["panel"], foreground=T["fg"])
        s.configure("Dim.TLabel", background=T["panel"], foreground=T["fg_dim"])
        s.configure("TButton",
            background=T["btn"], foreground=T["fg"],
            bordercolor=T["sep"], relief="flat",
            padding=(8, 4), focuscolor=T["panel"],
        )
        s.map("TButton",
            background=[("active", T["btn_hover"]), ("pressed", T["sep"])],
            foreground=[("active", T["fg"])],
            relief=[("pressed", "flat")],
        )
        s.configure("TRadiobutton",
            background=T["panel"], foreground=T["fg"], focuscolor=T["panel"])
        s.map("TRadiobutton",
            background=[("active", T["panel"])], foreground=[("active", T["fg"])])
        s.configure("TCheckbutton",
            background=T["panel"], foreground=T["fg"], focuscolor=T["panel"])
        s.map("TCheckbutton",
            background=[("active", T["panel"])], foreground=[("active", T["fg"])])
        s.configure("TEntry",
            fieldbackground=T["input"], foreground=T["fg"],
            bordercolor=T["sep"], insertcolor=T["fg"],
            selectbackground=T["sel_bg"], selectforeground=T["sel_fg"],
        )
        s.configure("Vertical.TScrollbar",
            background=T["btn"], troughcolor=T["root"],
            bordercolor=T["sep"], arrowcolor=T["fg"],
        )
        s.configure("TSeparator", background=T["sep"])
        s.configure("TProgressbar",
            background=T["accent"], troughcolor=T["root"],
            bordercolor=T["sep"],
        )

        self.configure(bg=T["root"])
        self._canvas.configure(bg=T["panel"], highlightbackground=T["sep"])
        self._hdr.configure(bg=T["hdr_bg"])
        self._hdr_lbl.configure(bg=T["hdr_bg"], fg=T["hdr_fg"])
        self._theme_btn.configure(
            bg=T["hdr_bg"], fg=T["hdr_fg"],
            activebackground=T["btn"], activeforeground=T["fg"],
            text="Hell" if dark else "Dark",
        )
        self._log.configure(bg=T["log_bg"], fg=T["log_fg"],
                             insertbackground=T["log_fg"])

        # Checkbox-Bereich (tk-Widgets, nicht ttk)
        self._chk_canvas.configure(bg=T["chk_bg"])
        self._chk_frame.configure(bg=T["chk_bg"])
        self._recolor_chk_widgets(T)

        if str(self._del_btn["state"]) == "disabled":
            self._del_btn.configure(
                bg=T["btn"], fg=T["fg_dim"],
                activebackground=T["btn_hover"], activeforeground=T["fg"],
            )
        else:
            self._del_btn.configure(
                bg=T["err"], fg="#ffffff",
                activebackground="#b71c1c" if dark else "#c62828",
                activeforeground="#ffffff",
            )

        self._set_titlebar_dark(dark)

    def _recolor_chk_widgets(self, T: dict):
        """Färbt alle tk-Widgets im Checkbox-Frame neu (für Theme-Wechsel)."""
        def recolor(widget):
            cls = widget.winfo_class()
            if cls == "Frame":
                widget.configure(bg=T["chk_bg"])
            elif cls == "Label":
                if getattr(widget, "_is_item_header", False):
                    widget.configure(bg=T["chk_bg"], fg=T["chk_item"])
                elif getattr(widget, "_is_status", False):
                    # Statusfarbe (ok/err/hint/fg_dim) erhalten, nur Hintergrund neu
                    ck = getattr(widget, "_status_color_key", "fg_dim")
                    widget.configure(bg=T["chk_bg"], fg=T[ck])
                else:
                    widget.configure(bg=T["chk_bg"], fg=T["fg_dim"])
            elif cls == "Checkbutton":
                widget.configure(
                    bg=T["chk_bg"], fg=T["fg"],
                    selectcolor=T["input"],
                    activebackground=T["chk_bg"], activeforeground=T["fg"],
                )
            for child in widget.winfo_children():
                recolor(child)
        recolor(self._chk_frame)

    def _set_titlebar_dark(self, dark: bool):
        if not self.winfo_ismapped():
            self.after(50, lambda: self._set_titlebar_dark(dark))
            return
        try:
            hwnd  = int(self.wm_frame(), 16)
            value = ctypes.c_int(1 if dark else 0)
            for attr in (20, 19):
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                    break
            ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0027)
        except Exception:
            pass

    # ── Event Handler ─────────────────────────────────────────────────────────

    def _on_env_change(self):
        env = self._env_var.get()
        self._url_lbl.configure(text=ENVIRONMENTS[env])
        self._auth = None
        self._cred_status.configure(text="nicht geladen")
        self._fetch_direct_btn.config(state="disabled")
        self._fetch_all_btn.config(state="disabled")
        self._del_btn.config(state="disabled")
        self._apply_theme(self._dark)

    def _on_auftragstyp_change(self):
        typ     = self._auftragstyp_var.get()
        suggest = AUFTRAGSTYPEN[typ]
        known   = set(AUFTRAGSTYPEN.values())
        if not self._item_id_var.get().strip() or self._item_id_var.get() in known:
            self._item_id_var.set(suggest)

    def _load_credentials(self):
        env = self._env_var.get()
        try:
            if PROXY_AVAILABLE:
                initialize_proxy()
                username, password, _ = load_stac_credentials(environment=env)
            else:
                cfg_path = Path(__file__).parent / "secrets" / "stac_credentials.json"
                with open(cfg_path, encoding="utf-8") as f:
                    cfg = json.load(f)
                env_cfg  = cfg.get(env, {})
                username = env_cfg["username"]
                password = env_cfg["password"]

            self._auth     = (username, password)
            self._base_url = ENVIRONMENTS[env]

            T = DARK if self._dark else LIGHT
            self._cred_status.configure(text=f"Geladen: {username}", foreground=T["ok"])
            self._fetch_direct_btn.config(state="normal")
            self._fetch_all_btn.config(state="normal")
            self._log_write(f"[Credentials] {env} – Benutzer: {username}\n")

        except Exception as exc:
            T = DARK if self._dark else LIGHT
            self._cred_status.configure(text="Fehler!", foreground=T["err"])
            messagebox.showerror("Credentials-Fehler", str(exc))

    def _fetch_direct(self):
        item_id = self._item_id_var.get().strip()
        if not item_id:
            messagebox.showwarning("Eingabe fehlt", "Bitte eine Item-ID eingeben.")
            return
        self._disable_search_btns()
        self._del_btn.config(state="disabled")
        self._apply_theme(self._dark)
        self._clear_chk_frame()
        self._preview_lbl.configure(text="Abruf läuft …")
        self._clear_state()
        threading.Thread(target=self._fetch_direct_worker, args=(item_id,), daemon=True).start()

    def _fetch_all(self):
        search_term = self._item_id_var.get().strip()
        if not search_term:
            if not messagebox.askyesno(
                    "Alle Items laden?",
                    "Kein Filter eingegeben.\nAlle Items der Collection laden?\n\n"
                    "(Kann bei 5000+ Items mehrere Minuten dauern.)"):
                return
        self._disable_search_btns()
        self._del_btn.config(state="disabled")
        self._apply_theme(self._dark)
        self._clear_chk_frame()
        self._preview_lbl.configure(text="Lade Items …")
        self._clear_state()
        threading.Thread(target=self._fetch_all_worker, args=(search_term,), daemon=True).start()

    # ── Worker-Threads ────────────────────────────────────────────────────────

    def _fetch_direct_worker(self, item_id: str):
        try:
            self._log_write(f"[Abruf] Item direkt: {item_id} …\n")
            item = get_item_direct(self._base_url, self._auth, item_id)
            if item is None:
                self._log_write(f"[Info] Nicht gefunden: {item_id}\n")
                self.after(0, lambda: self._preview_lbl.configure(
                    text=f"Item nicht gefunden: {item_id}"))
                self.after(0, self._enable_search_btns)
                return

            hrefs = {k: v.get("href", "") for k, v in item.get("assets", {}).items()}
            self._log_write(f"[OK] {item['id']}: {len(hrefs)} Asset(s) total\n")
            self._items_preview     = [item]
            self._items_asset_hrefs = {item["id"]: hrefs}
            self.after(0, self._apply_filters)

        except Exception as exc:
            self._log_write(f"[FEHLER] {exc}\n")
            self.after(0, lambda: messagebox.showerror("Fehler", str(exc)))
            self.after(0, self._enable_search_btns)

    def _fetch_all_worker(self, search_term: str):
        try:
            self._log_write("[Abruf] Hole alle Items der Collection …\n")
            all_items = get_collection_items(self._base_url, self._auth, self._log_write)
            self._log_write(f"[Abruf] {len(all_items)} Items total.\n")

            filtered = filter_items(all_items, search_term)
            self._log_write(f"[Filter ID] '{search_term}': {len(filtered)} Items.\n")

            if not filtered:
                self._log_write("[Debug] Erste 10 IDs:\n")
                for item in all_items[:10]:
                    self._log_write(f"  {item.get('id', '?')}\n")
                self.after(0, lambda: self._preview_lbl.configure(text="Keine Items gefunden."))
                self.after(0, self._enable_search_btns)
                return

            hrefs: Dict[str, Dict[str, str]] = {}
            for i, item in enumerate(filtered, 1):
                iid    = item["id"]
                assets = item.get("assets", {})
                if not assets:
                    full   = get_item_direct(self._base_url, self._auth, iid)
                    assets = full.get("assets", {}) if full else {}
                hrefs[iid] = {k: v.get("href", "") for k, v in assets.items()}
                self._log_write(
                    f"  [{i}/{len(filtered)}] {iid}: {len(hrefs[iid])} Asset(s)\n")

            self._items_preview     = filtered
            self._items_asset_hrefs = hrefs
            self.after(0, self._apply_filters)

        except Exception as exc:
            self._log_write(f"[FEHLER] {exc}\n")
            self.after(0, lambda: messagebox.showerror("Fehler", str(exc)))
            self.after(0, self._enable_search_btns)

    # ── Filterung ─────────────────────────────────────────────────────────────

    def _get_active_extensions(self) -> List[str]:
        result = []
        for var, exts in self._ext_vars:
            if var.get():
                result.extend(exts)
        for part in self._ext_custom_var.get().replace(",", " ").split():
            result.append(part if part.startswith(".") else f".{part}")
        return result

    def _apply_filters(self):
        if not self._items_asset_hrefs:
            return
        key_filter = self._asset_filter_var.get().strip().lower()
        extensions = self._get_active_extensions()

        assets_map: Dict[str, List[str]] = {}
        for iid, key_href in self._items_asset_hrefs.items():
            keys = []
            for k, href in key_href.items():
                if key_filter and key_filter not in k.lower():
                    continue
                if extensions:
                    href_l, k_l = href.lower(), k.lower()
                    if not any(href_l.endswith(e) or k_l.endswith(e) for e in extensions):
                        continue
                keys.append(k)
            assets_map[iid] = keys

        self._items_assets = assets_map
        self._populate_checkboxes(self._items_preview, assets_map)

    # ── Checkbox-Bereich ──────────────────────────────────────────────────────

    def _clear_chk_frame(self):
        for w in self._chk_frame.winfo_children():
            w.destroy()

    def _populate_checkboxes(self, items: List[Dict], assets_map: Dict[str, List[str]]):
        self._clear_chk_frame()
        self._asset_selection.clear()
        self._asset_status_labels.clear()

        T = DARK if self._dark else LIGHT
        any_visible = False

        for item in items:
            iid        = item["id"]
            asset_keys = assets_map.get(iid, [])
            if not asset_keys:
                continue
            any_visible = True
            self._asset_selection[iid]      = {}
            self._asset_status_labels[iid]  = {}

            # Item-Header
            hdr = tk.Label(
                self._chk_frame,
                text=f"▸  {iid}",
                font=("Segoe UI", 9, "bold"),
                bg=T["chk_bg"], fg=T["chk_item"],
                anchor="w", padx=6,
            )
            hdr._is_item_header = True
            hdr.pack(fill="x", pady=(8, 2))

            # Trennlinie
            tk.Frame(self._chk_frame, bg=T["sep"], height=1).pack(fill="x", padx=6)

            # Checkboxen je Asset
            for ak in asset_keys:
                href    = self._items_asset_hrefs.get(iid, {}).get(ak, "")
                suffix  = Path(href).suffix if href else ""
                var     = tk.BooleanVar(value=False)
                var.trace_add("write", lambda *_: self._on_checkbox_change())
                self._asset_selection[iid][ak] = var

                row = tk.Frame(self._chk_frame, bg=T["chk_bg"])
                row.pack(fill="x", padx=6, pady=1)

                tk.Checkbutton(
                    row, variable=var,
                    bg=T["chk_bg"], fg=T["fg"],
                    selectcolor=T["input"],
                    activebackground=T["chk_bg"], activeforeground=T["fg"],
                ).pack(side="left")

                # Asset-Key in Monospace
                tk.Label(
                    row, text=ak,
                    font=("Cascadia Mono", 9),
                    bg=T["chk_bg"], fg=T["fg"],
                    anchor="w",
                ).pack(side="left")

                # Dateiendung gedimmt
                if suffix:
                    tk.Label(
                        row, text=f"  {suffix}",
                        font=("Cascadia Mono", 9),
                        bg=T["chk_bg"], fg=T["fg_dim"],
                        anchor="w",
                    ).pack(side="left")

                # Status-Label (wird durch HEAD-Prüfung befüllt)
                status_lbl = tk.Label(
                    row, text="",
                    font=("Cascadia Mono", 9),
                    bg=T["chk_bg"], fg=T["fg_dim"],
                    anchor="w", width=8,
                )
                status_lbl._is_status       = True
                status_lbl._status_color_key = "fg_dim"
                status_lbl.pack(side="left", padx=(10, 0))
                self._asset_status_labels[iid][ak] = status_lbl

        if not any_visible:
            tk.Label(
                self._chk_frame,
                text="Keine Assets nach aktuellem Filter.",
                font=("Segoe UI", 9, "italic"),
                bg=T["chk_bg"], fg=T["fg_dim"],
                padx=8, pady=8,
            ).pack(anchor="w")

        self._enable_search_btns()
        st = "normal" if any_visible else "disabled"
        self._sel_all_btn.config(state=st)
        self._sel_none_btn.config(state=st)
        self._check_btn.config(state=st)
        self._sel_faulty_btn.config(state="disabled")   # erst nach Prüfung aktiv
        self._update_preview_label()
        self._apply_theme(self._dark)

    def _on_checkbox_change(self):
        self._update_preview_label()

    # ── Asset-Prüfung (HEAD-Requests) ─────────────────────────────────────────

    def _check_assets(self):
        """Startet HEAD-Prüfung aller sichtbaren Assets im Hintergrund."""
        if not self._asset_status_labels:
            return
        self._check_btn.config(state="disabled")
        self._sel_faulty_btn.config(state="disabled")
        self._log_write("[Prüfung] Starte HEAD-Requests …\n")

        # Alle sichtbaren Assets sammeln
        tasks = [
            (iid, ak, self._items_asset_hrefs.get(iid, {}).get(ak, ""))
            for iid, keys in self._asset_status_labels.items()
            for ak in keys
        ]

        # Laufende Anzeige "⟳" setzen
        T = DARK if self._dark else LIGHT
        for iid, ak, _ in tasks:
            lbl = self._asset_status_labels[iid][ak]
            lbl._status_color_key = "fg_dim"
            lbl.configure(text="  ⟳", fg=T["fg_dim"])

        threading.Thread(
            target=self._check_worker, args=(tasks,), daemon=True
        ).start()

    def _check_worker(self, tasks: List[Tuple[str, str, str]]):
        T       = DARK if self._dark else LIGHT
        errors  = 0

        def _update(lbl, text, color_key):
            lbl._status_color_key = color_key
            lbl.configure(text=text, fg=T[color_key])

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            future_map = {
                pool.submit(check_asset_status, href, self._auth): (iid, ak)
                for iid, ak, href in tasks
            }
            for future in concurrent.futures.as_completed(future_map):
                iid, ak = future_map[future]
                lbl = self._asset_status_labels.get(iid, {}).get(ak)
                if lbl is None:
                    continue
                try:
                    code = future.result()
                except Exception:
                    code = -3

                if code == 200:
                    text, ck = "✓ 200", "ok"
                elif code > 0:
                    text, ck = f"✗ {code}", "err"
                    errors += 1
                elif code == -2:
                    text, ck = "✗ timeout", "hint"
                    errors += 1
                else:
                    text, ck = "✗ err", "hint"
                    errors += 1

                self.after(0, lambda l=lbl, t=text, c=ck: _update(l, t, c))
                self._log_write(f"  {iid}/{ak}  →  {text}\n")

        summary = f"[Prüfung] {len(tasks)} Assets geprüft — {errors} fehlerhaft.\n"
        self._log_write(summary)
        self.after(0, lambda: self._check_btn.config(state="normal"))
        if errors > 0:
            self.after(0, lambda: self._sel_faulty_btn.config(state="normal"))

    def _select_faulty_assets(self):
        """Wählt alle Assets mit Fehler-Status (nicht 200) automatisch aus."""
        count = 0
        for iid, ak_labels in self._asset_status_labels.items():
            for ak, lbl in ak_labels.items():
                is_error = getattr(lbl, "_status_color_key", "fg_dim") in ("err", "hint")
                var = self._asset_selection.get(iid, {}).get(ak)
                if var is not None:
                    var.set(is_error)
                    if is_error:
                        count += 1
        self._log_write(f"[Auswahl] {count} fehlerhafte Assets ausgewählt.\n")

    def _select_all_assets(self):
        for assets in self._asset_selection.values():
            for var in assets.values():
                var.set(True)

    def _deselect_all_assets(self):
        for assets in self._asset_selection.values():
            for var in assets.values():
                var.set(False)

    def _update_preview_label(self):
        total    = sum(len(v) for v in self._asset_selection.values())
        selected = sum(v.get() for assets in self._asset_selection.values()
                       for v in assets.values())
        n_total  = sum(len(v) for v in self._items_asset_hrefs.values())
        self._preview_lbl.configure(
            text=f"{len(self._items_preview)} Item(s)  |  "
                 f"{n_total} Assets total  →  {total} nach Filter  |  "
                 f"{selected} ausgewählt zum Löschen"
        )
        self._del_btn.config(
            text=f"Asset Auswahl ({selected}) löschen",
            state="normal" if selected > 0 else "disabled",
        )
        self._apply_theme(self._dark)

    # ── Löschung ──────────────────────────────────────────────────────────────

    def _start_deletion(self):
        selected_items = {
            iid: [ak for ak, var in assets.items() if var.get()]
            for iid, assets in self._asset_selection.items()
            if any(v.get() for v in assets.values())
        }
        if not selected_items:
            messagebox.showwarning("Nichts ausgewählt", "Keine Assets ausgewählt.")
            return

        total_assets = sum(len(v) for v in selected_items.values())
        dlg = ConfirmDialog(self, self._env_var.get(), len(selected_items),
                            total_assets, self._dark)
        if not dlg.result:
            self._log_write("[Abbruch] Löschung durch Benutzer abgebrochen.\n")
            return

        self._del_btn.config(state="disabled")
        self._disable_search_btns()
        self._apply_theme(self._dark)
        self._progress["maximum"] = total_assets
        self._progress["value"]   = 0
        self._status_lbl.configure(text="Lösche …")

        threading.Thread(
            target=self._delete_worker, args=(selected_items,), daemon=True
        ).start()

    def _delete_worker(self, selected_items: Dict[str, List[str]]):
        ok_list   = []
        fail_list = []
        done      = 0
        total     = sum(len(v) for v in selected_items.values())
        ts        = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        self._log_write(f"\n{'='*60}\n[{ts}] LÖSCHUNG GESTARTET\n{'='*60}\n")
        self._log_write(f"Umgebung:   {self._env_var.get()}\n")
        self._log_write(f"Collection: {COLLECTION_ID}\n")
        self._log_write(f"Items: {len(selected_items)}  |  Assets: {total}\n\n")

        for iid, asset_keys in selected_items.items():
            self._log_write(f"Item: {iid}  ({len(asset_keys)} Assets)\n")
            for ak in asset_keys:
                try:
                    success = delete_asset(self._base_url, self._auth, iid, ak)
                except Exception as exc:
                    success = False
                    self._log_write(f"  [FEHLER] {ak}: {exc}\n")

                if success:
                    ok_list.append(f"{iid}/{ak}")
                    self._log_write(f"  [OK]   gelöscht: {ak}\n")
                else:
                    fail_list.append(f"{iid}/{ak}")
                    self._log_write(f"  [FAIL] nicht gelöscht: {ak}\n")

                done += 1
                self.after(0, lambda d=done: self._progress.configure(value=d))

        ts2 = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._log_write(f"\n{'='*60}\n[{ts2}] ABGESCHLOSSEN\n")
        self._log_write(f"  Erfolgreich:    {len(ok_list)}\n")
        self._log_write(f"  Fehlgeschlagen: {len(fail_list)}\n")
        if fail_list:
            for f in fail_list:
                self._log_write(f"    - {f}\n")
        self._log_write(f"{'='*60}\n")

        self.after(0, lambda: self._status_lbl.configure(
            text=f"Fertig: {len(ok_list)} OK  /  {len(fail_list)} Fehler"
        ))
        self.after(0, self._enable_search_btns)
        self.after(0, lambda: messagebox.showinfo(
            "Abgeschlossen",
            f"Löschung abgeschlossen.\n\n"
            f"Erfolgreich:    {len(ok_list)}\n"
            f"Fehlgeschlagen: {len(fail_list)}",
        ))

    # ── Hilfsfunktionen ───────────────────────────────────────────────────────

    def _clear_state(self):
        self._items_preview       = []
        self._items_asset_hrefs   = {}
        self._items_assets        = {}
        self._asset_selection     = {}
        self._asset_status_labels = {}

    def _disable_search_btns(self):
        self._fetch_direct_btn.config(state="disabled")
        self._fetch_all_btn.config(state="disabled")

    def _enable_search_btns(self):
        self._fetch_direct_btn.config(state="normal")
        self._fetch_all_btn.config(state="normal")

    def _log_write(self, text: str):
        def _do():
            self._log.configure(state="normal")
            self._log.insert("end", text)
            self._log.see("end")
            self._log.configure(state="disabled")
        self.after(0, _do)


# ─── Einstiegspunkt ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    app = KryDeleteApp()
    app.mainloop()
