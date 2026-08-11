from __future__ import annotations

import json
import os
import queue
import threading
import ctypes
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from .assets import ASSET_OWNERS, AssetValidationError, register_manual_asset
from .gui_model import ReviewModel
from .naming import DOCUMENT_TYPE_CHOICES
from .processor import analyze_pdf
from .review import (
    ApprovalError,
    ReviewValidationError,
    apply_correction,
    approve_document,
    load_review_session,
    mark_duplicate_document,
    mark_not_dot_document,
    record_correction,
    restore_archived_document,
    save_review_session,
)


APP_NAME = "DocMarshal"
ASSET_DIR = Path(__file__).resolve().parents[1] / "assets"
APP_ICON_PATH = ASSET_DIR / "docmarshal.ico"
APP_ICON_PNG_PATH = ASSET_DIR / "docmarshal-icon.png"

DARK_THEME = {
    "window": "#07111F",
    "surface": "#0D1B2A",
    "surface_hover": "#132A42",
    "input": "#081523",
    "border": "#204467",
    "border_focus": "#2F81F7",
    "accent": "#F59E0B",
    "accent_hover": "#FFB21A",
    "text": "#F0F6FC",
    "muted": "#8DA6C0",
    "success": "#3FB950",
    "warning": "#D29922",
    "danger": "#F85149",
}

DOCUMENT_TYPE_LABELS = {
    "DOT": "DOT Inspection",
    "RP": "Repair / Maintenance",
    "REG": "Registration",
    "TITLE": "Title",
    "CERTORIGIN": "Certificate of Origin",
    "INS": "Insurance",
}

DISPLAY_ACRONYMS = {"DOT", "PDF", "VIN", "OCR", "ID", "RP", "REG", "INS"}


