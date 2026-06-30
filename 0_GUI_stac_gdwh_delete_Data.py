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

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import ctypes
import threading
import concurrent.futures
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from stac_api import (
    COLLECTION_ID, ENVIRONMENTS, AUFTRAGSTYPEN, EXT_PRESETS,
    get_item_direct, get_collection_items, filter_items,
    delete_asset, delete_item, check_asset_status,
)
from gdwh_api import (
    GDWH_ENVIRONMENTS, GDWH_GDS_KEYS,
    gdwh_get_imports, gdwh_delete_import,
    gdwh_import_id, gdwh_import_name, gdwh_import_date, gdwh_import_status,
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
        self._asset_selection:     Dict[str, Dict[str, tk.BooleanVar]] = {}
        self._asset_status_labels: Dict[str, Dict[str, tk.Label]]      = {}

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
                                       font=("Segoe UI", 9, "italic"),
                                       style="Dim.TLabel")
        self._cred_status.grid(row=0, column=6, padx=8)

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

        ttk.Label(
            sec,
            text='„Exakt" = vollständige Item-ID nötig  ·  '
                 '„Alle suchen" = Teilstring genügt, z.B. "2024-08-20" (langsam)',
            font=("Segoe UI", 8, "italic"), style="Dim.TLabel",
        ).grid(row=2, column=1, columnspan=3, sticky="w", pady=(2, 0))

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
            lambda _: self._chk_canvas.configure(scrollregion=self._chk_canvas.bbox("all")))
        self._chk_canvas.bind(
            "<Configure>",
            lambda e: self._chk_canvas.itemconfig(chk_win, width=e.width))

        self._chk_canvas.bind("<Enter>", lambda _: self._chk_canvas.bind_all(
            "<MouseWheel>",
            lambda e: self._chk_canvas.yview_scroll(-1 * (e.delta // 120), "units")))
        self._chk_canvas.bind("<Leave>", lambda _: self._canvas.bind_all(
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

        ttk.Label(sec, text="GDS-Key:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._gdwh_gds_key_var = tk.StringVar(value=GDWH_GDS_KEYS[0])
        self._gdwh_gds_combo = ttk.Combobox(
            sec, textvariable=self._gdwh_gds_key_var,
            values=GDWH_GDS_KEYS, state="readonly", width=28,
        )
        self._gdwh_gds_combo.grid(row=0, column=1, sticky="w", padx=(0, 10))

        self._gdwh_fetch_btn = ttk.Button(
            sec, text="Imports laden",
            command=self._gdwh_fetch_imports, state="normal",
        )
        self._gdwh_fetch_btn.grid(row=0, column=2)

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

        # Checkbox-Bereiche (tk-Widgets)
        self._chk_canvas.configure(bg=T["chk_bg"])
        self._chk_frame.configure(bg=T["chk_bg"])
        self._recolor_chk_widgets(T)

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

    def _recolor_chk_widgets(self, T: dict):
        def recolor(widget):
            cls = widget.winfo_class()
            if cls == "Frame":
                widget.configure(bg=T["chk_bg"])
            elif cls == "Label":
                if getattr(widget, "_is_item_header", False):
                    widget.configure(bg=T["chk_bg"], fg=T["chk_item"])
                elif getattr(widget, "_is_status", False):
                    ck = getattr(widget, "_status_color_key", "fg_dim")
                    widget.configure(bg=T["chk_bg"], fg=T[ck])
                else:
                    widget.configure(bg=T["chk_bg"], fg=T["fg_dim"])
            elif cls == "Checkbutton":
                widget.configure(
                    bg=T["chk_bg"], fg=T["fg"],
                    selectcolor=T["input"],
                    activebackground=T["chk_bg"], activeforeground=T["fg"])
            for child in widget.winfo_children():
                recolor(child)
        recolor(self._chk_frame)

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
            self._fetch_direct_btn.config(state="normal")
            self._fetch_all_btn.config(state="normal")
            self._log_write(f"[STAC Credentials] {env} – Benutzer: {username}\n")
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
        threading.Thread(target=self._fetch_direct_worker,
                         args=(item_id,), daemon=True).start()

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
        threading.Thread(target=self._fetch_all_worker,
                         args=(search_term,), daemon=True).start()

    # ── STAC Worker-Threads ───────────────────────────────────────────────────

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
                self.after(0, lambda: self._preview_lbl.configure(
                    text="Keine Items gefunden."))
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

    # ── STAC Checkbox-Bereich ─────────────────────────────────────────────────

    def _clear_chk_frame(self):
        for w in self._chk_frame.winfo_children():
            w.destroy()

    def _populate_checkboxes(self, items: List[Dict],
                              assets_map: Dict[str, List[str]]):
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
            self._asset_selection[iid]     = {}
            self._asset_status_labels[iid] = {}

            hdr = tk.Label(
                self._chk_frame, text=f"▸  {iid}",
                font=("Segoe UI", 9, "bold"),
                bg=T["chk_bg"], fg=T["chk_item"], anchor="w", padx=6,
            )
            hdr._is_item_header = True
            hdr.pack(fill="x", pady=(8, 2))
            tk.Frame(self._chk_frame, bg=T["sep"], height=1).pack(fill="x", padx=6)

            for ak in asset_keys:
                href   = self._items_asset_hrefs.get(iid, {}).get(ak, "")
                suffix = Path(href).suffix if href else ""
                var    = tk.BooleanVar(value=False)
                var.trace_add("write", lambda *_: self._on_checkbox_change())
                self._asset_selection[iid][ak] = var

                row = tk.Frame(self._chk_frame, bg=T["chk_bg"])
                row.pack(fill="x", padx=6, pady=1)
                tk.Checkbutton(
                    row, variable=var,
                    bg=T["chk_bg"], fg=T["fg"], selectcolor=T["input"],
                    activebackground=T["chk_bg"], activeforeground=T["fg"],
                ).pack(side="left")
                tk.Label(row, text=ak, font=("Cascadia Mono", 9),
                         bg=T["chk_bg"], fg=T["fg"], anchor="w").pack(side="left")
                if suffix:
                    tk.Label(row, text=f"  {suffix}", font=("Cascadia Mono", 9),
                             bg=T["chk_bg"], fg=T["fg_dim"], anchor="w").pack(side="left")

                status_lbl = tk.Label(row, text="", font=("Cascadia Mono", 9),
                                      bg=T["chk_bg"], fg=T["fg_dim"],
                                      anchor="w", width=8)
                status_lbl._is_status        = True
                status_lbl._status_color_key = "fg_dim"
                status_lbl.pack(side="left", padx=(10, 0))
                self._asset_status_labels[iid][ak] = status_lbl

        if not any_visible:
            tk.Label(
                self._chk_frame,
                text="Keine Assets nach aktuellem Filter.",
                font=("Segoe UI", 9, "italic"),
                bg=T["chk_bg"], fg=T["fg_dim"], padx=8, pady=8,
            ).pack(anchor="w")

        self._enable_search_btns()
        st = "normal" if any_visible else "disabled"
        self._sel_all_btn.config(state=st)
        self._sel_none_btn.config(state=st)
        self._check_btn.config(state=st)
        self._sel_faulty_btn.config(state="disabled")
        self._update_preview_label()
        self._apply_theme(self._dark)

    def _on_checkbox_change(self):
        self._update_preview_label()

    # ── STAC Asset-Prüfung ────────────────────────────────────────────────────

    def _check_assets(self):
        if not self._asset_status_labels:
            return
        self._check_btn.config(state="disabled")
        self._sel_faulty_btn.config(state="disabled")
        self._log_write("[Prüfung] Starte HEAD-Requests …\n")
        tasks = [
            (iid, ak, self._items_asset_hrefs.get(iid, {}).get(ak, ""))
            for iid, keys in self._asset_status_labels.items()
            for ak in keys
        ]
        T = DARK if self._dark else LIGHT
        for iid, ak, _ in tasks:
            lbl = self._asset_status_labels[iid][ak]
            lbl._status_color_key = "fg_dim"
            lbl.configure(text="  ⟳", fg=T["fg_dim"])
        threading.Thread(target=self._check_worker, args=(tasks,), daemon=True).start()

    def _check_worker(self, tasks: List[Tuple[str, str, str]]):
        T      = DARK if self._dark else LIGHT
        errors = 0

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

        self._log_write(f"[Prüfung] {len(tasks)} Assets geprüft — {errors} fehlerhaft.\n")
        self.after(0, lambda: self._check_btn.config(state="normal"))
        if errors > 0:
            self.after(0, lambda: self._sel_faulty_btn.config(state="normal"))

    def _select_faulty_assets(self):
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

    # ── STAC Löschung ─────────────────────────────────────────────────────────

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

        self._log_write(f"\n{'='*60}\n[{ts}] STAC LÖSCHUNG GESTARTET\n{'='*60}\n")
        self._log_write(f"Umgebung:   {env}\nCollection: {COLLECTION_ID}\n"
                        f"Items: {len(selected_items)}  |  Assets: {total}\n\n")
        self._file_logger.info(
            f"[STAC START] {env} | {COLLECTION_ID} | "
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
                    self._file_logger.error(f"[STAC FEHLER] {env}/{iid}/{ak}: {exc}")

                if success:
                    ok_for_item += 1
                    ok_list.append(f"{iid}/{ak}")
                    self._log_write(f"  [OK]   gelöscht: {ak}  (HTTP {http_code})\n")
                    self._file_logger.info(f"[STAC OK]   {env}/{iid}/{ak}  HTTP {http_code}")
                else:
                    fail_list.append(f"{iid}/{ak}")
                    self._log_write(f"  [FAIL] nicht gelöscht: {ak}  (HTTP {http_code})\n")
                    self._file_logger.warning(
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
                    self._file_logger.error(f"[STAC FEHLER] Item {env}/{iid}: {exc}")

                if item_ok:
                    items_deleted.append(iid)
                    self._log_write(f"  [OK]   Item gelöscht: {iid}  (HTTP {item_code})\n")
                    self._file_logger.info(f"[STAC OK]   Item {env}/{iid}  HTTP {item_code}")
                else:
                    items_del_fail.append(iid)
                    self._log_write(
                        f"  [FAIL] Item nicht gelöscht: {iid}  (HTTP {item_code})\n")
                    self._file_logger.warning(
                        f"[STAC FAIL] Item {env}/{iid}  HTTP {item_code}")

        ts2 = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._log_write(f"\n{'='*60}\n[{ts2}] ABGESCHLOSSEN\n"
                        f"  Assets erfolgreich:    {len(ok_list)}\n"
                        f"  Assets fehlgeschlagen: {len(fail_list)}\n")
        if items_deleted or items_del_fail:
            self._log_write(f"  Items gelöscht:        {len(items_deleted)}\n"
                            f"  Items fehlgeschlagen:  {len(items_del_fail)}\n")
        self._log_write(f"{'='*60}\n")
        self._file_logger.info(
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
        try:
            self._gdwh_log_write(f"[GDWH] Lade Imports für GDS-Key: {gds_key} …\n")
            imports = gdwh_get_imports(self._gdwh_base_url, gds_key)
            self._gdwh_imports = imports
            self._gdwh_log_write(f"[GDWH] {len(imports)} DataPackage(s) gefunden.\n")
            self.after(0, lambda: self._gdwh_populate_list(imports))
        except Exception as exc:
            self._gdwh_log_write(f"[FEHLER] {exc}\n")
            self.after(0, lambda: messagebox.showerror("GDWH Fehler", str(exc)))
            self.after(0, lambda: self._gdwh_fetch_btn.config(state="normal"))
            self.after(0, lambda: self._gdwh_preview_lbl.configure(
                text="Fehler beim Laden."))

    def _gdwh_clear_list(self):
        for w in self._gdwh_list_frame.winfo_children():
            w.destroy()
        self._gdwh_selection.clear()

    def _gdwh_populate_list(self, imports: List[Dict]):
        self._gdwh_clear_list()
        T = DARK if self._dark else LIGHT

        if not imports:
            tk.Label(
                self._gdwh_list_frame,
                text="Keine DataPackages gefunden.",
                font=("Segoe UI", 9, "italic"),
                bg=T["chk_bg"], fg=T["fg_dim"], padx=8, pady=8,
            ).pack(anchor="w")
            self._gdwh_fetch_btn.config(state="normal")
            self._gdwh_preview_lbl.configure(text="0 DataPackages gefunden.")
            return

        for imp in imports:
            pkg_id   = gdwh_import_id(imp)
            pkg_name = gdwh_import_name(imp)
            pkg_date = gdwh_import_date(imp)
            pkg_stat = gdwh_import_status(imp)

            var = tk.BooleanVar(value=False)
            var.trace_add("write", lambda *_: self._gdwh_on_checkbox_change())
            self._gdwh_selection[pkg_id] = var

            row = tk.Frame(self._gdwh_list_frame, bg=T["chk_bg"])
            row.pack(fill="x", padx=6, pady=2)

            tk.Checkbutton(
                row, variable=var,
                bg=T["chk_bg"], fg=T["fg"], selectcolor=T["input"],
                activebackground=T["chk_bg"], activeforeground=T["fg"],
            ).pack(side="left")

            # Name / ID
            name_lbl = tk.Label(
                row, text=pkg_name,
                font=("Cascadia Mono", 9, "bold"),
                bg=T["chk_bg"], fg=T["fg"], anchor="w",
            )
            name_lbl._is_pkg_header = True
            name_lbl.pack(side="left")

            # Datum
            if pkg_date != "–":
                tk.Label(
                    row, text=f"    {pkg_date}",
                    font=("Cascadia Mono", 9),
                    bg=T["chk_bg"], fg=T["fg_dim"], anchor="w",
                ).pack(side="left")

            # Status
            if pkg_stat:
                status_color = "ok" if pkg_stat.lower() in (
                    "completed", "done", "success", "finished") else "hint"
                tk.Label(
                    row, text=f"    [{pkg_stat}]",
                    font=("Cascadia Mono", 9),
                    bg=T["chk_bg"],
                    fg=T[status_color], anchor="w",
                ).pack(side="left")

            # ID (gedimmt, falls Name bereits vorhanden)
            if pkg_name != pkg_id:
                tk.Label(
                    row, text=f"    ID: {pkg_id}",
                    font=("Cascadia Mono", 8),
                    bg=T["chk_bg"], fg=T["fg_dim"], anchor="w",
                ).pack(side="left")

        self._gdwh_fetch_btn.config(state="normal")
        st = "normal" if imports else "disabled"
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

        self._gdwh_log_write(
            f"\n{'='*60}\n[{ts}] GDWH LÖSCHUNG GESTARTET\n{'='*60}\n"
            f"Umgebung:   {env}\n"
            f"GDS-Key:    {gds_key}\n"
            f"Packages:   {len(pkg_ids)}\n"
            f"E-Mail:     {email or '(keine)'}\n\n"
        )
        self._file_logger.info(
            f"[GDWH START] {env} | {gds_key} | Packages: {len(pkg_ids)}")

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
                self._file_logger.info(
                    f"[GDWH OK] {env}/{gds_key}/{pkg_id}  Job: {job_id}")
                ok_list.append(pkg_id)
            except Exception as exc:
                self._gdwh_log_write(f"  [FAIL] Package: {pkg_id}  →  {exc}\n")
                self._file_logger.warning(
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
        self._file_logger.info(
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
