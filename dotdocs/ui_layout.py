from __future__ import annotations

import tkinter as tk
from tkinter import ttk


PAGE_SUFFIXES = ("", "PG2", "PG3", "PG4", "PG5", "PG6", "PG7", "PG8", "PG9", "PG10")
FILTERS = ("Active", "All", "Ready", "Needs Review", "Approved", "Duplicates", "Not DOT", "Failed")


def _section_header(parent, title: str, subtitle_var=None) -> ttk.Frame:
    frame = ttk.Frame(parent, style="GlassContent.TFrame", padding=(10, 7))
    ttk.Label(frame, text=title, style="Field.TLabel").pack(side="left")
    if subtitle_var is not None:
        ttk.Label(frame, textvariable=subtitle_var, style="Glass.TLabel").pack(side="left", padx=(10, 0))
    return frame


def _build_top_bar(app, app_name: str, theme: dict) -> None:
    header = ttk.Frame(app.root, style="Navigation.TFrame", padding=(16, 10))
    header.pack(fill="x")
    if app.header_icon_image is not None:
        ttk.Label(header, image=app.header_icon_image, style="Navigation.TLabel").pack(side="left", padx=(0, 10))
    titles = ttk.Frame(header, style="Navigation.TFrame")
    titles.pack(side="left")
    ttk.Label(
        titles,
        text=app_name,
        style="Navigation.TLabel",
        font=("Segoe UI Variable Display", 17, "bold"),
    ).pack(anchor="w")
    ttk.Label(titles, text="Fleet document review command center", style="Navigation.TLabel").pack(anchor="w")
    actions = ttk.Frame(header, style="Navigation.TFrame")
    actions.pack(side="right")
    ttk.Label(actions, text="●  READY", foreground=theme["teal"], style="Navigation.TLabel").pack(
        side="left", padx=(0, 14)
    )
    app.scan_button = ttk.Button(
        actions,
        text="↻  Scan Incoming Documents",
        command=app.scan_incoming,
        style="Primary.TButton",
    )
    app.scan_button.pack(side="left")


