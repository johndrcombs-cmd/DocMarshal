from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk


PAGE_SUFFIXES = ("", "PG2", "PG3", "PG4", "PG5", "PG6", "PG7", "PG8", "PG9", "PG10")
FILTERS = ("Active", "All", "Ready", "Needs Review", "Approved", "Duplicates", "Not DOT", "Failed")


class _RemovedControl:
    """Compatibility shim for callbacks that still coordinate retired controls."""

    def configure(self, **_kwargs) -> None:
        pass


def button_role_visuals(theme: dict) -> dict[str, tuple[str, str, str, str]]:
    """Return normal, hover, pressed, and border colors for each action role."""
    return {
        "main": (theme["accent"], theme["accent_hover"], "#D68B08", theme["accent"]),
        "primary": (theme["selected"], theme["surface_hover"], theme["input"], theme["border_focus"]),
        "secondary": (theme["selected"], theme["surface_hover"], theme["input"], theme["border"]),
        "utility": (theme["surface"], theme["raised"], theme["input"], theme["border_quiet"]),
        "warning": (theme["selected"], theme["surface_hover"], theme["input"], theme["warning"]),
        "danger": (theme["selected"], theme["surface_hover"], theme["input"], theme["danger"]),
    }


def _rounded_rectangle(canvas, x1, y1, x2, y2, *, radius, **kwargs):
    radius = max(1, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    points = (
        x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
        x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
        x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
    )
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)


def _rounded_style_image(root, color: str, *, radius: int, border: str | None = None, backdrop: str, size=(32, 32)):
    """Create a scalable rounded patch for a native ttk style element."""
    scale = max(1.0, float(root.tk.call("tk", "scaling")))
    width, height = (max(16, round(value * scale)) for value in size)
    image = Image.new("RGB", (width, height), backdrop)
    draw = ImageDraw.Draw(image)
    scaled_radius = max(2, round(radius * scale))
    draw.rounded_rectangle(
        (0, 0, width - 1, height - 1),
        radius=scaled_radius,
        fill=color,
        outline=border or color,
        width=max(1, round(scale)),
    )
    return ImageTk.PhotoImage(image, master=root)


