from __future__ import annotations

import json
import os
import queue
import threading
import ctypes
from ctypes import wintypes
import base64
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from .assets import ASSET_OWNERS, AssetValidationError, register_manual_asset
from .binder import (
    BINDER_SECTIONS,
    advance_binder_position,
    list_binder_documents,
    list_binders,
    render_binder_page,
    render_pdf_page,
)
from .database import (
    EDITABLE_UNIT_FIELDS,
    add_custom_field,
    export_fleet_workbook,
    list_custom_fields,
    list_unit_records,
    merge_fleet_workbook,
    update_unit_record,
)
from .gui_model import ReviewModel
from .document_import import (
    find_tesseract,
    import_pdf_documents,
    ocr_candidate_paths,
    run_pdf_ocr,
)
from .naming import DOCUMENT_TYPE_CHOICES
from .processor import analyze_pdf
from .settings import SETTING_DEFINITIONS, save_user_settings
from .review import (
    NON_DOT_DOCUMENT_TYPES,
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
    "CAB": "CAB Card",
    "INS": "Insurance",
    "MISC": "Misc",
}

DISPLAY_ACRONYMS = {"DOT", "PDF", "VIN", "OCR", "ID", "RP", "REG", "INS", "MISC"}


def next_active_source(
    prior_order: tuple[str, ...],
    completed_source: str,
    remaining_order: tuple[str, ...],
) -> str | None:
    if not remaining_order:
        return None
    if completed_source not in prior_order:
        return remaining_order[0]
    completed_index = prior_order.index(completed_source)
    candidates = prior_order[completed_index + 1 :] + prior_order[:completed_index]
    remaining = set(remaining_order)
    return next((source for source in candidates if source in remaining), remaining_order[0])