class DotReviewApp:
    def __init__(self, root: tk.Tk, config: dict):
        self.root = root
        self.config = config
        self.incoming = Path(config["scan_incoming"])
        self.processed = Path(config["scan_processed"])
        self.exceptions = Path(config["scan_exceptions"])
        self.review_folder = Path(config["scan_review"])
        self.database = Path(config["fleet_database"])
        self.manual_assets_registry = Path(
            config.get("manual_assets_registry", self.database.parent / "manual_assets.json")
        )
        self.unit_root = Path(config["unit_folders_root"])
        self.farm_unit_root = Path(config["farm_asset_folders_root"])
        self.session_path = self.review_folder / "active_review.json"
        self.audit_path = self.review_folder / "audit.jsonl"
        self.events: queue.Queue = queue.Queue()
        self.row_sources: dict[str, str] = {}
        self.scanning = False
        self.session_load_error = None

        initial_results = []
        if self.session_path.exists():
            try:
                initial_results = load_review_session(self.session_path)
            except Exception as error:
                self.session_load_error = str(error)
                initial_results = []
        self.model = ReviewModel(initial_results)

        self.root.title(APP_NAME)
        self.root.geometry("1440x900")
        self.root.minsize(1180, 900)
        self.root.configure(background=DARK_THEME["window"])
        self._apply_app_icon()
        self._build_ui()
        self._apply_dark_title_bar()
        self._refresh()
        self.root.after(100, self._poll_events)
        if self.session_load_error:
            self.root.after(0, self._show_session_error)

    def _show_session_error(self) -> None:
        messagebox.showerror(
            "Review session could not be loaded",
            "The active review session is damaged or unreadable. Scanning is disabled so it is not overwritten.\n\n"
            f"File: {self.session_path}\n\nError: {self.session_load_error}\n\nContact IT to recover or archive it.",
        )
        self.status_var.set("Scanning disabled: active review session could not be loaded.")

    def _apply_app_icon(self) -> None:
        self.app_icon_image = None
        self.header_icon_image = None
        try:
            self.app_icon_image = tk.PhotoImage(file=str(APP_ICON_PNG_PATH))
            self.header_icon_image = self.app_icon_image.subsample(8, 8)
            self.root.iconphoto(True, self.app_icon_image)
            if os.name == "nt":
                self.root.iconbitmap(default=str(APP_ICON_PATH))
        except (OSError, tk.TclError):
            self.app_icon_image = None
            self.header_icon_image = None

    @staticmethod
    def _humanize_user_text(value: object) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if "_" not in text and "-" not in text and not text.isupper():
            return text
        words = text.replace("-", "_").split("_")
        return " ".join(
            word.upper() if word.upper() in DISPLAY_ACRONYMS else word.capitalize()
            for word in words
            if word
        )

    @staticmethod
    def _document_type_label(value: object) -> str:
        code = str(value or "").strip().upper()
        return DOCUMENT_TYPE_LABELS.get(code, DotReviewApp._humanize_user_text(code))

    @staticmethod
    def _document_type_code(value: object) -> str:
        text = str(value or "").strip()
        for code, label in DOCUMENT_TYPE_LABELS.items():
            if text == label:
                return code
        return text.upper()

    def _apply_dark_title_bar(self) -> None:
        if os.name != "nt":
            return
        try:
            self.root.update_idletasks()
            window_id = ctypes.windll.user32.GetParent(self.root.winfo_id())
            enabled = ctypes.c_int(1)
            for attribute in (20, 19):
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    window_id,
                    attribute,
                    ctypes.byref(enabled),
                    ctypes.sizeof(enabled),
                ) == 0:
                    break
        except (AttributeError, OSError):
            pass

    def _configure_theme(self) -> ttk.Style:
        theme = DARK_THEME
        style = ttk.Style(self.root)
        style.theme_use("clam")
        self.root.option_add("*Font", "{Segoe UI} 10")
        self.root.option_add("*TCombobox*Listbox.background", theme["input"])
        self.root.option_add("*TCombobox*Listbox.foreground", theme["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", theme["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", theme["text"])

        style.configure("TFrame", background=theme["window"])
        style.configure(
            "Glass.TFrame",
            background=theme["surface"],
            bordercolor=theme["border"],
            lightcolor=theme["border"],
            darkcolor=theme["border"],
            borderwidth=1,
            relief="solid",
        )
        style.configure("GlassContent.TFrame", background=theme["surface"], borderwidth=0)
        style.configure("TLabel", background=theme["window"], foreground=theme["text"])
        style.configure("Glass.TLabel", background=theme["surface"], foreground=theme["text"])
        style.configure(
            "Header.TLabel",
            background=theme["surface"],
            foreground=theme["text"],
            font=("Segoe UI Variable Display", 19, "bold"),
        )
        style.configure(
            "Subtitle.TLabel",
            background=theme["surface"],
            foreground=theme["muted"],
            font=("Segoe UI", 9),
        )
        style.configure(
            "Field.TLabel",
            background=theme["surface"],
            foreground=theme["muted"],
            font=("Segoe UI", 9, "bold"),
        )
        style.configure(
            "Count.TLabel",
            background=theme["surface"],
            foreground=theme["text"],
            font=("Segoe UI", 10, "bold"),
            padding=(14, 9),
            anchor="center",
            bordercolor=theme["border"],
            borderwidth=1,
            relief="solid",
        )
        style.configure(
            "Status.TLabel",
            background=theme["surface"],
            foreground=theme["muted"],
            padding=(14, 9),
            bordercolor=theme["border"],
            borderwidth=1,
            relief="solid",
        )
        style.configure(
            "Glass.TLabelframe",
            background=theme["surface"],
            bordercolor=theme["border"],
            lightcolor=theme["border"],
            darkcolor=theme["border"],
            borderwidth=1,
            relief="solid",
        )
        style.configure(
            "Glass.TLabelframe.Label",
            background=theme["surface"],
            foreground=theme["text"],
            font=("Segoe UI", 11, "bold"),
        )

        style.configure(
            "TEntry",
            fieldbackground=theme["input"],
            foreground=theme["text"],
            insertcolor=theme["text"],
            bordercolor=theme["border"],
            lightcolor=theme["border"],
            darkcolor=theme["border"],
            padding=(9, 7),
        )
        style.map(
            "TEntry",
            bordercolor=[("focus", theme["border_focus"])],
            lightcolor=[("focus", theme["border_focus"])],
            darkcolor=[("focus", theme["border_focus"])],
        )
        style.configure(
            "TCombobox",
            fieldbackground=theme["input"],
            background=theme["input"],
            foreground=theme["text"],
            arrowcolor=theme["muted"],
            bordercolor=theme["border"],
            lightcolor=theme["border"],
            darkcolor=theme["border"],
            padding=(8, 6),
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", theme["input"]), ("focus", theme["input"])],
            foreground=[("readonly", theme["text"]), ("focus", theme["text"])],
            bordercolor=[("focus", theme["border_focus"])],
            arrowcolor=[("active", theme["text"])],
        )

        style.configure(
            "TButton",
            background=theme["surface_hover"],
            foreground=theme["text"],
            bordercolor=theme["border"],
            lightcolor=theme["border"],
            darkcolor=theme["border"],
            focusthickness=2,
            focuscolor=theme["border_focus"],
            padding=(12, 8),
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "TButton",
            background=[("active", theme["border"]), ("pressed", theme["input"]), ("disabled", theme["surface"])],
            foreground=[("disabled", theme["muted"])],
            bordercolor=[("focus", theme["border_focus"]), ("active", theme["border_focus"])],
        )
        style.configure(
            "Primary.TButton",
            background=theme["accent"],
            foreground=theme["window"],
            bordercolor=theme["accent"],
            lightcolor=theme["accent"],
            darkcolor=theme["accent"],
            padding=(15, 9),
        )
        style.map(
            "Primary.TButton",
            background=[("active", theme["accent_hover"]), ("pressed", "#D97706"), ("disabled", theme["border"])],
            foreground=[("disabled", theme["muted"])],
            bordercolor=[("focus", theme["border_focus"]), ("active", theme["accent_hover"])],
        )
        style.configure("Warning.TButton", foreground="#FFD98A")
        style.map("Warning.TButton", bordercolor=[("active", theme["warning"]), ("focus", theme["warning"])])
        style.configure("Danger.TButton", foreground="#FF9B96")
        style.map("Danger.TButton", bordercolor=[("active", theme["danger"]), ("focus", theme["danger"])])

        style.configure(
            "Treeview",
            background=theme["input"],
            fieldbackground=theme["input"],
            foreground=theme["text"],
            bordercolor=theme["border"],
            lightcolor=theme["border"],
            darkcolor=theme["border"],
            rowheight=30,
            font=("Segoe UI", 9),
        )
        style.map(
            "Treeview",
            background=[("selected", theme["accent"])],
            foreground=[("selected", theme["window"])],
        )
        style.configure(
            "Treeview.Heading",
            background=theme["surface_hover"],
            foreground=theme["text"],
            bordercolor=theme["border"],
            lightcolor=theme["border"],
            darkcolor=theme["border"],
            padding=(8, 8),
            font=("Segoe UI", 9, "bold"),
        )
        style.map("Treeview.Heading", background=[("active", theme["border"])])
        style.configure("TProgressbar", troughcolor=theme["input"], background=theme["accent"], bordercolor=theme["border"])
        for scrollbar_style in ("TScrollbar", "Vertical.TScrollbar", "Horizontal.TScrollbar"):
            style.configure(
                scrollbar_style,
                background=theme["surface_hover"],
                troughcolor=theme["input"],
                bordercolor=theme["border"],
                lightcolor=theme["border"],
                darkcolor=theme["border"],
                arrowcolor=theme["muted"],
            )
            style.map(
                scrollbar_style,
                background=[("active", theme["border"]), ("pressed", theme["accent"])],
            )
        style.configure("TPanedwindow", background=theme["window"], sashwidth=8)
        return style

    def _build_ui(self) -> None:
        self._configure_theme()

        header = ttk.Frame(self.root, style="Glass.TFrame", padding=(18, 14))
        header.pack(fill="x", padx=16, pady=(16, 10))
        if self.header_icon_image is not None:
            ttk.Label(header, image=self.header_icon_image, style="Glass.TLabel").pack(side="left", padx=(0, 12))
        title_group = ttk.Frame(header, style="GlassContent.TFrame")
        title_group.pack(side="left")
        ttk.Label(title_group, text=APP_NAME, style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            title_group,
            text="Review, verify, and file fleet documents safely",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 0))
        self.scan_button = ttk.Button(
            header,
            text="Scan Incoming Documents",
            command=self.scan_incoming,
            style="Primary.TButton",
        )
        self.scan_button.pack(side="right", padx=(12, 0))

        counters = ttk.Frame(self.root)
        counters.pack(fill="x", padx=16, pady=(0, 10))
        self.count_labels = {}
        for key, title in (
            ("total", "Total"),
            ("ready", "Ready"),
            ("needs_review", "Needs Review"),
            ("approved", "Approved"),
            ("failed", "Failed"),
            ("duplicate", "Duplicates"),
            ("not_dot", "Not DOT"),
        ):
            label = ttk.Label(counters, text=f"{title}  •  0", style="Count.TLabel")
            label.pack(side="left", fill="x", expand=True, padx=(0, 8 if key != "not_dot" else 0))
            self.count_labels[key] = (label, title)

        progress_frame = ttk.Frame(self.root, style="Glass.TFrame", padding=(14, 9))
        progress_frame.pack(fill="x", padx=16, pady=(0, 10))
        self.progress = ttk.Progressbar(progress_frame, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True)
        self.progress_text = ttk.Label(progress_frame, text="Ready", style="Glass.TLabel")
        self.progress_text.pack(side="left", padx=(10, 0))

        toolbar = ttk.Frame(self.root, style="Glass.TFrame", padding=(12, 10))
        toolbar.pack(fill="x", padx=16, pady=(0, 10))
        ttk.Label(toolbar, text="Show Documents", style="Field.TLabel").pack(side="left")
        self.filter_var = tk.StringVar(value="Active")
        filter_box = ttk.Combobox(
            toolbar,
            textvariable=self.filter_var,
            values=("Active", "All", "Ready", "Needs Review", "Approved", "Duplicates", "Not DOT", "Failed"),
            state="readonly",
            width=18,
        )
        filter_box.pack(side="left", padx=(6, 12))
        filter_box.bind("<<ComboboxSelected>>", lambda _event: self._refresh_table())
        ttk.Button(toolbar, text="Open PDF", command=self.open_pdf).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Open Destination Folder", command=self.open_destination).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Add New Asset", command=self.add_new_asset).pack(side="left", padx=(14, 3))
        ttk.Button(toolbar, text="Restore to Active", command=self.restore_selected).pack(side="left", padx=3)

        pane = ttk.Panedwindow(self.root, orient="vertical")
        pane.pack(fill="both", expand=True, padx=16, pady=(0, 10))

        table_frame = ttk.Frame(pane, style="Glass.TFrame", padding=1)
        pane.add(table_frame, weight=3)
        columns = ("file", "status", "unit", "owner", "type", "date", "filename", "reason")
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        headings = {
            "file": "Source File",
            "status": "Status",
            "unit": "Unit",
            "owner": "Ownership",
            "type": "Document Type",
            "date": "Controlling Date",
            "filename": "Proposed Filename",
            "reason": "Review Notes",
        }
        widths = {"file": 145, "status": 100, "unit": 60, "owner": 95, "type": 125, "date": 130, "filename": 190, "reason": 270}
        for column in columns:
            self.table.heading(column, text=headings[column])
            self.table.column(
                column,
                width=widths[column],
                minwidth=widths[column],
                stretch=column == "reason",
                anchor="w",
            )
        vertical_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        horizontal_scroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.table.xview)
        self.table.configure(yscrollcommand=vertical_scroll.set, xscrollcommand=horizontal_scroll.set)
        self.table.grid(row=0, column=0, sticky="nsew")
        vertical_scroll.grid(row=0, column=1, sticky="ns")
        horizontal_scroll.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.table.bind("<<TreeviewSelect>>", self._on_selection)
        self.table.bind("<Double-1>", lambda _event: self.open_pdf())
        self.table.tag_configure("needs_review", background="#2A2213", foreground="#FFD98A")
        self.table.tag_configure("ready_for_review", background="#13251B", foreground="#8FE09B")
        self.table.tag_configure("approved", background="#112338", foreground="#9CCBFF")
        self.table.tag_configure("failed", background="#30181B", foreground="#FFAAA5")
        self.table.tag_configure("duplicate", background="#1B202A", foreground="#B8C2D1")
        self.table.tag_configure("not_dot", background="#1B202A", foreground="#B8C2D1")

        review_panel = ttk.LabelFrame(
            pane,
            text="Review Selected Document",
            style="Glass.TLabelframe",
            padding=(14, 12),
        )
        pane.add(review_panel, weight=1)
        self.unit_var = tk.StringVar()
        self.type_var = tk.StringVar()
        self.date_var = tk.StringVar()
        self.page_var = tk.StringVar()
        self.destination_var = tk.StringVar()
        self.reason_var = tk.StringVar()

        ttk.Label(review_panel, text="Unit", style="Field.TLabel").grid(row=0, column=0, sticky="w")
        unit_entry = ttk.Entry(review_panel, textvariable=self.unit_var, width=14)
        unit_entry.grid(row=1, column=0, sticky="ew", padx=(0, 10))
        ttk.Label(review_panel, text="Document Type", style="Field.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Combobox(
            review_panel,
            textvariable=self.type_var,
            values=tuple(DOCUMENT_TYPE_LABELS[code] for code in DOCUMENT_TYPE_CHOICES),
            state="readonly",
            width=22,
        ).grid(row=1, column=1, sticky="ew", padx=(0, 10))
        ttk.Label(review_panel, text="Controlling Date  •  M/D/YY or M/D/YYYY", style="Field.TLabel").grid(row=0, column=2, sticky="w")
        date_entry = ttk.Entry(review_panel, textvariable=self.date_var, width=24)
        date_entry.grid(row=1, column=2, sticky="ew", padx=(0, 10))
        self._bind_approval_on_enter(unit_entry, date_entry)
        ttk.Label(review_panel, text="Additional Page", style="Field.TLabel").grid(row=0, column=3, sticky="w")
        ttk.Combobox(
            review_panel,
            textvariable=self.page_var,
            values=("", "PG2", "PG3", "PG4", "PG5", "PG6", "PG7", "PG8", "PG9", "PG10"),
            width=9,
        ).grid(row=1, column=3, sticky="ew", padx=(0, 10))
        action_bar = ttk.Frame(review_panel, style="GlassContent.TFrame")
        action_bar.grid(row=2, column=0, columnspan=8, sticky="e", pady=(12, 2))
        ttk.Button(action_bar, text="Save Correction", command=self.save_correction).pack(side="left", padx=(0, 8))
        ttk.Button(
            action_bar,
            text="Approve and File Copy",
            command=self.approve_selected,
            style="Primary.TButton",
        ).pack(side="left")
        ttk.Button(action_bar, text="Mark Duplicate", command=self.mark_selected_duplicate, style="Warning.TButton").pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(action_bar, text="Not a DOT Document", command=self.mark_selected_not_dot, style="Danger.TButton").pack(
            side="left", padx=(8, 0)
        )
        ttk.Label(review_panel, text="Review Notes", style="Field.TLabel").grid(row=3, column=0, sticky="nw", pady=(8, 0))
        ttk.Label(review_panel, textvariable=self.reason_var, wraplength=1100, style="Glass.TLabel").grid(
            row=3, column=1, columnspan=7, sticky="w", pady=(8, 0)
        )
        ttk.Label(review_panel, text="Destination", style="Field.TLabel").grid(row=4, column=0, sticky="nw", pady=(7, 0))
        ttk.Label(review_panel, textvariable=self.destination_var, wraplength=1100, style="Glass.TLabel").grid(
            row=4, column=1, columnspan=7, sticky="w", pady=(7, 0)
        )
        review_panel.columnconfigure(2, weight=1)

        self.status_var = tk.StringVar(value="Ready. Click Scan Incoming Documents to analyze PDFs.")
        ttk.Label(self.root, textvariable=self.status_var, style="Status.TLabel", anchor="w").pack(
            fill="x", padx=16, pady=(0, 16)
        )

    def _bind_approval_on_enter(self, *widgets) -> None:
        for widget in widgets:
            widget.bind("<Return>", self._approve_from_enter)
            widget.bind("<KP_Enter>", self._approve_from_enter)

    def _approve_from_enter(self, _event=None) -> str:
        self.approve_selected()
        return "break"

    def scan_incoming(self) -> None:
        if self.scanning:
            return
        if self.session_load_error:
            self._show_session_error()
            return
        if not self.incoming.is_dir():
            messagebox.showerror("Incoming folder unavailable", f"Cannot access:\n{self.incoming}")
            return
        if not self.database.is_file():
            messagebox.showerror("Fleet database unavailable", f"Cannot access:\n{self.database}")
            return
        files = sorted(self.incoming.glob("*.pdf"), key=lambda path: path.name.lower())
        if not files:
            messagebox.showinfo("No documents", f"No PDFs were found in:\n{self.incoming}")
            return

        retained = [
            item
            for item in self.model.results
            if (
                item.get("status") == "approved"
                and Path(item.get("source_file", "")).exists()
            )
            or (
                item.get("status") == "duplicate"
                and Path(item.get("duplicate_archived_file", "")).exists()
            )
            or (
                item.get("status") == "not_dot"
                and Path(item.get("not_dot_archived_file", "")).exists()
            )
        ]
        self.model = ReviewModel(retained)
        self.scanning = True
        self.scan_button.configure(state="disabled")
        self.progress.configure(maximum=len(files), value=0)
        self.progress_text.configure(text=f"0 of {len(files)}")
        self.status_var.set("Scanning incoming documents...")
        self._refresh()
        threading.Thread(target=self._scan_worker, args=(files,), daemon=True).start()

    def _scan_worker(self, files: list[Path]) -> None:
        approved_sources = {
            item.get("source_file"): item for item in self.model.results if item.get("status") == "approved"
        }
        for index, pdf_path in enumerate(files, start=1):
            existing = approved_sources.get(str(pdf_path))
            if existing:
                result = existing
            else:
                try:
                    result = analyze_pdf(
                        pdf_path,
                        self.database,
                        self.unit_root,
                        farm_asset_folders_root=self.farm_unit_root,
                    )
                except Exception as error:
                    result = {
                        "source_file": str(pdf_path),
                        "status": "failed",
                        "reasons": [str(error)],
                        "unit": None,
                        "document_type": None,
                        "controlling_date": None,
                        "page_suffix": None,
                        "proposed_filename": None,
                        "proposed_destination": None,
                    }
            self.events.put(("result", result, index, len(files)))
        self.events.put(("scan_done",))

    def _poll_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "result":
                    _, result, index, total = event
                    self.model.replace(result)
                    self.progress.configure(value=index)
                    self.progress_text.configure(text=f"{index} of {total}")
                    self.status_var.set(f"Analyzed {Path(result['source_file']).name}")
                    self._refresh()
                elif event[0] == "scan_done":
                    self._finish_scan()
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _finish_scan(self) -> None:
        self.scanning = False
        self.scan_button.configure(state="normal")
        save_review_session(self.session_path, self.model.results)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        history_path = self.review_folder / f"GUI_review_{timestamp}.json"
        save_review_session(history_path, self.model.results)
        counts = self.model.counts()
        self.status_var.set(
            f"Scan complete: {counts['ready']} ready, {counts['needs_review']} need review, "
            f"{counts['approved']} approved, {counts['failed']} failed."
        )
        self._refresh()

    def _refresh(self) -> None:
        counts = self.model.counts()
        for key, value in counts.items():
            label, title = self.count_labels[key]
            label.configure(text=f"{title}  •  {value}")
        self._refresh_table()

    def _refresh_table(self) -> None:
        selected_source = self._selected_source()
        self.table.delete(*self.table.get_children())
        self.row_sources.clear()
        for index, result in enumerate(self.model.filtered(self.filter_var.get())):
            source = result.get("source_file", "")
            item_id = f"row-{index}"
            status = result.get("status", "")
            self.table.insert(
                "",
                "end",
                iid=item_id,
                values=(
                    Path(source).name,
                    self._humanize_user_text(status),
                    result.get("unit") or "",
                    result.get("asset_owner") or "",
                    self._document_type_label(result.get("document_type")),
                    self._display_date(result.get("controlling_date")),
                    result.get("proposed_filename") or "",
                    "; ".join(self._humanize_user_text(reason) for reason in result.get("reasons", [])),
                ),
                tags=(status,),
            )
            self.row_sources[item_id] = source
            if source == selected_source:
                self.table.selection_set(item_id)

    @staticmethod
    def _display_date(value: str | None) -> str:
        if not value:
            return ""
        try:
            return datetime.strptime(value, "%Y-%m-%d").strftime("%m/%d/%Y")
        except ValueError:
            return value

    def _selected_source(self) -> str | None:
        selected = self.table.selection()
        return self.row_sources.get(selected[0]) if selected else None

    def _selected_result(self) -> dict | None:
        source = self._selected_source()
        if source is None:
            return None
        return next((item for item in self.model.results if item.get("source_file") == source), None)

    def _on_selection(self, _event=None) -> None:
        result = self._selected_result()
        if not result:
            return
        self.unit_var.set(result.get("unit") or "")
        self.type_var.set(self._document_type_label(result.get("document_type")))
        self.date_var.set(self._display_date(result.get("controlling_date")))
        self.page_var.set(result.get("page_suffix") or "")
        self.reason_var.set(
            "; ".join(self._humanize_user_text(reason) for reason in result.get("reasons", []))
            or "No unresolved issues."
        )
        self.destination_var.set(result.get("proposed_destination") or "Not available until all fields are valid.")

    def save_correction(self) -> None:
        before = self._selected_result()
        if not before:
            messagebox.showinfo("Select a document", "Select a document to correct.")
            return
        if before.get("status") == "approved":
            messagebox.showwarning("Already approved", "Approved records cannot be edited in this session.")
            return
        try:
            after = apply_correction(
                before,
                unit=self.unit_var.get(),
                document_type=self._document_type_code(self.type_var.get()),
                controlling_date=self.date_var.get(),
                page_suffix=self.page_var.get(),
                unit_folders_root=self.unit_root,
                farm_asset_folders_root=self.farm_unit_root,
                database_path=self.database,
                audit_path=self.audit_path,
            )
        except ReviewValidationError as error:
            messagebox.showerror("Correction not saved", str(error))
            return
        record_correction(self.audit_path, before, after)
        self.model.replace(after)
        save_review_session(self.session_path, self.model.results)
        self.status_var.set(f"Correction saved for {Path(after['source_file']).name}.")
        self._refresh()
        self._select_source(after["source_file"])

    def add_new_asset(self) -> None:
        selected = self._selected_result()
        if not selected:
            messagebox.showinfo("Select a document", "Select the document for the new asset first.")
            return
        if self.scanning:
            messagebox.showwarning("Scan in progress", "Wait for the current scan to finish before adding an asset.")
            return
        if selected.get("status") == "approved":
            messagebox.showwarning("Already approved", "A new asset cannot be added from an approved document.")
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Add New Asset")
        dialog.configure(background=DARK_THEME["window"])
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.grab_set()

        ttk.Label(
            dialog,
            text=f"Create a fleet record for: {Path(selected['source_file']).name}",
            font=("Segoe UI", 11, "bold"),
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=14, pady=(14, 10))
        ttk.Label(
            dialog,
            text="The unit number, ownership, and at least a VIN/serial number or plate/tag are required.",
        ).grid(row=1, column=0, columnspan=4, sticky="w", padx=14, pady=(0, 10))

        definitions = (
            ("unit", "Unit Number", self.unit_var.get()),
            ("unit_type", "Unit Type", ""),
            ("year", "Year", ""),
            ("make", "Make", ""),
            ("model", "Model", ""),
            ("vehicle_type", "Vehicle/Equipment Type", ""),
            ("plate", "License Plate / Tag", ""),
            ("vin", "VIN / Serial Number", ""),
            ("fuel_type", "Fuel Type", ""),
        )
        variables = {name: tk.StringVar(value=value) for name, _label, value in definitions}
        owner_var = tk.StringVar(value=ASSET_OWNERS[0])

        ttk.Label(dialog, text="Ownership").grid(row=2, column=0, sticky="w", padx=(14, 6), pady=4)
        ttk.Combobox(
            dialog,
            textvariable=owner_var,
            values=ASSET_OWNERS,
            state="readonly",
            width=25,
        ).grid(row=2, column=1, sticky="ew", padx=(0, 14), pady=4)

        for index, (name, label, _value) in enumerate(definitions):
            row = 3 + index // 2
            column = (index % 2) * 2
            ttk.Label(dialog, text=label).grid(row=row, column=column, sticky="w", padx=(14, 6), pady=4)
            ttk.Entry(dialog, textvariable=variables[name], width=28).grid(
                row=row, column=column + 1, sticky="ew", padx=(0, 14), pady=4
            )

        button_row = 3 + (len(definitions) + 1) // 2
        button_frame = ttk.Frame(dialog)
        button_frame.grid(row=button_row, column=0, columnspan=4, sticky="e", padx=14, pady=14)
        ttk.Button(button_frame, text="Open Selected PDF", command=self.open_pdf).pack(side="left", padx=(0, 8))
        ttk.Button(button_frame, text="Cancel", command=dialog.destroy).pack(side="left", padx=(0, 8))

        def save_asset() -> None:
            payload = {name: variable.get() for name, variable in variables.items()}
            payload["asset_owner"] = owner_var.get()
            confirmation = (
                f"Add unit {payload['unit']} as {payload['asset_owner']}?\n\n"
                f"VIN/Serial: {payload['vin'] or '(none)'}\n"
                f"Plate/Tag: {payload['plate'] or '(none)'}\n\n"
                "This creates a persistent fleet record and the standard production folders."
            )
            if not messagebox.askyesno("Confirm new asset", confirmation, parent=dialog, icon="warning"):
                return
            try:
                asset = register_manual_asset(
                    database_path=self.database,
                    registry_path=self.manual_assets_registry,
                    audit_path=self.audit_path,
                    unit_folders_root=self.unit_root,
                    farm_asset_folders_root=self.farm_unit_root,
                    **payload,
                )
            except AssetValidationError as error:
                messagebox.showerror("New asset not added", str(error), parent=dialog)
                return
            except Exception as error:
                messagebox.showerror("New asset not added", f"The asset could not be saved: {error}", parent=dialog)
                return

            source = Path(selected["source_file"])
            try:
                refreshed = analyze_pdf(
                    source,
                    self.database,
                    self.unit_root,
                    farm_asset_folders_root=self.farm_unit_root,
                )
                self.model.replace(refreshed)
                save_review_session(self.session_path, self.model.results)
            except Exception as error:
                refreshed = selected
                messagebox.showwarning(
                    "Asset added; document needs review",
                    f"Unit {asset['unit']} was added, but the PDF could not be reanalyzed. Enter the unit manually and save the correction.\n\n{error}",
                    parent=dialog,
                )

            dialog.destroy()
            self._refresh()
            self._select_source(str(source))
            if not refreshed.get("unit"):
                self.unit_var.set(asset["unit"])
            self.status_var.set(f"Added {asset['asset_owner']} unit {asset['unit']}. Review the document before approval.")

        ttk.Button(button_frame, text="Add Asset", command=save_asset).pack(side="left")
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.wait_visibility()
        dialog.focus_set()

    def approve_selected(self) -> None:
        before = self._selected_result()
        if not before:
            messagebox.showinfo("Select a document", "Select a document to approve.")
            return
        if before.get("status") == "approved":
            messagebox.showwarning("Already approved", "This document has already been approved.")
            return
        try:
            corrected = apply_correction(
                before,
                unit=self.unit_var.get(),
                document_type=self._document_type_code(self.type_var.get()),
                controlling_date=self.date_var.get(),
                page_suffix=self.page_var.get(),
                unit_folders_root=self.unit_root,
                farm_asset_folders_root=self.farm_unit_root,
                database_path=self.database,
                audit_path=self.audit_path,
            )
        except ReviewValidationError as error:
            messagebox.showerror("Approval failed", str(error))
            return
        record_correction(self.audit_path, before, corrected)
        self.model.replace(corrected)
        save_review_session(self.session_path, self.model.results)
        try:
            approved = approve_document(
                corrected,
                audit_path=self.audit_path,
                unit_folders_root=self.unit_root,
                incoming_folder=self.incoming,
                farm_asset_folders_root=self.farm_unit_root,
                database_path=self.database,
            )
        except ApprovalError as error:
            messagebox.showerror("Approval failed", str(error))
            return
        self.model.replace(approved)
        save_review_session(self.session_path, self.model.results)
        self.status_var.set(f"Approved and copied: {approved['proposed_filename']}")
        self._refresh()
        self._select_source(approved["source_file"])

    def mark_selected_duplicate(self) -> None:
        result = self._selected_result()
        if not result:
            messagebox.showinfo("Select a document", "Select a document to mark as a duplicate.")
            return
        if result.get("status") in {"approved", "duplicate"}:
            messagebox.showwarning(
                "Cannot mark duplicate",
                "Approved documents and records already marked duplicate cannot be changed this way.",
            )
            return
        source_name = Path(result.get("source_file") or "").name
        if not messagebox.askyesno(
            "Confirm duplicate",
            f"Mark {source_name} as a duplicate?\n\n"
            "The existing production document will not be changed. The incoming PDF will be moved to "
            "Processed\\Duplicates and removed from the Active review list.",
            icon="warning",
        ):
            return
        try:
            duplicate = mark_duplicate_document(
                result,
                unit=self.unit_var.get(),
                document_type=self._document_type_code(self.type_var.get()),
                controlling_date=self.date_var.get(),
                audit_path=self.audit_path,
                unit_folders_root=self.unit_root,
                incoming_folder=self.incoming,
                processed_folder=self.processed,
                farm_asset_folders_root=self.farm_unit_root,
                database_path=self.database,
            )
        except ReviewValidationError as error:
            messagebox.showerror("Duplicate not marked", str(error))
            return
        self.model.replace(duplicate)
        save_review_session(self.session_path, self.model.results)
        self.filter_var.set("Active")
        self.status_var.set(f"Marked duplicate and archived: {source_name}")
        self._refresh()

    def mark_selected_not_dot(self) -> None:
        result = self._selected_result()
        if not result:
            messagebox.showinfo("Select a document", "Select a document to remove from the DOT workflow.")
            return
        if result.get("status") in {"approved", "duplicate", "not_dot"}:
            messagebox.showwarning(
                "Cannot remove document",
                "Approved, duplicate, and Not DOT records cannot be changed this way.",
            )
            return
        source_name = Path(result.get("source_file") or "").name
        if not messagebox.askyesno(
            "Confirm Not DOT document",
            f"Remove {source_name} from the DOT workflow?\n\n"
            "The PDF will be moved to Exceptions\\Not DOT and removed from the Active review list. "
            "It will not be copied to any unit folder.",
            icon="warning",
        ):
            return
        try:
            not_dot = mark_not_dot_document(
                result,
                audit_path=self.audit_path,
                incoming_folder=self.incoming,
                exceptions_folder=self.exceptions,
            )
        except ReviewValidationError as error:
            messagebox.showerror("Document not removed", str(error))
            return
        self.model.replace(not_dot)
        save_review_session(self.session_path, self.model.results)
        self.filter_var.set("Active")
        self.status_var.set(f"Removed from DOT workflow: {source_name}")
        self._refresh()

    def restore_selected(self) -> None:
        result = self._selected_result()
        if not result:
            messagebox.showinfo("Select a document", "Select a Duplicate or Not DOT document to restore.")
            return
        if result.get("status") not in {"duplicate", "not_dot"}:
            messagebox.showwarning(
                "Cannot restore document",
                "Only records in the Duplicates or Not DOT views can be restored to Active.",
            )
            return
        source_name = Path(result.get("source_file") or "").name
        if not messagebox.askyesno(
            "Confirm restore",
            f"Restore {source_name} to the Active review list?\n\n"
            "The archived PDF will be moved back to Incoming and must be reviewed again.",
            icon="warning",
        ):
            return
        try:
            restored = restore_archived_document(
                result,
                audit_path=self.audit_path,
                incoming_folder=self.incoming,
                processed_folder=self.processed,
                exceptions_folder=self.exceptions,
            )
        except ReviewValidationError as error:
            messagebox.showerror("Document not restored", str(error))
            return
        self.model.replace(restored)
        save_review_session(self.session_path, self.model.results)
        self.filter_var.set("Active")
        self.status_var.set(f"Restored to Active review: {source_name}")
        self._refresh()
        self._select_source(restored["source_file"])

    def _select_source(self, source: str) -> None:
        for item_id, item_source in self.row_sources.items():
            if item_source == source:
                self.table.selection_set(item_id)
                self.table.focus(item_id)
                self.table.see(item_id)
                self._on_selection()
                break

    def open_pdf(self) -> None:
        result = self._selected_result()
        if not result:
            messagebox.showinfo("Select a document", "Select a document to open.")
            return
        if result.get("status") == "duplicate":
            path = Path(result.get("duplicate_archived_file") or "")
            expected_parent = (self.processed / "Duplicates").resolve()
            unsafe_message = "The selected PDF is not directly inside the configured duplicate archive."
        elif result.get("status") == "not_dot":
            path = Path(result.get("not_dot_archived_file") or "")
            expected_parent = (self.exceptions / "Not DOT").resolve()
            unsafe_message = "The selected PDF is not directly inside the configured Not DOT archive."
        else:
            path = Path(result["source_file"])
            expected_parent = self.incoming.resolve()
            unsafe_message = "The selected source is not a PDF directly inside Incoming."
        if path.suffix.lower() != ".pdf" or path.resolve().parent != expected_parent:
            messagebox.showerror("Unsafe source path", unsafe_message)
            return
        if not path.exists():
            messagebox.showerror("File unavailable", f"Cannot find:\n{path}")
            return
        os.startfile(path)

    def open_destination(self) -> None:
        result = self._selected_result()
        if not result:
            messagebox.showinfo("Select a document", "Select a document first.")
            return
        destination_value = result.get("approved_destination") or result.get("proposed_destination")
        if not destination_value:
            messagebox.showinfo("No destination", "Correct the document before opening its destination.")
            return
        folder = Path(destination_value).parent
        if not folder.is_dir():
            messagebox.showerror("Folder unavailable", f"Cannot find:\n{folder}")
            return
        os.startfile(folder)


def launch(config_path: str | Path) -> None:
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    root = tk.Tk()
    DotReviewApp(root, config)
    root.mainloop()