def install_rounded_ttk_elements(root, style: ttk.Style, theme: dict) -> None:
    """Install image-backed rounded ttk elements while retaining native widget semantics."""
    images = []

    def image(color, *, radius=8, border=None, backdrop=None, size=(32, 32)):
        result = _rounded_style_image(
            root, color, radius=radius, border=border,
            backdrop=backdrop or theme["raised"], size=size,
        )
        images.append(result)
        return result

    role_prefixes = {
        "main": "PolishMain",
        "primary": "PolishPrimary",
        "secondary": "PolishSecondary",
        "utility": "PolishUtility",
        "warning": "PolishWarning",
        "danger": "PolishDanger",
    }
    role_colors = {
        role_prefixes[role]: colors for role, colors in button_role_visuals(theme).items()
    }
    role_colors.update({
        "Segment": (theme["navigation"], theme["surface_hover"], theme["input"], theme["navigation"]),
        "Selected.Segment": (theme["selected"], theme["selected"], theme["input"], theme["selected"]),
    })
    for prefix, (normal_color, hover_color, pressed_color, border_color) in role_colors.items():
        backdrop = theme["navigation"] if "Segment" in prefix or prefix == "PolishMain" else theme["raised"]
        normal = image(normal_color, border=border_color, backdrop=backdrop)
        hover = image(hover_color, border=theme["border_focus"] if prefix == "Segment" else hover_color, backdrop=backdrop)
        pressed = image(pressed_color, border=pressed_color, backdrop=backdrop)
        focus = image(normal_color, border=theme["border_focus"], backdrop=backdrop)
        disabled = image(theme["surface"], border=theme["border_quiet"], backdrop=backdrop)
        element = f"{prefix}.rounded"
        style.element_create(
            element,
            "image",
            normal,
            ("disabled", disabled),
            ("pressed", pressed),
            ("active", hover),
            ("focus", focus),
            border=10,
            sticky="nsew",
        )
        style.layout(
            f"{prefix}.TButton",
            [(element, {"sticky": "nsew", "children": [("Button.padding", {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]})]})],
        )

    entry_normal = image(theme["input"], border=theme["border_quiet"], backdrop=theme["input"])
    entry_focus = image(theme["input"], border=theme["border_focus"], backdrop=theme["input"])
    entry_disabled = image(theme["surface"], border=theme["border_quiet"], backdrop=theme["surface"])
    style.element_create(
        "Rounded.Entry.field", "image", entry_normal,
        ("disabled", entry_disabled), ("focus", entry_focus), border=10, sticky="nsew",
    )
    style.layout(
        "Rounded.TEntry",
        [("Rounded.Entry.field", {"sticky": "nsew", "children": [("Entry.padding", {"sticky": "nsew", "children": [("Entry.textarea", {"sticky": "nsew"})]})]})],
    )

    combo_normal = image(theme["input"], border=theme["border_quiet"], backdrop=theme["input"])
    combo_focus = image(theme["input"], border=theme["border_focus"], backdrop=theme["input"])
    combo_disabled = image(theme["surface"], border=theme["border_quiet"], backdrop=theme["surface"])
    style.element_create(
        "Rounded.Combo.field", "image", combo_normal,
        ("disabled", combo_disabled), ("focus", combo_focus), border=10, sticky="nsew",
    )
    style.layout(
        "Rounded.TCombobox",
        [("Rounded.Combo.field", {"sticky": "nsew", "children": [
            ("Combobox.downarrow", {"side": "right", "sticky": "ns"}),
            ("Combobox.padding", {"sticky": "nsew", "children": [("Combobox.textarea", {"sticky": "nsew"})]}),
        ]})],
    )

    for orientation, sticky, trough_sticky in (("Vertical", "ns", "ns"), ("Horizontal", "ew", "ew")):
        trough = image(theme["input"], radius=4, border=theme["input"], backdrop=theme["input"], size=(10, 10))
        thumb = image(theme["border"], radius=4, border=theme["border"], backdrop=theme["input"], size=(10, 10))
        thumb_hover = image(theme["muted"], radius=4, border=theme["muted"], backdrop=theme["input"], size=(10, 10))
        trough_element = f"Thin.{orientation}.trough"
        thumb_element = f"Thin.{orientation}.thumb"
        style.element_create(trough_element, "image", trough, border=5, sticky=trough_sticky)
        style.element_create(thumb_element, "image", thumb, ("active", thumb_hover), border=5, sticky=sticky)
        style.layout(
            f"Thin.{orientation}.TScrollbar",
            [(trough_element, {"sticky": "nsew", "children": [(thumb_element, {"expand": "1", "sticky": sticky})]})],
        )
    root._docmarshal_style_images = images


class RoundedSurface(tk.Frame):
    """Canvas-backed rounded outer surface with one simple content frame."""

    def __init__(self, parent, *, theme: dict, radius=11, fill=None, padding=0, border=None, elevation=2, auto_height=False, **kwargs):
        super().__init__(parent, background=theme["window"], borderwidth=0, highlightthickness=0, **kwargs)
        self._theme = theme
        self._radius = radius
        self._fill = fill or theme["surface"]
        self._border = border
        self._padding = padding
        self._elevation = elevation
        self._auto_height = auto_height
        self.canvas = tk.Canvas(
            self, background=theme["window"], borderwidth=0, highlightthickness=0,
            height=1 if auto_height else 276,
        )
        self.canvas.pack(fill="both", expand=True)
        self.content = tk.Frame(self.canvas, background=self._fill, borderwidth=0, highlightthickness=0)
        self._window = self.canvas.create_window((padding, padding), window=self.content, anchor="nw")
        self.bind("<Configure>", self._redraw)
        if auto_height:
            self.content.bind("<Configure>", self._fit_content_height, add="+")
            self.after_idle(self._fit_content_height)

    def _fit_content_height(self, _event=None):
        requested = self.content.winfo_reqheight() + self._padding * 2 + self._elevation + 2
        if int(self.canvas.cget("height")) != requested:
            self.canvas.configure(height=requested)

    def _redraw(self, event=None):
        width = max(1, event.width if event else self.winfo_width())
        height = max(1, event.height if event else self.winfo_height())
        self.canvas.delete("surface")
        if self._elevation:
            _rounded_rectangle(
                self.canvas, 2 + self._elevation, 2 + self._elevation,
                width - 1, height - 1, radius=self._radius,
                fill=self._theme["window"], outline="", tags="surface",
            )
        _rounded_rectangle(
            self.canvas,
            1,
            1,
            width - 1 - self._elevation,
            height - 1 - self._elevation,
            radius=self._radius,
            fill=self._fill,
            outline=self._border or self._fill,
            width=1,
            tags="surface",
        )
        self.canvas.tag_lower("surface")
        inset = self._padding
        self.canvas.coords(self._window, inset, inset)
        window_options = {"width": max(1, width - inset * 2)}
        if not self._auto_height:
            window_options["height"] = max(1, height - inset * 2)
        self.canvas.itemconfigure(self._window, **window_options)