class DotReviewApp:
    def __init__(self, root: tk.Tk, config: dict, config_path: str | Path | None = None):
        self.root = root
        self.config = config
        self.config_path = Path(config_path) if config_path is not None else None
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
        self.ocr_running = False
        self.bulk_action_running = False
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
        self.root.geometry("1440x1000")
        self.root.minsize(1180, 1000)
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

    @staticmethod
    def _scroll_canvas_with_mouse_wheel(canvas: tk.Canvas, event) -> str:
        if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0:
            direction = -1
        elif getattr(event, "num", None) == 5 or getattr(event, "delta", 0) < 0:
            direction = 1
        else:
            return "break"
        canvas.yview_scroll(direction, "units")
        return "break"

    def _bind_canvas_mouse_wheel(self, canvas: tk.Canvas) -> None:
        callback = lambda event: self._scroll_canvas_with_mouse_wheel(canvas, event)
        canvas.bind("<MouseWheel>", callback)
        canvas.bind("<Button-4>", callback)
        canvas.bind("<Button-5>", callback)

    def _apply_app_icon(self) -> None:
        self.app_icon_image = None
        self.header_icon_image = None
        self.native_icon_handles = []
        try:
            self.app_icon_image = tk.PhotoImage(file=str(APP_ICON_PNG_PATH))
            self.header_icon_image = self.app_icon_image.subsample(8, 8)
            self.root.iconphoto(True, self.app_icon_image)
            if os.name == "nt":
                try:
                    self.root.iconbitmap(default=str(APP_ICON_PATH))
                except tk.TclError:
                    pass
                self._apply_native_windows_icons()
        except (OSError, tk.TclError):
            self.app_icon_image = None
            self.header_icon_image = None

    def _apply_native_windows_icons(self) -> None:
        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x0010
        SM_CXICON, SM_CYICON = 11, 12
        SM_CXSMICON, SM_CYSMICON = 49, 50

        try:
            self.root.update_idletasks()
            user32 = ctypes.windll.user32
            user32.LoadImageW.argtypes = (
                ctypes.c_void_p,
                ctypes.c_wchar_p,
                ctypes.c_uint,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_uint,
            )
            user32.LoadImageW.restype = ctypes.c_void_p
            user32.GetParent.argtypes = (wintypes.HWND,)
            user32.GetParent.restype = wintypes.HWND
            user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
            user32.GetSystemMetrics.restype = ctypes.c_int
            user32.SendMessageW.argtypes = (
                wintypes.HWND,
                wintypes.UINT,
                wintypes.WPARAM,
                wintypes.LPARAM,
            )
            user32.SendMessageW.restype = ctypes.c_ssize_t
            window_id = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            sizes = (
                (ICON_SMALL, user32.GetSystemMetrics(SM_CXSMICON), user32.GetSystemMetrics(SM_CYSMICON)),
                (ICON_BIG, user32.GetSystemMetrics(SM_CXICON), user32.GetSystemMetrics(SM_CYICON)),
            )
            for icon_kind, width, height in sizes:
                handle = user32.LoadImageW(
                    None,
                    str(APP_ICON_PATH),
                    IMAGE_ICON,
                    width,
                    height,
                    LR_LOADFROMFILE,
                )
                if handle:
                    icon_value = ctypes.cast(handle, ctypes.c_void_p).value
                    user32.SendMessageW(window_id, WM_SETICON, icon_kind, icon_value)
                    self.native_icon_handles.append(handle)
        except (AttributeError, OSError, tk.TclError):
            self.native_icon_handles = []

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
        style.configure(
            "TNotebook",
            background=theme["window"],
            bordercolor=theme["border"],
            tabmargins=(0, 0, 0, 0),
        )
        style.configure(
            "TNotebook.Tab",
            background=theme["surface"],
            foreground=theme["muted"],
            bordercolor=theme["border"],
            padding=(22, 10),
            font=("Segoe UI", 10, "bold"),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", theme["surface_hover"]), ("active", theme["border"])],
            foreground=[("selected", theme["text"]), ("active", theme["text"])],
            bordercolor=[("selected", theme["accent"])],
        )
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

        self.navigation = ttk.Notebook(self.root)
        self.navigation.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self.sort_tab = ttk.Frame(self.navigation)
        self.database_tab = ttk.Frame(self.navigation)
        self.settings_tab = ttk.Frame(self.navigation)
        self.binder_tab = ttk.Frame(self.navigation)
        self.navigation.add(self.sort_tab, text="Sort")
        self.navigation.add(self.database_tab, text="Database")
        self.navigation.add(self.settings_tab, text="Settings")
        self.navigation.add(self.binder_tab, text="Virtual Binder")
        self.navigation.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        counters = ttk.Frame(self.sort_tab)
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

        progress_frame = ttk.Frame(self.sort_tab, style="Glass.TFrame", padding=(14, 9))
        progress_frame.pack(fill="x", padx=16, pady=(0, 10))
        self.progress = ttk.Progressbar(progress_frame, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True)
        self.progress_text = ttk.Label(progress_frame, text="Ready", style="Glass.TLabel")
        self.progress_text.pack(side="left", padx=(10, 0))
        self.bulk_ocr_button = ttk.Button(
            progress_frame,
            text="Run OCR on All Needing OCR",
            command=self.run_ocr_on_all,
        )
        self.bulk_ocr_button.pack(side="right", padx=(12, 0))

        toolbar = ttk.Frame(self.sort_tab, style="Glass.TFrame", padding=(12, 10))
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
        self.import_button = ttk.Button(toolbar, text="Import PDFs", command=self.import_documents)
        self.import_button.pack(side="left", padx=3)
        self.ocr_button = ttk.Button(toolbar, text="Run OCR on Selected", command=self.run_ocr_on_selected)
        self.ocr_button.pack(side="left", padx=3)
        ttk.Button(toolbar, text="Open PDF", command=self.open_pdf).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Open Destination", command=self.open_destination).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Add New Asset", command=self.add_new_asset).pack(side="left", padx=(14, 3))
        ttk.Button(toolbar, text="Restore Active", command=self.restore_selected).pack(side="left", padx=3)

        sort_workspace = ttk.Panedwindow(self.sort_tab, orient="horizontal")
        sort_workspace.pack(fill="both", expand=True, padx=16, pady=(0, 10))
        queue_workspace = ttk.Panedwindow(sort_workspace, orient="vertical")
        sort_workspace.add(queue_workspace, weight=1)

        table_frame = ttk.Frame(queue_workspace, style="Glass.TFrame", padding=1)
        queue_workspace.add(table_frame, weight=2)
        selection_toolbar = ttk.Frame(table_frame, style="GlassContent.TFrame", padding=(8, 5))
        selection_toolbar.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.select_all_button = ttk.Button(
            selection_toolbar,
            text="Select All Visible",
            command=self.select_all_visible,
        )
        self.select_all_button.pack(side="left")
        ttk.Label(
            selection_toolbar,
            text="Use Ctrl or Shift to select multiple documents",
            style="Muted.TLabel",
        ).pack(side="left", padx=(10, 0))
        columns = ("file", "status", "unit", "owner", "type", "date", "filename", "reason")
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="extended")
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
        widths = {"file": 105, "status": 80, "unit": 45, "owner": 65, "type": 95, "date": 90, "filename": 120, "reason": 140}
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
        self.table.grid(row=1, column=0, sticky="nsew")
        vertical_scroll.grid(row=1, column=1, sticky="ns")
        horizontal_scroll.grid(row=2, column=0, sticky="ew")
        table_frame.rowconfigure(1, weight=1)
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
            queue_workspace,
            text="Review Selected Document",
            style="Glass.TLabelframe",
            padding=(14, 12),
        )
        queue_workspace.add(review_panel, weight=3)
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
        self.save_correction_button = ttk.Button(action_bar, text="Save Correction", command=self.save_correction)
        self.save_correction_button.pack(side="left", padx=(0, 8))
        self.approve_button = ttk.Button(
            action_bar,
            text="Approve and File Copy",
            command=self.approve_selected,
            style="Primary.TButton",
        )
        self.approve_button.pack(side="left")
        self.duplicate_button = ttk.Button(
            action_bar,
            text="Mark Duplicate",
            command=self.mark_selected_duplicate,
            style="Warning.TButton",
        )
        self.duplicate_button.pack(side="left", padx=(8, 0))
        self.not_dot_button = ttk.Button(
            action_bar,
            text="Not a DOT Document",
            command=self.mark_selected_not_dot,
            style="Danger.TButton",
        )
        self.not_dot_button.pack(side="left", padx=(8, 0))
        ttk.Label(review_panel, text="Review Notes", style="Field.TLabel").grid(row=3, column=0, sticky="nw", pady=(8, 0))
        ttk.Label(review_panel, textvariable=self.reason_var, wraplength=1100, style="Glass.TLabel").grid(
            row=3, column=1, columnspan=7, sticky="w", pady=(8, 0)
        )
        ttk.Label(review_panel, text="Destination", style="Field.TLabel").grid(row=4, column=0, sticky="nw", pady=(7, 0))
        ttk.Label(review_panel, textvariable=self.destination_var, wraplength=1100, style="Glass.TLabel").grid(
            row=4, column=1, columnspan=7, sticky="w", pady=(7, 0)
        )
        review_panel.columnconfigure(2, weight=1)

        viewer = ttk.LabelFrame(
            sort_workspace,
            text="Document Viewer",
            style="Glass.TLabelframe",
            padding=(10, 8),
        )
        sort_workspace.add(viewer, weight=1)
        viewer_toolbar = ttk.Frame(viewer, style="GlassContent.TFrame")
        viewer_toolbar.pack(fill="x", pady=(0, 8))
        ttk.Button(viewer_toolbar, text="−", width=3, command=lambda: self._change_sort_zoom(-0.25)).pack(side="left")
        self.sort_zoom_var = tk.StringVar(value="100%")
        ttk.Label(viewer_toolbar, textvariable=self.sort_zoom_var, style="Glass.TLabel", width=6, anchor="center").pack(
            side="left", padx=3
        )
        ttk.Button(viewer_toolbar, text="Fit", width=5, command=self._fit_sort_page).pack(side="left", padx=3)
        ttk.Button(viewer_toolbar, text="+", width=3, command=lambda: self._change_sort_zoom(0.25)).pack(side="left")

        rotation_toolbar = ttk.Frame(viewer, style="GlassContent.TFrame")
        rotation_toolbar.pack(fill="x", pady=(0, 8))
        ttk.Label(rotation_toolbar, text="Rotate Preview", style="Glass.TLabel").pack(side="left")
        ttk.Button(rotation_toolbar, text="Left", width=7, command=lambda: self._rotate_sort_page(-90)).pack(
            side="left", padx=(8, 3)
        )
        ttk.Button(rotation_toolbar, text="Right", width=7, command=lambda: self._rotate_sort_page(90)).pack(side="left")

        sort_canvas_frame = ttk.Frame(viewer, style="GlassContent.TFrame")
        sort_canvas_frame.pack(fill="both", expand=True)
        self.sort_page_canvas = tk.Canvas(
            sort_canvas_frame,
            background=DARK_THEME["input"],
            highlightbackground=DARK_THEME["border"],
            highlightthickness=1,
        )
        sort_vertical = ttk.Scrollbar(sort_canvas_frame, orient="vertical", command=self.sort_page_canvas.yview)
        sort_horizontal = ttk.Scrollbar(sort_canvas_frame, orient="horizontal", command=self.sort_page_canvas.xview)
        self.sort_page_canvas.configure(yscrollcommand=sort_vertical.set, xscrollcommand=sort_horizontal.set)
        self._bind_canvas_mouse_wheel(self.sort_page_canvas)
        self.sort_page_canvas.grid(row=0, column=0, sticky="nsew")
        sort_vertical.grid(row=0, column=1, sticky="ns")
        sort_horizontal.grid(row=1, column=0, sticky="ew")
        sort_canvas_frame.rowconfigure(0, weight=1)
        sort_canvas_frame.columnconfigure(0, weight=1)

        viewer_navigation = ttk.Frame(viewer, style="GlassContent.TFrame")
        viewer_navigation.pack(fill="x", pady=(8, 0))
        ttk.Button(viewer_navigation, text="‹ Previous", command=lambda: self._turn_sort_page(-1)).pack(side="left")
        self.sort_page_status_var = tk.StringVar(value="No document selected")
        ttk.Label(viewer_navigation, textvariable=self.sort_page_status_var, style="Glass.TLabel", anchor="center").pack(
            side="left", fill="x", expand=True, padx=8
        )
        ttk.Button(viewer_navigation, text="Next ›", command=lambda: self._turn_sort_page(1)).pack(side="right")
        self.sort_page_index = 0
        self.sort_page_count = 0
        self.sort_page_image = None
        self.sort_zoom_factor = 1.0
        self.sort_page_rotations = {}
        self.sort_preview_generation = 0
        self._set_sort_page_message("Select a document to preview it here.")

        self.status_var = tk.StringVar(value="Ready. Click Scan Incoming Documents to analyze PDFs.")
        ttk.Label(self.sort_tab, textvariable=self.status_var, style="Status.TLabel", anchor="w").pack(
            side="bottom", fill="x", padx=16, pady=(0, 10), before=sort_workspace
        )

        self._build_database_tab()
        self._build_settings_tab()
        self._build_binder_tab()

    def _on_tab_changed(self, _event=None) -> None:
        selected = self.navigation.select()
        if selected == str(self.database_tab):
            self._refresh_database_table()
        elif selected == str(self.binder_tab):
            self._refresh_binder_shelf()

    def _build_database_tab(self) -> None:
        toolbar = ttk.Frame(self.database_tab, style="Glass.TFrame", padding=(12, 10))
        toolbar.pack(fill="x", padx=12, pady=(12, 8))
        ttk.Label(toolbar, text="Search Assets", style="Field.TLabel").pack(side="left")
        self.database_search_var = tk.StringVar()
        search = ttk.Entry(toolbar, textvariable=self.database_search_var, width=28)
        search.pack(side="left", padx=(8, 10))
        search.bind("<Return>", lambda _event: self._refresh_database_table())
        ttk.Button(toolbar, text="Search", command=self._refresh_database_table).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Refresh", command=self._refresh_database_table).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Add Trackable Field", command=self._add_database_field).pack(side="right", padx=3)
        ttk.Button(toolbar, text="Export XLSX", command=self._export_database).pack(side="right", padx=3)
        ttk.Button(toolbar, text="Import XLSX", command=self._import_database).pack(side="right", padx=3)

        pane = ttk.Panedwindow(self.database_tab, orient="vertical")
        pane.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        table_frame = ttk.Frame(pane, style="Glass.TFrame", padding=1)
        editor = ttk.LabelFrame(
            pane,
            text="Edit Selected Asset",
            style="Glass.TLabelframe",
            padding=(12, 10),
        )
        pane.add(table_frame, weight=2)
        pane.add(editor, weight=2)

        columns = ("unit", "owner", "type", "year", "make", "model", "plate", "vin", "dot")
        self.database_table = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        headings = {
            "unit": "Unit", "owner": "Ownership", "type": "Unit Type", "year": "Year",
            "make": "Make", "model": "Model", "plate": "Plate / Tag", "vin": "VIN / Serial",
            "dot": "DOT Status",
        }
        widths = {"unit": 70, "owner": 125, "type": 100, "year": 65, "make": 110, "model": 120, "plate": 100, "vin": 180, "dot": 100}
        for column in columns:
            self.database_table.heading(column, text=headings[column])
            self.database_table.column(column, width=widths[column], minwidth=widths[column], anchor="w")
        db_vertical = ttk.Scrollbar(table_frame, orient="vertical", command=self.database_table.yview)
        db_horizontal = ttk.Scrollbar(table_frame, orient="horizontal", command=self.database_table.xview)
        self.database_table.configure(yscrollcommand=db_vertical.set, xscrollcommand=db_horizontal.set)
        self.database_table.grid(row=0, column=0, sticky="nsew")
        db_vertical.grid(row=0, column=1, sticky="ns")
        db_horizontal.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.database_table.bind("<<TreeviewSelect>>", self._on_database_selection)

        self.database_field_definitions = (
            ("display_unit", "Unit Number"), ("asset_owner", "Ownership"),
            ("unit_type", "Unit Type"), ("year", "Year"),
            ("make", "Make"), ("model", "Model"),
            ("vehicle_type", "Vehicle / Equipment Type"), ("plate", "Plate / Tag"),
            ("vin", "VIN / Serial Number"), ("fuel_type", "Fuel Type"),
            ("next_dot", "Next DOT"), ("dot_status", "DOT Status"),
        )
        self.database_field_vars = {}
        for index, (field, label) in enumerate(self.database_field_definitions):
            row = (index // 4) * 2
            column = (index % 4) * 2
            ttk.Label(editor, text=label, style="Field.TLabel").grid(row=row, column=column, sticky="w", padx=(0, 6), pady=(0, 2))
            variable = tk.StringVar()
            self.database_field_vars[field] = variable
            if field == "asset_owner":
                widget = ttk.Combobox(editor, textvariable=variable, values=("", *ASSET_OWNERS), state="readonly", width=20)
            else:
                widget = ttk.Entry(editor, textvariable=variable, width=22)
            widget.grid(row=row + 1, column=column, columnspan=2, sticky="ew", padx=(0, 12), pady=(0, 7))
        for column in range(8):
            editor.columnconfigure(column, weight=1)
        self.database_custom_frame = ttk.Frame(editor, style="GlassContent.TFrame")
        self.database_custom_frame.grid(row=6, column=0, columnspan=8, sticky="ew", pady=(2, 0))
        self.database_custom_vars: dict[int, tk.StringVar] = {}
        actions = ttk.Frame(editor, style="GlassContent.TFrame")
        actions.grid(row=8, column=0, columnspan=8, sticky="e", pady=(8, 0))
        ttk.Button(actions, text="Save Asset Updates", command=self._save_database_record, style="Primary.TButton").pack(side="left")
        self.database_status_var = tk.StringVar(value="Select an asset to view or edit its database record.")
        ttk.Label(self.database_tab, textvariable=self.database_status_var, style="Status.TLabel", anchor="w").pack(
            side="bottom", fill="x", padx=12, pady=(0, 8), before=pane
        )
        self.database_rows: dict[str, int] = {}

    def _refresh_database_table(self) -> None:
        if not hasattr(self, "database_table"):
            return
        self.database_table.delete(*self.database_table.get_children())
        self.database_rows.clear()
        try:
            records = list_unit_records(self.database, self.database_search_var.get())
        except Exception as error:
            self.database_status_var.set(f"Database unavailable: {error}")
            return
        for record in records:
            item_id = f"asset-{record['id']}"
            self.database_table.insert(
                "", "end", iid=item_id,
                values=(record["display_unit"] or "", record["asset_owner"] or "", record["unit_type"] or "", record["year"] or "",
                        record["make"] or "", record["model"] or "", record["plate"] or "", record["vin"] or "", record["dot_status"] or ""),
            )
            self.database_rows[item_id] = record["id"]
        self.database_status_var.set(f"{len(records)} asset record(s). Select one to edit.")

    def _selected_database_id(self) -> int | None:
        selected = self.database_table.selection()
        return self.database_rows.get(selected[0]) if selected else None

    def _on_database_selection(self, _event=None) -> None:
        unit_id = self._selected_database_id()
        if unit_id is None:
            return
        try:
            record = next(item for item in list_unit_records(self.database) if item["id"] == unit_id)
            fields = list_custom_fields(self.database)
        except Exception as error:
            self.database_status_var.set(f"Asset could not be loaded: {error}")
            return
        for field, _label in self.database_field_definitions:
            self.database_field_vars[field].set(record.get(field) or "")
        for child in self.database_custom_frame.winfo_children():
            child.destroy()
        self.database_custom_vars.clear()
        for index, field in enumerate(fields):
            column = (index % 4) * 2
            row = (index // 4) * 2
            label = f"{field['name']} ({field['field_type'].replace('_', '/')})"
            ttk.Label(self.database_custom_frame, text=label, style="Field.TLabel").grid(row=row, column=column, sticky="w", padx=(0, 6))
            variable = tk.StringVar(value=record["custom_values"].get(field["id"], ""))
            self.database_custom_vars[field["id"]] = variable
            ttk.Entry(self.database_custom_frame, textvariable=variable).grid(
                row=row + 1, column=column, columnspan=2, sticky="ew", padx=(0, 12), pady=(0, 6)
            )
        for column in range(8):
            self.database_custom_frame.columnconfigure(column, weight=1)
        self.database_status_var.set(f"Editing unit {record['display_unit']}.")

    def _save_database_record(self) -> None:
        unit_id = self._selected_database_id()
        if unit_id is None:
            messagebox.showinfo("Select an asset", "Select an asset record to update.")
            return
        try:
            updated = update_unit_record(
                self.database,
                unit_id,
                {field: variable.get() for field, variable in self.database_field_vars.items()},
                {field_id: variable.get() for field_id, variable in self.database_custom_vars.items()},
            )
        except Exception as error:
            messagebox.showerror("Asset update failed", str(error))
            return
        self._refresh_database_table()
        item_id = f"asset-{unit_id}"
        if self.database_table.exists(item_id):
            self.database_table.selection_set(item_id)
            self.database_table.see(item_id)
            self._on_database_selection()
        self.database_status_var.set(f"Updated unit {updated['display_unit']}.")

    def _add_database_field(self) -> None:
        name = simpledialog.askstring("Add Trackable Field", "Field name:", parent=self.root)
        if not name:
            return
        field_type = simpledialog.askstring(
            "Trackable Field Type",
            "Type: text, date, number, or yes_no",
            initialvalue="text",
            parent=self.root,
        )
        if not field_type:
            return
        try:
            field = add_custom_field(self.database, name, field_type)
        except Exception as error:
            messagebox.showerror("Field not added", str(error))
            return
        self.database_status_var.set(f"Added trackable field: {field['name']}.")
        self._on_database_selection()

    def _import_database(self) -> None:
        path = filedialog.askopenfilename(
            title="Import Fleet Assets",
            filetypes=(("Excel workbook", "*.xlsx"),),
            parent=self.root,
        )
        if not path:
            return
        if not messagebox.askyesno(
            "Merge fleet workbook",
            "Validated rows will update matching unit numbers and add new units. The import is cancelled if an identifier conflict is found. Continue?",
            parent=self.root,
            icon="warning",
        ):
            return
        try:
            summary = merge_fleet_workbook(path, self.database)
        except Exception as error:
            messagebox.showerror("Import cancelled", str(error))
            return
        self._refresh_database_table()
        self.database_status_var.set(
            f"Import complete: {summary['updated']} updated, {summary['inserted']} added, "
            f"{summary['custom_fields_added']} trackable field(s) added."
        )

    def _export_database(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Export Complete Fleet Database",
            defaultextension=".xlsx",
            filetypes=(("Excel workbook", "*.xlsx"),),
            initialfile=f"DocMarshal-Fleet-{datetime.now():%Y-%m-%d}.xlsx",
            parent=self.root,
        )
        if not path:
            return
        try:
            export_fleet_workbook(self.database, path)
        except Exception as error:
            messagebox.showerror("Export failed", str(error))
            return
        self.database_status_var.set(f"Complete database exported to {path}")

    def _build_settings_tab(self) -> None:
        panel = ttk.LabelFrame(
            self.settings_tab,
            text="Installation and Folder Settings",
            style="Glass.TLabelframe",
            padding=(18, 14),
        )
        panel.pack(fill="both", expand=True, padx=12, pady=12)
        ttk.Label(
            panel,
            text="Choose paths for this DocMarshal installation. Saved changes take effect after restarting the app.",
            style="Glass.TLabel",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))
        self.settings_vars = {}
        for row, definition in enumerate(SETTING_DEFINITIONS, start=1):
            ttk.Label(panel, text=definition["label"], style="Field.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 10), pady=5)
            variable = tk.StringVar(value=str(self.config.get(definition["key"], "")))
            self.settings_vars[definition["key"]] = variable
            ttk.Entry(panel, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=5)
            ttk.Button(
                panel,
                text="Browse…",
                command=lambda item=definition: self._browse_setting(item),
            ).grid(row=row, column=2, padx=(8, 0), pady=5)
        panel.columnconfigure(1, weight=1)
        ttk.Button(panel, text="Save Settings", command=self._save_settings, style="Primary.TButton").grid(
            row=len(SETTING_DEFINITIONS) + 1, column=2, sticky="e", pady=(14, 0)
        )
        self.settings_status_var = tk.StringVar(value="Settings are stored locally in config.json and are not published.")
        ttk.Label(panel, textvariable=self.settings_status_var, style="Status.TLabel", anchor="w").grid(
            row=len(SETTING_DEFINITIONS) + 2, column=0, columnspan=3, sticky="ew", pady=(14, 0)
        )

    def _browse_setting(self, definition: dict) -> None:
        current = self.settings_vars[definition["key"]].get()
        if definition["kind"] == "directory":
            selected = filedialog.askdirectory(title=definition["label"], initialdir=current or None, parent=self.root)
        else:
            selected = filedialog.askopenfilename(title=definition["label"], initialfile=Path(current).name if current else None, parent=self.root)
        if selected:
            self.settings_vars[definition["key"]].set(selected)

    def _save_settings(self) -> None:
        if self.config_path is None:
            messagebox.showerror("Settings unavailable", "This DocMarshal instance was not launched from a configuration file.")
            return
        try:
            self.config = save_user_settings(
                self.config_path,
                self.config,
                {key: variable.get() for key, variable in self.settings_vars.items()},
            )
        except Exception as error:
            messagebox.showerror("Settings not saved", str(error))
            return
        self.settings_status_var.set("Settings saved. Restart DocMarshal to apply the updated paths safely.")

    def _build_binder_tab(self) -> None:
        pane = ttk.Panedwindow(self.binder_tab, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=12, pady=12)
        shelf = ttk.LabelFrame(pane, text="Digital Binder Shelf", style="Glass.TLabelframe", padding=(8, 8))
        viewer = ttk.LabelFrame(pane, text="Binder Viewer", style="Glass.TLabelframe", padding=(10, 10))
        pane.add(shelf, weight=1)
        pane.add(viewer, weight=4)
        shelf_toolbar = ttk.Frame(shelf, style="GlassContent.TFrame")
        shelf_toolbar.pack(fill="x", pady=(0, 8))
        self.binder_filter_var = tk.StringVar()
        binder_filter = ttk.Entry(shelf_toolbar, textvariable=self.binder_filter_var, width=12)
        binder_filter.pack(side="left", fill="x", expand=True)
        binder_filter.bind("<Return>", lambda _event: self._refresh_binder_shelf())
        ttk.Button(shelf_toolbar, text="Find Unit", command=self._refresh_binder_shelf).pack(side="left", padx=(6, 0))
        shelf_frame = ttk.Frame(shelf, style="GlassContent.TFrame")
        shelf_frame.pack(fill="both", expand=True)
        self.binder_shelf = tk.Canvas(
            shelf_frame,
            width=230,
            background=DARK_THEME["input"],
            highlightbackground=DARK_THEME["border"],
            highlightthickness=1,
        )
        shelf_scroll = ttk.Scrollbar(shelf_frame, orient="vertical", command=self.binder_shelf.yview)
        self.binder_shelf.configure(yscrollcommand=shelf_scroll.set)
        self._bind_canvas_mouse_wheel(self.binder_shelf)
        self.binder_shelf.pack(side="left", fill="both", expand=True)
        shelf_scroll.pack(side="right", fill="y")
        self.binder_shelf.bind("<Button-1>", self._select_binder_from_shelf)

        self.binder_title_var = tk.StringVar(value="Select a binder from the shelf")
        ttk.Label(viewer, textvariable=self.binder_title_var, style="Header.TLabel").pack(anchor="w", pady=(0, 8))
        document_bar = ttk.Frame(viewer, style="GlassContent.TFrame")
        document_bar.pack(fill="x", pady=(0, 8))
        ttk.Label(document_bar, text="Document", style="Field.TLabel").pack(side="left")
        self.binder_document_var = tk.StringVar()
        self.binder_document_box = ttk.Combobox(document_bar, textvariable=self.binder_document_var, state="readonly")
        self.binder_document_box.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.binder_document_box.bind("<<ComboboxSelected>>", lambda _event: self._select_binder_document())
        ttk.Button(document_bar, text="Zoom Out", command=lambda: self._change_binder_zoom(-0.25)).pack(side="left", padx=(10, 3))
        self.binder_zoom_var = tk.StringVar(value="100%")
        ttk.Label(document_bar, textvariable=self.binder_zoom_var, style="Glass.TLabel", width=6, anchor="center").pack(side="left")
        ttk.Button(document_bar, text="Fit Page", command=self._fit_binder_page).pack(side="left", padx=3)
        ttk.Button(document_bar, text="Zoom In", command=lambda: self._change_binder_zoom(0.25)).pack(side="left", padx=3)

        page_area = ttk.Frame(viewer, style="GlassContent.TFrame")
        page_area.pack(fill="both", expand=True)
        canvas_frame = ttk.Frame(page_area, style="GlassContent.TFrame")
        canvas_frame.pack(side="left", fill="both", expand=True)
        self.binder_page_canvas = tk.Canvas(
            canvas_frame,
            background=DARK_THEME["input"],
            highlightbackground=DARK_THEME["border"],
            highlightthickness=1,
        )
        binder_vertical = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.binder_page_canvas.yview)
        binder_horizontal = ttk.Scrollbar(canvas_frame, orient="horizontal", command=self.binder_page_canvas.xview)
        self.binder_page_canvas.configure(
            yscrollcommand=binder_vertical.set,
            xscrollcommand=binder_horizontal.set,
        )
        self._bind_canvas_mouse_wheel(self.binder_page_canvas)
        self.binder_page_canvas.grid(row=0, column=0, sticky="nsew")
        binder_vertical.grid(row=0, column=1, sticky="ns")
        binder_horizontal.grid(row=1, column=0, sticky="ew")
        canvas_frame.rowconfigure(0, weight=1)
        canvas_frame.columnconfigure(0, weight=1)
        self._set_binder_page_message("Choose a binder tab and PDF to view one page at a time.")
        tabs = ttk.Frame(page_area, style="GlassContent.TFrame")
        tabs.pack(side="right", fill="y", padx=(10, 0))
        self.binder_section_buttons = []
        for label, folder in BINDER_SECTIONS:
            button = ttk.Button(tabs, text=label, command=lambda selected=folder: self._open_binder_section(selected))
            button.pack(fill="x", pady=(0, 8))
            self.binder_section_buttons.append(button)

        navigation = ttk.Frame(viewer, style="GlassContent.TFrame")
        navigation.pack(fill="x", pady=(8, 0))
        ttk.Button(navigation, text="‹ Previous Page", command=lambda: self._turn_binder_page(-1)).pack(side="left")
        self.binder_page_status_var = tk.StringVar(value="No document selected")
        ttk.Label(navigation, textvariable=self.binder_page_status_var, style="Glass.TLabel").pack(side="left", expand=True)
        ttk.Button(navigation, text="Next Page ›", command=lambda: self._turn_binder_page(1)).pack(side="right")
        self.binder_records: list[dict] = []
        self.active_binder: dict | None = None
        self.active_binder_section: str | None = None
        self.binder_document_paths: dict[str, Path] = {}
        self.active_binder_pdf: Path | None = None
        self.binder_page_index = 0
        self.binder_page_count = 0
        self.binder_page_image = None
        self.binder_zoom_factor = 1.0

    def _refresh_binder_shelf(self) -> None:
        if not hasattr(self, "binder_shelf"):
            return
        self.binder_shelf.delete("all")
        try:
            all_records = list_binders(self.database, self.unit_root, self.farm_unit_root)
        except Exception as error:
            self.binder_page_status_var.set(f"Binder shelf unavailable: {error}")
            self.binder_records = []
            return
        query = self.binder_filter_var.get().strip().casefold()
        self.binder_records = [record for record in all_records if query in record["unit"].casefold()]
        y = 12
        for index, binder in enumerate(self.binder_records):
            fill = "#123D6A" if binder["available"] else "#263241"
            outline = DARK_THEME["accent"] if binder["available"] else DARK_THEME["muted"]
            tag = f"binder-{index}"
            self.binder_shelf.create_rectangle(12, y, 210, y + 46, fill=fill, outline=outline, width=2, tags=("binder", tag))
            self.binder_shelf.create_line(20, y + 7, 202, y + 7, fill=outline, width=2, tags=("binder", tag))
            self.binder_shelf.create_line(20, y + 39, 202, y + 39, fill=outline, width=2, tags=("binder", tag))
            suffix = "" if binder["available"] else "  •  folder missing"
            self.binder_shelf.create_text(
                28, y + 23, anchor="w", text=f"UNIT {binder['unit']}{suffix}",
                fill=DARK_THEME["text"], font=("Segoe UI", 10, "bold"), tags=("binder", tag),
            )
            y += 56
        self.binder_shelf.configure(scrollregion=(0, 0, 225, max(y, 1)))
        available = sum(1 for record in all_records if record["available"])
        visible = f"{len(self.binder_records)} shown • " if query else ""
        self.binder_page_status_var.set(
            f"{visible}{available} binder folder(s) available • {len(all_records)} database asset record(s)."
        )

    def _select_binder_from_shelf(self, _event=None) -> None:
        tags = self.binder_shelf.gettags("current")
        index_tag = next((tag for tag in tags if tag.startswith("binder-")), None)
        if index_tag is None:
            return
        binder = self.binder_records[int(index_tag.split("-", 1)[1])]
        self.active_binder = binder
        self.binder_title_var.set(f"Unit {binder['unit']}  •  {binder['owner'] or 'Unassigned ownership'}")
        if not binder["available"]:
            self._set_binder_page_message("The canonical unit binder folder has not been created yet.")
            self.binder_page_status_var.set(str(binder["folder"]))
            self.binder_document_box.configure(values=())
            self.binder_document_var.set("")
            return
        self._open_binder_section(BINDER_SECTIONS[0][1])

    def _set_binder_page_message(self, message: str) -> None:
        self.binder_page_image = None
        self.binder_page_canvas.delete("all")
        width = max(300, self.binder_page_canvas.winfo_width())
        height = max(300, self.binder_page_canvas.winfo_height())
        self.binder_page_canvas.create_text(
            width // 2,
            height // 2,
            text=message,
            fill=DARK_THEME["muted"],
            font=("Segoe UI", 11),
            justify="center",
            width=max(260, width - 60),
        )
        self.binder_page_canvas.configure(scrollregion=(0, 0, width, height))

    def _open_binder_section(
        self,
        section_folder: str,
        *,
        document_index: int = 0,
        last_page: bool = False,
    ) -> None:
        if self.active_binder is None or not self.active_binder["available"]:
            return
        self.active_binder_section = section_folder
        try:
            documents = list_binder_documents(self.active_binder["folder"], section_folder)
        except Exception as error:
            self.binder_page_status_var.set(f"Binder tab unavailable: {error}")
            return
        self.binder_document_paths = {path.name: path for path in documents}
        self.binder_document_box.configure(values=tuple(self.binder_document_paths))
        self.binder_document_var.set(documents[0].name if documents else "")
        label = next(label for label, folder in BINDER_SECTIONS if folder == section_folder)
        if not documents:
            self.active_binder_pdf = None
            self.binder_page_index = 0
            self.binder_page_count = 0
            self.binder_page_image = None
            self._set_binder_page_message(f"No PDF documents in the {label} tab.\n\nUse Next Page to continue to the next category.")
            self.binder_page_status_var.set(f"Unit {self.active_binder['unit']} • {label} • 0 documents")
            return
        document_index = min(max(document_index, 0), len(documents) - 1)
        self.binder_document_var.set(documents[document_index].name)
        self._select_binder_document(last_page=last_page)

    def _select_binder_document(self, *, last_page: bool = False) -> None:
        self.active_binder_pdf = self.binder_document_paths.get(self.binder_document_var.get())
        self.binder_page_index = 0
        self._render_active_binder_page()
        if last_page and self.binder_page_count > 1:
            self.binder_page_index = self.binder_page_count - 1
            self._render_active_binder_page()

    def _turn_binder_page(self, direction: int) -> None:
        if self.active_binder is None or self.active_binder_section is None:
            return
        section_index = next(
            index for index, (_label, folder) in enumerate(BINDER_SECTIONS) if folder == self.active_binder_section
        )
        page_counts = []
        documents_by_section = []
        for _label, folder in BINDER_SECTIONS:
            documents = list_binder_documents(self.active_binder["folder"], folder)
            documents_by_section.append(documents)
            page_counts.append(
                tuple(
                    self.binder_page_count if path == self.active_binder_pdf and self.binder_page_count else 1
                    for path in documents
                )
            )
        current_documents = documents_by_section[section_index]
        document_index = (
            current_documents.index(self.active_binder_pdf)
            if self.active_binder_pdf in current_documents
            else None
        )
        page_index = self.binder_page_index if document_index is not None else None
        target_section, target_document, target_page = advance_binder_position(
            page_counts,
            section_index,
            document_index,
            page_index,
            direction,
        )
        if (target_section, target_document, target_page) == (section_index, document_index, page_index):
            return
        if target_section != section_index:
            self._open_binder_section(
                BINDER_SECTIONS[target_section][1],
                document_index=target_document or 0,
                last_page=direction < 0 and target_document is not None,
            )
            return
        if target_document != document_index and target_document is not None:
            self.binder_document_var.set(current_documents[target_document].name)
            self._select_binder_document(last_page=direction < 0)
            return
        if target_page is not None:
            self.binder_page_index = target_page
            self._render_active_binder_page()

    def _change_binder_zoom(self, delta: float) -> None:
        self.binder_zoom_factor = min(4.0, max(0.25, self.binder_zoom_factor + delta))
        self.binder_zoom_var.set(f"{round(self.binder_zoom_factor * 100):d}%")
        self._render_active_binder_page()

    def _fit_binder_page(self) -> None:
        self.binder_zoom_factor = 1.0
        self.binder_zoom_var.set("100%")
        self._render_active_binder_page()

    def _render_active_binder_page(self) -> None:
        if self.active_binder_pdf is None or self.active_binder_section is None or self.active_binder is None:
            return
        expected_folder = self.active_binder["folder"] / self.active_binder_section
        width = max(300, self.binder_page_canvas.winfo_width() - 20)
        height = max(300, self.binder_page_canvas.winfo_height() - 20)
        try:
            rendered = render_binder_page(
                self.active_binder_pdf,
                expected_folder,
                self.binder_page_index,
                max_width=width,
                max_height=height,
                zoom_factor=self.binder_zoom_factor,
            )
            encoded = base64.b64encode(rendered["png"])
            self.binder_page_image = tk.PhotoImage(data=encoded)
        except Exception as error:
            self.binder_page_image = None
            self._set_binder_page_message(f"This PDF page could not be rendered.\n\n{error}")
            self.binder_page_status_var.set("Page rendering failed")
            return
        self.binder_page_count = rendered["page_count"]
        self.binder_page_canvas.delete("all")
        canvas_width = max(1, self.binder_page_canvas.winfo_width())
        canvas_height = max(1, self.binder_page_canvas.winfo_height())
        x = max(10, (canvas_width - rendered["width"]) // 2)
        y = max(10, (canvas_height - rendered["height"]) // 2)
        self.binder_page_canvas.create_image(x, y, image=self.binder_page_image, anchor="nw")
        self.binder_page_canvas.configure(
            scrollregion=(
                0,
                0,
                max(canvas_width, x + rendered["width"] + 10),
                max(canvas_height, y + rendered["height"] + 10),
            )
        )
        self.binder_page_canvas.xview_moveto(0)
        self.binder_page_canvas.yview_moveto(0)
        label = next(label for label, folder in BINDER_SECTIONS if folder == self.active_binder_section)
        self.binder_page_status_var.set(
            f"{label} • {self.active_binder_pdf.name} • Page {self.binder_page_index + 1} of {self.binder_page_count}"
        )

    def _bind_approval_on_enter(self, *widgets) -> None:
        for widget in widgets:
            widget.bind("<Return>", self._approve_from_enter)
            widget.bind("<KP_Enter>", self._approve_from_enter)

    def _approve_from_enter(self, _event=None) -> str:
        self.approve_selected()
        return "break"

    def import_documents(self) -> None:
        if self.scanning or self.ocr_running:
            self.status_var.set("Wait for the current scan or OCR operation to finish before importing more PDFs.")
            return
        selected = filedialog.askopenfilenames(
            title="Import PDFs into DocMarshal",
            filetypes=(("PDF documents", "*.pdf"),),
            parent=self.root,
        )
        if not selected:
            return
        self.import_button.configure(state="disabled")
        self.status_var.set(f"Importing {len(selected)} PDF document(s)...")
        threading.Thread(target=self._import_worker, args=(tuple(selected),), daemon=True).start()

    def _import_worker(self, selected: tuple[str, ...]) -> None:
        try:
            results = import_pdf_documents(selected, self.incoming)
        except Exception as error:
            self.events.put(("import_error", str(error)))
            return
        self.events.put(("import_done", results))

    def run_ocr_on_selected(self) -> None:
        if self.scanning or self.ocr_running:
            self.status_var.set("Wait for the current scan or OCR operation to finish before running OCR.")
            return
        result = self._selected_result()
        if not result:
            messagebox.showinfo("Select a document", "Select an Incoming PDF to run OCR.")
            return
        source = Path(result.get("source_file", ""))
        self.ocr_running = True
        self.ocr_button.configure(state="disabled")
        self.bulk_ocr_button.configure(state="disabled")
        self.status_var.set(f"Running OCR on {source.name}. This may take several minutes...")
        threading.Thread(target=self._ocr_worker, args=(source,), daemon=True).start()

    def _ocr_worker(self, source: Path) -> None:
        try:
            result = run_pdf_ocr(
                source,
                incoming_root=self.incoming,
                backup_root=self.processed / "OCR Originals",
            )
        except Exception as error:
            self.events.put(("ocr_error", source.name, str(error)))
            return
        if result["status"] == "ocr_completed":
            self._record_ocr_audit(result)
        self.events.put(("ocr_done", result))

    def _record_ocr_audit(self, result: dict) -> None:
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "event": "ocr_completed",
                        "timestamp": datetime.now().isoformat(timespec="seconds"),
                        "source_file": result["source"],
                        "backup_file": result["backup_path"],
                        "page_count": result["page_count"],
                        "sha256_before": result["sha256_before"],
                        "sha256_after": result["sha256_after"],
                    },
                    sort_keys=True,
                )
                + "\n"
            )

    def run_ocr_on_all(self) -> None:
        if self.scanning or self.ocr_running:
            self.status_var.set("Wait for the current scan or OCR operation to finish.")
            return
        candidates = ocr_candidate_paths(self.model.results, self.incoming)
        if not candidates:
            self.status_var.set("No Incoming PDFs are currently flagged as needing OCR.")
            return
        if not messagebox.askyesno(
            "Run bulk OCR",
            f"Run OCR on {len(candidates)} image-only PDF(s)?\n\n"
            "This can take a long time. Originals will be preserved under Processed\\OCR Originals.",
            parent=self.root,
        ):
            return
        self.ocr_running = True
        self.ocr_button.configure(state="disabled")
        self.bulk_ocr_button.configure(state="disabled")
        self.import_button.configure(state="disabled")
        self.scan_button.configure(state="disabled")
        self.progress.configure(maximum=len(candidates), value=0)
        self.progress_text.configure(text=f"0 of {len(candidates)}")
        self.status_var.set(f"Starting bulk OCR for {len(candidates)} PDF(s)...")
        threading.Thread(target=self._bulk_ocr_worker, args=(candidates,), daemon=True).start()

    def _bulk_ocr_worker(self, candidates: list[Path]) -> None:
        try:
            tesseract = find_tesseract()
        except Exception as error:
            self.events.put(("bulk_ocr_error", str(error)))
            return
        completed = 0
        already_searchable = 0
        errors = []
        total = len(candidates)
        for index, source in enumerate(candidates, start=1):
            try:
                result = run_pdf_ocr(
                    source,
                    incoming_root=self.incoming,
                    backup_root=self.processed / "OCR Originals",
                    tesseract_executable=tesseract,
                )
                if result["status"] == "ocr_completed":
                    completed += 1
                    self._record_ocr_audit(result)
                else:
                    already_searchable += 1
                detail = result["status"]
            except Exception as error:
                errors.append({"filename": source.name, "error": str(error)})
                detail = "failed"
            self.events.put(("bulk_ocr_progress", index, total, source.name, detail))
        summary = {
            "completed": completed,
            "already_searchable": already_searchable,
            "errors": errors,
            "total": total,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.review_folder.mkdir(parents=True, exist_ok=True)
        report_path = self.review_folder / f"bulk_ocr_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.json"
        temporary_report = report_path.with_suffix(".tmp")
        temporary_report.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        os.replace(temporary_report, report_path)
        summary["report_path"] = str(report_path)
        self.events.put(("bulk_ocr_done", summary))

    def scan_incoming(self) -> None:
        if self.scanning or self.ocr_running or getattr(self, "bulk_action_running", False):
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
                elif event[0] == "import_done":
                    _, results = event
                    self.import_button.configure(state="normal")
                    copied = sum(item["status"] == "copied_verified" for item in results)
                    identical = sum(item["status"] == "already_identical" for item in results)
                    conflicts = sum(item["status"] == "destination_conflict" for item in results)
                    if conflicts:
                        messagebox.showwarning(
                            "Some PDFs were not imported",
                            f"{conflicts} file(s) had the same filename as a different Incoming PDF and were not overwritten.",
                        )
                    if copied or identical:
                        self.status_var.set(
                            f"Imported {copied} PDF(s); {identical} already present. Scanning Incoming..."
                        )
                        self.scan_incoming()
                    else:
                        self.status_var.set("No PDFs were imported.")
                elif event[0] == "import_error":
                    self.import_button.configure(state="normal")
                    self.status_var.set(f"Import failed: {event[1]}")
                    messagebox.showerror("PDF import failed", event[1])
                elif event[0] == "ocr_done":
                    _, result = event
                    self.ocr_running = False
                    self.ocr_button.configure(state="normal")
                    self.bulk_ocr_button.configure(state="normal")
                    if result["status"] == "already_searchable":
                        self.status_var.set(f"{Path(result['source']).name} already contains searchable text.")
                    else:
                        self.status_var.set(f"OCR completed for {Path(result['source']).name}. Reanalyzing Incoming...")
                        self.scan_incoming()
                elif event[0] == "ocr_error":
                    _, filename, detail = event
                    self.ocr_running = False
                    self.ocr_button.configure(state="normal")
                    self.bulk_ocr_button.configure(state="normal")
                    self.status_var.set(f"OCR failed for {filename}: {detail}")
                    messagebox.showerror("OCR failed", f"{filename}\n\n{detail}")
                elif event[0] == "bulk_ocr_progress":
                    _, index, total, filename, detail = event
                    self.progress.configure(value=index)
                    self.progress_text.configure(text=f"{index} of {total}")
                    self.status_var.set(f"Bulk OCR {index} of {total}: {filename} • {detail}")
                elif event[0] == "bulk_ocr_done":
                    _, summary = event
                    self.ocr_running = False
                    self.ocr_button.configure(state="normal")
                    self.bulk_ocr_button.configure(state="normal")
                    self.import_button.configure(state="normal")
                    self.scan_button.configure(state="normal")
                    failures = len(summary["errors"])
                    self.status_var.set(
                        f"Bulk OCR complete: {summary['completed']} converted, "
                        f"{summary['already_searchable']} already searchable, {failures} failed."
                    )
                    if failures:
                        examples = "\n".join(
                            f"• {item['filename']}: {item['error']}" for item in summary["errors"][:3]
                        )
                        messagebox.showwarning(
                            "Bulk OCR completed with failures",
                            f"{failures} PDF(s) could not be converted. The remaining files continued normally.\n\n"
                            f"{examples}\n\nFull report: {summary['report_path']}",
                        )
                    if summary["completed"] or summary["already_searchable"]:
                        self.scan_incoming()
                elif event[0] == "bulk_ocr_error":
                    self.ocr_running = False
                    self.ocr_button.configure(state="normal")
                    self.bulk_ocr_button.configure(state="normal")
                    self.import_button.configure(state="normal")
                    self.scan_button.configure(state="normal")
                    self.status_var.set(f"Bulk OCR could not start: {event[1]}")
                    messagebox.showerror("Bulk OCR unavailable", event[1])
                elif event[0] == "bulk_not_dot_progress":
                    _, index, total, filename, detail = event
                    self.progress.configure(value=index)
                    self.progress_text.configure(text=f"{index} of {total}")
                    self.status_var.set(f"Bulk Not DOT {index} of {total}: {filename} • {detail}")
                elif event[0] == "bulk_not_dot_done":
                    _, summary = event
                    self.bulk_action_running = False
                    self.not_dot_button.configure(state="normal")
                    self.select_all_button.configure(state="normal")
                    self.save_correction_button.configure(state="normal")
                    self.approve_button.configure(state="normal")
                    self.duplicate_button.configure(state="normal")
                    self.ocr_button.configure(state="normal")
                    self.bulk_ocr_button.configure(state="normal")
                    self.scan_button.configure(state="normal")
                    self.import_button.configure(state="normal")
                    self.model = ReviewModel(summary["results"])
                    self._refresh()
                    failures = summary["failed"]
                    self.status_var.set(
                        f"Bulk Not DOT complete: {summary['completed']} archived, {failures} failed."
                    )
                    if failures:
                        examples = "\n".join(
                            f"• {item['filename']}: {item['error']}" for item in summary["errors"][:5]
                        )
                        messagebox.showwarning(
                            "Bulk Not DOT completed with failures",
                            f"{failures} document(s) could not be archived. The remaining selections continued.\n\n{examples}",
                        )
                elif event[0] == "sort_preview_done":
                    _, generation, source, rendered = event
                    if generation == self.sort_preview_generation and source == self._selected_source():
                        self._display_sort_page(source, rendered)
                elif event[0] == "sort_preview_error":
                    _, generation, source, detail = event
                    if generation == self.sort_preview_generation and source == self._selected_source():
                        self.sort_page_count = 0
                        self._set_sort_page_message(f"This PDF page could not be rendered.\n\n{detail}")
                        self.sort_page_status_var.set("Page rendering failed")
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
            review_notes = "; ".join(
                self._humanize_user_text(reason) for reason in result.get("reasons", [])
            )
            if result.get("non_dot_classification_label"):
                review_notes = (
                    f"{result['non_dot_classification_label']}; {review_notes}"
                    if review_notes
                    else result["non_dot_classification_label"]
                )
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
                    review_notes,
                ),
                tags=(status,),
            )
            self.row_sources[item_id] = source
            if source == selected_source:
                self.table.selection_set(item_id)
        if not self.table.selection():
            children = self.table.get_children()
            if children:
                first = children[0]
                self.table.selection_set(first)
                self.table.focus(first)
                self.table.see(first)
                self._on_selection()
            else:
                self._clear_sort_selection()

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

    def _selected_results(self) -> list[dict]:
        selected = set(self.table.selection())
        sources = [
            self.row_sources[item_id]
            for item_id in self.table.get_children()
            if item_id in selected and item_id in self.row_sources
        ]
        results_by_source = {
            item.get("source_file"): item
            for item in self.model.results
        }
        return [results_by_source[source] for source in sources if source in results_by_source]

    def select_all_visible(self) -> None:
        children = self.table.get_children()
        if children:
            self.table.selection_set(children)

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
        review_notes = "; ".join(
            self._humanize_user_text(reason) for reason in result.get("reasons", [])
        )
        if result.get("non_dot_classification_label"):
            review_notes = (
                f"{result['non_dot_classification_label']}; {review_notes}"
                if review_notes
                else result["non_dot_classification_label"]
            )
        self.reason_var.set(review_notes or "No unresolved issues.")
        self.destination_var.set(result.get("proposed_destination") or "Not available until all fields are valid.")
        self.sort_page_index = 0
        self._render_selected_sort_page()

    def _sort_preview_path(self, result: dict) -> tuple[Path, Path]:
        status = result.get("status")
        if status == "duplicate":
            return Path(result.get("duplicate_archived_file") or ""), self.processed / "Duplicates"
        if status == "not_dot":
            return Path(result.get("not_dot_archived_file") or ""), self.exceptions / "Not DOT"
        return Path(result.get("source_file") or ""), self.incoming

    def _set_sort_page_message(self, message: str) -> None:
        self.sort_page_image = None
        self.sort_page_canvas.delete("all")
        width = max(300, self.sort_page_canvas.winfo_width())
        height = max(300, self.sort_page_canvas.winfo_height())
        self.sort_page_canvas.create_text(
            width // 2,
            height // 2,
            text=message,
            fill=DARK_THEME["muted"],
            font=("Segoe UI", 11),
            justify="center",
            width=max(260, width - 50),
        )
        self.sort_page_canvas.configure(scrollregion=(0, 0, width, height))

    def _render_selected_sort_page(self) -> None:
        result = self._selected_result()
        if not result:
            self.sort_page_count = 0
            self.sort_page_status_var.set("No document selected")
            self._set_sort_page_message("Select a document to preview it here.")
            return
        path, expected_folder = self._sort_preview_path(result)
        self.sort_preview_generation += 1
        generation = self.sort_preview_generation
        source = result.get("source_file", "")
        rotation = self.sort_page_rotations.get((source, self.sort_page_index), 0)
        width = max(300, self.sort_page_canvas.winfo_width() - 20)
        height = max(300, self.sort_page_canvas.winfo_height() - 20)
        self.sort_page_status_var.set(f"Rendering {path.name}...")
        threading.Thread(
            target=self._sort_preview_worker,
            args=(
                generation,
                source,
                path,
                expected_folder,
                self.sort_page_index,
                width,
                height,
                self.sort_zoom_factor,
                rotation,
            ),
            daemon=True,
        ).start()

    def _sort_preview_worker(
        self,
        generation: int,
        source: str,
        path: Path,
        expected_folder: Path,
        page_index: int,
        width: int,
        height: int,
        zoom_factor: float,
        rotation: int,
    ) -> None:
        try:
            rendered = render_pdf_page(
                path,
                expected_folder,
                page_index,
                max_width=width,
                max_height=height,
                zoom_factor=zoom_factor,
                rotation=rotation,
            )
        except Exception as error:
            self.events.put(("sort_preview_error", generation, source, str(error)))
            return
        self.events.put(("sort_preview_done", generation, source, rendered))

    def _display_sort_page(self, source: str, rendered: dict) -> None:
        self.sort_page_image = tk.PhotoImage(data=base64.b64encode(rendered["png"]))
        self.sort_page_count = rendered["page_count"]
        self.sort_page_canvas.delete("all")
        canvas_width = max(1, self.sort_page_canvas.winfo_width())
        canvas_height = max(1, self.sort_page_canvas.winfo_height())
        x = max(10, (canvas_width - rendered["width"]) // 2)
        y = max(10, (canvas_height - rendered["height"]) // 2)
        self.sort_page_canvas.create_image(x, y, image=self.sort_page_image, anchor="nw")
        self.sort_page_canvas.configure(
            scrollregion=(0, 0, max(canvas_width, x + rendered["width"] + 10), max(canvas_height, y + rendered["height"] + 10))
        )
        self.sort_page_canvas.xview_moveto(0)
        self.sort_page_canvas.yview_moveto(0)
        self.sort_page_status_var.set(f"Page {rendered['page_index'] + 1} of {rendered['page_count']}")

    def _turn_sort_page(self, direction: int) -> None:
        if not self._selected_result() or self.sort_page_count < 1:
            return
        target = min(max(self.sort_page_index + direction, 0), self.sort_page_count - 1)
        if target != self.sort_page_index:
            self.sort_page_index = target
            self._render_selected_sort_page()

    def _change_sort_zoom(self, delta: float) -> None:
        self.sort_zoom_factor = min(4.0, max(0.25, self.sort_zoom_factor + delta))
        self.sort_zoom_var.set(f"{round(self.sort_zoom_factor * 100):d}%")
        self._render_selected_sort_page()

    def _fit_sort_page(self) -> None:
        self.sort_zoom_factor = 1.0
        self.sort_zoom_var.set("100%")
        self._render_selected_sort_page()

    def _rotate_sort_page(self, degrees: int) -> None:
        source = self._selected_source()
        if source is None:
            return
        key = (source, self.sort_page_index)
        self.sort_page_rotations[key] = (self.sort_page_rotations.get(key, 0) + degrees) % 360
        self._render_selected_sort_page()

    @staticmethod
    def _empty_queue_message(filter_name: str) -> str:
        return f"No {filter_name} documents remain in the queue."

    def _clear_sort_selection(self) -> None:
        for variable in (self.unit_var, self.type_var, self.date_var, self.page_var):
            variable.set("")
        self.reason_var.set("No document selected.")
        filter_name = self.filter_var.get()
        self.destination_var.set(f"Select a {filter_name} document to review.")
        self.sort_preview_generation += 1
        self.sort_page_index = 0
        self.sort_page_count = 0
        self.sort_page_status_var.set("No document selected")
        self._set_sort_page_message(self._empty_queue_message(filter_name))

    def _visible_source_order(self) -> tuple[str, ...]:
        return tuple(
            item.get("source_file", "")
            for item in self.model.filtered(self.filter_var.get())
        )

    def _refresh_and_select_next(self, prior_order: tuple[str, ...], completed_source: str) -> None:
        self._refresh()
        target = next_active_source(prior_order, completed_source, self._visible_source_order())
        if target is None:
            self._clear_sort_selection()
            return
        self._select_source(target)

    def save_correction(self) -> None:
        if getattr(self, "bulk_action_running", False):
            self.status_var.set("Wait for the bulk Not DOT action to finish.")
            return
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
        if getattr(self, "bulk_action_running", False):
            self.status_var.set("Wait for the bulk Not DOT action to finish.")
            return
        before = self._selected_result()
        if not before:
            messagebox.showinfo("Select a document", "Select a document to approve.")
            return
        if before.get("status") == "approved":
            messagebox.showwarning("Already approved", "This document has already been approved.")
            return
        prior_order = self._visible_source_order()
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
        self._refresh_and_select_next(prior_order, approved["source_file"])

    def mark_selected_duplicate(self) -> None:
        if getattr(self, "bulk_action_running", False):
            self.status_var.set("Wait for the bulk Not DOT action to finish.")
            return
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
        prior_order = self._visible_source_order()
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
        self.status_var.set(f"Marked duplicate and archived: {source_name}")
        self._refresh_and_select_next(prior_order, duplicate["source_file"])

    def mark_selected_not_dot(self) -> None:
        if getattr(self, "bulk_action_running", False):
            self.status_var.set("Wait for the bulk Not DOT action to finish.")
            return
        selected = self._selected_results()
        if not selected:
            messagebox.showinfo("Select a document", "Select a document to remove from the DOT workflow.")
            return
        ineligible = [
            result
            for result in selected
            if result.get("status") in {"approved", "duplicate", "not_dot"}
        ]
        if ineligible:
            messagebox.showwarning(
                "Cannot remove document",
                "The selection includes Approved, Duplicate, or Not DOT records. Select only active review documents.",
            )
            return
        if len(selected) > 1:
            self._start_bulk_not_dot(selected)
            return
        result = selected[0]
        prior_order = self._visible_source_order()
        source_name = Path(result.get("source_file") or "").name
        classification = self._choose_non_dot_classification(source_name)
        if classification is None:
            return
        try:
            not_dot = mark_not_dot_document(
                result,
                classification=classification,
                audit_path=self.audit_path,
                incoming_folder=self.incoming,
                exceptions_folder=self.exceptions,
            )
        except ReviewValidationError as error:
            messagebox.showerror("Document not removed", str(error))
            return
        self.model.replace(not_dot)
        save_review_session(self.session_path, self.model.results)
        self.status_var.set(
            f"Classified as {not_dot['non_dot_classification_label']} and removed from DOT workflow: {source_name}"
        )
        self._refresh_and_select_next(prior_order, not_dot["source_file"])

    def _start_bulk_not_dot(self, candidates: list[dict]) -> None:
        if self.scanning or self.ocr_running or getattr(self, "bulk_action_running", False):
            self.status_var.set("Wait for the current scan, OCR, or bulk action to finish.")
            return
        classification = self._choose_non_dot_classification(f"{len(candidates)} selected documents")
        if classification is None:
            return
        label = NON_DOT_DOCUMENT_TYPES[classification]
        filter_name = self.filter_var.get()
        if not messagebox.askyesno(
            "Confirm bulk Not DOT",
            f"Classify and archive {len(candidates)} selected documents from {filter_name} as {label}?\n\n"
            "Each source fingerprint will be verified. Failures will be left in place and the batch will continue.",
            parent=self.root,
            icon="warning",
        ):
            return
        self.bulk_action_running = True
        self.not_dot_button.configure(state="disabled")
        self.select_all_button.configure(state="disabled")
        self.save_correction_button.configure(state="disabled")
        self.approve_button.configure(state="disabled")
        self.duplicate_button.configure(state="disabled")
        self.ocr_button.configure(state="disabled")
        self.bulk_ocr_button.configure(state="disabled")
        self.scan_button.configure(state="disabled")
        self.import_button.configure(state="disabled")
        self.progress.configure(maximum=len(candidates), value=0)
        self.progress_text.configure(text=f"0 of {len(candidates)}")
        self.status_var.set(f"Starting bulk Not DOT for {len(candidates)} documents...")
        snapshot = [dict(item) for item in self.model.results]
        threading.Thread(
            target=self._bulk_not_dot_worker,
            args=([dict(item) for item in candidates], classification, snapshot),
            daemon=True,
        ).start()

    def _bulk_not_dot_worker(
        self,
        candidates: list[dict],
        classification: str,
        results: list[dict],
    ) -> None:
        completed = 0
        errors = []
        total = len(candidates)
        indexes = {item.get("source_file"): index for index, item in enumerate(results)}
        for index, result in enumerate(candidates, start=1):
            filename = Path(result.get("source_file") or "").name
            try:
                not_dot = mark_not_dot_document(
                    result,
                    classification=classification,
                    audit_path=self.audit_path,
                    incoming_folder=self.incoming,
                    exceptions_folder=self.exceptions,
                )
                result_index = indexes.get(not_dot.get("source_file"))
                if result_index is None:
                    results.append(not_dot)
                    indexes[not_dot.get("source_file")] = len(results) - 1
                else:
                    results[result_index] = not_dot
                save_review_session(self.session_path, results)
                completed += 1
                detail = "archived"
            except Exception as error:
                errors.append({"filename": filename, "error": str(error)})
                detail = "failed"
            self.events.put(("bulk_not_dot_progress", index, total, filename, detail))
        self.events.put(
            (
                "bulk_not_dot_done",
                {
                    "completed": completed,
                    "failed": len(errors),
                    "errors": errors,
                    "total": total,
                    "results": results,
                },
            )
        )

    def _choose_non_dot_classification(self, source_name: str) -> str | None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Classify Not DOT Document")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.configure(background=DARK_THEME["surface"])
        selected_label = tk.StringVar()
        choice = {"code": None}

        ttk.Label(
            dialog,
            text=f"Choose the classification for {source_name}:",
            style="Glass.TLabel",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(18, 8))
        selector = ttk.Combobox(
            dialog,
            textvariable=selected_label,
            values=tuple(NON_DOT_DOCUMENT_TYPES.values()),
            state="readonly",
            width=32,
        )
        selector.grid(row=1, column=0, columnspan=2, sticky="ew", padx=18, pady=(0, 10))
        ttk.Label(
            dialog,
            text="The PDF will be archived under Exceptions\\Not DOT.\n"
            "This classification will be saved for future classifier training.",
            style="Muted.TLabel",
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=18, pady=(0, 14))

        def archive() -> None:
            label = selected_label.get()
            code = next((key for key, value in NON_DOT_DOCUMENT_TYPES.items() if value == label), None)
            if code is None:
                messagebox.showwarning(
                    "Choose a classification",
                    "Select a document classification before archiving.",
                    parent=dialog,
                )
                return
            choice["code"] = code
            dialog.destroy()

        ttk.Button(dialog, text="Cancel", command=dialog.destroy).grid(
            row=3, column=0, sticky="e", padx=(18, 6), pady=(0, 18)
        )
        ttk.Button(dialog, text="Classify and Archive", command=archive).grid(
            row=3, column=1, sticky="w", padx=(6, 18), pady=(0, 18)
        )
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.grab_set()
        selector.focus_set()
        dialog.wait_window()
        return choice["code"]

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
    config_path = Path(config_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    root = tk.Tk()
    DotReviewApp(root, config, config_path=config_path)
    root.mainloop()
