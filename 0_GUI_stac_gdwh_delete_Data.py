"""
0_GUI_stac_gdwh_delete_Data.py  –  STAC / GDWH Deleting-Tool

Tab 1 – STAC Assets:
  Löscht Assets/Items aus der Collection "ch.swisstopo.spezialbefliegungen".
  Credentials: secrets/stac_credentials.json
  Format: {"INT": {"username": "...", "password": "..."}, "PROD": {...}}

Tab 2 – GDWH Imports:
  Löscht DataPackages (Imports) aus dem GDWH.
  Credentials: secrets/gdwh_credentials.json
  Format: {"INT": {"username": "...", "password": "..."}, "PROD": {...}}

Autor: (basierend auf util_stac_delete_ram.py von David Oesch)
Datum: 2025-12
Lizenz: MIT
"""

import re
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import ctypes
import threading
import concurrent.futures
import json
import logging
import webbrowser
from email.utils import parsedate
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from stac_api import (
    COLLECTION_ID, ENVIRONMENTS, AUFTRAGSTYPEN, EXT_PRESETS,
    get_item_direct, get_collection_items, filter_items,
    delete_asset, delete_item, check_asset_info, asset_area, browser_url,
    stac_item_year, stac_item_area, stac_item_acq_date,
)
from gdwh_api import (
    GDWH_ENVIRONMENTS, GDWH_GDS_KEYS,
    gdwh_get_imports, gdwh_delete_import,
    gdwh_import_id, gdwh_import_date,
    gdwh_import_footprint_bbox,
    gdwh_scan_bucket, gdwh_match_folder, gdwh_bucket_path,
)

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


# ─── Hilfsfunktionen (Formatierung Tree-Spalten) ──────────────────────────────

def _fmt_size(size_bytes: Optional[int]) -> str:
    if size_bytes is None:
        return "–"
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 ** 3:
        return f"{size_bytes / 1024 ** 2:.1f} MB"
    return f"{size_bytes / 1024 ** 3:.2f} GB"


def _fmt_date(lm_str: Optional[str]) -> str:
    """Parst HTTP Last-Modified-Header auf YYYY-MM-DD."""
    if not lm_str:
        return "–"
    try:
        t = parsedate(lm_str)
        if t:
            return f"{t[0]}-{t[1]:02d}-{t[2]:02d}"
    except Exception:
        pass
    return lm_str[:10] if len(lm_str) >= 10 else lm_str


def _status_label(sc: Optional[int]) -> Tuple[str, str]:
    """Gibt (Anzeigetext, Tag-Name) für einen HTTP-Statuscode zurück."""
    if sc is None:
        return "–", "asset_dim"
    if sc == 200:
        return "✓  200", "asset_ok"
    if sc > 0:
        return f"✗  {sc}", "asset_err"
    if sc == -2:
        return "✗  timeout", "asset_warn"
    return "✗  err", "asset_warn"


# ─── Bestätigungs-Dialog (STAC) ───────────────────────────────────────────────

class ConfirmDialog(tk.Toplevel):
    def __init__(self, parent, environment: str, item_count: int,
                 asset_count: int, items_fully_deleted: int, dark: bool):
        super().__init__(parent)
        self.result       = False
        self._environment = environment
        T = DARK if dark else LIGHT
        self.title("Löschung bestätigen")
        self.resizable(False, False)
        self.configure(bg=T["root"])
        self.grab_set()
        self.focus_set()
        self._build(T, environment, item_count, asset_count, items_fully_deleted)
        self.transient(parent)
        self.wait_window(self)

    def _build(self, T, env, items, assets, items_fully_deleted):
        hdr = tk.Frame(self, bg=T["err"], pady=6)
        hdr.pack(fill="x")
        tk.Label(hdr, text="  WARNUNG – DIESE AKTION IST NICHT UMKEHRBAR  ",
                 bg=T["err"], fg="#ffffff", font=("Segoe UI", 11, "bold")).pack()

        body = tk.Frame(self, bg=T["root"], padx=20, pady=10)
        body.pack(fill="both")

        if items_fully_deleted > 0:
            item_note = (
                f"davon {items_fully_deleted} Item(s) vollständig leer →\n"
                "  werden ebenfalls gelöscht. Restliche Items bleiben erhalten."
            )
        else:
            item_note = "Die Items selbst bleiben erhalten."

        info = (f"Umgebung:              {env}\n"
                f"Collection:            {COLLECTION_ID}\n"
                f"Betroffene Items:      {items}\n"
                f"Assets zum Löschen:   {assets}\n\n"
                f"{item_note}\n"
                "Assets werden permanent gelöscht.")
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


# ─── Bestätigungs-Dialog (GDWH) ──────────────────────────────────────────────