class SegmentedNavigation(ttk.Frame):
    """Accessible rounded navigation that delegates page ownership to the Notebook."""

    def __init__(self, parent, notebook: ttk.Notebook, tabs, *, theme: dict):
        super().__init__(parent, style="Navigation.TFrame", padding=(5, 4))
        self.notebook = notebook
        self.buttons = []
        for tab, label in tabs:
            button = ttk.Button(
                self,
                text=label,
                style="Segment.TButton",
                command=lambda selected=tab: self.notebook.select(selected),
                takefocus=True,
            )
            button.pack(side="left", padx=2)
            self.buttons.append((tab, button))
        self.notebook.bind("<<NotebookTabChanged>>", self._sync, add="+")
        self._sync()

    def _sync(self, _event=None):
        selected = self.notebook.select()
        for tab, button in self.buttons:
            button.configure(style="Selected.Segment.TButton" if str(tab) == selected else "Segment.TButton")


class RoundedButton(ttk.Button):
    """Native accessible button using the shared role-specific visual styles."""

    def __init__(
        self,
        parent,
        *,
        text,
        command,
        theme,
        role="utility",
        width=None,
        compact=False,
        radius=8,
    ):
        del theme, radius
        style_name = {
            "main": "PolishMain.TButton",
            "primary": "PolishPrimary.TButton",
            "secondary": "PolishSecondary.TButton",
            "utility": "PolishUtility.TButton",
            "warning": "PolishWarning.TButton",
            "danger": "PolishDanger.TButton",
        }[role]
        character_width = None
        super().__init__(
            parent,
            text=text,
            command=command,
            style=style_name,
            width=character_width,
            takefocus=True,
            padding=(8, 4) if compact else (11, 6),
        )


class StatusCard(tk.Canvas):
    def __init__(self, parent, *, title, color, theme):
        super().__init__(
            parent,
            width=160,
            height=46,
            background=theme["window"],
            borderwidth=0,
            highlightthickness=0,
        )
        self._title = title
        self._value = "0"
        self._color = color
        self._theme = theme
        self.bind("<Configure>", lambda _event: self._draw())

    @property
    def value(self) -> str:
        return self._value

    def set_value(self, value) -> None:
        self._value = str(value)
        self._draw()

    def _draw(self):
        self.delete("all")
        width = max(2, self.winfo_width())
        _rounded_rectangle(
            self,
            1,
            1,
            width - 1,
            45,
            radius=10,
            fill=self._theme["raised"],
            outline=self._theme["raised"],
        )
        self.create_oval(12, 20, 18, 26, fill=self._color, outline="")
        self.create_text(25, 14, text=self._title, fill=self._theme["muted"], anchor="w", font=("Segoe UI", 8))
        self.create_text(25, 31, text=self._value, fill=self._color, anchor="w", font=("Segoe UI", 12, "bold"))


def _section_header(parent, title: str, subtitle_var=None) -> ttk.Frame:
    frame = ttk.Frame(parent, style="Borderless.TFrame", padding=(14, 11, 14, 9))
    ttk.Label(frame, text=title, style="PaneTitle.TLabel").pack(side="left")
    if subtitle_var is not None:
        ttk.Label(frame, textvariable=subtitle_var, style="Glass.TLabel").pack(side="left", padx=(10, 0))
    return frame


