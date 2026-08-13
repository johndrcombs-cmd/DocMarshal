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
    TOOL_BINDER_SECTIONS,
    advance_binder_position,
    list_binder_documents,
    list_binders,
    list_tool_binders,
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
from .processor import analyze_pdf, source_fingerprint
from .settings import SETTING_DEFINITIONS, save_user_settings
from .tool_calibration import calibration_status
from .tools_database import (
    add_tool_certification,
    create_tool,
    ensure_tools_schema,
    export_tools_workbook,
    get_tool,
    import_tools_workbook,
    list_tool_certifications,
    list_tools,
    update_tool,
)
from .ui_layout import (
    _rounded_rectangle,
    button_role_visuals,
    RoundedSurface,
    build_application_ui,
    install_rounded_ttk_elements,
)
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
    "window": "#090E17",
    "navigation": "#0D1420",
    "surface": "#111B29",
    "raised": "#172334",
    "input": "#1B293B",
    "surface_hover": "#22334A",
    "selected": "#203B50",
    "border": "#223247",
    "border_quiet": "#182536",
    "border_focus": "#27C2D1",
    "accent": "#F5A814",
    "accent_hover": "#FFB92E",
    "amber_soft": "#D7A247",
    "cyan": "#27C2D1",
    "teal": "#27D3A2",
    "indigo": "#8B7CF6",
    "magenta": "#D65AD1",
    "text": "#F2F6FA",
    "secondary": "#A5B3C4",
    "muted": "#7F91A9",
    "success": "#28C890",
    "warning": "#F2B84B",
    "danger": "#F06464",
}

UI_TOKENS = {
    "font": {
        "family": "Segoe UI Variable",
        "fallback": "Segoe UI",
        "product": 18,
        "pane_title": 11,
        "body": 10,
        "label": 9,
        "count": 13,
    },
    "spacing": {
        "outer": 8,
        "region": 10,
        "pane_gap": 8,
        "panel_padding": 14,
        "field_group": 10,
        "control": 7,
    },
    "radius": {
        "panel": 11,
        "card": 10,
        "button": 8,
        "input": 8,
        "pill": 18,
        "dialog": 13,
    },
    "elevation": {"toolbar": 1, "panel": 2, "menu": 2, "footer": 2},
    "motion_ms": {"hover": 140, "focus": 140, "press": 120},
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
    "CAL": "Calibration / Certification",
}

DISPLAY_ACRONYMS = {"DOT", "PDF", "VIN", "OCR", "ID", "RP", "REG", "INS", "MISC", "CAL"}


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


def approved_record_path(result: dict) -> Path | None:
    if "approved_archived_file" in result and result.get("approved_archived_file"):
        archive = Path(result["approved_archived_file"])
        return archive if archive.is_file() else None
    source_value = result.get("source_file")
    source = Path(source_value) if source_value else None
    return source if source is not None and source.is_file() else None


def approved_record_file_exists(result: dict) -> bool:
    return approved_record_path(result) is not None