class GDWHConfirmDialog(tk.Toplevel):
    def __init__(self, parent, environment: str, gds_key: str,
                 pkg_count: int, dark: bool):
        super().__init__(parent)
        self.result       = False
        self._environment = environment
        T = DARK if dark else LIGHT
        self.title("GDWH Löschung bestätigen")
        self.resizable(False, False)
        self.configure(bg=T["root"])
        self.grab_set()
        self.focus_set()
        self._build(T, environment, gds_key, pkg_count)
        self.transient(parent)
        self.wait_window(self)

    def _build(self, T, env, gds_key, pkg_count):
        hdr = tk.Frame(self, bg=T["err"], pady=6)
        hdr.pack(fill="x")
        tk.Label(hdr, text="  WARNUNG – DIESE AKTION IST NICHT UMKEHRBAR  ",
                 bg=T["err"], fg="#ffffff", font=("Segoe UI", 11, "bold")).pack()

        body = tk.Frame(self, bg=T["root"], padx=20, pady=10)
        body.pack(fill="both")

        info = (f"Umgebung:                    {env}\n"
                f"GDS-Key:                     {gds_key}\n"
                f"DataPackages zum Löschen:   {pkg_count}\n\n"
                "Alle Daten der ausgewählten DataPackages\n"
                "werden permanent und unwiderruflich gelöscht.\n"
                "Die Löschung im GDWH ist asynchron (Job wird gestartet).")
        tk.Label(body, text=info, bg=T["root"], fg=T["fg"],
                 font=("Segoe UI", 10), justify="left").pack(anchor="w", pady=(6, 10))

        tk.Frame(body, bg=T["sep"], height=1).pack(fill="x", pady=6)

        self._check_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            body,
            text="Ich verstehe, dass die Daten permanent gelöscht werden.",
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

    _COLS      = ("sel", "area", "status", "typ", "groesse", "geaendert")
    _COL_HEADS = {"sel": "Auswahl", "area": "Area", "status": "Status", "typ": "Typ / Ext.",
                  "groesse": "Grösse", "geaendert": "Geändert"}
    _COL_W     = {"sel": 60, "area": 90, "status": 100, "typ": 90,
                  "groesse": 90, "geaendert": 105}

    _CHK_ON      = "●"
    _CHK_OFF     = "○"
    _CHK_PARTIAL = "◐"

    def __init__(self):
        super().__init__()
        self.title("STAC / GDWH Deleting-Tool  —  ch.swisstopo.spezialbefliegungen")
        self.minsize(960, 740)

        self._dark: bool = True

        # STAC State
        self._auth: Optional[Tuple] = None
        self._base_url: str = ""
        self._items_preview: List[Dict] = []
        self._items_asset_hrefs: Dict[str, Dict[str, str]] = {}
        self._items_assets: Dict[str, List[str]] = {}
        # Baum-Metadaten: tree_iid → dict mit kind/item_id/asset_key/href/item
        self._nodes: Dict[str, Dict] = {}
        # Auswahl zum Löschen je Asset-Knoten (tree_iid → bool). Default = False
        # (bewusstes Opt-in, anders als beim read-only Monitor-Tool).
        self._checked: Dict[str, bool] = {}

        # GDWH State
        self._gdwh_base_url: str = GDWH_ENVIRONMENTS["INT"]
        self._gdwh_imports: List[Dict] = []
        self._gdwh_selection: Dict[str, tk.BooleanVar] = {}

        self._file_logger = self._setup_file_logger()
        self._build_ui()
        self._apply_theme(True)

    # ── File-Logger Setup ─────────────────────────────────────────────────────

    def _setup_file_logger(self) -> logging.Logger:
        log_dir = Path(__file__).parent / "logs"
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / f"stac_gdwh_delete_{datetime.now().strftime('%Y-%m-%d')}.log"
        logger = logging.getLogger("stac_gdwh_delete_file")
        logger.setLevel(logging.DEBUG)
        if not logger.handlers:
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setFormatter(logging.Formatter(
                "%(asctime)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
            logger.addHandler(fh)
        return logger

    def _make_session_logger(self, mode: str, env: str,
                              year: str, area: str, stac_dt: str) -> logging.Logger:
        """Erstellt pro Lösch-Vorgang einen Logger mit beschreibendem Dateinamen.
        Format: LOG_STAC_INT_2024_ALETSCH_2024-08-20.log"""
        def _s(s: str) -> str:
            return re.sub(r"[^\w\-]", "_", s).strip("_") or ""
        dt_short = stac_dt[:10] if stac_dt else ""
        parts    = [p for p in ["LOG", mode, env, year, _s(area), _s(dt_short)] if p]
        log_name = "_".join(parts)
        log_dir  = Path(__file__).parent / "logs"
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / f"{log_name}.log"
        logger   = logging.getLogger(f"session_{log_name}")
        logger.setLevel(logging.DEBUG)
        if not logger.handlers:
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setFormatter(logging.Formatter(
                "%(asctime)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
            logger.addHandler(fh)
        return logger

    # ── UI aufbauen ───────────────────────────────────────────────────────────

    def _build_ui(self):
        # Header
        self._hdr = tk.Frame(self, height=52)
        self._hdr.pack(fill="x")
        self._hdr.pack_propagate(False)
        self._hdr_lbl = tk.Label(
            self._hdr,
            text="  STAC / GDWH Deleting-Tool  —  ch.swisstopo.spezialbefliegungen",
            font=("Segoe UI", 13, "bold"),
        )
        self._hdr_lbl.pack(side="left", padx=16, pady=10)
        self._theme_btn = tk.Button(
            self._hdr, text="Hell", relief="flat", borderwidth=0,
            font=("Segoe UI", 9), cursor="hand2", padx=10, pady=4,
            command=self._toggle_theme,
        )
        self._theme_btn.pack(side="right", padx=12)

        # Notebook mit 2 Tabs
        self._nb = ttk.Notebook(self)
        self._nb.pack(fill="both", expand=True, padx=12, pady=8)
        self._nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # ── Tab 1: STAC ───────────────────────────────────────────────────────
        stac_tab = ttk.Frame(self._nb)
        self._nb.add(stac_tab, text="  STAC  Assets  ")

        outer = ttk.Frame(stac_tab)
        outer.pack(fill="both", expand=True)

        self._canvas = tk.Canvas(outer, highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        self._sf = ttk.Frame(self._canvas)
        win_id   = self._canvas.create_window((0, 0), window=self._sf, anchor="nw")
        self._sf.bind("<Configure>",
                      lambda _: self._canvas.configure(
                          scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>",
                          lambda e: self._canvas.itemconfig(win_id, width=e.width))
        self._canvas.bind_all("<MouseWheel>",
                              lambda e: self._canvas.yview_scroll(
                                  -1 * (e.delta // 120), "units"))

        self._build_step1(self._sf)
        self._build_step2(self._sf)
        self._build_actions(self._sf)
        self._build_step3(self._sf)
        self._build_step4(self._sf)

        # ── Tab 2: GDWH ───────────────────────────────────────────────────────
        gdwh_tab = ttk.Frame(self._nb)
        self._nb.add(gdwh_tab, text="  GDWH  Imports  ")
        self._build_gdwh_tab(gdwh_tab)

    def _on_tab_changed(self, _):
        """Mausrad-Scrollziel je nach aktivem Tab umschalten."""
        tab_text = self._nb.tab(self._nb.select(), "text")
        if "STAC" in tab_text:
            self._canvas.bind_all(
                "<MouseWheel>",
                lambda e: self._canvas.yview_scroll(-1 * (e.delta // 120), "units"))
        else:
            self._gdwh_canvas.bind_all(
                "<MouseWheel>",
                lambda e: self._gdwh_canvas.yview_scroll(-1 * (e.delta // 120), "units"))

    # ═══════════════════════════════════════════════════════════════════════════
    # STAC Tab – Schritte 1–4
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_step1(self, parent):
        sec = ttk.LabelFrame(parent, text="1   Umgebung & Credentials",
                             padding=10, style="Section.TLabelframe")
        sec.pack(fill="x", pady=(0, 6))
        sec.columnconfigure(5, weight=1)

        ttk.Label(sec, text="Umgebung:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._env_var = tk.StringVar(value="INT")
        for col, env in enumerate(("INT", "PROD"), 1):
            ttk.Radiobutton(sec, text=env, variable=self._env_var, value=env,
                            command=self._on_env_change).grid(
                row=0, column=col, sticky="w", padx=4)

        self._url_lbl = ttk.Label(sec, text=ENVIRONMENTS["INT"],
                                   font=("Segoe UI", 8), style="Dim.TLabel")
        self._url_lbl.grid(row=0, column=3, sticky="w", padx=12)

        ttk.Button(sec, text="STAC Browser öffnen",
                   command=self._open_stac_browser).grid(row=0, column=4, padx=(0, 12))

        self._cred_btn = ttk.Button(sec, text="Credentials laden",
                                     command=self._load_credentials,
                                     style="Amber.TButton")
        self._cred_btn.grid(row=0, column=6, padx=(12, 0))

        self._cred_status = ttk.Label(sec, text="nicht geladen",
                                       font=("Segoe UI", 9, "italic"),
                                       style="Dim.TLabel")
        self._cred_status.grid(row=0, column=7, padx=8)

    def _build_step2(self, parent):
        sec = ttk.LabelFrame(parent, text="2   Auftragstyp, Item & Asset-Filter",
                             padding=10, style="Section.TLabelframe")
        sec.pack(fill="x", pady=(0, 6))
        sec.columnconfigure(1, weight=1)

        ttk.Label(sec, text="Auftragstyp:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._auftragstyp_var = tk.StringVar(value=list(AUFTRAGSTYPEN.keys())[0])
        typ_frame = ttk.Frame(sec)
        typ_frame.grid(row=0, column=1, columnspan=3, sticky="w")
        for typ in AUFTRAGSTYPEN:
            ttk.Radiobutton(typ_frame, text=typ, variable=self._auftragstyp_var, value=typ,
                            command=self._on_auftragstyp_change).pack(side="left", padx=(0, 14))

        ttk.Label(sec, text="Jahr:").grid(row=1, column=0, sticky="w",
                                          padx=(0, 8), pady=(6, 0))
        self._year_filter_var = tk.StringVar()
        self._year_filter_var.trace_add("write", lambda *_: self._apply_filters())
        ttk.Entry(sec, textvariable=self._year_filter_var, width=8).grid(
            row=1, column=1, sticky="w", pady=(6, 0))
        ttk.Label(
            sec, text="z.B. 2023  —  Leer = alle Jahre",
            font=("Segoe UI", 8, "italic"), style="Dim.TLabel",
        ).grid(row=1, column=2, columnspan=2, sticky="w", pady=(6, 0))

        ttk.Label(sec, text="Item-ID:").grid(row=2, column=0, sticky="w",
                                              padx=(0, 8), pady=(6, 0))
        self._item_id_var = tk.StringVar(value=list(AUFTRAGSTYPEN.values())[0])
        ttk.Entry(sec, textvariable=self._item_id_var, width=46).grid(
            row=2, column=1, columnspan=2, sticky="ew", padx=(0, 10), pady=(6, 0))
        ttk.Label(
            sec, text="Teilstring genügt  (für direkten Abruf: vollständige ID)",
            font=("Segoe UI", 8, "italic"), style="Dim.TLabel",
        ).grid(row=2, column=3, sticky="w", pady=(6, 0))

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

    def _build_actions(self, parent):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(0, 6))

        self._load_btn = ttk.Button(
            row, text="Laden", command=self._load, state="disabled",
            style="AmberBold.TButton")
        self._load_btn.pack(side="left", padx=(0, 16))

        ttk.Separator(row, orient="vertical").pack(side="left", fill="y", padx=(0, 16))

        self._expand_btn = ttk.Button(
            row, text="Alle aufklappen", command=self._expand_all, state="disabled")
        self._expand_btn.pack(side="left", padx=(0, 4))

        self._collapse_btn = ttk.Button(
            row, text="Alle einklappen", command=self._collapse_all, state="disabled")
        self._collapse_btn.pack(side="left")

    def _build_step3(self, parent):
        sec = ttk.LabelFrame(parent, text="3   Assets auswählen zum Löschen",
                             padding=10, style="Section.TLabelframe")
        sec.pack(fill="x", pady=(0, 6))
        sec.columnconfigure(0, weight=1)

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

        tree_outer = ttk.Frame(sec)
        tree_outer.grid(row=1, column=0, sticky="nsew", pady=(0, 4))
        tree_outer.rowconfigure(0, weight=1)
        tree_outer.columnconfigure(0, weight=1)
        sec.rowconfigure(1, weight=1)

        self._tree = ttk.Treeview(
            tree_outer, columns=self._COLS, show="tree headings",
            selectmode="browse", height=12)

        self._tree.column("#0", width=320, minwidth=200, stretch=False)
        self._tree.heading("#0", text="Item / Asset")
        for col in self._COLS:
            self._tree.column(col, width=self._COL_W[col],
                              minwidth=55, stretch=False, anchor="center")
            self._tree.heading(col, text=self._COL_HEADS[col])

        vsb = ttk.Scrollbar(tree_outer, orient="vertical",   command=self._tree.yview)
        hsb = ttk.Scrollbar(tree_outer, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        self._ctx = tk.Menu(self, tearoff=0)
        self._tree.bind("<Button-3>", self._on_right_click)
        self._tree.bind("<Double-1>", self._on_double_click)
        self._tree.bind("<Button-1>", self._on_tree_click)

        self._tree.bind("<Enter>", lambda _: self._tree.bind_all(
            "<MouseWheel>",
            lambda e: self._tree.yview_scroll(-1 * (e.delta // 120), "units")))
        self._tree.bind("<Leave>", lambda _: self._canvas.bind_all(
            "<MouseWheel>",
            lambda e: self._canvas.yview_scroll(-1 * (e.delta // 120), "units")))

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

    # ═══════════════════════════════════════════════════════════════════════════
    # GDWH Tab – Schritte 1–4
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_gdwh_tab(self, parent):
        outer = ttk.Frame(parent)
        outer.pack(fill="both", expand=True)

        self._gdwh_canvas = tk.Canvas(outer, highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=self._gdwh_canvas.yview)
        self._gdwh_canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._gdwh_canvas.pack(side="left", fill="both", expand=True)

        self._gdwh_sf = ttk.Frame(self._gdwh_canvas)
        win_id = self._gdwh_canvas.create_window((0, 0), window=self._gdwh_sf, anchor="nw")
        self._gdwh_sf.bind(
            "<Configure>",
            lambda _: self._gdwh_canvas.configure(
                scrollregion=self._gdwh_canvas.bbox("all")))
        self._gdwh_canvas.bind(
            "<Configure>",
            lambda e: self._gdwh_canvas.itemconfig(win_id, width=e.width))

        self._build_gdwh_step1(self._gdwh_sf)
        self._build_gdwh_step2(self._gdwh_sf)
        self._build_gdwh_step3(self._gdwh_sf)
        self._build_gdwh_step4(self._gdwh_sf)

    def _build_gdwh_step1(self, parent):
        sec = ttk.LabelFrame(
            parent,
            text="1   Umgebung",
            padding=10, style="Section.TLabelframe")
        sec.pack(fill="x", pady=(0, 6))
        sec.columnconfigure(3, weight=1)

        ttk.Label(sec, text="Umgebung:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._gdwh_env_var = tk.StringVar(value="INT")
        for col, env in enumerate(("INT", "PROD"), 1):
            ttk.Radiobutton(sec, text=env, variable=self._gdwh_env_var, value=env,
                            command=self._gdwh_on_env_change).grid(
                row=0, column=col, sticky="w", padx=4)

        self._gdwh_url_lbl = ttk.Label(sec, text=GDWH_ENVIRONMENTS["INT"],
                                        font=("Segoe UI", 8), style="Dim.TLabel")
        self._gdwh_url_lbl.grid(row=0, column=3, sticky="w", padx=12)

        ttk.Label(
            sec,
            text="Authentifizierung: Windows-Session (aktuell eingeloggter User, wie im Browser)",
            font=("Segoe UI", 8, "italic"), style="Dim.TLabel",
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))

    def _build_gdwh_step2(self, parent):
        sec = ttk.LabelFrame(parent, text="2   GDS-Key & Imports laden",
                             padding=10, style="Section.TLabelframe")
        sec.pack(fill="x", pady=(0, 6))
        sec.columnconfigure(1, weight=1)

        ttk.Label(sec, text="Jahr:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._gdwh_year_filter_var = tk.StringVar()
        self._gdwh_year_filter_var.trace_add("write", lambda *_: self._gdwh_apply_filter())
        ttk.Entry(sec, textvariable=self._gdwh_year_filter_var, width=8).grid(
            row=0, column=1, sticky="w")
        ttk.Label(
            sec, text="z.B. 2023  —  Leer = alle Jahre",
            font=("Segoe UI", 8, "italic"), style="Dim.TLabel",
        ).grid(row=0, column=2, sticky="w", padx=(8, 0))

        ttk.Label(sec, text="GDS-Key:").grid(row=1, column=0, sticky="w",
                                              padx=(0, 8), pady=(6, 0))
        self._gdwh_gds_key_var = tk.StringVar(value=GDWH_GDS_KEYS[0])
        self._gdwh_gds_combo = ttk.Combobox(
            sec, textvariable=self._gdwh_gds_key_var,
            values=GDWH_GDS_KEYS, state="readonly", width=28,
        )
        self._gdwh_gds_combo.grid(row=1, column=1, sticky="w", padx=(0, 10), pady=(6, 0))

        self._gdwh_fetch_btn = ttk.Button(
            sec, text="Imports laden",
            command=self._gdwh_fetch_imports, state="normal",
        )
        self._gdwh_fetch_btn.grid(row=1, column=2, pady=(6, 0))

    def _build_gdwh_step3(self, parent):
        sec = ttk.LabelFrame(parent, text="3   DataPackages auswählen zum Löschen",
                             padding=10, style="Section.TLabelframe")
        sec.pack(fill="x", pady=(0, 6))
        sec.columnconfigure(0, weight=1)

        sel_row = ttk.Frame(sec)
        sel_row.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        self._gdwh_sel_all_btn = ttk.Button(
            sel_row, text="Alle auswählen",
            command=self._gdwh_select_all, state="disabled",
        )
        self._gdwh_sel_all_btn.pack(side="left", padx=(0, 4))

        self._gdwh_sel_none_btn = ttk.Button(
            sel_row, text="Alle abwählen",
            command=self._gdwh_deselect_all, state="disabled",
        )
        self._gdwh_sel_none_btn.pack(side="left")

        list_outer = tk.Frame(sec, bd=1, relief="sunken")
        list_outer.grid(row=1, column=0, sticky="nsew", pady=(0, 4))
        sec.rowconfigure(1, weight=1)

        self._gdwh_list_canvas = tk.Canvas(list_outer, height=220, highlightthickness=0)
        vsb = ttk.Scrollbar(list_outer, orient="vertical",
                             command=self._gdwh_list_canvas.yview)
        self._gdwh_list_canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._gdwh_list_canvas.pack(side="left", fill="both", expand=True)

        self._gdwh_list_frame = tk.Frame(self._gdwh_list_canvas)
        lwin = self._gdwh_list_canvas.create_window(
            (0, 0), window=self._gdwh_list_frame, anchor="nw")
        self._gdwh_list_frame.bind(
            "<Configure>",
            lambda _: self._gdwh_list_canvas.configure(
                scrollregion=self._gdwh_list_canvas.bbox("all")))
        self._gdwh_list_canvas.bind(
            "<Configure>",
            lambda e: self._gdwh_list_canvas.itemconfig(lwin, width=e.width))

        # Mausrad: innerer Canvas bei Hover, äusserer GDWH-Canvas sonst
        self._gdwh_list_canvas.bind("<Enter>", lambda _: self._gdwh_list_canvas.bind_all(
            "<MouseWheel>",
            lambda e: self._gdwh_list_canvas.yview_scroll(-1 * (e.delta // 120), "units")))
        self._gdwh_list_canvas.bind("<Leave>", lambda _: self._gdwh_canvas.bind_all(
            "<MouseWheel>",
            lambda e: self._gdwh_canvas.yview_scroll(-1 * (e.delta // 120), "units")))

        self._gdwh_preview_lbl = ttk.Label(
            sec, text="Noch keine Imports geladen.",
            font=("Segoe UI", 9, "italic"), style="Dim.TLabel",
        )
        self._gdwh_preview_lbl.grid(row=2, column=0, sticky="w", pady=(2, 0))

    def _build_gdwh_step4(self, parent):
        sec = ttk.LabelFrame(parent, text="4   Löschung ausführen",
                             padding=10, style="Section.TLabelframe")
        sec.pack(fill="both", expand=True, pady=(0, 4))
        sec.columnconfigure(1, weight=1)
        sec.rowconfigure(1, weight=1)

        # E-Mail (optional – GDWH schickt Benachrichtigung nach Abschluss)
        ttk.Label(sec, text="E-Mail (optional):").grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=(0, 8))
        self._gdwh_email_var = tk.StringVar()
        ttk.Entry(sec, textvariable=self._gdwh_email_var, width=40).grid(
            row=0, column=1, sticky="w", pady=(0, 8))
        ttk.Label(
            sec,
            text="GDWH schickt Benachrichtigung wenn der Lösch-Job abgeschlossen ist",
            font=("Segoe UI", 8, "italic"), style="Dim.TLabel",
        ).grid(row=0, column=2, sticky="w", padx=(10, 0), pady=(0, 8))

        self._gdwh_log = scrolledtext.ScrolledText(
            sec, height=10, state="disabled",
            font=("Cascadia Mono", 9), wrap="word",
        )
        self._gdwh_log.grid(row=1, column=0, columnspan=3, sticky="nsew", pady=(0, 8))

        btn_row = ttk.Frame(sec)
        btn_row.grid(row=2, column=0, columnspan=3, sticky="ew")

        self._gdwh_del_btn = tk.Button(
            btn_row, text="Ausgewählte DataPackages löschen …",
            relief="flat", padx=14, pady=6,
            font=("Segoe UI", 10, "bold"), state="disabled",
            command=self._gdwh_start_deletion,
        )
        self._gdwh_del_btn.pack(side="left")

        self._gdwh_progress = ttk.Progressbar(btn_row, mode="determinate", length=220)
        self._gdwh_progress.pack(side="left", padx=16)

        self._gdwh_status_lbl = ttk.Label(btn_row, text="", font=("Segoe UI", 9))
        self._gdwh_status_lbl.pack(side="left")

    # ═══════════════════════════════════════════════════════════════════════════
    # Theme
    # ═══════════════════════════════════════════════════════════════════════════

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
        s.configure("Amber.TButton",
            background=T["btn"], foreground=T["hint"],
            bordercolor=T["sep"], relief="flat",
            padding=(8, 4), focuscolor=T["panel"],
        )
        s.map("Amber.TButton",
            background=[("active", T["btn_hover"]), ("pressed", T["sep"])],
            foreground=[("active", T["hint"])],
            relief=[("pressed", "flat")],
        )
        s.configure("AmberBold.TButton",
            background=T["btn"], foreground=T["hint"],
            bordercolor=T["sep"], relief="flat",
            padding=(8, 4), focuscolor=T["panel"],
            font=("Segoe UI", 10, "bold"),
        )
        s.map("AmberBold.TButton",
            background=[("active", T["btn_hover"]), ("pressed", T["sep"])],
            foreground=[("active", T["hint"])],
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
        s.configure("Horizontal.TScrollbar",
            background=T["btn"], troughcolor=T["root"],
            bordercolor=T["sep"], arrowcolor=T["fg"],
        )
        s.configure("Treeview",
            background=T["list"], foreground=T["fg"],
            fieldbackground=T["list"], rowheight=22, bordercolor=T["sep"])
        s.configure("Treeview.Heading",
            background=T["btn"], foreground=T["fg"],
            relief="flat", padding=(4, 4))
        s.map("Treeview",
            background=[("selected", T["sel_bg"])],
            foreground=[("selected", T["sel_fg"])])
        s.map("Treeview.Heading",
            background=[("active", T["btn_hover"])])
        s.configure("TSeparator", background=T["sep"])
        s.configure("TProgressbar",
            background=T["accent"], troughcolor=T["root"], bordercolor=T["sep"])
        s.configure("TNotebook",
            background=T["root"], bordercolor=T["sep"],
            tabmargins=[2, 4, 0, 0])
        s.configure("TNotebook.Tab",
            background=T["btn"], foreground=T["fg_dim"],
            padding=[10, 3],          # klein: nicht-aktiver Tab
            focuscolor=T["panel"])
        s.map("TNotebook.Tab",
            background=[("selected", T["sel_bg"]), ("active", T["btn_hover"])],
            foreground=[("selected", T["sel_fg"]),  ("active", T["fg"])],
            padding=[("selected", [16, 7])],   # grösser: aktiver Tab
        )

        self.configure(bg=T["root"])
        self._canvas.configure(bg=T["panel"], highlightbackground=T["sep"])
        self._gdwh_canvas.configure(bg=T["panel"], highlightbackground=T["sep"])
        self._hdr.configure(bg=T["hdr_bg"])
        self._hdr_lbl.configure(bg=T["hdr_bg"], fg=T["hdr_fg"])
        self._theme_btn.configure(
            bg=T["hdr_bg"], fg=T["hdr_fg"],
            activebackground=T["btn"], activeforeground=T["fg"],
            text="Hell" if dark else "Dark",
        )
        self._log.configure(bg=T["log_bg"], fg=T["log_fg"],
                             insertbackground=T["log_fg"])
        self._gdwh_log.configure(bg=T["log_bg"], fg=T["log_fg"],
                                  insertbackground=T["log_fg"])

        # STAC Asset-Tree
        self._tree.tag_configure("item", foreground=T["chk_item"], font=("Segoe UI", 9, "bold"))
        self._tree.tag_configure("asset_ok",   foreground=T["ok"])
        self._tree.tag_configure("asset_err",  foreground=T["err"])
        self._tree.tag_configure("asset_warn", foreground=T["hint"])
        self._tree.tag_configure("asset_dim",  foreground=T["fg_dim"])

        self._gdwh_list_canvas.configure(bg=T["chk_bg"])
        self._gdwh_list_frame.configure(bg=T["chk_bg"])
        self._gdwh_recolor_list(T)

        # STAC Lösch-Button
        if str(self._del_btn["state"]) == "disabled":
            self._del_btn.configure(
                bg=T["btn"], fg=T["fg_dim"],
                activebackground=T["btn_hover"], activeforeground=T["fg"])
        else:
            self._del_btn.configure(
                bg=T["err"], fg="#ffffff",
                activebackground="#b71c1c" if dark else "#c62828",
                activeforeground="#ffffff")

        # GDWH Lösch-Button
        if str(self._gdwh_del_btn["state"]) == "disabled":
            self._gdwh_del_btn.configure(
                bg=T["btn"], fg=T["fg_dim"],
                activebackground=T["btn_hover"], activeforeground=T["fg"])
        else:
            self._gdwh_del_btn.configure(
                bg=T["err"], fg="#ffffff",
                activebackground="#b71c1c" if dark else "#c62828",
                activeforeground="#ffffff")

        self._set_titlebar_dark(dark)

    def _gdwh_recolor_list(self, T: dict):
        def recolor(widget):
            cls = widget.winfo_class()
            if cls == "Frame":
                widget.configure(bg=T["chk_bg"])
            elif cls == "Label":
                if getattr(widget, "_is_pkg_header", False):
                    widget.configure(bg=T["chk_bg"], fg=T["chk_item"])
                else:
                    widget.configure(bg=T["chk_bg"], fg=T["fg_dim"])
            elif cls == "Checkbutton":
                widget.configure(
                    bg=T["chk_bg"], fg=T["fg"],
                    selectcolor=T["input"],
                    activebackground=T["chk_bg"], activeforeground=T["fg"])
            for child in widget.winfo_children():
                recolor(child)
        recolor(self._gdwh_list_frame)

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

    # ═══════════════════════════════════════════════════════════════════════════
    # STAC – Event Handler
    # ═══════════════════════════════════════════════════════════════════════════

    def _on_env_change(self):
        env = self._env_var.get()
        self._url_lbl.configure(text=ENVIRONMENTS[env])
        self._auth = None
        self._cred_status.configure(text="nicht geladen")
        self._cred_btn.configure(style="Amber.TButton")
        self._load_btn.config(state="disabled")
        self._load_btn.configure(style="AmberBold.TButton")
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
            cfg_path = Path(__file__).parent / "secrets" / "stac_credentials.json"
            with open(cfg_path, encoding="utf-8") as f:
                cfg = json.load(f)
            env_cfg  = cfg[env]
            username = env_cfg["username"]
            password = env_cfg["password"]
            self._auth     = (username, password)
            self._base_url = ENVIRONMENTS[env]
            T = DARK if self._dark else LIGHT
            self._cred_status.configure(text=f"Geladen: {username}", foreground=T["ok"])
            self._cred_btn.configure(style="TButton")
            self._load_btn.config(state="normal")
            self._load_btn.configure(style="AmberBold.TButton")
            self._log_write(f"[STAC Credentials] {env} – Benutzer: {username}\n")
        except Exception as exc:
            T = DARK if self._dark else LIGHT
            self._cred_status.configure(text="Fehler!", foreground=T["err"])
            self._cred_btn.configure(style="Amber.TButton")
            messagebox.showerror("Credentials-Fehler", str(exc))

    def _load(self):
        search_term = self._item_id_var.get().strip()
        if not search_term:
            if not messagebox.askyesno(
                    "Alle Items laden?",
                    "Kein Filter eingegeben.\nAlle Items der Collection laden?\n\n"
                    "(Kann bei 5000+ Items mehrere Minuten dauern.)"):
                return
        self._load_btn.configure(style="TButton")
        self._disable_search_btns()
        self._del_btn.config(state="disabled")
        self._apply_theme(self._dark)
        self._clear_tree()
        self._preview_lbl.configure(text="Lade …")
        self._clear_state()
        threading.Thread(target=self._load_worker,
                         args=(search_term,), daemon=True).start()

    # ── STAC Worker-Thread ────────────────────────────────────────────────────

    def _load_worker(self, search_term: str):
        try:
            if search_term:
                self._log_write(f"[Abruf] Prüfe exakte Item-ID: {search_term} …\n")
                item = get_item_direct(self._base_url, self._auth, search_term)
                if item is not None:
                    hrefs = {k: v.get("href", "") for k, v in item.get("assets", {}).items()}
                    self._log_write(
                        f"[OK] {item['id']}: {len(hrefs)} Asset(s) total (Direct-Lookup)\n")
                    self._items_preview     = [item]
                    self._items_asset_hrefs = {item["id"]: hrefs}
                    self.after(0, self._apply_filters)
                    return
                self._log_write(
                    "[Info] Keine exakte Übereinstimmung – lade gesamte Collection …\n")
            else:
                self._log_write("[Abruf] Hole alle Items der Collection …\n")

            all_items = get_collection_items(self._base_url, self._auth, self._log_write)
            self._log_write(f"[Abruf] {len(all_items)} Items total.\n")
            filtered = filter_items(all_items, search_term)
            self._log_write(f"[Filter ID] '{search_term}': {len(filtered)} Items.\n")
            if not filtered:
                self.after(0, lambda: self._preview_lbl.configure(
                    text="Keine Items gefunden."))
                self.after(0, self._enable_search_btns)
                return

            hrefs_map: Dict[str, Dict[str, str]] = {}
            full_items: List[Dict] = []
            for i, item in enumerate(filtered, 1):
                iid    = item["id"]
                assets = item.get("assets", {})
                if not assets:
                    full = get_item_direct(self._base_url, self._auth, iid)
                    if full is not None:
                        item   = full
                        assets = item.get("assets", {})
                hrefs_map[iid] = {k: v.get("href", "") for k, v in assets.items()}
                full_items.append(item)
                self._log_write(
                    f"  [{i}/{len(filtered)}] {iid}: {len(hrefs_map[iid])} Asset(s)\n")
            self._items_preview     = full_items
            self._items_asset_hrefs = hrefs_map
            self.after(0, self._apply_filters)
        except Exception as exc:
            self._log_write(f"[FEHLER] {exc}\n")
            self.after(0, lambda: messagebox.showerror("Fehler", str(exc)))
            self.after(0, self._enable_search_btns)

    # ── STAC Filterung ────────────────────────────────────────────────────────

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
        year_filter = self._year_filter_var.get().strip()
        key_filter  = self._asset_filter_var.get().strip().lower()
        extensions  = self._get_active_extensions()
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
        items = self._items_preview
        if year_filter:
            items = [it for it in items if stac_item_year(it) == year_filter]
        self._populate_tree(items, assets_map)

    # ── STAC Asset-Tree ───────────────────────────────────────────────────────

    def _clear_tree(self):
        self._tree.delete(*self._tree.get_children())
        self._nodes.clear()
        self._checked.clear()

    def _is_checked(self, asset_nid: str) -> bool:
        return self._checked.get(asset_nid, False)

    def _chk_glyph(self, asset_nid: str) -> str:
        return self._CHK_ON if self._is_checked(asset_nid) else self._CHK_OFF

    def _item_asset_nids(self, item_id: str) -> List[str]:
        return [nid for nid, d in self._nodes.items()
                if d["kind"] == "asset" and d["item_id"] == item_id]

    def _item_check_glyph(self, asset_nids: List[str]) -> str:
        if not asset_nids:
            return self._CHK_OFF
        states = [self._is_checked(n) for n in asset_nids]
        if all(states):
            return self._CHK_ON
        if not any(states):
            return self._CHK_OFF
        return self._CHK_PARTIAL

    def _refresh_item_glyph(self, item_id: str):
        item_nid = f"item::{item_id}"
        if not self._tree.exists(item_nid):
            return
        vals = list(self._tree.item(item_nid, "values"))
        vals[0] = self._item_check_glyph(self._item_asset_nids(item_id))
        self._tree.item(item_nid, values=vals)

    def _on_tree_click(self, event):
        if self._tree.identify_region(event.x, event.y) != "cell":
            return
        if self._tree.identify_column(event.x) != "#1":  # "sel"-Spalte
            return
        row = self._tree.identify_row(event.y)
        d = self._nodes.get(row)
        if not d:
            return

        if d["kind"] == "asset":
            self._checked[row] = not self._is_checked(row)
            vals = list(self._tree.item(row, "values"))
            vals[0] = self._chk_glyph(row)
            self._tree.item(row, values=vals)
            self._refresh_item_glyph(d["item_id"])
        else:  # item: alle zugehörigen Assets gemeinsam (de)selektieren
            asset_nids = self._item_asset_nids(d["item_id"])
            new_state  = self._item_check_glyph(asset_nids) != self._CHK_ON
            for nid in asset_nids:
                self._checked[nid] = new_state
                vals = list(self._tree.item(nid, "values"))
                vals[0] = self._chk_glyph(nid)
                self._tree.item(nid, values=vals)
            self._refresh_item_glyph(d["item_id"])
        self._update_preview_label()
        return "break"

    def _populate_tree(self, items: List[Dict],
                        assets_map: Dict[str, List[str]]):
        self._clear_tree()
        any_visible = False

        # Nur Items mit Assets, sortiert nach Aufnahmedatum aus Item-ID (neueste zuerst)
        visible = [it for it in items if assets_map.get(it["id"])]
        visible.sort(key=stac_item_acq_date, reverse=True)
        _pfx = COLLECTION_ID + "_"

        for item in visible:
            iid        = item["id"]
            asset_keys = assets_map.get(iid, [])
            any_visible = True

            area = stac_item_area(item)
            acq  = stac_item_acq_date(item)
            display = iid[len(_pfx):] if iid.startswith(_pfx) else iid
            meta  = "  ".join(p for p in [area, acq] if p)
            label = display + (f"   [{meta}]" if meta else "")

            asset_node_ids = [f"asset::{iid}::{ak}" for ak in asset_keys]
            node_id = f"item::{iid}"
            self._tree.insert("", "end", iid=node_id, text=f"  {label}",
                              values=(self._item_check_glyph(asset_node_ids), area,
                                      "", "", f"{len(asset_keys)} Assets", ""),
                              tags=("item",), open=True)
            self._nodes[node_id] = {"kind": "item", "item_id": iid, "item": item}

            assets_dict = item.get("assets", {})
            for ak in asset_keys:
                aval   = assets_dict.get(ak, {})
                href   = self._items_asset_hrefs.get(iid, {}).get(ak, "")
                a_area = asset_area(aval) or area
                ext    = Path(href).suffix if href else ""
                atype  = aval.get("type", "")

                anid = f"asset::{iid}::{ak}"
                self._tree.insert(node_id, "end", iid=anid, text=f"        {ak}",
                                  values=(self._chk_glyph(anid), a_area, "",
                                          ext or atype[:22], "", ""),
                                  tags=("asset_dim",))
                self._nodes[anid] = {
                    "kind": "asset", "item_id": iid, "asset_key": ak,
                    "href": href, "item": item,
                }

        if not any_visible:
            self._preview_lbl.configure(text="Keine Assets nach aktuellem Filter.")

        self._enable_search_btns()
        st = "normal" if any_visible else "disabled"
        self._sel_all_btn.config(state=st)
        self._sel_none_btn.config(state=st)
        self._check_btn.config(state=st)
        self._expand_btn.config(state=st)
        self._collapse_btn.config(state=st)
        self._sel_faulty_btn.config(state="disabled")
        self._update_preview_label()
        self._apply_theme(self._dark)

    def _expand_all(self):
        for node in self._tree.get_children():
            self._tree.item(node, open=True)

    def _collapse_all(self):
        for node in self._tree.get_children():
            self._tree.item(node, open=False)

    # ── STAC Asset-Prüfung ────────────────────────────────────────────────────

    def _check_assets(self):
        tasks = [
            (nid, d["item_id"], d["asset_key"], d["href"])
            for nid, d in self._nodes.items()
            if d["kind"] == "asset" and d.get("href")
        ]
        if not tasks:
            self._log_write("[Prüfung] Keine Assets zum Prüfen.\n")
            return
        self._check_btn.config(state="disabled")
        self._sel_faulty_btn.config(state="disabled")
        self._log_write(f"[Prüfung] Starte HEAD-Requests für {len(tasks)} Assets …\n")
        for nid, _, _, _ in tasks:
            if self._tree.exists(nid):
                cur = list(self._tree.item(nid, "values"))
                cur[2] = "⟳"
                self._tree.item(nid, values=cur, tags=("asset_dim",))
        threading.Thread(target=self._check_worker, args=(tasks,), daemon=True).start()

    def _check_worker(self, tasks: List[Tuple[str, str, str, str]]):
        errors = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            future_map = {
                pool.submit(check_asset_info, href, self._auth): (nid, iid, ak)
                for nid, iid, ak, href in tasks
            }
            for future in concurrent.futures.as_completed(future_map):
                nid, iid, ak = future_map[future]
                try:
                    info = future.result()
                except Exception:
                    info = {"status": -3, "size_bytes": None, "last_modified": None}

                sc       = info.get("status")
                sz       = info.get("size_bytes")
                lm       = info.get("last_modified")
                stxt, tg = _status_label(sc)
                if sc is None or sc != 200:
                    errors += 1

                self.after(0, lambda n=nid, s=stxt, t=tg,
                           sz_=_fmt_size(sz), lm_=_fmt_date(lm):
                           self._update_tree_row(n, s, t, sz_, lm_))
                self._log_write(f"  {iid}/{ak}  →  {stxt}  {_fmt_size(sz)}\n")

        self._log_write(f"[Prüfung] {len(tasks)} Assets geprüft — {errors} fehlerhaft.\n")
        self.after(0, lambda: self._check_btn.config(state="normal"))
        if errors > 0:
            self.after(0, lambda: self._sel_faulty_btn.config(state="normal"))

    def _update_tree_row(self, nid: str, status_text: str, tag: str,
                          size_text: str, date_text: str):
        if not self._tree.exists(nid):
            return
        cur = list(self._tree.item(nid, "values"))
        cur[2], cur[4], cur[5] = status_text, size_text, date_text
        self._tree.item(nid, values=cur, tags=(tag,))

    def _select_faulty_assets(self):
        count = 0
        for nid, d in self._nodes.items():
            if d["kind"] != "asset":
                continue
            tags     = self._tree.item(nid, "tags") if self._tree.exists(nid) else ()
            tag      = tags[0] if tags else "asset_dim"
            is_error = tag in ("asset_err", "asset_warn")
            self._checked[nid] = is_error
            if is_error:
                count += 1
            if self._tree.exists(nid):
                vals = list(self._tree.item(nid, "values"))
                vals[0] = self._chk_glyph(nid)
                self._tree.item(nid, values=vals)
        for nid, d in self._nodes.items():
            if d["kind"] == "item":
                self._refresh_item_glyph(d["item_id"])
        self._log_write(f"[Auswahl] {count} fehlerhafte Assets ausgewählt.\n")
        self._update_preview_label()

    def _select_all_assets(self):
        self._set_all_checked(True)

    def _deselect_all_assets(self):
        self._set_all_checked(False)

    def _set_all_checked(self, state: bool):
        for nid, d in self._nodes.items():
            if d["kind"] != "asset":
                continue
            self._checked[nid] = state
            if self._tree.exists(nid):
                vals = list(self._tree.item(nid, "values"))
                vals[0] = self._chk_glyph(nid)
                self._tree.item(nid, values=vals)
        for nid, d in self._nodes.items():
            if d["kind"] == "item":
                self._refresh_item_glyph(d["item_id"])
        self._update_preview_label()

    def _update_preview_label(self):
        asset_nids = [nid for nid, d in self._nodes.items() if d["kind"] == "asset"]
        total      = len(asset_nids)
        selected   = sum(1 for nid in asset_nids if self._is_checked(nid))
        n_total    = sum(len(v) for v in self._items_asset_hrefs.values())
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

    # ── STAC Kontextmenü / Doppelklick ────────────────────────────────────────

    def _open_stac_browser(self, item_id: Optional[str] = None):
        url = browser_url(self._env_var.get(), item_id)
        webbrowser.open(url)
        self.clipboard_clear()
        self.clipboard_append(url)
        self._log_write(f"[STAC Browser] geöffnet & kopiert: {url}\n")

    def _on_right_click(self, event):
        row = self._tree.identify_row(event.y)
        if not row:
            return
        self._tree.selection_set(row)
        d = self._nodes.get(row, {})
        self._ctx.delete(0, "end")

        if d.get("kind") == "asset":
            href = d.get("href", "")
            if href:
                self._ctx.add_command(
                    label="URL kopieren", command=lambda h=href: self._clip(h))
                self._ctx.add_command(
                    label="Im Browser öffnen", command=lambda h=href: webbrowser.open(h))
                self._ctx.add_separator()

        iid = d.get("item_id")
        if iid:
            self._ctx.add_command(
                label="Item-ID kopieren", command=lambda i=iid: self._clip(i))
            self._ctx.add_command(
                label="Im STAC Browser öffnen",
                command=lambda i=iid: self._open_stac_browser(i))

        self._ctx.tk_popup(event.x_root, event.y_root)

    def _on_double_click(self, event):
        row = self._tree.identify_row(event.y)
        if not row:
            return
        d = self._nodes.get(row, {})
        if d.get("kind") == "asset":
            href = d.get("href", "")
            if href:
                webbrowser.open(href)

    def _clip(self, text: str):
        self.clipboard_clear()
        self.clipboard_append(text)
        self._log_write(f"[Clipboard] {text}\n")

    # ── STAC Löschung ─────────────────────────────────────────────────────────

    def _start_deletion(self):
        selected_items: Dict[str, List[str]] = {}
        for nid, d in self._nodes.items():
            if d["kind"] == "asset" and self._is_checked(nid):
                selected_items.setdefault(d["item_id"], []).append(d["asset_key"])
        if not selected_items:
            messagebox.showwarning("Nichts ausgewählt", "Keine Assets ausgewählt.")
            return
        total_assets = sum(len(v) for v in selected_items.values())
        items_fully_deleted = sum(
            1 for iid, asset_keys in selected_items.items()
            if len(asset_keys) == len(self._items_asset_hrefs.get(iid, {}))
        )
        dlg = ConfirmDialog(self, self._env_var.get(), len(selected_items),
                            total_assets, items_fully_deleted, self._dark)
        if not dlg.result:
            self._log_write("[Abbruch] Löschung durch Benutzer abgebrochen.\n")
            return
        self._del_btn.config(state="disabled")
        self._disable_search_btns()
        self._apply_theme(self._dark)
        self._progress["maximum"] = total_assets
        self._progress["value"]   = 0
        self._status_lbl.configure(text="Lösche …")
        threading.Thread(target=self._delete_worker,
                         args=(selected_items,), daemon=True).start()

    def _delete_worker(self, selected_items: Dict[str, List[str]]):
        ok_list        = []
        fail_list      = []
        items_deleted  = []
        items_del_fail = []
        done           = 0
        total          = sum(len(v) for v in selected_items.values())
        ts             = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        env            = self._env_var.get()

        # Metadaten aus ausgewählten Items für Log-Dateiname und Protokoll
        sel_objs = [it for it in self._items_preview if it["id"] in selected_items]
        _yrs = list(dict.fromkeys(stac_item_year(it)     for it in sel_objs if stac_item_year(it)))
        _ars = list(dict.fromkeys(stac_item_area(it)     for it in sel_objs if stac_item_area(it)))
        _dts = list(dict.fromkeys(stac_item_acq_date(it) for it in sel_objs if stac_item_acq_date(it)))
        meta_year    = _yrs[0] if len(_yrs) == 1 else ("multi" if _yrs else "unbekannt")
        meta_area    = _ars[0] if len(_ars) == 1 else ("multi" if _ars else "unbekannt")
        meta_stac_dt = _dts[0] if len(_dts) == 1 else (f"multi_{len(sel_objs)}" if _dts else "")
        auftragstyp  = self._auftragstyp_var.get().split("(")[0].strip()

        session_logger = self._make_session_logger("STAC", env, meta_year, meta_area, meta_stac_dt)

        self._log_write(f"\n{'='*60}\n[{ts}] STAC LÖSCHUNG GESTARTET\n{'='*60}\n")
        self._log_write(
            f"Umgebung:        {env}\n"
            f"Collection:      {COLLECTION_ID}\n"
            f"Auftragstyp:     {auftragstyp}\n"
            f"Jahr:            {meta_year}\n"
            f"AREA:            {meta_area}\n"
            f"STAC-Datetime:   {meta_stac_dt or '(unbekannt)'}\n"
            f"Items:           {len(selected_items)}  |  Assets: {total}\n\n"
        )
        session_logger.info(
            f"[STAC START] {env} | {COLLECTION_ID} | Auftragstyp: {auftragstyp} | "
            f"Jahr: {meta_year} | AREA: {meta_area} | StacDatetime: {meta_stac_dt} | "
            f"Items: {len(selected_items)} | Assets: {total}")

        for iid, asset_keys in selected_items.items():
            total_in_item = len(self._items_asset_hrefs.get(iid, {}))
            self._log_write(f"Item: {iid}  ({len(asset_keys)} von {total_in_item} Assets)\n")
            ok_for_item = 0

            for ak in asset_keys:
                http_code = 0
                try:
                    success, http_code = delete_asset(
                        self._base_url, self._auth, iid, ak)
                except Exception as exc:
                    success = False
                    self._log_write(f"  [FEHLER] {ak}: {exc}\n")
                    session_logger.error(f"[STAC FEHLER] {env}/{iid}/{ak}: {exc}")

                if success:
                    ok_for_item += 1
                    ok_list.append(f"{iid}/{ak}")
                    self._log_write(f"  [OK]   gelöscht: {ak}  (HTTP {http_code})\n")
                    session_logger.info(f"[STAC OK]   {env}/{iid}/{ak}  HTTP {http_code}")
                else:
                    fail_list.append(f"{iid}/{ak}")
                    self._log_write(f"  [FAIL] nicht gelöscht: {ak}  (HTTP {http_code})\n")
                    session_logger.warning(
                        f"[STAC FAIL] {env}/{iid}/{ak}  HTTP {http_code}")

                done += 1
                self.after(0, lambda d=done: self._progress.configure(value=d))

            # Item löschen falls alle Assets des gesamten Items erfolgreich gelöscht
            if ok_for_item == total_in_item:
                self._log_write(f"  → Item vollständig leer, wird gelöscht …\n")
                item_code = 0
                try:
                    item_ok, item_code = delete_item(self._base_url, self._auth, iid)
                except Exception as exc:
                    item_ok = False
                    self._log_write(f"  [FEHLER] Item {iid}: {exc}\n")
                    session_logger.error(f"[STAC FEHLER] Item {env}/{iid}: {exc}")

                if item_ok:
                    items_deleted.append(iid)
                    self._log_write(f"  [OK]   Item gelöscht: {iid}  (HTTP {item_code})\n")
                    session_logger.info(f"[STAC OK]   Item {env}/{iid}  HTTP {item_code}")
                else:
                    items_del_fail.append(iid)
                    self._log_write(
                        f"  [FAIL] Item nicht gelöscht: {iid}  (HTTP {item_code})\n")
                    session_logger.warning(
                        f"[STAC FAIL] Item {env}/{iid}  HTTP {item_code}")

        ts2 = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._log_write(f"\n{'='*60}\n[{ts2}] ABGESCHLOSSEN\n"
                        f"  Assets erfolgreich:    {len(ok_list)}\n"
                        f"  Assets fehlgeschlagen: {len(fail_list)}\n")
        if items_deleted or items_del_fail:
            self._log_write(f"  Items gelöscht:        {len(items_deleted)}\n"
                            f"  Items fehlgeschlagen:  {len(items_del_fail)}\n")
        self._log_write(f"{'='*60}\n")
        session_logger.info(
            f"[STAC END] Assets OK: {len(ok_list)} | FAIL: {len(fail_list)} | "
            f"Items gelöscht: {len(items_deleted)} | FAIL: {len(items_del_fail)}")

        item_summary = ""
        if items_deleted or items_del_fail:
            item_summary = (f"\n\nItems vollständig gelöscht:  {len(items_deleted)}\n"
                            f"Item-Löschung fehlgeschl.:   {len(items_del_fail)}")

        self.after(0, lambda: self._status_lbl.configure(
            text=f"Fertig: {len(ok_list)} OK  /  {len(fail_list)} Fehler"))
        self.after(0, self._enable_search_btns)
        self.after(0, lambda: messagebox.showinfo(
            "STAC Löschung abgeschlossen",
            f"Assets erfolgreich:    {len(ok_list)}\n"
            f"Assets fehlgeschlagen: {len(fail_list)}"
            f"{item_summary}",
        ))

    # ═══════════════════════════════════════════════════════════════════════════
    # GDWH – Event Handler
    # ═══════════════════════════════════════════════════════════════════════════

    def _gdwh_on_env_change(self):
        env = self._gdwh_env_var.get()
        self._gdwh_url_lbl.configure(text=GDWH_ENVIRONMENTS[env])
        self._gdwh_base_url = GDWH_ENVIRONMENTS[env]
        self._gdwh_fetch_btn.config(state="normal")
        self._apply_theme(self._dark)

    def _gdwh_fetch_imports(self):
        gds_key = self._gdwh_gds_key_var.get().strip()
        if not gds_key:
            messagebox.showwarning("Eingabe fehlt", "Bitte einen GDS-Key eingeben.")
            return
        self._gdwh_fetch_btn.config(state="disabled")
        self._gdwh_del_btn.config(state="disabled")
        self._gdwh_preview_lbl.configure(text="Lade Imports …")
        self._gdwh_clear_list()
        threading.Thread(target=self._gdwh_fetch_worker,
                         args=(gds_key,), daemon=True).start()

    def _gdwh_fetch_worker(self, gds_key: str):
        self._gdwh_current_gds_key = gds_key
        try:
            self._gdwh_log_write(f"[GDWH] Lade Imports für GDS-Key: {gds_key} …\n")
            imports = gdwh_get_imports(self._gdwh_base_url, gds_key)
            self._gdwh_imports = imports
            self._gdwh_log_write(f"[GDWH] {len(imports)} DataPackage(s) gefunden.\n")

            # Bucket scannen und Imports mit XML-Metadaten anreichern
            env = self._gdwh_env_var.get()
            bucket = gdwh_bucket_path(env, gds_key)
            self._gdwh_log_write(f"[GDWH] Scanne Bucket: {bucket} …\n")
            bucket_entries = gdwh_scan_bucket(env, gds_key, log_fn=self._gdwh_log_write)
            self._gdwh_log_write(
                f"[GDWH] {len(bucket_entries)} Ordner im Bucket gefunden.\n")

            # Jedem Import den passenden Ordner zuordnen
            enriched = []
            for imp in imports:
                match = gdwh_match_folder(imp, bucket_entries)
                enriched.append((imp, match))
                if match:
                    self._gdwh_log_write(
                        f"  → {gdwh_import_date(imp)}  ↔  {match['folder']}"
                        + (f"  [{match['area']}]" if match['area'] else "") + "\n")

            self._gdwh_enriched = enriched
            self.after(0, self._gdwh_apply_filter)
        except Exception as exc:
            self._gdwh_log_write(f"[FEHLER] {exc}\n")
            self.after(0, lambda: messagebox.showerror("GDWH Fehler", str(exc)))
            self.after(0, lambda: self._gdwh_fetch_btn.config(state="normal"))
            self.after(0, lambda: self._gdwh_preview_lbl.configure(
                text="Fehler beim Laden."))

    def _gdwh_apply_filter(self):
        if not hasattr(self, "_gdwh_enriched"):
            return
        year = self._gdwh_year_filter_var.get().strip()
        data = self._gdwh_enriched
        if year:
            def _year_matches(item):
                imp, match = item
                if match:
                    for src in (match.get("stac_datetime", ""), match.get("year", "")):
                        m = re.search(r"\b(20\d{2})\b", src)
                        if m and m.group(1) == year:
                            return True
                m = re.search(r"\b(20\d{2})\b", gdwh_import_date(imp))
                return bool(m and m.group(1) == year)
            data = [item for item in data if _year_matches(item)]
        self._gdwh_populate_list(data)

    def _gdwh_clear_list(self):
        for w in self._gdwh_list_frame.winfo_children():
            w.destroy()
        self._gdwh_selection.clear()

    def _gdwh_populate_list(self, enriched: List[Tuple]):
        self._gdwh_clear_list()
        T = DARK if self._dark else LIGHT

        if not enriched:
            tk.Label(
                self._gdwh_list_frame,
                text="Keine DataPackages gefunden.",
                font=("Segoe UI", 9, "italic"),
                bg=T["chk_bg"], fg=T["fg_dim"], padx=8, pady=8,
            ).pack(anchor="w")
            self._gdwh_fetch_btn.config(state="normal")
            self._gdwh_preview_lbl.configure(text="0 DataPackages gefunden.")
            return

        def _year_key(item):
            """Sortierschlüssel: Jahr aus stac_datetime > Ordnername > importDate."""
            imp, match = item
            if match:
                for src in (match.get("stac_datetime", ""), match.get("year", "")):
                    m = re.search(r"\b(20\d{2})\b", src)
                    if m:
                        return int(m.group(1))
            m = re.search(r"\b(20\d{2})\b", gdwh_import_date(imp))
            return int(m.group(1)) if m else 0

        for imp, match in sorted(enriched, key=_year_key, reverse=True):
            pkg_id   = gdwh_import_id(imp)
            pkg_date = gdwh_import_date(imp)
            pkg_bbox = gdwh_import_footprint_bbox(imp)

            auftragstyp   = match.get("auftragstyp", "")  if match else ""
            area          = match.get("area", "")          if match else ""
            stac_datetime = match.get("stac_datetime", "") if match else ""
            commentary    = match.get("commentary", "")    if match else ""

            # Jahr für Anzeige: stac_datetime > Ordnername > importDate
            year = ""
            if match:
                for src in (stac_datetime, match.get("year", "")):
                    m = re.search(r"\b(20\d{2})\b", src)
                    if m:
                        year = m.group(1)
                        break
            if not year:
                m = re.search(r"\b(20\d{2})\b", pkg_date)
                year = m.group(1) if m else ""

            area_color  = T["accent"] if area else T["fg_dim"]
            area_suffix = ""

            var = tk.BooleanVar(value=False)
            var.trace_add("write", lambda *_: self._gdwh_on_checkbox_change())
            self._gdwh_selection[pkg_id] = var

            # ── Zeile 1: Jahr  AREA  GDS-Key ─────────────────────────────────
            row1 = tk.Frame(self._gdwh_list_frame, bg=T["chk_bg"])
            row1.pack(fill="x", padx=6, pady=(5, 0))

            tk.Checkbutton(
                row1, variable=var,
                bg=T["chk_bg"], fg=T["fg"], selectcolor=T["input"],
                activebackground=T["chk_bg"], activeforeground=T["fg"],
            ).pack(side="left")

            tk.Label(
                row1, text=year if year else "????",
                font=("Cascadia Mono", 9, "bold"),
                bg=T["chk_bg"], fg=T["fg"], anchor="w", width=5,
            ).pack(side="left")

            tk.Label(
                row1, text=(area + area_suffix) if area else pkg_id[:12] + "…",
                font=("Cascadia Mono", 9, "bold"),
                bg=T["chk_bg"], fg=area_color if area else T["fg_dim"], anchor="w",
            ).pack(side="left")

            gds_key = getattr(self, "_gdwh_current_gds_key", "")
            if gds_key:
                tk.Label(
                    row1, text=f"    [{gds_key}]",
                    font=("Cascadia Mono", 8),
                    bg=T["chk_bg"], fg=T["fg_dim"], anchor="w",
                ).pack(side="left")

            # ── Zeile 2 (eingerückt): Auftragstyp  StacItemIdDatetime ─────────
            row2 = tk.Frame(self._gdwh_list_frame, bg=T["chk_bg"])
            row2.pack(fill="x", padx=30, pady=0)

            if auftragstyp:
                tk.Label(
                    row2, text=auftragstyp,
                    font=("Cascadia Mono", 8, "bold"),
                    bg=T["chk_bg"], fg=T["ok"], anchor="w",
                ).pack(side="left")

            if stac_datetime:
                tk.Label(
                    row2, text=("    " if auftragstyp else "") + stac_datetime,
                    font=("Segoe UI", 8),
                    bg=T["chk_bg"], fg=T["fg_dim"], anchor="w",
                ).pack(side="left")
            elif not auftragstyp and pkg_bbox:
                tk.Label(
                    row2, text=pkg_bbox,
                    font=("Segoe UI", 8),
                    bg=T["chk_bg"], fg=T["fg_dim"], anchor="w",
                ).pack(side="left")

            # ── Zeile 3 (eingerückt): Commentary  Import-Datum ──────────────
            row3 = tk.Frame(self._gdwh_list_frame, bg=T["chk_bg"])
            row3.pack(fill="x", padx=30, pady=(0, 2))

            parts3 = []
            if commentary:
                parts3.append(commentary)
            parts3.append(pkg_date)

            tk.Label(
                row3, text="   ·   ".join(parts3),
                font=("Segoe UI", 8),
                bg=T["chk_bg"], fg=T["fg_dim"], anchor="w",
            ).pack(side="left")

        self._gdwh_fetch_btn.config(state="normal")
        st = "normal" if enriched else "disabled"
        self._gdwh_sel_all_btn.config(state=st)
        self._gdwh_sel_none_btn.config(state=st)
        self._gdwh_on_checkbox_change()
        self._apply_theme(self._dark)

    def _gdwh_on_checkbox_change(self):
        self._gdwh_update_preview()

    def _gdwh_select_all(self):
        for var in self._gdwh_selection.values():
            var.set(True)

    def _gdwh_deselect_all(self):
        for var in self._gdwh_selection.values():
            var.set(False)

    def _gdwh_update_preview(self):
        total    = len(self._gdwh_selection)
        selected = sum(v.get() for v in self._gdwh_selection.values())
        self._gdwh_preview_lbl.configure(
            text=f"{total} DataPackage(s) geladen  |  {selected} ausgewählt zum Löschen"
        )
        self._gdwh_del_btn.config(
            text=f"Ausgewählte DataPackages ({selected}) löschen …",
            state="normal" if selected > 0 else "disabled",
        )
        self._apply_theme(self._dark)

    # ── GDWH Löschung ─────────────────────────────────────────────────────────

    def _gdwh_start_deletion(self):
        selected = {
            pkg_id: var.get()
            for pkg_id, var in self._gdwh_selection.items()
            if var.get()
        }
        if not selected:
            messagebox.showwarning("Nichts ausgewählt", "Keine DataPackages ausgewählt.")
            return

        gds_key = self._gdwh_gds_key_var.get().strip()
        env     = self._gdwh_env_var.get()
        email   = self._gdwh_email_var.get().strip()

        dlg = GDWHConfirmDialog(self, env, gds_key, len(selected), self._dark)
        if not dlg.result:
            self._gdwh_log_write("[Abbruch] Löschung durch Benutzer abgebrochen.\n")
            return

        self._gdwh_del_btn.config(state="disabled")
        self._gdwh_fetch_btn.config(state="disabled")
        self._gdwh_progress["maximum"] = len(selected)
        self._gdwh_progress["value"]   = 0
        self._gdwh_status_lbl.configure(text="Lösche …")
        self._apply_theme(self._dark)

        threading.Thread(
            target=self._gdwh_delete_worker,
            args=(list(selected.keys()), gds_key, email),
            daemon=True,
        ).start()

    def _gdwh_delete_worker(self, pkg_ids: List[str], gds_key: str, email: str):
        ok_list   = []
        fail_list = []
        ts        = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        env       = self._gdwh_env_var.get()

        # Metadaten aus ausgewählten Packages für Log-Dateiname und Protokoll
        enriched_map = {gdwh_import_id(imp): (imp, match)
                        for imp, match in getattr(self, "_gdwh_enriched", [])}
        sel_enriched = [enriched_map[pid] for pid in pkg_ids if pid in enriched_map]

        _yrs, _ars, _dts, _typs = [], [], [], []
        for imp, match in sel_enriched:
            year_found = False
            if match:
                for src in (match.get("stac_datetime", ""), match.get("year", "")):
                    m = re.search(r"\b(20\d{2})\b", src)
                    if m:
                        y = m.group(1)
                        if y not in _yrs:
                            _yrs.append(y)
                        year_found = True
                        break
                if match.get("area") and match["area"] not in _ars:
                    _ars.append(match["area"])
                if match.get("stac_datetime") and match["stac_datetime"] not in _dts:
                    _dts.append(match["stac_datetime"])
                if match.get("auftragstyp") and match["auftragstyp"] not in _typs:
                    _typs.append(match["auftragstyp"])
            if not year_found:
                m = re.search(r"\b(20\d{2})\b", gdwh_import_date(imp))
                if m and m.group(1) not in _yrs:
                    _yrs.append(m.group(1))

        meta_year        = _yrs[0] if len(_yrs) == 1 else ("multi" if _yrs else "unbekannt")
        meta_area        = _ars[0] if len(_ars) == 1 else ("multi" if _ars else "unbekannt")
        meta_stac_dt     = _dts[0] if len(_dts) == 1 else (f"multi_{len(pkg_ids)}" if _dts else "")
        meta_auftragstyp = _typs[0] if _typs else ""

        session_logger = self._make_session_logger("GDWH", env, meta_year, meta_area, meta_stac_dt)

        self._gdwh_log_write(
            f"\n{'='*60}\n[{ts}] GDWH LÖSCHUNG GESTARTET\n{'='*60}\n"
            f"Umgebung:        {env}\n"
            f"GDS-Key:         {gds_key}\n"
            f"Auftragstyp:     {meta_auftragstyp or '(unbekannt)'}\n"
            f"Jahr:            {meta_year}\n"
            f"AREA:            {meta_area}\n"
            f"STAC-Datetime:   {meta_stac_dt or '(unbekannt)'}\n"
            f"Packages:        {len(pkg_ids)}\n"
            f"E-Mail:          {email or '(keine)'}\n\n"
        )
        session_logger.info(
            f"[GDWH START] {env} | {gds_key} | Auftragstyp: {meta_auftragstyp} | "
            f"Jahr: {meta_year} | AREA: {meta_area} | StacDatetime: {meta_stac_dt} | "
            f"Packages: {len(pkg_ids)}")

        for i, pkg_id in enumerate(pkg_ids, 1):
            try:
                job = gdwh_delete_import(
                    self._gdwh_base_url,
                    gds_key, pkg_id, email)
                job_id     = job.get("id", "?")
                job_status = job.get("status", "gestartet")
                self._gdwh_log_write(
                    f"  [OK]  Package gelöscht: {pkg_id}\n"
                    f"        Job-ID: {job_id}  |  Status: {job_status}\n")
                session_logger.info(
                    f"[GDWH OK] {env}/{gds_key}/{pkg_id}  Job: {job_id}")
                ok_list.append(pkg_id)
            except Exception as exc:
                self._gdwh_log_write(f"  [FAIL] Package: {pkg_id}  →  {exc}\n")
                session_logger.warning(
                    f"[GDWH FAIL] {env}/{gds_key}/{pkg_id}  →  {exc}")
                fail_list.append(pkg_id)

            self.after(0, lambda v=i: self._gdwh_progress.configure(value=v))

        ts2 = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._gdwh_log_write(
            f"\n{'='*60}\n[{ts2}] ABGESCHLOSSEN\n"
            f"  Erfolgreich:    {len(ok_list)}\n"
            f"  Fehlgeschlagen: {len(fail_list)}\n"
            f"{'='*60}\n"
        )
        session_logger.info(
            f"[GDWH END] OK: {len(ok_list)} | FAIL: {len(fail_list)}")

        note = ""
        if ok_list and email:
            note = f"\n\nBenachrichtigung wird an\n{email}\ngeschickt sobald die Jobs abgeschlossen sind."

        self.after(0, lambda: self._gdwh_status_lbl.configure(
            text=f"Fertig: {len(ok_list)} OK  /  {len(fail_list)} Fehler"))
        self.after(0, lambda: self._gdwh_fetch_btn.config(state="normal"))
        self.after(0, lambda: messagebox.showinfo(
            "GDWH Löschung abgeschlossen",
            f"Erfolgreich:    {len(ok_list)}\n"
            f"Fehlgeschlagen: {len(fail_list)}"
            f"{note}",
        ))

    # ═══════════════════════════════════════════════════════════════════════════
    # Hilfsfunktionen
    # ═══════════════════════════════════════════════════════════════════════════

    def _clear_state(self):
        self._items_preview     = []
        self._items_asset_hrefs = {}
        self._items_assets      = {}
        self._nodes             = {}
        self._checked           = {}

    def _disable_search_btns(self):
        self._load_btn.config(state="disabled")

    def _enable_search_btns(self):
        self._load_btn.config(state="normal")

    def _log_write(self, text: str):
        def _do():
            self._log.configure(state="normal")
            self._log.insert("end", text)
            self._log.see("end")
            self._log.configure(state="disabled")
        self.after(0, _do)

    def _gdwh_log_write(self, text: str):
        def _do():
            self._gdwh_log.configure(state="normal")
            self._gdwh_log.insert("end", text)
            self._gdwh_log.see("end")
            self._gdwh_log.configure(state="disabled")
        self.after(0, _do)


# ─── Einstiegspunkt ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    app = KryDeleteApp()
    app.mainloop()