def _build_header(app, app_name: str, theme: dict) -> None:
    from .scanner import SCAN_MODE_COMBINED, SCAN_MODES

    header = ttk.Frame(app.root, style="Navigation.TFrame", padding=(16, 11))
    header.pack(fill="x", padx=8, pady=(8, 4))
    if app.header_icon_image is not None:
        ttk.Label(header, image=app.header_icon_image, style="Navigation.TLabel").pack(side="left", padx=(0, 10))
    titles = ttk.Frame(header, style="Navigation.TFrame")
    titles.pack(side="left")
    ttk.Label(
        titles,
        text=app_name,
        style="Navigation.TLabel",
        font=("Segoe UI Variable Display", 18, "bold"),
    ).pack(anchor="w")
    ttk.Label(titles, text="Fleet document review command center", style="NavigationMuted.TLabel").pack(anchor="w")
    actions = ttk.Frame(header, style="Navigation.TFrame")
    actions.pack(side="right")
    ttk.Label(actions, text="●  READY", style="ReadyPill.TLabel").pack(
        side="left", padx=(0, 14)
    )
    scan_mode = ttk.Frame(actions, style="Navigation.TFrame")
    scan_mode.pack(side="left", padx=(0, 8))
    ttk.Label(scan_mode, text="Scan Mode", style="NavigationMuted.TLabel").pack(anchor="w")
    app.scan_mode_var = tk.StringVar(value=SCAN_MODE_COMBINED)
    app.scan_mode_box = ttk.Combobox(
        scan_mode,
        textvariable=app.scan_mode_var,
        values=SCAN_MODES,
        state="readonly",
        width=27,
        style="Rounded.TCombobox",
    )
    app.scan_mode_box.pack(anchor="w")
    app.scanner_button = RoundedButton(
        actions,
        text="▣  Scan Documents",
        command=app.scan_documents,
        theme=theme,
        role="main",
        width=166,
    )
    app.scanner_button.pack(side="left", padx=(0, 8))
    app.scan_button = RoundedButton(
        actions,
        text="↻  Refresh Incoming",
        command=app.scan_incoming,
        theme=theme,
        role="secondary",
        width=166,
    )
    app.scan_button.pack(side="left")


def _build_summary(app, theme: dict) -> None:
    cards = ttk.Frame(app.sort_tab)
    cards.pack(fill="x", padx=12, pady=(10, 6))
    app.count_labels = {}
    card_colors = {
        "total": theme["cyan"],
        "ready": theme["teal"],
        "needs_review": theme["warning"],
        "approved": theme["indigo"],
        "failed": theme["danger"],
        "duplicate": theme["magenta"],
        "not_dot": theme["secondary"],
    }
    for key, title in (
        ("total", "Total"),
        ("ready", "Ready"),
        ("needs_review", "Needs Review"),
        ("approved", "Approved"),
        ("failed", "Failed"),
        ("duplicate", "Duplicates"),
        ("not_dot", "Not DOT"),
    ):
        label = StatusCard(cards, title=title, color=card_colors[key], theme=theme)
        label.pack(side="left", fill="x", expand=True, padx=(0, 7 if key != "not_dot" else 0))
        app.count_labels[key] = (label, title)