class DotReviewApp:
    def __init__(self, root: tk.Tk, config: dict, config_path: str | Path | None = None):
        self.root = root
        self.config = config
        self.config_path = Path(config_path) if config_path is not None else None
        self.incoming = Path(config["scan_incoming"])
        self.processed = Path(config["scan_processed"])
        self.approved = Path(config["scan_approved"])
        self.exceptions = Path(config["scan_exceptions"])
        self.review_folder = Path(config["scan_review"])
        self.database = Path(config["fleet_database"])
        self.manual_assets_registry = Path(
            config.get("manual_assets_registry", self.database.parent / "manual_assets.json")
        )
        self.unit_root = Path(config["unit_folders_root"])
        self.farm_unit_root = Path(config["farm_asset_folders_root"])
        self.tool_root = Path(config["tool_folders_root"])
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
        self.root.geometry("1920x1080")
        self.root.minsize(1280, 820)
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

    def _bind_mouse_wheel_to_widget_tree(self, canvas: tk.Canvas, widget: tk.Widget) -> None:
        callback = lambda event: self._scroll_canvas_with_mouse_wheel(canvas, event)
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            widget.bind(sequence, callback)
        for child in widget.winfo_children():
            self._bind_mouse_wheel_to_widget_tree(canvas, child)

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

    @staticmethod
    def _review_field_labels(document_type: object) -> tuple[str, str]:
        code = DotReviewApp._document_type_code(document_type)
        subject_label = "Tool ID" if code == "CAL" else "Unit"
        date_labels = {
            "DOT": "Inspection Date",
            "RP": "Service / Invoice Date",
            "REG": "Expiration Date",
            "CAB": "Expiration Date",
            "INS": "Expiration Date",
            "TITLE": "Issue Date",
            "CERTORIGIN": "Issue Date",
            "MISC": "Document Date",
            "CAL": "Due / Expiration Date",
        }
        return subject_label, date_labels.get(code, "Controlling Date")

    def _sync_review_field_labels(self, *_args) -> None:
        subject_label, date_label = self._review_field_labels(self.type_var.get())
        self.subject_field_label_var.set(subject_label)
        self.date_field_label_var.set(date_label)

    def _on_review_document_type_changed(self, *_args) -> None:
        previous_subject_label = self.subject_field_label_var.get()
        subject_label, _date_label = self._review_field_labels(self.type_var.get())
        if previous_subject_label and previous_subject_label != subject_label:
            self.unit_var.set("")
            self.date_var.set("")
        self._sync_review_field_labels()

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
        install_rounded_ttk_elements(self.root, style, theme)
        style.layout("Primary.TButton", style.layout("PolishPrimary.TButton"))
        style.layout("Warning.TButton", style.layout("PolishWarning.TButton"))
        style.layout("Danger.TButton", style.layout("PolishDanger.TButton"))
        self.root.option_add("*Font", "{Segoe UI} 10")
        self.root.option_add("*TCombobox*Listbox.background", theme["input"])
        self.root.option_add("*TCombobox*Listbox.foreground", theme["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", theme["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", theme["text"])

        style.configure("TFrame", background=theme["window"])
        style.configure("Navigation.TFrame", background=theme["navigation"])
        style.configure("Glass.TFrame", background=theme["surface"], borderwidth=0)
        style.configure("GlassContent.TFrame", background=theme["surface"], borderwidth=0)
        style.configure("Borderless.TFrame", background=theme["surface"], borderwidth=0)
        style.configure("Raised.TFrame", background=theme["raised"], borderwidth=0)
        style.configure("Toolbar.TFrame", background=theme["raised"], borderwidth=0)
        style.configure("ActionBar.TFrame", background=theme["raised"], borderwidth=0)
        style.configure("StatusBar.TFrame", background=theme["navigation"], borderwidth=0)
        style.configure("ShortDivider.TFrame", background=theme["border"], borderwidth=0)
        style.configure("TLabel", background=theme["window"], foreground=theme["text"])
        style.configure("Glass.TLabel", background=theme["surface"], foreground=theme["text"])
        style.configure("Raised.TLabel", background=theme["raised"], foreground=theme["text"])
        style.configure("Navigation.TLabel", background=theme["navigation"], foreground=theme["text"])
        style.configure("StatusBar.TLabel", background=theme["navigation"], foreground=theme["secondary"])
        style.configure("NavigationMuted.TLabel", background=theme["navigation"], foreground=theme["muted"])
        style.configure(
            "ReadyPill.TLabel",
            background="#123127",
            foreground=theme["teal"],
            padding=(10, 5),
            font=("Segoe UI", 8, "bold"),
        )
        style.configure(
            "PaneTitle.TLabel",
            background=theme["surface"],
            foreground=theme["text"],
            font=("Segoe UI", 11, "bold"),
        )
        style.configure(
            "SectionTitle.TLabel",
            background=theme["surface"],
            foreground=theme["secondary"],
            font=("Segoe UI", 10, "bold"),
        )
        style.configure(
            "Header.TLabel",
            background=theme["surface"],
            foreground=theme["text"],
            font=("Segoe UI Variable Display", 17, "bold"),
        )
        style.configure(
            "Subtitle.TLabel",
            background=theme["surface"],
            foreground=theme["secondary"],
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
            background=theme["raised"], foreground=theme["text"],
            font=("Segoe UI", 10, "bold"), padding=(12, 8), anchor="w", borderwidth=0,
        )
        for name, color in (("Total", theme["cyan"]), ("Ready", theme["teal"]),
                            ("Review", theme["warning"]), ("Approved", theme["indigo"]),
                            ("Failed", theme["danger"]), ("Duplicate", theme["magenta"]),
                            ("NotDot", theme["secondary"])):
            style.configure(f"{name}.Count.TLabel", foreground=color)
        style.configure(
            "Status.TLabel",
            background=theme["navigation"],
            foreground=theme["secondary"],
            padding=(10, 6),
            borderwidth=0,
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
            bordercolor=theme["border_quiet"],
            lightcolor=theme["border_quiet"],
            darkcolor=theme["border_quiet"],
            padding=(8, 5),
        )
        style.map(
            "TEntry",
            bordercolor=[("focus", theme["border_focus"])],
            lightcolor=[("focus", theme["border_focus"])],
            darkcolor=[("focus", theme["border_focus"])],
        )
        style.configure(
            "Rounded.TEntry", fieldbackground=theme["input"], foreground=theme["text"],
            insertcolor=theme["text"], padding=(9, 6), borderwidth=0,
        )
        style.configure(
            "TCombobox",
            fieldbackground=theme["input"],
            background=theme["input"],
            foreground=theme["text"],
            arrowcolor=theme["muted"],
            bordercolor=theme["border_quiet"],
            lightcolor=theme["border_quiet"],
            darkcolor=theme["border_quiet"],
            padding=(7, 5),
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", theme["input"]), ("focus", theme["input"])],
            foreground=[("readonly", theme["text"]), ("focus", theme["text"])],
            bordercolor=[("focus", theme["border_focus"])],
            arrowcolor=[("active", theme["text"])],
        )
        style.configure(
            "Rounded.TCombobox", fieldbackground=theme["input"], background=theme["input"],
            foreground=theme["text"], arrowcolor=theme["muted"], padding=(9, 6), borderwidth=0,
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
            padding=(10, 6),
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
            padding=(13, 7),
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

        role_foregrounds = {
            "primary": theme["text"],
            "secondary": theme["text"],
            "utility": theme["secondary"],
            "warning": theme["warning"],
            "danger": theme["danger"],
        }
        for role, (normal, hover, _pressed, border) in button_role_visuals(theme).items():
            if role == "main":
                continue
            style_name = f"Polish{role.title()}.TButton"
            foreground = role_foregrounds[role]
            style.configure(
                style_name,
                background=normal,
                foreground=foreground,
                bordercolor=border,
                lightcolor=normal,
                darkcolor=normal,
                focusthickness=2,
                focuscolor=theme["border_focus"],
                font=("Segoe UI", 9, "bold" if "Primary" in style_name or "Secondary" in style_name else "normal"),
            )
            style.map(
                style_name,
                background=[("pressed", theme["input"]), ("active", hover), ("disabled", theme["surface"])],
                foreground=[("disabled", theme["muted"])],
                bordercolor=[("focus", theme["border_focus"]), ("disabled", theme["surface"])],
                lightcolor=[("pressed", theme["input"]), ("active", hover), ("disabled", theme["surface"])],
                darkcolor=[("pressed", theme["input"]), ("active", hover), ("disabled", theme["surface"])],
            )

        for style_name, foreground in (
            ("Segment.TButton", theme["muted"]),
            ("Selected.Segment.TButton", theme["text"]),
        ):
            style.configure(
                style_name, foreground=foreground, padding=(17, 7),
                font=("Segoe UI", 9, "bold"), borderwidth=0,
            )
            style.map(style_name, foreground=[("active", theme["text"]), ("focus", theme["text"])])

        style.configure(
            "Modern.Treeview",
            background=theme["input"],
            fieldbackground=theme["input"],
            foreground=theme["text"],
            bordercolor=theme["input"],
            lightcolor=theme["input"],
            darkcolor=theme["input"],
            borderwidth=0,
            rowheight=34,
            font=("Segoe UI", 9),
        )
        style.map(
            "Modern.Treeview",
            background=[("selected", theme["selected"])],
            foreground=[("selected", theme["text"])],
        )
        style.configure(
            "Modern.Treeview.Heading",
            background=theme["surface_hover"],
            foreground=theme["text"],
            bordercolor=theme["surface_hover"],
            lightcolor=theme["surface_hover"],
            darkcolor=theme["surface_hover"],
            padding=(8, 8),
            font=("Segoe UI", 9, "bold"),
        )
        style.map("Modern.Treeview.Heading", background=[("active", theme["raised"])])
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
        style.configure("Thin.Vertical.TScrollbar", width=8, arrowsize=0, borderwidth=0)
        style.configure("Thin.Horizontal.TScrollbar", width=8, arrowsize=0, borderwidth=0)
        style.configure("TPanedwindow", background=theme["window"], sashwidth=8)
        style.configure(
            "TNotebook",
            background=theme["navigation"],
            bordercolor=theme["navigation"],
            tabmargins=(0, 0, 0, 0),
        )
        style.configure("Polished.TNotebook", background=theme["window"], borderwidth=0, tabmargins=0)
        style.layout("Polished.TNotebook", [("Notebook.client", {"sticky": "nsew"})])
        style.layout("Polished.TNotebook.Tab", [])
        style.configure(
            "TNotebook.Tab",
            background=theme["navigation"],
            foreground=theme["muted"],
            bordercolor=theme["navigation"],
            lightcolor=theme["navigation"],
            darkcolor=theme["navigation"],
            padding=(22, 8),
            font=("Segoe UI", 10, "bold"),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", theme["raised"]), ("active", theme["surface_hover"])],
            foreground=[("selected", theme["text"]), ("active", theme["text"])],
            bordercolor=[("selected", theme["cyan"])],
            lightcolor=[("selected", theme["raised"]), ("active", theme["surface_hover"])],
            darkcolor=[("selected", theme["raised"]), ("active", theme["surface_hover"])],
        )
        return style

    def _build_ui(self) -> None:
        build_application_ui(
            self,
            app_name=APP_NAME,
            theme=DARK_THEME,
            document_type_choices=DOCUMENT_TYPE_CHOICES,
            document_type_labels=DOCUMENT_TYPE_LABELS,
        )

    def _reset_sort_panes(self) -> None:
        if not hasattr(self, "sort_workspace"):
            return
        self.sort_workspace.update_idletasks()
        width = self.sort_workspace.winfo_width()
        if width > 900:
            self.sort_workspace.sashpos(0, round(width * 0.29))
            self.sort_workspace.sashpos(1, round(width * 0.72))

    def _set_review_action_state(self, selected: bool) -> None:
        state = "normal" if selected else "disabled"
        for widget in (
            self.open_pdf_button,
            self.open_destination_button,
            self.ocr_button,
            self.save_correction_button,
            self.approve_button,
            self.duplicate_button,
            self.not_dot_button,
            self.restore_button,
        ):
            widget.configure(state=state)

    def _on_tab_changed(self, _event=None) -> None:
        selected = self.navigation.select()
        if selected == str(self.database_tab):
            self._refresh_database_table()
        elif selected == str(self.binder_tab):
            self._refresh_binder_shelf()

    def _build_database_tab(self) -> None:
        selector = ttk.Frame(self.database_tab, style="Navigation.TFrame", padding=(8, 6))
        selector.pack(fill="x", padx=12, pady=(10, 0))
        self.database_fleet_frame = ttk.Frame(self.database_tab)
        self.database_tools_frame = ttk.Frame(self.database_tab)
        self.database_fleet_view_button = ttk.Button(
            selector, text="Fleet Assets", style="Selected.Segment.TButton",
            command=lambda: self._show_database_view("fleet"),
        )
        self.database_fleet_view_button.pack(side="left", padx=(0, 4))
        self.database_tools_view_button = ttk.Button(
            selector, text="Tools & Calibration", style="Segment.TButton",
            command=lambda: self._show_database_view("tools"),
        )
        self.database_tools_view_button.pack(side="left")
        self.database_fleet_frame.pack(fill="both", expand=True)
        self.database_toolbar_surface = RoundedSurface(
            self.database_fleet_frame, theme=DARK_THEME, radius=11, fill=DARK_THEME["raised"], padding=8, elevation=1, auto_height=True,
        )
        self.database_toolbar_surface.pack(fill="x", padx=12, pady=(10, 6))
        toolbar = self.database_toolbar_surface.content
        ttk.Label(toolbar, text="FLEET ASSET DATABASE", style="Field.TLabel").pack(side="left", padx=(0, 14))
        self.database_search_var = tk.StringVar()
        search = ttk.Entry(toolbar, textvariable=self.database_search_var, width=28, style="Rounded.TEntry")
        search.pack(side="left", padx=(8, 10))
        search.bind("<Return>", lambda _event: self._refresh_database_table())
        ttk.Button(toolbar, text="Search", command=self._refresh_database_table, style="PolishUtility.TButton").pack(side="left", padx=3)
        ttk.Button(toolbar, text="Refresh", command=self._refresh_database_table, style="PolishUtility.TButton").pack(side="left", padx=3)
        ttk.Button(toolbar, text="Add Trackable Field", command=self._add_database_field, style="PolishUtility.TButton").pack(side="right", padx=3)
        ttk.Button(toolbar, text="Export XLSX", command=self._export_database, style="PolishUtility.TButton").pack(side="right", padx=3)
        ttk.Button(toolbar, text="Import XLSX", command=self._import_database, style="PolishUtility.TButton").pack(side="right", padx=3)

        pane = ttk.Panedwindow(self.database_fleet_frame, orient="vertical")
        pane.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        self.database_table_surface = RoundedSurface(
            pane, theme=DARK_THEME, radius=11, fill=DARK_THEME["input"], padding=6, elevation=2,
        )
        table_frame = self.database_table_surface.content
        self.database_editor_surface = RoundedSurface(
            pane, theme=DARK_THEME, radius=11, fill=DARK_THEME["surface"], padding=12, elevation=2,
        )
        editor = self.database_editor_surface.content
        pane.add(self.database_table_surface, weight=4)
        pane.add(self.database_editor_surface, weight=1)
        ttk.Label(editor, text="Edit Selected Asset", style="PaneTitle.TLabel").grid(
            row=0, column=0, columnspan=8, sticky="w", pady=(0, 8)
        )

        columns = ("unit", "owner", "type", "year", "make", "model", "plate", "vin", "dot")
        self.database_table = ttk.Treeview(
            table_frame, columns=columns, show="headings", selectmode="browse", style="Modern.Treeview",
        )
        headings = {
            "unit": "Unit", "owner": "Ownership", "type": "Unit Type", "year": "Year",
            "make": "Make", "model": "Model", "plate": "Plate / Tag", "vin": "VIN / Serial",
            "dot": "DOT Status",
        }
        widths = {"unit": 70, "owner": 125, "type": 100, "year": 65, "make": 110, "model": 120, "plate": 100, "vin": 180, "dot": 100}
        for column in columns:
            self.database_table.heading(column, text=headings[column])
            self.database_table.column(column, width=widths[column], minwidth=widths[column], anchor="w")
        db_vertical = ttk.Scrollbar(table_frame, orient="vertical", command=self.database_table.yview, style="Thin.Vertical.TScrollbar")
        db_horizontal = ttk.Scrollbar(table_frame, orient="horizontal", command=self.database_table.xview, style="Thin.Horizontal.TScrollbar")
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
            row = (index // 4) * 2 + 1
            column = (index % 4) * 2
            ttk.Label(editor, text=label, style="Field.TLabel").grid(row=row, column=column, sticky="w", padx=(0, 6), pady=(0, 2))
            variable = tk.StringVar()
            self.database_field_vars[field] = variable
            if field == "asset_owner":
                widget = ttk.Combobox(editor, textvariable=variable, values=("", *ASSET_OWNERS), state="readonly", width=20, style="Rounded.TCombobox")
            else:
                widget = ttk.Entry(editor, textvariable=variable, width=22, style="Rounded.TEntry")
            widget.grid(row=row + 1, column=column, columnspan=2, sticky="ew", padx=(0, 12), pady=(0, 7))
        for column in range(8):
            editor.columnconfigure(column, weight=1)
        self.database_custom_frame = ttk.Frame(editor, style="GlassContent.TFrame")
        self.database_custom_frame.grid(row=7, column=0, columnspan=8, sticky="ew", pady=(2, 0))
        self.database_custom_vars: dict[int, tk.StringVar] = {}
        actions = ttk.Frame(editor, style="GlassContent.TFrame")
        actions.grid(row=9, column=0, columnspan=8, sticky="e", pady=(8, 0))
        ttk.Button(actions, text="Save Asset Updates", command=self._save_database_record, style="Primary.TButton").pack(side="left")
        self.database_status_var = tk.StringVar(value="Select an asset to view or edit its database record.")
        ttk.Label(self.database_fleet_frame, textvariable=self.database_status_var, style="Status.TLabel", anchor="w").pack(
            side="bottom", fill="x", padx=12, pady=(0, 8), before=pane
        )
        self.database_rows: dict[str, int] = {}
        self._build_tools_database_view()

    def _show_database_view(self, view: str) -> None:
        self.database_fleet_frame.pack_forget()
        self.database_tools_frame.pack_forget()
        self.database_fleet_view_button.configure(style="Selected.Segment.TButton" if view == "fleet" else "Segment.TButton")
        self.database_tools_view_button.configure(style="Selected.Segment.TButton" if view == "tools" else "Segment.TButton")
        if view == "tools":
            self.database_tools_frame.pack(fill="both", expand=True)
            self._refresh_tools_table()
        else:
            self.database_fleet_frame.pack(fill="both", expand=True)
            self._refresh_database_table()

    def _build_tools_database_view(self) -> None:
        toolbar = ttk.Frame(self.database_tools_frame, style="Toolbar.TFrame", padding=(10, 8))
        toolbar.pack(fill="x", padx=12, pady=(6, 6))
        ttk.Label(toolbar, text="TOOLS & CALIBRATION", style="Field.TLabel").pack(side="left")
        self.tools_search_var = tk.StringVar()
        search = ttk.Entry(toolbar, textvariable=self.tools_search_var, width=28, style="Rounded.TEntry")
        search.pack(side="left", padx=(14, 6))
        search.bind("<Return>", lambda _event: self._refresh_tools_table())
        ttk.Button(toolbar, text="Search", command=self._refresh_tools_table, style="PolishUtility.TButton").pack(side="left", padx=3)
        ttk.Button(toolbar, text="Add Tool", command=self._add_tool_record, style="PolishSecondary.TButton").pack(side="right", padx=3)
        ttk.Button(toolbar, text="Export XLSX", command=self._export_tools_database, style="PolishUtility.TButton").pack(side="right", padx=3)
        ttk.Button(toolbar, text="Import XLSX", command=self._import_tools_database, style="PolishUtility.TButton").pack(side="right", padx=3)

        summary = ttk.Frame(self.database_tools_frame)
        summary.pack(fill="x", padx=12, pady=(0, 6))
        self.tool_status_vars = {}
        for code, label, color in (
            ("current", "Current", DARK_THEME["success"]),
            ("due_soon", "Due Soon", DARK_THEME["warning"]),
            ("expired", "Expired", DARK_THEME["danger"]),
            ("no_date", "No Date", DARK_THEME["muted"]),
            ("failed", "Failed", DARK_THEME["danger"]),
        ):
            variable = tk.StringVar(value=f"{label}: 0")
            self.tool_status_vars[code] = variable
            ttk.Label(summary, textvariable=variable, style="Raised.TLabel", foreground=color, padding=(12, 7)).pack(side="left", fill="x", expand=True, padx=(0, 6))

        pane = ttk.Panedwindow(self.database_tools_frame, orient="vertical")
        pane.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        table_frame = ttk.Frame(pane, style="Glass.TFrame", padding=6)
        editor = ttk.Frame(pane, style="Glass.TFrame", padding=12)
        pane.add(table_frame, weight=2)
        pane.add(editor, weight=3)
        columns = ("id", "description", "category", "maker", "model", "serial", "location", "due", "status")
        self.tools_table = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse", style="Modern.Treeview")
        headings = {"id": "Tool ID", "description": "Description", "category": "Category", "maker": "Manufacturer", "model": "Model", "serial": "Serial Number", "location": "Location", "due": "Due / Expires", "status": "Status"}
        widths = {"id": 95, "description": 190, "category": 100, "maker": 110, "model": 100, "serial": 120, "location": 110, "due": 105, "status": 105}
        for column in columns:
            self.tools_table.heading(column, text=headings[column])
            self.tools_table.column(column, width=widths[column], minwidth=70, anchor="w")
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tools_table.yview, style="Thin.Vertical.TScrollbar")
        self.tools_table.configure(yscrollcommand=scroll.set)
        self.tools_table.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.tools_table.bind("<<TreeviewSelect>>", self._on_tool_selection)
        for code, color in (("current", DARK_THEME["success"]), ("due_soon", DARK_THEME["warning"]), ("expired", DARK_THEME["danger"]), ("no_date", DARK_THEME["muted"]), ("failed", DARK_THEME["danger"])):
            self.tools_table.tag_configure(code, foreground=color)

        details = ttk.Frame(editor, style="GlassContent.TFrame")
        details.pack(side="left", fill="both", expand=True, padx=(0, 10))
        history = ttk.Frame(editor, style="GlassContent.TFrame")
        history.pack(side="left", fill="both", expand=True)
        ttk.Label(details, text="Tool Details", style="PaneTitle.TLabel").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 7))
        fields = (
            ("tool_id", "Tool ID"), ("description", "Description"), ("category", "Category"),
            ("manufacturer", "Manufacturer"), ("model", "Model"), ("serial_number", "Serial Number"),
            ("location", "Location"), ("custodian", "Custodian"),
            ("calibration_interval_months", "Interval Months"), ("notes", "Notes"),
        )
        self.tool_field_vars = {}
        for index, (field, label) in enumerate(fields):
            row, column = (index // 2) * 2 + 1, (index % 2) * 2
            ttk.Label(details, text=label, style="Field.TLabel").grid(row=row, column=column, columnspan=2, sticky="w")
            variable = tk.StringVar()
            self.tool_field_vars[field] = variable
            ttk.Entry(details, textvariable=variable, style="Rounded.TEntry").grid(row=row + 1, column=column, columnspan=2, sticky="ew", padx=(0, 8), pady=(2, 5))
        self.tool_required_var = tk.BooleanVar(value=True)
        self.tool_active_var = tk.BooleanVar(value=True)
        checks = ttk.Frame(details, style="GlassContent.TFrame")
        checks.grid(row=11, column=0, columnspan=4, sticky="ew", pady=(5, 0))
        ttk.Checkbutton(checks, text="Calibration required", variable=self.tool_required_var).pack(side="left")
        ttk.Checkbutton(checks, text="Active", variable=self.tool_active_var).pack(side="left", padx=12)
        ttk.Button(checks, text="Save Tool", command=self._save_tool_record, style="PolishPrimary.TButton").pack(side="right")
        for column in range(4):
            details.columnconfigure(column, weight=1)

        header = ttk.Frame(history, style="GlassContent.TFrame")
        header.pack(fill="x", pady=(0, 7))
        ttk.Label(header, text="Calibration / Certification History", style="PaneTitle.TLabel").pack(side="left")
        ttk.Button(header, text="Add Record", command=self._add_tool_certification, style="PolishSecondary.TButton").pack(side="right")
        history_columns = ("type", "performed", "due", "result", "provider")
        self.tool_history_table = ttk.Treeview(history, columns=history_columns, show="headings", style="Modern.Treeview")
        for column, label, width in (("type", "Type", 95), ("performed", "Performed", 90), ("due", "Due", 90), ("result", "Result", 75), ("provider", "Provider", 130)):
            self.tool_history_table.heading(column, text=label)
            self.tool_history_table.column(column, width=width, anchor="w")
        self.tool_history_table.pack(fill="both", expand=True)
        self.tools_rows = {}
        self.active_tool_id = None
        self.tools_status_var = tk.StringVar(value="No tool records loaded.")
        ttk.Label(self.database_tools_frame, textvariable=self.tools_status_var, style="Status.TLabel").pack(fill="x", padx=12, pady=(0, 8))

    def _tool_status(self, tool_id: int):
        history = list_tool_certifications(self.database, tool_id)
        if not history:
            return calibration_status(None)
        latest = history[0]
        return calibration_status(latest.get("due_date"), result=latest.get("result"))

    def _refresh_tools_table(self) -> None:
        if not hasattr(self, "tools_table"):
            return
        self.tools_table.delete(*self.tools_table.get_children())
        self.tools_rows.clear()
        try:
            ensure_tools_schema(self.database)
            tools = list_tools(self.database, self.tools_search_var.get())
            counts = {code: 0 for code in self.tool_status_vars}
            for tool in tools:
                status = self._tool_status(tool["id"])
                counts[status.code] += 1
                history = list_tool_certifications(self.database, tool["id"])
                due = history[0].get("due_date") if history else ""
                item_id = f"tool-{tool['id']}"
                self.tools_table.insert(
                    "", "end", iid=item_id, tags=(status.code,),
                    values=(
                        tool["display_tool_id"], tool["description"], tool["category"],
                        tool["manufacturer"], tool["model"], tool["serial_number"], tool["location"],
                        due or "—", status.label if tool["active"] else "Inactive",
                    ),
                )
                self.tools_rows[item_id] = tool["id"]
            labels = {"current": "Current", "due_soon": "Due Soon", "expired": "Expired", "no_date": "No Date", "failed": "Failed"}
            for code, variable in self.tool_status_vars.items():
                variable.set(f"{labels[code]}: {counts[code]}")
            self.tools_status_var.set(f"{len(tools)} tool record(s). Select one to edit or review calibration history.")
        except Exception as error:
            self.tools_status_var.set(f"Tools database unavailable: {error}")

    def _selected_tool_id(self) -> int | None:
        selected = self.tools_table.selection()
        return self.tools_rows.get(selected[0]) if selected else None

    def _on_tool_selection(self, _event=None) -> None:
        tool_id = self._selected_tool_id()
        if tool_id is None:
            return
        try:
            tool = get_tool(self.database, tool_id)
            history = list_tool_certifications(self.database, tool_id)
        except Exception as error:
            self.tools_status_var.set(f"Tool could not be loaded: {error}")
            return
        self.active_tool_id = tool_id
        for field, variable in self.tool_field_vars.items():
            key = "display_tool_id" if field == "tool_id" else field
            variable.set(tool.get(key) if tool.get(key) is not None else "")
        self.tool_required_var.set(tool["calibration_required"])
        self.tool_active_var.set(tool["active"])
        self.tool_history_table.delete(*self.tool_history_table.get_children())
        for item in history:
            self.tool_history_table.insert(
                "", "end", values=(item["certificate_type"], item["performed_date"] or "—", item["due_date"] or "—", item["result"].title(), item["provider"]),
            )
        status = self._tool_status(tool_id)
        self.tools_status_var.set(f"{tool['display_tool_id']} • {status.message}")

    def _new_tool_record(self) -> None:
        self.active_tool_id = None
        self.tools_table.selection_remove(self.tools_table.selection())
        for variable in self.tool_field_vars.values():
            variable.set("")
        self.tool_required_var.set(True)
        self.tool_active_var.set(True)
        self.tool_history_table.delete(*self.tool_history_table.get_children())
        self.tools_status_var.set("Enter the new tool details, then select Save Tool.")

    def _add_tool_record(self) -> None:
        has_new_details = self.active_tool_id is None and any(
            str(variable.get() or "").strip() for variable in self.tool_field_vars.values()
        )
        if has_new_details:
            self._save_tool_record()
        else:
            self._new_tool_record()

    def _save_tool_record(self) -> None:
        values = {field: variable.get() for field, variable in self.tool_field_vars.items()}
        values["calibration_required"] = self.tool_required_var.get()
        values["active"] = self.tool_active_var.get()
        try:
            if self.active_tool_id is None:
                tool = create_tool(self.database, values)
            else:
                tool = update_tool(self.database, self.active_tool_id, values)
        except Exception as error:
            messagebox.showerror("Tool not saved", str(error), parent=self.root)
            return
        self.active_tool_id = tool["id"]
        self._refresh_tools_table()
        item_id = f"tool-{tool['id']}"
        if self.tools_table.exists(item_id):
            self.tools_table.selection_set(item_id)
            self.tools_table.see(item_id)
            self._on_tool_selection()
        self.tools_status_var.set(f"Saved tool {tool['display_tool_id']}.")

    def _add_tool_certification(self) -> None:
        tool_id = self._selected_tool_id() or self.active_tool_id
        if tool_id is None:
            messagebox.showinfo("Select a tool", "Select a tool before adding calibration history.", parent=self.root)
            return
        certificate_type = simpledialog.askstring("Calibration Record", "Type: Calibration or Certification", initialvalue="Calibration", parent=self.root)
        if not certificate_type:
            return
        performed = simpledialog.askstring("Calibration Record", "Performed date (YYYY-MM-DD):", parent=self.root)
        if performed is None:
            return
        due = simpledialog.askstring("Calibration Record", "Due / expiration date (YYYY-MM-DD):", parent=self.root)
        if due is None:
            return
        result = simpledialog.askstring("Calibration Record", "Result: pass, fail, limited, or unknown", initialvalue="pass", parent=self.root)
        if not result:
            return
        provider = simpledialog.askstring("Calibration Record", "Calibration provider (optional):", parent=self.root)
        if provider is None:
            return
        try:
            add_tool_certification(
                self.database, tool_id,
                {"certificate_type": certificate_type, "performed_date": performed, "due_date": due, "result": result, "provider": provider},
            )
        except Exception as error:
            messagebox.showerror("Calibration record not added", str(error), parent=self.root)
            return
        self._refresh_tools_table()
        item_id = f"tool-{tool_id}"
        if self.tools_table.exists(item_id):
            self.tools_table.selection_set(item_id)
            self._on_tool_selection()

    def _import_tools_database(self) -> None:
        path = filedialog.askopenfilename(title="Import Tools & Calibration", filetypes=(("Excel workbook", "*.xlsx"),), parent=self.root)
        if not path:
            return
        try:
            summary = import_tools_workbook(path, self.database)
        except Exception as error:
            messagebox.showerror("Tools import cancelled", str(error), parent=self.root)
            return
        self._refresh_tools_table()
        self.tools_status_var.set(f"Tools import complete: {summary['updated']} updated, {summary['inserted']} added.")

    def _export_tools_database(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Export Tools & Calibration", defaultextension=".xlsx",
            filetypes=(("Excel workbook", "*.xlsx"),),
            initialfile=f"DocMarshal-Tools-{datetime.now():%Y-%m-%d}.xlsx", parent=self.root,
        )
        if not path:
            return
        try:
            export_tools_workbook(self.database, path)
        except Exception as error:
            messagebox.showerror("Tools export failed", str(error), parent=self.root)
            return
        self.tools_status_var.set(f"Tools and certification history exported to {path}")

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
            ttk.Entry(self.database_custom_frame, textvariable=variable, style="Rounded.TEntry").grid(
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
        self.settings_heading_surface = RoundedSurface(
            self.settings_tab, theme=DARK_THEME, radius=11, fill=DARK_THEME["raised"], padding=12, elevation=1, auto_height=True,
        )
        self.settings_heading_surface.pack(fill="x", padx=12, pady=(10, 6))
        heading = self.settings_heading_surface.content
        ttk.Label(
            heading,
            text="INSTALLATION SETTINGS",
            style="Field.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            heading,
            text="Local paths for document intake, processing data, and virtual binders. Changes apply after restart.",
            style="Raised.TLabel",
        ).pack(anchor="w", pady=(3, 0))
        groups = ttk.Frame(self.settings_tab)
        groups.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        self.settings_processing_surface = RoundedSurface(
            groups, theme=DARK_THEME, radius=11, fill=DARK_THEME["surface"], padding=14, elevation=2,
        )
        self.settings_processing_surface.pack(side="left", fill="both", expand=True, padx=(0, 4))
        processing_panel = self.settings_processing_surface.content
        self.settings_binder_surface = RoundedSurface(
            groups, theme=DARK_THEME, radius=11, fill=DARK_THEME["surface"], padding=14, elevation=2,
        )
        self.settings_binder_surface.pack(side="left", fill="both", expand=True, padx=(4, 0))
        binder_panel = self.settings_binder_surface.content
        ttk.Label(processing_panel, text="Document Processing", style="PaneTitle.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 9)
        )
        ttk.Label(binder_panel, text="Assets and Virtual Binders", style="PaneTitle.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 9)
        )
        self.settings_vars = {}
        processing_keys = {"scan_incoming", "scan_processed", "scan_approved", "scan_exceptions", "scan_review"}
        group_rows = {"processing": 0, "binder": 0}
        for definition in SETTING_DEFINITIONS:
            group_name = "processing" if definition["key"] in processing_keys else "binder"
            panel = processing_panel if group_name == "processing" else binder_panel
            row = group_rows[group_name]
            variable = tk.StringVar(value=str(self.config.get(definition["key"], "")))
            self.settings_vars[definition["key"]] = variable
            ttk.Label(panel, text=definition["label"], style="Field.TLabel").grid(
                row=row * 2 + 1, column=0, columnspan=2, sticky="w", pady=(3, 2)
            )
            ttk.Entry(panel, textvariable=variable, style="Rounded.TEntry").grid(row=row * 2 + 2, column=0, sticky="ew", pady=(0, 7))
            ttk.Button(
                panel,
                text="Browse…",
                command=lambda item=definition: self._browse_setting(item),
                style="PolishUtility.TButton",
            ).grid(row=row * 2 + 2, column=1, padx=(8, 0), pady=(0, 7))
            group_rows[group_name] += 1
        processing_panel.columnconfigure(0, weight=1)
        binder_panel.columnconfigure(0, weight=1)
        self.settings_footer_surface = RoundedSurface(
            self.settings_tab, theme=DARK_THEME, radius=11, fill=DARK_THEME["navigation"], padding=8, elevation=2, auto_height=True,
        )
        self.settings_footer_surface.pack(side="bottom", fill="x", padx=12, pady=(0, 8), before=groups)
        settings_footer = self.settings_footer_surface.content
        self.settings_status_var = tk.StringVar(value="Settings are stored locally in config.json and are not published.")
        ttk.Label(settings_footer, textvariable=self.settings_status_var, style="Navigation.TLabel", anchor="w").pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(settings_footer, text="Save Settings", command=self._save_settings, style="Primary.TButton").pack(side="right")

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
        pane.pack(fill="both", expand=True, padx=12, pady=(10, 8))
        self.binder_shelf_surface = RoundedSurface(pane, theme=DARK_THEME, radius=11, fill=DARK_THEME["surface"], padding=10, elevation=2)
        shelf = self.binder_shelf_surface.content
        self.binder_viewer_surface = RoundedSurface(pane, theme=DARK_THEME, radius=11, fill=DARK_THEME["surface"], padding=10, elevation=2)
        viewer = self.binder_viewer_surface.content
        pane.add(self.binder_shelf_surface, weight=1)
        pane.add(self.binder_viewer_surface, weight=4)
        ttk.Label(shelf, text="Virtual Binder Shelves", style="PaneTitle.TLabel").pack(anchor="w", pady=(0, 8))
        shelf_selector = ttk.Frame(shelf, style="GlassContent.TFrame")
        shelf_selector.pack(fill="x", pady=(0, 8))
        self.fleet_binder_button = ttk.Button(
            shelf_selector, text="Fleet Binders", style="Selected.Segment.TButton",
            command=lambda: self._set_binder_mode("fleet"),
        )
        self.fleet_binder_button.pack(side="left", fill="x", expand=True, padx=(0, 3))
        self.tool_binder_button = ttk.Button(
            shelf_selector, text="Tool Binders", style="Segment.TButton",
            command=lambda: self._set_binder_mode("tools"),
        )
        self.tool_binder_button.pack(side="left", fill="x", expand=True, padx=(3, 0))
        ttk.Label(viewer, text="Document Viewer", style="PaneTitle.TLabel").pack(anchor="w", pady=(0, 8))
        shelf_toolbar = ttk.Frame(shelf, style="GlassContent.TFrame")
        shelf_toolbar.pack(fill="x", pady=(0, 8))
        self.binder_filter_var = tk.StringVar()
        binder_filter = ttk.Entry(shelf_toolbar, textvariable=self.binder_filter_var, width=12, style="Rounded.TEntry")
        binder_filter.pack(side="left", fill="x", expand=True)
        binder_filter.bind("<Return>", lambda _event: self._refresh_binder_shelf())
        ttk.Button(
            shelf_toolbar, text="Find", command=self._refresh_binder_shelf,
            style="PolishUtility.TButton",
        ).pack(side="left", padx=(6, 0))
        shelf_frame = ttk.Frame(shelf, style="GlassContent.TFrame")
        shelf_frame.pack(fill="both", expand=True)
        self.binder_shelf = tk.Canvas(
            shelf_frame,
            width=230,
            background=DARK_THEME["input"],
            highlightthickness=0,
        )
        shelf_scroll = ttk.Scrollbar(shelf_frame, orient="vertical", command=self.binder_shelf.yview, style="Thin.Vertical.TScrollbar")
        self.binder_shelf.configure(yscrollcommand=shelf_scroll.set)
        self._bind_canvas_mouse_wheel(self.binder_shelf)
        self.binder_shelf.pack(side="left", fill="both", expand=True)
        shelf_scroll.pack(side="right", fill="y")
        self.binder_shelf.bind("<Button-1>", self._select_binder_from_shelf)
        self.binder_shelf.bind("<Motion>", self._hover_binder_shelf)
        self.binder_shelf.bind("<Leave>", self._leave_binder_shelf)
        self._binder_hover_index = None
        self._binder_selected_index = None
        self._binder_selected_identity = None

        self.binder_title_var = tk.StringVar(value="Select a binder from the shelf")
        ttk.Label(viewer, textvariable=self.binder_title_var, style="Header.TLabel").pack(anchor="w", pady=(0, 8))
        self.binder_toolbar_surface = RoundedSurface(viewer, theme=DARK_THEME, radius=10, fill=DARK_THEME["raised"], padding=7, elevation=1, auto_height=True)
        self.binder_toolbar_surface.pack(fill="x", pady=(0, 8))
        document_bar = self.binder_toolbar_surface.content
        ttk.Label(document_bar, text="Document", style="Field.TLabel").pack(side="left")
        self.binder_document_var = tk.StringVar()
        self.binder_document_box = ttk.Combobox(document_bar, textvariable=self.binder_document_var, state="readonly", style="Rounded.TCombobox")
        self.binder_document_box.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.binder_document_box.bind("<<ComboboxSelected>>", lambda _event: self._select_binder_document())
        ttk.Button(
            document_bar, text="Zoom Out", command=lambda: self._change_binder_zoom(-0.25),
            style="PolishUtility.TButton",
        ).pack(side="left", padx=(10, 3))
        self.binder_zoom_var = tk.StringVar(value="100%")
        ttk.Label(document_bar, textvariable=self.binder_zoom_var, style="Glass.TLabel", width=6, anchor="center").pack(side="left")
        ttk.Button(document_bar, text="Fit Page", command=self._fit_binder_page, style="PolishUtility.TButton").pack(side="left", padx=3)
        ttk.Button(
            document_bar, text="Zoom In", command=lambda: self._change_binder_zoom(0.25),
            style="PolishUtility.TButton",
        ).pack(side="left", padx=3)

        page_area = ttk.Frame(viewer, style="GlassContent.TFrame")
        page_area.pack(fill="both", expand=True)
        canvas_frame = ttk.Frame(page_area, style="GlassContent.TFrame")
        canvas_frame.pack(side="left", fill="both", expand=True)
        self.binder_page_canvas = tk.Canvas(
            canvas_frame,
            background=DARK_THEME["input"],
            highlightthickness=0,
        )
        binder_vertical = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.binder_page_canvas.yview, style="Thin.Vertical.TScrollbar")
        binder_horizontal = ttk.Scrollbar(canvas_frame, orient="horizontal", command=self.binder_page_canvas.xview, style="Thin.Horizontal.TScrollbar")
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
        self.binder_tabs_frame = tabs
        for label, folder in BINDER_SECTIONS:
            button = ttk.Button(
                tabs, text=label, command=lambda selected=folder: self._open_binder_section(selected),
                style="PolishUtility.TButton",
            )
            button.pack(fill="x", pady=(0, 8))
            self.binder_section_buttons.append(button)

        navigation = ttk.Frame(viewer, style="GlassContent.TFrame")
        navigation.pack(fill="x", pady=(8, 0))
        ttk.Button(
            navigation, text="‹ Previous Page", command=lambda: self._turn_binder_page(-1),
            style="PolishUtility.TButton",
        ).pack(side="left")
        self.binder_page_status_var = tk.StringVar(value="No document selected")
        ttk.Label(navigation, textvariable=self.binder_page_status_var, style="Glass.TLabel").pack(side="left", expand=True)
        ttk.Button(
            navigation, text="Next Page ›", command=lambda: self._turn_binder_page(1),
            style="PolishUtility.TButton",
        ).pack(side="right")
        self.binder_records: list[dict] = []
        self.active_binder: dict | None = None
        self.active_binder_section: str | None = None
        self.binder_document_paths: dict[str, Path] = {}
        self.active_binder_pdf: Path | None = None
        self.binder_page_index = 0
        self.binder_page_count = 0
        self.binder_page_image = None
        self.binder_zoom_factor = 1.0
        self.binder_mode = "fleet"

    def _binder_sections(self):
        return TOOL_BINDER_SECTIONS if getattr(self, "binder_mode", "fleet") == "tools" else BINDER_SECTIONS

    def _set_binder_mode(self, mode: str) -> None:
        self.binder_mode = mode
        self.fleet_binder_button.configure(style="Selected.Segment.TButton" if mode == "fleet" else "Segment.TButton")
        self.tool_binder_button.configure(style="Selected.Segment.TButton" if mode == "tools" else "Segment.TButton")
        self._binder_selected_identity = None
        self._binder_selected_index = None
        self.active_binder = None
        self.active_binder_section = None
        self.binder_document_box.configure(values=())
        self.binder_document_var.set("")
        for button in self.binder_section_buttons:
            button.destroy()
        self.binder_section_buttons = []
        for label, folder in self._binder_sections():
            button = ttk.Button(
                self.binder_tabs_frame, text=label,
                command=lambda selected=folder: self._open_binder_section(selected),
                style="PolishUtility.TButton",
            )
            button.pack(fill="x", pady=(0, 8))
            self.binder_section_buttons.append(button)
        self._refresh_binder_shelf()
        self._set_binder_page_message("Select a binder from the shelf.")

    def _refresh_binder_shelf(self) -> None:
        if not hasattr(self, "binder_shelf"):
            return
        try:
            all_records = (
                list_tool_binders(self.database, self.tool_root)
                if getattr(self, "binder_mode", "fleet") == "tools"
                else list_binders(self.database, self.unit_root, self.farm_unit_root)
            )
        except Exception as error:
            self.binder_page_status_var.set(f"Binder shelf unavailable: {error}")
            self.binder_records = []
            self.active_binder = None
            self._binder_selected_identity = None
            self._binder_selected_index = None
            self._binder_hover_index = None
            self._draw_binder_shelf()
            return
        query = self.binder_filter_var.get().strip().casefold()
        binder_mode = getattr(self, "binder_mode", "fleet")
        identity_field = "tool_id" if binder_mode == "tools" else "unit"
        self.binder_records = [
            record for record in all_records
            if query in record[identity_field].casefold()
            or (binder_mode == "tools" and query in record["description"].casefold())
        ]
        self._binder_hover_index = None
        self._binder_selected_index = next(
            (
                index
                for index, record in enumerate(self.binder_records)
                if self._binder_identity(record) == self._binder_selected_identity
            ),
            None,
        )
        if self._binder_selected_index is None and self._binder_selected_identity is not None:
            self._binder_selected_identity = None
            self.active_binder = None
        elif self._binder_selected_index is not None:
            self.active_binder = self.binder_records[self._binder_selected_index]
        self._draw_binder_shelf()
        available = sum(1 for record in all_records if record["available"])
        visible = f"{len(self.binder_records)} shown • " if query else ""
        self.binder_page_status_var.set(
            f"{visible}{available} binder folder(s) available • {len(all_records)} database record(s)."
        )

    def _draw_binder_shelf(self) -> None:
        self.binder_shelf.delete("all")
        width = max(210, self.binder_shelf.winfo_width())
        y = 12
        for index, binder in enumerate(self.binder_records):
            selected = index == self._binder_selected_index
            hovered = index == self._binder_hover_index
            fill = DARK_THEME["selected"] if selected else DARK_THEME["surface_hover"] if hovered else DARK_THEME["raised"]
            if self.binder_mode == "tools":
                accent = {
                    "current": DARK_THEME["success"], "due_soon": DARK_THEME["warning"],
                    "expired": DARK_THEME["danger"], "failed": DARK_THEME["danger"],
                    "no_date": DARK_THEME["muted"],
                }[binder["status"].code]
            else:
                accent = DARK_THEME["teal"] if binder["available"] else DARK_THEME["muted"]
            tag = f"binder-{index}"
            _rounded_rectangle(self.binder_shelf, 10, y, width - 12, y + 44, radius=9, fill=fill, outline=fill, tags=("binder", tag))
            _rounded_rectangle(self.binder_shelf, 14, y + 10, 18, y + 34, radius=2, fill=accent, outline=accent, tags=("binder", tag))
            suffix = "" if binder["available"] else "  •  folder missing"
            title = f"TOOL {binder['tool_id']}" if self.binder_mode == "tools" else f"UNIT {binder['unit']}"
            if self.binder_mode == "tools":
                suffix = f"  •  {binder['status'].label}" + suffix
            self.binder_shelf.create_text(
                26, y + 21, anchor="w", text=f"{title}{suffix}",
                fill=DARK_THEME["text"], font=("Segoe UI", 10, "bold"), tags=("binder", tag),
            )
            y += 52
        self.binder_shelf.configure(scrollregion=(0, 0, width, max(y, 1)))

    @staticmethod
    def _binder_identity(binder: dict) -> tuple[str, str]:
        return str(binder.get("tool_id", binder.get("unit", ""))), str(binder["folder"])

    def _hover_binder_shelf(self, event) -> None:
        canvas_y = self.binder_shelf.canvasy(event.y)
        index = int((canvas_y - 12) // 52) if canvas_y >= 12 else None
        if index is not None and not 0 <= index < len(self.binder_records):
            index = None
        if index != self._binder_hover_index:
            self._binder_hover_index = index
            self._draw_binder_shelf()

    def _leave_binder_shelf(self, _event=None) -> None:
        if self._binder_hover_index is not None:
            self._binder_hover_index = None
            self._draw_binder_shelf()

    def _select_binder_from_shelf(self, _event=None) -> None:
        tags = self.binder_shelf.gettags("current")
        index_tag = next((tag for tag in tags if tag.startswith("binder-")), None)
        if index_tag is None:
            return
        selected_index = int(index_tag.split("-", 1)[1])
        if not 0 <= selected_index < len(self.binder_records):
            return
        self._binder_selected_index = selected_index
        binder = self.binder_records[selected_index]
        self._binder_selected_identity = self._binder_identity(binder)
        self.active_binder = binder
        self._draw_binder_shelf()
        if self.binder_mode == "tools":
            self.binder_title_var.set(f"Tool {binder['tool_id']}  •  {binder['description']}  •  {binder['status'].message}")
        else:
            self.binder_title_var.set(f"Unit {binder['unit']}  •  {binder['owner'] or 'Unassigned ownership'}")
        if not binder["available"]:
            self._set_binder_page_message("The canonical binder folder has not been created yet.")
            self.binder_page_status_var.set(str(binder["folder"]))
            self.binder_document_box.configure(values=())
            self.binder_document_var.set("")
            return
        self._open_binder_section(self._binder_sections()[0][1])

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
        label = next(label for label, folder in self._binder_sections() if folder == section_folder)
        if not documents:
            self.active_binder_pdf = None
            self.binder_page_index = 0
            self.binder_page_count = 0
            self.binder_page_image = None
            self._set_binder_page_message(f"No PDF documents in the {label} tab.\n\nUse Next Page to continue to the next category.")
            identity = self.active_binder.get("tool_id", self.active_binder.get("unit", ""))
            self.binder_page_status_var.set(f"{identity} • {label} • 0 documents")
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
            index for index, (_label, folder) in enumerate(self._binder_sections()) if folder == self.active_binder_section
        )
        page_counts = []
        documents_by_section = []
        for _label, folder in self._binder_sections():
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
                self._binder_sections()[target_section][1],
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
        label = next(label for label, folder in self._binder_sections() if folder == self.active_binder_section)
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
                and approved_record_file_exists(item)
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
            if existing and self._source_matches_review_fingerprint(pdf_path, existing):
                result = existing
            else:
                try:
                    result = analyze_pdf(
                        pdf_path,
                        self.database,
                        self.unit_root,
                        farm_asset_folders_root=self.farm_unit_root,
                        tool_folders_root=getattr(self, "tool_root", None),
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

    @staticmethod
    def _source_matches_review_fingerprint(source: Path, result: dict) -> bool:
        expected_hash = result.get("source_sha256")
        expected_size = result.get("source_size")
        if not expected_hash or expected_size is None:
            return False
        try:
            current_size = source.stat().st_size
            if current_size != int(expected_size):
                return False
            current_hash, _size = source_fingerprint(source)
        except (OSError, TypeError, ValueError):
            return False
        return current_hash == expected_hash

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
            label.set_value(value)
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
        selected_count = len(self.table.selection())
        self.selection_count_var.set(f"{selected_count} selected")
        result = self._selected_result()
        if not result:
            self._set_review_action_state(False)
            return
        self._set_review_action_state(True)
        self.unit_var.set(
            result.get("subject_id") if result.get("subject_type") == "tool" else result.get("unit") or ""
        )
        self.type_var.set(self._document_type_label(result.get("document_type")))
        self._sync_review_field_labels()
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
        self.source_filename_var.set(Path(result.get("source_file") or "").name or "—")
        self.proposed_filename_var.set(result.get("proposed_filename") or "Not available until all fields are valid.")
        status = self._humanize_user_text(result.get("status") or "unknown")
        owner = result.get("asset_owner") or "Owner not identified"
        self.owner_status_var.set(f"{status}  •  {owner}")
        self.sort_page_index = 0
        self._render_selected_sort_page()

    def _sort_preview_path(self, result: dict) -> tuple[Path, Path]:
        status = result.get("status")
        if status == "approved":
            approved_path = approved_record_path(result)
            if approved_path is None:
                return Path(), self.approved
            archive_declared = bool(result.get("approved_archived_file"))
            return approved_path, self.approved if archive_declared else self.incoming
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
        center_x = width // 2
        center_y = height // 2
        icon_top = center_y - 58
        self.sort_page_canvas.create_rectangle(
            center_x - 18,
            icon_top,
            center_x + 18,
            icon_top + 42,
            outline=DARK_THEME["muted"],
            width=2,
        )
        self.sort_page_canvas.create_line(
            center_x + 7,
            icon_top,
            center_x + 18,
            icon_top + 11,
            fill=DARK_THEME["muted"],
            width=2,
        )
        lines = message.split("\n\n", 1)
        self.sort_page_canvas.create_text(
            center_x,
            center_y + 8,
            text=lines[0],
            fill=DARK_THEME["secondary"],
            font=("Segoe UI", 11, "bold"),
            justify="center",
            width=max(260, width - 50),
        )
        if len(lines) > 1:
            self.sort_page_canvas.create_text(
                center_x,
                center_y + 34,
                text=lines[1],
                fill=DARK_THEME["muted"],
                font=("Segoe UI", 9),
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
        self.source_filename_var.set("—")
        self.proposed_filename_var.set("—")
        self.owner_status_var.set("No document selected")
        self.selection_count_var.set("0 selected")
        self._set_review_action_state(False)
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
                tool_folders_root=getattr(self, "tool_root", None),
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
                tool_folders_root=getattr(self, "tool_root", None),
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
                approved_folder=self.approved,
                farm_asset_folders_root=self.farm_unit_root,
                tool_folders_root=getattr(self, "tool_root", None),
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
        if result.get("status") == "approved":
            path = approved_record_path(result)
            if path is None:
                messagebox.showerror("File unavailable", "The approved PDF archive cannot be found.")
                return
            if result.get("approved_archived_file"):
                expected_parent = self.approved.resolve()
                unsafe_message = "The selected PDF is not directly inside the configured Approved archive."
            else:
                expected_parent = self.incoming.resolve()
                unsafe_message = "The selected PDF is not directly inside the configured Incoming folder."
        elif result.get("status") == "duplicate":
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