def _build_summary(app) -> None:
    cards = ttk.Frame(app.sort_tab)
    cards.pack(fill="x", padx=12, pady=(10, 6))
    app.count_labels = {}
    card_styles = {
        "total": "Total",
        "ready": "Ready",
        "needs_review": "Review",
        "approved": "Approved",
        "failed": "Failed",
        "duplicate": "Duplicate",
        "not_dot": "NotDot",
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
        card = ttk.Frame(cards, style="Raised.TFrame", padding=2)
        card.pack(side="left", fill="x", expand=True, padx=(0, 6 if key != "not_dot" else 0))
        label = ttk.Label(card, text=f"{title.upper()}   0", style=f"{card_styles[key]}.Count.TLabel")
        label.pack(fill="both", expand=True)
        app.count_labels[key] = (label, title)


def _build_command_row(app) -> None:
    row = ttk.Frame(app.sort_tab)
    row.pack(fill="x", padx=12, pady=(0, 6))
    processing = ttk.Frame(row, style="Glass.TFrame", padding=(9, 6))
    processing.pack(side="left", fill="x", expand=True, padx=(0, 6))
    ttk.Label(processing, text="OCR", style="Field.TLabel").pack(side="left", padx=(0, 8))
    app.progress = ttk.Progressbar(processing, mode="determinate", length=160)
    app.progress.pack(side="left", fill="x", expand=True)
    app.progress_text = ttk.Label(processing, text="Ready", style="Glass.TLabel", width=10, anchor="center")
    app.progress_text.pack(side="left", padx=8)
    app.bulk_ocr_button = ttk.Button(processing, text="OCR All Needing OCR", command=app.run_ocr_on_all)
    app.bulk_ocr_button.pack(side="right")

    tools = ttk.Frame(row, style="Glass.TFrame", padding=(8, 6))
    tools.pack(side="right")
    app.filter_var = tk.StringVar(value="Active")
    filter_box = ttk.Combobox(tools, textvariable=app.filter_var, values=FILTERS, state="readonly", width=13)
    filter_box.pack(side="left", padx=(0, 5))
    filter_box.bind("<<ComboboxSelected>>", lambda _event: app._refresh_table())
    specs = (
        ("import_button", "＋ Import PDFs", app.import_documents),
        ("ocr_button", "OCR Selected", app.run_ocr_on_selected),
        ("open_pdf_button", "Open PDF", app.open_pdf),
        ("open_destination_button", "Open Destination", app.open_destination),
        ("add_asset_button", "Add Asset", app.add_new_asset),
        ("restore_button", "Restore Active", app.restore_selected),
    )
    for name, text, command in specs:
        button = ttk.Button(tools, text=text, command=command)
        button.pack(side="left", padx=2)
        setattr(app, name, button)


def _build_queue(app) -> None:
    header = _section_header(app.sort_queue_pane, "DOCUMENT QUEUE", app.selection_count_var)
    header.pack(fill="x")
    app.select_all_button = ttk.Button(header, text="Select All Visible", command=app.select_all_visible)
    app.select_all_button.pack(side="right")
    frame = ttk.Frame(app.sort_queue_pane, style="GlassContent.TFrame")
    frame.pack(fill="both", expand=True, padx=1, pady=(0, 1))
    columns = ("file", "status", "unit", "owner", "type", "date", "filename", "reason")
    app.table = ttk.Treeview(
        frame,
        columns=columns,
        displaycolumns=("file", "status", "unit", "type", "date"),
        show="headings",
        selectmode="extended",
    )
    headings = {
        "file": "File",
        "status": "Status",
        "unit": "Unit",
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
    vertical = ttk.Scrollbar(frame, orient="vertical", command=app.table.yview)
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
    toolbar = _section_header(app.sort_viewer_pane, "DOCUMENT VIEWER")
    toolbar.pack(fill="x")
    ttk.Button(toolbar, text="−", width=3, command=lambda: app._change_sort_zoom(-0.25)).pack(side="left", padx=(12, 2))
    app.sort_zoom_var = tk.StringVar(value="100%")
    ttk.Label(toolbar, textvariable=app.sort_zoom_var, style="Glass.TLabel", width=6, anchor="center").pack(side="left")
    ttk.Button(toolbar, text="+", width=3, command=lambda: app._change_sort_zoom(0.25)).pack(side="left", padx=2)
    ttk.Button(toolbar, text="Fit", command=app._fit_sort_page).pack(side="left", padx=2)
    ttk.Button(toolbar, text="↶", width=3, command=lambda: app._rotate_sort_page(-90)).pack(side="left", padx=(8, 2))
    ttk.Button(toolbar, text="↷", width=3, command=lambda: app._rotate_sort_page(90)).pack(side="left", padx=2)


    frame = ttk.Frame(app.sort_viewer_pane, style="GlassContent.TFrame")
    frame.pack(fill="both", expand=True, padx=1)
    app.sort_page_canvas = tk.Canvas(
        frame,
        background=theme["window"],
        highlightbackground=theme["border"],
        highlightthickness=1,
    )
    vertical = ttk.Scrollbar(frame, orient="vertical", command=app.sort_page_canvas.yview)
    horizontal = ttk.Scrollbar(frame, orient="horizontal", command=app.sort_page_canvas.xview)
    app.sort_page_canvas.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    app._bind_canvas_mouse_wheel(app.sort_page_canvas)
    app.sort_page_canvas.grid(row=0, column=0, sticky="nsew")
    vertical.grid(row=0, column=1, sticky="ns")
    horizontal.grid(row=1, column=0, sticky="ew")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    navigation = ttk.Frame(app.sort_viewer_pane, style="GlassContent.TFrame", padding=(10, 7))
    navigation.pack(fill="x")
    ttk.Button(navigation, text="‹ Previous", command=lambda: app._turn_sort_page(-1)).pack(side="left")
    app.sort_page_status_var = tk.StringVar(value="No document selected")
    ttk.Label(navigation, textvariable=app.sort_page_status_var, style="Glass.TLabel", anchor="center").pack(
        side="left", fill="x", expand=True, padx=8
    )
    ttk.Button(navigation, text="Next ›", command=lambda: app._turn_sort_page(1)).pack(side="right")
    app.sort_page_index = 0
    app.sort_page_count = 0
    app.sort_page_image = None
    app.sort_zoom_factor = 1.0
    app.sort_page_rotations = {}
    app.sort_preview_generation = 0
    app._set_sort_page_message("No document selected\n\nChoose a queue row to preview its PDF.")


def _build_inspector(app, document_type_choices, document_type_labels, theme: dict) -> None:
    header = _section_header(app.sort_inspector_pane, "REVIEW INSPECTOR")
    header.pack(fill="x")
    ttk.Label(header, textvariable=app.owner_status_var, style="Glass.TLabel").pack(side="right")
    actions = ttk.Frame(app.sort_inspector_pane, style="Raised.TFrame", padding=(10, 9))
    actions.pack(side="bottom", fill="x", padx=1, pady=1)
    app.approve_button = ttk.Button(actions, text="Approve and File Copy", command=app.approve_selected, style="Primary.TButton")
    app.approve_button.pack(fill="x", pady=(0, 6))
    secondary = ttk.Frame(actions, style="Raised.TFrame")
    secondary.pack(fill="x")
    app.save_correction_button = ttk.Button(secondary, text="Save Correction", command=app.save_correction)
    app.save_correction_button.pack(side="left", fill="x", expand=True, padx=(0, 3))
    app.duplicate_button = ttk.Button(secondary, text="Mark Duplicate", command=app.mark_selected_duplicate, style="Warning.TButton")
    app.duplicate_button.pack(side="left", fill="x", expand=True, padx=(3, 0))
    app.not_dot_button = ttk.Button(actions, text="Not a DOT Document", command=app.mark_selected_not_dot, style="Danger.TButton")
    app.not_dot_button.pack(fill="x", pady=(6, 0))

    inspector_region = ttk.Frame(app.sort_inspector_pane, style="GlassContent.TFrame")
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
    )
    app.inspector_canvas.configure(yscrollcommand=inspector_scrollbar.set)
    app.inspector_canvas.pack(side="left", fill="both", expand=True)
    inspector_scrollbar.pack(side="right", fill="y")
    inspector = ttk.Frame(app.inspector_canvas, style="GlassContent.TFrame", padding=(12, 4))
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
    ttk.Label(inspector, text="CLASSIFICATION", style="Field.TLabel").pack(anchor="w", pady=(4, 7))
    ttk.Label(inspector, text="Unit", style="Field.TLabel").pack(anchor="w")
    unit_entry = ttk.Entry(inspector, textvariable=app.unit_var)
    unit_entry.pack(fill="x", pady=(2, 6))
    ttk.Label(inspector, text="Document Type", style="Field.TLabel").pack(anchor="w")
    ttk.Combobox(
        inspector,
        textvariable=app.type_var,
        values=tuple(document_type_labels[code] for code in document_type_choices),
        state="readonly",
    ).pack(fill="x", pady=(2, 6))
    pair = ttk.Frame(inspector, style="GlassContent.TFrame")
    pair.pack(fill="x")
    date_group = ttk.Frame(pair, style="GlassContent.TFrame")
    date_group.pack(side="left", fill="x", expand=True, padx=(0, 4))
    ttk.Label(date_group, text="Controlling Date", style="Field.TLabel").pack(anchor="w")
    date_entry = ttk.Entry(date_group, textvariable=app.date_var)
    date_entry.pack(fill="x", pady=(2, 6))
    page_group = ttk.Frame(pair, style="GlassContent.TFrame")
    page_group.pack(side="left", fill="x", expand=True, padx=(4, 0))
    ttk.Label(page_group, text="Additional Page", style="Field.TLabel").pack(anchor="w")
    ttk.Combobox(page_group, textvariable=app.page_var, values=PAGE_SUFFIXES).pack(fill="x", pady=(2, 6))
    app._bind_approval_on_enter(unit_entry, date_entry)

    ttk.Separator(inspector).pack(fill="x", pady=7)
    ttk.Label(inspector, text="FILE DETAILS", style="Field.TLabel").pack(anchor="w", pady=(0, 6))
    for label, variable in (
        ("Source filename", app.source_filename_var),
        ("Proposed filename", app.proposed_filename_var),
        ("Destination", app.destination_var),
    ):
        ttk.Label(inspector, text=label, style="Field.TLabel").pack(anchor="w")
        ttk.Label(inspector, textvariable=variable, style="Glass.TLabel", wraplength=410).pack(
            anchor="w", fill="x", pady=(2, 6)
        )
    ttk.Separator(inspector).pack(fill="x", pady=7)
    ttk.Label(inspector, text="REVIEW", style="Field.TLabel").pack(anchor="w", pady=(0, 6))
    ttk.Label(inspector, text="Review Notes", style="Field.TLabel").pack(anchor="w")
    ttk.Label(inspector, textvariable=app.reason_var, style="Glass.TLabel", wraplength=410).pack(
        anchor="w", fill="x", pady=(2, 6)
    )
    app._bind_mouse_wheel_to_widget_tree(app.inspector_canvas, inspector)


def build_application_ui(app, *, app_name: str, theme: dict, document_type_choices, document_type_labels) -> None:
    app._configure_theme()
    _build_top_bar(app, app_name, theme)
    app.navigation = ttk.Notebook(app.root)
    app.navigation.pack(fill="both", expand=True)
    app.sort_tab, app.database_tab, app.settings_tab, app.binder_tab = (ttk.Frame(app.navigation) for _ in range(4))
    for tab, label in ((app.sort_tab, "Sort"), (app.database_tab, "Database"), (app.settings_tab, "Settings"), (app.binder_tab, "Virtual Binder")):
        app.navigation.add(tab, text=label)
    app.navigation.bind("<<NotebookTabChanged>>", app._on_tab_changed)
    _build_summary(app)
    _build_command_row(app)

    app.status_var = tk.StringVar(value="Ready. Click Scan Incoming Documents to analyze PDFs.")
    status = ttk.Frame(app.sort_tab, style="Navigation.TFrame", padding=(10, 5))
    status.pack(side="bottom", fill="x", padx=12, pady=(0, 8))
    ttk.Label(status, text="●", foreground=theme["teal"], style="Navigation.TLabel").pack(side="left")
    ttk.Label(status, textvariable=app.status_var, style="Navigation.TLabel").pack(side="left", fill="x", expand=True, padx=7)
    ttk.Button(status, text="Reset Layout", command=app._reset_sort_panes).pack(side="right")

    app.sort_workspace = ttk.Panedwindow(app.sort_tab, orient="horizontal")
    app.sort_workspace.pack(fill="both", expand=True, padx=12, pady=(0, 6))
    app.sort_queue_pane = ttk.Frame(app.sort_workspace, style="Glass.TFrame", width=500)
    app.sort_viewer_pane = ttk.Frame(app.sort_workspace, style="Glass.TFrame", width=760)
    app.sort_inspector_pane = ttk.Frame(app.sort_workspace, style="Glass.TFrame", width=500)
    for pane, weight in ((app.sort_queue_pane, 29), (app.sort_viewer_pane, 43), (app.sort_inspector_pane, 28)):
        app.sort_workspace.add(pane, weight=weight)

    app.selection_count_var = tk.StringVar(value="0 selected")
    app.owner_status_var = tk.StringVar(value="No document selected")
    app.unit_var, app.type_var, app.date_var, app.page_var = (tk.StringVar() for _ in range(4))
    app.destination_var = tk.StringVar()
    app.reason_var = tk.StringVar()
    app.source_filename_var = tk.StringVar(value="—")
    app.proposed_filename_var = tk.StringVar(value="—")
    _build_queue(app)
    _build_viewer(app, theme)
    _build_inspector(app, document_type_choices, document_type_labels, theme)
    app._set_review_action_state(False)
    app.root.after_idle(app._reset_sort_panes)
    app._build_database_tab()
    app._build_settings_tab()
    app._build_binder_tab()