def _build_command_row(app, theme: dict) -> None:
    row = ttk.Frame(app.sort_tab)
    row.pack(fill="x", padx=12, pady=(0, 6))
    processing = ttk.Frame(row, style="Toolbar.TFrame", padding=(10, 7))
    processing.pack(side="left", fill="x", expand=True, padx=(0, 6))
    ttk.Label(processing, text="OCR", style="Field.TLabel").pack(side="left", padx=(0, 8))
    app.progress = ttk.Progressbar(processing, mode="determinate", length=160)
    app.progress.pack(side="left", fill="x", expand=True)
    app.progress_text = ttk.Label(processing, text="Ready", style="Glass.TLabel", width=10, anchor="center")
    app.progress_text.pack(side="left", padx=8)
    app.bulk_ocr_button = RoundedButton(
        processing, text="Run OCR", command=app.run_ocr_on_all,
        theme=theme, role="secondary", width=155,
    )
    app.bulk_ocr_button.pack(side="right")

    tools = ttk.Frame(row, style="Toolbar.TFrame", padding=(8, 7))
    tools.pack(side="right")
    app.filter_var = tk.StringVar(value="Active")
    filter_box = ttk.Combobox(
        tools, textvariable=app.filter_var, values=FILTERS, state="readonly",
        width=10, style="Rounded.TCombobox",
    )
    filter_box.pack(side="left", padx=(0, 5))
    filter_box.bind("<<ComboboxSelected>>", lambda _event: app._refresh_table())
    app.ocr_button = _RemovedControl()
    app.open_destination_button = _RemovedControl()
    app.add_asset_button = _RemovedControl()
    app.restore_button = _RemovedControl()
    specs = (
        ("import_button", "＋  Import", app.import_documents, "secondary", 92),
        ("open_pdf_button", "▱  PDF", app.open_pdf, "utility", 68),
    )
    for name, text, command, role, width in specs:
        button = RoundedButton(
            tools, text=text, command=command, theme=theme, role=role, width=width, compact=True,
        )
        button.pack(side="left", padx=3)
        setattr(app, name, button)


def _build_queue(app, theme: dict) -> None:
    header = _section_header(app.sort_queue_pane, "Document Queue", app.selection_count_var)
    header.pack(fill="x")
    app.select_all_button = RoundedButton(
        header, text="Select All Visible", command=app.select_all_visible,
        theme=theme, role="secondary", width=126, compact=True,
    )
    app.select_all_button.pack(side="right")
    frame = ttk.Frame(app.sort_queue_pane, style="Borderless.TFrame")
    frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
    columns = ("file", "status", "unit", "owner", "type", "date", "filename", "reason")
    app.table = ttk.Treeview(
        frame,
        columns=columns,
        displaycolumns=("file", "status", "unit", "type", "date"),
        show="headings",
        selectmode="extended",
        style="Modern.Treeview",
    )
    headings = {
        "file": "File",
        "status": "Status",
        "unit": "Unit / Tool",
        "owner": "Owner",
        "type": "Type",
        "date": "Date",
        "filename": "Proposed Filename",
        "reason": "Review Notes",
    }
    widths = {"file": 72, "status": 95, "unit": 55, "owner": 100, "type": 82, "date": 72, "filename": 190, "reason": 220}
    for column in columns:
        app.table.heading(column, text=headings[column])
        app.table.column(column, width=widths[column], minwidth=55, stretch=column == "file", anchor="w")
    vertical = ttk.Scrollbar(
        frame, orient="vertical", command=app.table.yview,
        style="Thin.Vertical.TScrollbar",
    )
    app.table.configure(yscrollcommand=vertical.set)
    app.table.grid(row=0, column=0, sticky="nsew")
    vertical.grid(row=0, column=1, sticky="ns")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    app.table.bind("<<TreeviewSelect>>", app._on_selection)
    app.table.bind("<Double-1>", lambda _event: app.open_pdf())
    for tag, background, foreground in (
        ("needs_review", "#2B2418", "#FFD27A"),
        ("ready_for_review", "#142923", "#7FE1BD"),
        ("approved", "#1B2138", "#B7ADFF"),
        ("failed", "#301C22", "#FF9B9B"),
        ("duplicate", "#271D2C", "#E9A0E4"),
        ("not_dot", "#202936", "#B7C3D0"),
    ):
        app.table.tag_configure(tag, background=background, foreground=foreground)


def _build_viewer(app, theme: dict) -> None:
    toolbar = _section_header(app.sort_viewer_pane, "Document Viewer")
    toolbar.pack(fill="x")
    RoundedButton(toolbar, text="−", command=lambda: app._change_sort_zoom(-0.25), theme=theme, role="utility", width=32, compact=True).pack(side="left", padx=(12, 2))
    app.sort_zoom_var = tk.StringVar(value="100%")
    ttk.Label(toolbar, textvariable=app.sort_zoom_var, style="Glass.TLabel", width=6, anchor="center").pack(side="left")
    RoundedButton(toolbar, text="+", command=lambda: app._change_sort_zoom(0.25), theme=theme, role="utility", width=32, compact=True).pack(side="left", padx=2)
    RoundedButton(toolbar, text="Fit", command=app._fit_sort_page, theme=theme, role="secondary", width=48, compact=True).pack(side="left", padx=2)
    RoundedButton(toolbar, text="↶", command=lambda: app._rotate_sort_page(-90), theme=theme, role="utility", width=32, compact=True).pack(side="left", padx=(8, 2))
    RoundedButton(toolbar, text="↷", command=lambda: app._rotate_sort_page(90), theme=theme, role="utility", width=32, compact=True).pack(side="left", padx=2)


    frame = ttk.Frame(app.sort_viewer_pane, style="Borderless.TFrame")
    frame.pack(fill="both", expand=True, padx=10)
    app.sort_page_canvas = tk.Canvas(
        frame,
        background=theme["input"],
        highlightthickness=0,
    )
    vertical = ttk.Scrollbar(frame, orient="vertical", command=app.sort_page_canvas.yview, style="Thin.Vertical.TScrollbar")
    horizontal = ttk.Scrollbar(frame, orient="horizontal", command=app.sort_page_canvas.xview, style="Thin.Horizontal.TScrollbar")
    app.sort_page_canvas.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    app._bind_canvas_mouse_wheel(app.sort_page_canvas)
    app.sort_page_canvas.grid(row=0, column=0, sticky="nsew")
    vertical.grid(row=0, column=1, sticky="ns")
    horizontal.grid(row=1, column=0, sticky="ew")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    navigation = ttk.Frame(app.sort_viewer_pane, style="Borderless.TFrame", padding=(10, 9))
    navigation.pack(fill="x")
    RoundedButton(navigation, text="‹ Previous", command=lambda: app._turn_sort_page(-1), theme=theme, role="secondary", width=92, compact=True).pack(side="left")
    app.sort_page_status_var = tk.StringVar(value="No document selected")
    ttk.Label(navigation, textvariable=app.sort_page_status_var, style="Glass.TLabel", anchor="center").pack(
        side="left", fill="x", expand=True, padx=8
    )
    RoundedButton(navigation, text="Next ›", command=lambda: app._turn_sort_page(1), theme=theme, role="secondary", width=82, compact=True).pack(side="right")
    app.sort_page_index = 0
    app.sort_page_count = 0
    app.sort_page_image = None
    app.sort_zoom_factor = 1.0
    app.sort_page_rotations = {}
    app.sort_preview_generation = 0
    app._set_sort_page_message("No document selected\n\nChoose a queue row to preview its PDF.")


def _build_inspector(app, document_type_choices, document_type_labels, theme: dict) -> None:
    header = _section_header(app.sort_inspector_pane, "Review Inspector")
    header.pack(fill="x")
    ttk.Label(header, textvariable=app.owner_status_var, style="Glass.TLabel").pack(side="right")
    app.sort_action_footer_surface = RoundedSurface(
        app.sort_inspector_pane, theme=theme, radius=11, fill=theme["raised"], padding=8, elevation=2, auto_height=True,
    )
    app.sort_action_footer_surface.pack(side="bottom", fill="x", padx=8, pady=8)
    actions = app.sort_action_footer_surface.content
    app.approve_button = RoundedButton(actions, text="Approve and File Copy", command=app.approve_selected, theme=theme, role="primary", width=250)
    app.approve_button.pack(fill="x", pady=(0, 6))
    app.save_correction_button = _RemovedControl()
    app.duplicate_button = RoundedButton(actions, text="Mark Duplicate", command=app.mark_selected_duplicate, theme=theme, role="warning", width=250)
    app.duplicate_button.pack(fill="x")
    app.not_dot_button = RoundedButton(actions, text="Remove Document", command=app.mark_selected_not_dot, theme=theme, role="danger", width=250)
    app.not_dot_button.pack(fill="x", pady=(6, 0))

    inspector_region = ttk.Frame(app.sort_inspector_pane, style="Borderless.TFrame")
    inspector_region.pack(fill="both", expand=True)
    app.inspector_canvas = tk.Canvas(
        inspector_region,
        background=theme["surface"],
        highlightthickness=0,
        borderwidth=0,
    )
    inspector_scrollbar = ttk.Scrollbar(
        inspector_region,
        orient="vertical",
        command=app.inspector_canvas.yview,
        style="Thin.Vertical.TScrollbar",
    )
    app.inspector_canvas.configure(yscrollcommand=inspector_scrollbar.set)
    app.inspector_canvas.pack(side="left", fill="both", expand=True)
    inspector_scrollbar.pack(side="right", fill="y")
    inspector = ttk.Frame(app.inspector_canvas, style="Borderless.TFrame", padding=(14, 4))
    inspector_window = app.inspector_canvas.create_window((0, 0), window=inspector, anchor="nw")
    inspector.bind(
        "<Configure>",
        lambda _event: app.inspector_canvas.configure(scrollregion=app.inspector_canvas.bbox("all")),
    )
    app.inspector_canvas.bind(
        "<Configure>",
        lambda event: app.inspector_canvas.itemconfigure(inspector_window, width=event.width),
    )
    app._bind_canvas_mouse_wheel(app.inspector_canvas)
    ttk.Label(inspector, text="Classification", style="SectionTitle.TLabel").pack(anchor="w", pady=(6, 9))
    app.subject_field_label_var = tk.StringVar(value="Unit")
    ttk.Label(inspector, textvariable=app.subject_field_label_var, style="Field.TLabel").pack(anchor="w")
    unit_entry = ttk.Entry(inspector, textvariable=app.unit_var, style="Rounded.TEntry")
    unit_entry.pack(fill="x", pady=(2, 6))
    ttk.Label(inspector, text="Document Type", style="Field.TLabel").pack(anchor="w")
    document_type_box = ttk.Combobox(
        inspector,
        textvariable=app.type_var,
        values=tuple(document_type_labels[code] for code in document_type_choices),
        state="readonly",
        style="Rounded.TCombobox",
    )
    document_type_box.pack(fill="x", pady=(2, 6))
    document_type_box.bind("<<ComboboxSelected>>", app._on_review_document_type_changed)
    pair = ttk.Frame(inspector, style="Borderless.TFrame")
    pair.pack(fill="x")
    date_group = ttk.Frame(pair, style="Borderless.TFrame")
    date_group.pack(side="left", fill="x", expand=True, padx=(0, 4))
    app.date_field_label_var = tk.StringVar(value="Controlling Date")
    ttk.Label(date_group, textvariable=app.date_field_label_var, style="Field.TLabel").pack(anchor="w")
    date_entry = ttk.Entry(date_group, textvariable=app.date_var, style="Rounded.TEntry")
    date_entry.pack(fill="x", pady=(2, 6))
    page_group = ttk.Frame(pair, style="Borderless.TFrame")
    page_group.pack(side="left", fill="x", expand=True, padx=(4, 0))
    ttk.Label(page_group, text="Additional Page", style="Field.TLabel").pack(anchor="w")
    ttk.Combobox(
        page_group, textvariable=app.page_var, values=PAGE_SUFFIXES, style="Rounded.TCombobox",
    ).pack(fill="x", pady=(2, 6))
    app._bind_approval_on_enter(unit_entry, date_entry)

    ttk.Frame(inspector, style="ShortDivider.TFrame", height=1, width=54).pack(anchor="w", pady=(9, 9))
    ttk.Label(inspector, text="File Details", style="SectionTitle.TLabel").pack(anchor="w", pady=(0, 8))
    for label, variable in (
        ("Source filename", app.source_filename_var),
        ("Proposed filename", app.proposed_filename_var),
        ("Destination", app.destination_var),
    ):
        ttk.Label(inspector, text=label, style="Field.TLabel").pack(anchor="w")
        ttk.Label(inspector, textvariable=variable, style="Glass.TLabel", wraplength=410).pack(
            anchor="w", fill="x", pady=(2, 6)
        )
    ttk.Frame(inspector, style="ShortDivider.TFrame", height=1, width=54).pack(anchor="w", pady=(9, 9))
    ttk.Label(inspector, text="Review", style="SectionTitle.TLabel").pack(anchor="w", pady=(0, 8))
    ttk.Label(inspector, text="Review Notes", style="Field.TLabel").pack(anchor="w")
    ttk.Label(inspector, textvariable=app.reason_var, style="Glass.TLabel", wraplength=410).pack(
        anchor="w", fill="x", pady=(2, 6)
    )
    app._bind_mouse_wheel_to_widget_tree(app.inspector_canvas, inspector)


def build_application_ui(app, *, app_name: str, theme: dict, document_type_choices, document_type_labels) -> None:
    app._configure_theme()
    _build_header(app, app_name, theme)
    app.navigation = ttk.Notebook(app.root, style="Polished.TNotebook")
    app.navigation.pack(fill="both", expand=True, padx=8, pady=(0, 8))
    app.sort_tab, app.database_tab, app.settings_tab, app.binder_tab = (ttk.Frame(app.navigation) for _ in range(4))
    for tab, label in ((app.sort_tab, "Sort"), (app.database_tab, "Database"), (app.settings_tab, "Settings"), (app.binder_tab, "Virtual Binder")):
        app.navigation.add(tab, text=label)
    app.segmented_navigation = SegmentedNavigation(
        app.root,
        app.navigation,
        ((app.sort_tab, "Sort"), (app.database_tab, "Database"), (app.settings_tab, "Settings"), (app.binder_tab, "Virtual Binder")),
        theme=theme,
    )
    app.segmented_navigation.pack(fill="x", padx=12, pady=(0, 6), before=app.navigation)
    app.navigation.bind("<<NotebookTabChanged>>", app._on_tab_changed, add="+")
    _build_summary(app, theme)
    _build_command_row(app, theme)

    app.status_var = tk.StringVar(
        value="Ready. Click Scan Documents for a new 600 DPI color scan, or Refresh Incoming to analyze saved PDFs."
    )
    status = ttk.Frame(app.sort_tab, style="StatusBar.TFrame", padding=(12, 6))
    app.sort_status_bar = status
    status.pack(side="bottom", fill="x", padx=12, pady=(0, 8))
    ttk.Label(status, text="●", foreground=theme["teal"], style="StatusBar.TLabel").pack(side="left")
    ttk.Label(status, textvariable=app.status_var, style="StatusBar.TLabel").pack(side="left", fill="x", expand=True, padx=7)
    RoundedButton(
        status, text="Reset Layout", command=app._reset_sort_panes,
        theme=theme, role="utility", width=96, compact=True,
    ).pack(side="right")

    app.sort_workspace = ttk.Panedwindow(app.sort_tab, orient="horizontal")
    app.sort_workspace.pack(fill="both", expand=True, padx=12, pady=(0, 6))
    app.sort_queue_surface = RoundedSurface(app.sort_workspace, theme=theme, radius=11, fill=theme["surface"], width=500)
    app.sort_viewer_surface = RoundedSurface(app.sort_workspace, theme=theme, radius=11, fill=theme["surface"], width=760)
    app.sort_inspector_surface = RoundedSurface(app.sort_workspace, theme=theme, radius=11, fill=theme["surface"], width=500)
    app.sort_queue_pane = app.sort_queue_surface.content
    app.sort_viewer_pane = app.sort_viewer_surface.content
    app.sort_inspector_pane = app.sort_inspector_surface.content
    for pane, weight in ((app.sort_queue_surface, 29), (app.sort_viewer_surface, 43), (app.sort_inspector_surface, 28)):
        app.sort_workspace.add(pane, weight=weight)

    app.selection_count_var = tk.StringVar(value="0 selected")
    app.owner_status_var = tk.StringVar(value="No document selected")
    app.unit_var, app.type_var, app.date_var, app.page_var = (tk.StringVar() for _ in range(4))
    app.destination_var = tk.StringVar()
    app.reason_var = tk.StringVar()
    app.source_filename_var = tk.StringVar(value="—")
    app.proposed_filename_var = tk.StringVar(value="—")
    _build_queue(app, theme)
    _build_viewer(app, theme)
    _build_inspector(app, document_type_choices, document_type_labels, theme)
    app._set_review_action_state(False)
    app.root.after_idle(app._reset_sort_panes)
    app._build_database_tab()
    app._build_settings_tab()
    app._build_binder_tab()
