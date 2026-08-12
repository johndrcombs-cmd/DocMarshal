from pathlib import Path
import queue

from dotdocs import gui


def _gui_sources() -> str:
    gui_path = Path(gui.__file__)
    return gui_path.read_text(encoding="utf-8") + (gui_path.parent / "ui_layout.py").read_text(encoding="utf-8")


class _Value:
    def __init__(self, value=None):
        self.value = value

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


class _Model:
    def __init__(self):
        self.results = []
        self.replaced = None

    def replace(self, result):
        self.replaced = result
        source = result.get("source_file")
        for index, existing in enumerate(self.results):
            if existing.get("source_file") == source:
                self.results[index] = result
                return
        self.results.append(result)

    def filtered(self, filter_name):
        if filter_name == "Active":
            return [
                item
                for item in self.results
                if item.get("status") in {"ready_for_review", "needs_review", "failed"}
            ]
        return list(self.results)


class _Widget:
    def __init__(self, children=()):
        self.bindings = {}
        self.children = tuple(children)

    def bind(self, sequence, callback):
        self.bindings[sequence] = callback

    def winfo_children(self):
        return self.children


class _ScrollableWidget(_Widget):
    def __init__(self):
        super().__init__()
        self.scrolls = []

    def yview_scroll(self, amount, unit):
        self.scrolls.append((amount, unit))


class _SelectionTable:
    def __init__(self, children, selected=()):
        self.children = tuple(children)
        self.selected = tuple(selected)

    def get_children(self):
        return self.children

    def selection(self):
        return self.selected

    def selection_set(self, items):
        self.selected = tuple(items)


def test_document_type_choices_include_distinct_registration_documents():
    assert gui.DOCUMENT_TYPE_CHOICES == (
        "DOT",
        "RP",
        "REG",
        "TITLE",
        "CERTORIGIN",
        "CAB",
        "INS",
        "MISC",
    )
    assert gui.DOCUMENT_TYPE_LABELS["MISC"] == "Misc"
    assert gui.DOCUMENT_TYPE_LABELS["CAB"] == "CAB Card"


def test_fixed_dark_theme_declares_professional_command_center_palette():
    assert gui.DARK_THEME == {
        "window": "#080D14",
        "navigation": "#0D141F",
        "surface": "#111A27",
        "raised": "#162131",
        "input": "#1B293A",
        "surface_hover": "#22334A",
        "border": "#2A3B50",
        "border_focus": "#27C2D1",
        "accent": "#F5A814",
        "accent_hover": "#FFB92E",
        "cyan": "#27C2D1",
        "teal": "#27D3A2",
        "indigo": "#8B7CF6",
        "magenta": "#D65AD1",
        "text": "#F3F7FB",
        "secondary": "#A8B5C5",
        "muted": "#68778A",
        "success": "#28C890",
        "warning": "#F2B84B",
        "danger": "#F06464",
    }


def test_sort_workspace_declares_compact_shell_three_panes_and_reset_control():
    source = Path(gui.__file__).read_text(encoding="utf-8")
    layout_source = (Path(gui.__file__).parent / "ui_layout.py").read_text(encoding="utf-8")

    assert 'self.root.geometry("1920x1080")' in source
    assert "app.sort_queue_pane" in layout_source
    assert "app.sort_viewer_pane" in layout_source
    assert "app.sort_inspector_pane" in layout_source
    assert 'text="Reset Layout"' in layout_source
    assert "def _reset_sort_panes" in source


def test_review_inspector_exposes_existing_file_details_without_new_actions():
    source = Path(gui.__file__).read_text(encoding="utf-8")
    layout_source = (Path(gui.__file__).parent / "ui_layout.py").read_text(encoding="utf-8")

    assert "self.source_filename_var" in source
    assert "self.proposed_filename_var" in source
    assert "self.owner_status_var" in source
    assert "app.selection_count_var" in layout_source
    assert 'text="Approve and File Copy"' in layout_source
    assert 'text="Save Correction"' in layout_source
    assert 'text="Mark Duplicate"' in layout_source
    assert 'text="Not a DOT Document"' in layout_source


def test_scaled_sort_layout_keeps_reset_and_inspector_content_reachable():
    layout_source = (Path(gui.__file__).parent / "ui_layout.py").read_text(encoding="utf-8")

    assert '"file": "File"' in layout_source
    assert "app.inspector_canvas = tk.Canvas" in layout_source
    assert "app._bind_canvas_mouse_wheel(app.inspector_canvas)" in layout_source
    assert 'text="Reset Layout"' in layout_source
    assert "status.pack(side=\"bottom\"" in layout_source
    assert 'text="↶"' in layout_source
    assert 'text="↷"' in layout_source
    assert '"file": 72' in layout_source
    assert '"date": 72' in layout_source


def test_docmarshal_branding_declares_product_name_and_icon():
    assert gui.APP_NAME == "DocMarshal"
    assert gui.APP_ICON_PATH.name == "docmarshal.ico"
    assert gui.APP_ICON_PATH.is_file()


def test_windows_window_icon_sets_both_native_taskbar_icon_sizes():
    source = Path(gui.__file__).read_text(encoding="utf-8")

    assert "LoadImageW" in source
    assert "WM_SETICON" in source
    assert "ICON_SMALL" in source
    assert "ICON_BIG" in source
    assert "self.native_icon_handles" in source
    assert "user32.GetParent.argtypes = (wintypes.HWND,)" in source
    assert "user32.GetParent.restype = wintypes.HWND" in source
    assert "user32.SendMessageW.argtypes" in source
    assert "user32.SendMessageW.restype = ctypes.c_ssize_t" in source
    assert "user32.GetSystemMetrics.argtypes = (ctypes.c_int,)" in source


def test_windows_launcher_sets_docmarshal_taskbar_identity_before_tk_import():
    launcher = Path(gui.__file__).resolve().parents[1] / "launch_gui.pyw"
    source = launcher.read_text(encoding="utf-8")

    identity_call = source.index("identity_result = shell32.SetCurrentProcessExplicitAppUserModelID")
    tkinter_import = source.index("from tkinter import messagebox")
    gui_import = source.index("from dotdocs.gui import launch")

    assert 'APP_USER_MODEL_ID = "LittleBs.DocMarshal.Desktop"' in source
    assert identity_call < tkinter_import
    assert identity_call < gui_import


def test_main_window_declares_four_primary_navigation_tabs():
    source = _gui_sources()

    assert "ttk.Notebook" in source
    assert '"Sort"' in source
    assert '"Database"' in source
    assert '"Settings"' in source
    assert '"Virtual Binder"' in source
    assert "_build_database_tab" in source
    assert "_build_settings_tab" in source
    assert "_build_binder_tab" in source


def test_approved_record_retention_requires_a_real_archive_or_legacy_source(tmp_path):
    archived = tmp_path / "Approved" / "scan.pdf"
    archived.parent.mkdir()
    archived.write_bytes(b"pdf")
    legacy = tmp_path / "Incoming" / "legacy.pdf"
    legacy.parent.mkdir()
    legacy.write_bytes(b"pdf")

    assert gui.approved_record_file_exists({"approved_archived_file": str(archived)})
    assert gui.approved_record_file_exists({"source_file": str(legacy)})
    assert not gui.approved_record_file_exists({"source_file": str(tmp_path / "missing.pdf")})
    assert not gui.approved_record_file_exists({})


def test_new_approved_record_never_falls_back_when_declared_archive_is_missing(tmp_path):
    replacement = tmp_path / "Incoming" / "scan.pdf"
    replacement.parent.mkdir()
    replacement.write_bytes(b"different-pdf")
    record = {
        "approved_archived_file": str(tmp_path / "Approved" / "missing.pdf"),
        "source_file": str(replacement),
    }

    assert gui.approved_record_path(record) is None
    assert not gui.approved_record_file_exists(record)


def test_open_pdf_uses_archived_or_legacy_approved_path_without_unsafe_fallback(monkeypatch, tmp_path):
    incoming = tmp_path / "Incoming"
    approved = tmp_path / "Approved"
    incoming.mkdir()
    approved.mkdir()
    archived = approved / "archived.pdf"
    archived.write_bytes(b"pdf")
    legacy = incoming / "legacy.pdf"
    legacy.write_bytes(b"pdf")
    replacement = incoming / "missing.pdf"
    replacement.write_bytes(b"different-pdf")

    app = gui.DotReviewApp.__new__(gui.DotReviewApp)
    app.incoming = incoming
    app.approved = approved
    app.processed = tmp_path / "Processed"
    app.exceptions = tmp_path / "Exceptions"
    opened = []
    errors = []
    monkeypatch.setattr(gui.os, "startfile", lambda path: opened.append(Path(path)))
    monkeypatch.setattr(gui.messagebox, "showerror", lambda title, message: errors.append((title, message)))

    app._selected_result = lambda: {
        "status": "approved",
        "source_file": str(incoming / "archived.pdf"),
        "approved_archived_file": str(archived),
    }
    app.open_pdf()
    app._selected_result = lambda: {"status": "approved", "source_file": str(legacy)}
    app.open_pdf()
    app._selected_result = lambda: {
        "status": "approved",
        "source_file": str(replacement),
        "approved_archived_file": str(approved / "missing.pdf"),
    }
    app.open_pdf()

    assert opened == [archived, legacy]
    assert errors == [("File unavailable", "The approved PDF archive cannot be found.")]


def test_sort_and_virtual_binder_expose_import_ocr_zoom_and_sequence_controls():
    source = _gui_sources()

    assert "Import PDFs" in source
    assert "OCR Selected" in source
    assert "OCR All Needing OCR" in source
    assert "NON_DOT_DOCUMENT_TYPES" in source
    assert 'text="Classify and Archive"' in source
    assert "classification=classification" in source
    assert "non_dot_classification_label" in source
    assert 'text="Zoom Out"' in source
    assert 'text="Fit Page"' in source
    assert 'text="Zoom In"' in source
    assert "binder_page_canvas" in source
    assert "advance_binder_position" in source
    assert "sort_page_canvas" in source
    assert "_render_selected_sort_page" in source
    assert 'text="‹ Previous"' in source
    assert 'text="Next ›"' in source
    assert "_rotate_sort_page(-90)" in source
    assert "_rotate_sort_page(90)" in source
    assert 'text="Select All Visible"' in source
    assert 'selectmode="extended"' in source


def test_canvas_mouse_wheel_bindings_scroll_vertically_and_stop_propagation():
    app = gui.DotReviewApp.__new__(gui.DotReviewApp)
    canvas = _ScrollableWidget()

    app._bind_canvas_mouse_wheel(canvas)

    assert set(canvas.bindings) == {"<MouseWheel>", "<Button-4>", "<Button-5>"}
    assert canvas.bindings["<MouseWheel>"](type("Event", (), {"delta": 120})()) == "break"
    assert canvas.bindings["<MouseWheel>"](type("Event", (), {"delta": -120})()) == "break"
    assert canvas.bindings["<Button-4>"](type("Event", (), {"num": 4})()) == "break"
    assert canvas.bindings["<Button-5>"](type("Event", (), {"num": 5})()) == "break"
    assert canvas.scrolls == [(-1, "units"), (1, "units"), (-1, "units"), (1, "units")]


def test_embedded_widget_tree_routes_mouse_wheel_to_its_canvas():
    app = gui.DotReviewApp.__new__(gui.DotReviewApp)
    canvas = _ScrollableWidget()
    entry = _Widget()
    label = _Widget()
    nested_frame = _Widget((entry, label))
    inspector = _Widget((nested_frame,))

    app._bind_mouse_wheel_to_widget_tree(canvas, inspector)

    for widget in (inspector, nested_frame, entry, label):
        assert set(widget.bindings) == {"<MouseWheel>", "<Button-4>", "<Button-5>"}
    assert entry.bindings["<MouseWheel>"](type("Event", (), {"delta": -120})()) == "break"
    assert canvas.scrolls == [(1, "units")]


def test_all_scrollable_document_surfaces_receive_mouse_wheel_bindings():
    source = _gui_sources()

    assert "app._bind_canvas_mouse_wheel(app.sort_page_canvas)" in source
    assert "self._bind_canvas_mouse_wheel(self.binder_shelf)" in source
    assert "self._bind_canvas_mouse_wheel(self.binder_page_canvas)" in source


def test_select_all_visible_selects_every_displayed_queue_row():
    app = gui.DotReviewApp.__new__(gui.DotReviewApp)
    app.table = _SelectionTable(("row-0", "row-1", "row-2"))

    app.select_all_visible()

    assert app.table.selection() == ("row-0", "row-1", "row-2")


def test_selected_results_follow_visible_row_order():
    app = gui.DotReviewApp.__new__(gui.DotReviewApp)
    app.table = _SelectionTable(("row-0", "row-1", "row-2"), ("row-2", "row-0"))
    app.row_sources = {"row-0": "a.pdf", "row-1": "b.pdf", "row-2": "c.pdf"}
    app.model = gui.ReviewModel([
        {"source_file": "a.pdf", "status": "needs_review"},
        {"source_file": "b.pdf", "status": "needs_review"},
        {"source_file": "c.pdf", "status": "needs_review"},
    ])

    assert [item["source_file"] for item in app._selected_results()] == ["a.pdf", "c.pdf"]


def test_bulk_not_dot_worker_continues_after_failure_and_saves_each_success(monkeypatch, tmp_path):
    candidates = [
        {"source_file": "a.pdf", "status": "needs_review"},
        {"source_file": "b.pdf", "status": "needs_review"},
        {"source_file": "c.pdf", "status": "needs_review"},
    ]
    archived = []
    saved = []

    def mark(result, **kwargs):
        if result["source_file"] == "b.pdf":
            raise gui.ReviewValidationError("fingerprint mismatch")
        updated = dict(result, status="not_dot", non_dot_classification_label="Other / Unclassified")
        archived.append((result["source_file"], kwargs["classification"]))
        return updated

    monkeypatch.setattr(gui, "mark_not_dot_document", mark)
    monkeypatch.setattr(
        gui,
        "save_review_session",
        lambda path, results: saved.append((path, [dict(item) for item in results])),
    )

    app = gui.DotReviewApp.__new__(gui.DotReviewApp)
    app.events = queue.Queue()
    app.audit_path = tmp_path / "audit.jsonl"
    app.incoming = tmp_path / "Incoming"
    app.exceptions = tmp_path / "Exceptions"
    app.session_path = tmp_path / "active_review.json"

    app._bulk_not_dot_worker(candidates, "OTHER", [dict(item) for item in candidates])

    events = [app.events.get_nowait() for _ in range(4)]
    assert archived == [("a.pdf", "OTHER"), ("c.pdf", "OTHER")]
    assert len(saved) == 2
    assert [event[0] for event in events] == [
        "bulk_not_dot_progress",
        "bulk_not_dot_progress",
        "bulk_not_dot_progress",
        "bulk_not_dot_done",
    ]
    assert events[-1][1]["completed"] == 2
    assert events[-1][1]["failed"] == 1
    assert events[-1][1]["errors"] == [{"filename": "b.pdf", "error": "fingerprint mismatch"}]


def test_rotate_sort_page_remembers_quarter_turn_for_selected_page():
    app = gui.DotReviewApp.__new__(gui.DotReviewApp)
    app.sort_page_rotations = {}
    app.sort_page_index = 2
    app._selected_source = lambda: "scan.pdf"
    calls = []
    app._render_selected_sort_page = lambda: calls.append("render")

    app._rotate_sort_page(90)
    app._rotate_sort_page(90)
    app._rotate_sort_page(-90)

    assert app.sort_page_rotations[("scan.pdf", 2)] == 90
    assert calls == ["render", "render", "render"]


def test_next_active_source_follows_prior_queue_order_and_wraps():
    choose = gui.next_active_source

    assert choose(("a.pdf", "b.pdf", "c.pdf"), "b.pdf", ("a.pdf", "c.pdf")) == "c.pdf"
    assert choose(("a.pdf", "b.pdf", "c.pdf"), "c.pdf", ("a.pdf", "b.pdf")) == "a.pdf"
    assert choose(("a.pdf",), "a.pdf", ()) is None
    assert choose(("a.pdf", "b.pdf"), "missing.pdf", ("a.pdf", "b.pdf")) == "a.pdf"


def test_refresh_and_select_next_preserves_ready_filter_and_selects_next_ready_source():
    app = gui.DotReviewApp.__new__(gui.DotReviewApp)
    app.filter_var = _Value("Ready")
    app.model = gui.ReviewModel([
        {"source_file": "a.pdf", "status": "approved"},
        {"source_file": "b.pdf", "status": "ready_for_review"},
        {"source_file": "c.pdf", "status": "needs_review"},
    ])
    calls = []
    app._refresh = lambda: calls.append("refresh")
    app._select_source = lambda source: calls.append(("select", source))
    app._clear_sort_selection = lambda: calls.append("clear")

    app._refresh_and_select_next(("a.pdf", "b.pdf"), "a.pdf")

    assert app.filter_var.value == "Ready"
    assert calls == ["refresh", ("select", "b.pdf")]

    app.model.results[1]["status"] = "approved"
    calls.clear()
    app._refresh_and_select_next(("b.pdf",), "b.pdf")

    assert app.filter_var.value == "Ready"
    assert calls == ["refresh", "clear"]


def test_empty_queue_message_names_the_preserved_filter():
    assert gui.DotReviewApp._empty_queue_message("Ready") == "No Ready documents remain in the queue."
    assert gui.DotReviewApp._empty_queue_message("Active") == "No Active documents remain in the queue."


def test_humanizes_internal_codes_for_user_facing_text():
    humanize = gui.DotReviewApp._humanize_user_text

    assert humanize("UNIT_PATH") == "Unit Path"
    assert humanize("CONTROLLING_DATE_UNKNOWN") == "Controlling Date Unknown"
    assert humanize("NO_SEARCHABLE_TEXT") == "No Searchable Text"
    assert humanize("REMOVED_FROM_DOT_WORKFLOW") == "Removed From DOT Workflow"
    assert humanize("PDF_REQUIRES_OCR") == "PDF Requires OCR"
    assert humanize(None) == ""


def test_enter_in_unit_or_date_field_triggers_approval():
    app = gui.DotReviewApp.__new__(gui.DotReviewApp)
    approvals = []
    app.approve_selected = lambda: approvals.append("approved")
    unit_entry = _Widget()
    date_entry = _Widget()

    app._bind_approval_on_enter(unit_entry, date_entry)

    assert set(unit_entry.bindings) == {"<Return>", "<KP_Enter>"}
    assert set(date_entry.bindings) == {"<Return>", "<KP_Enter>"}
    assert unit_entry.bindings["<Return>"](object()) == "break"
    assert date_entry.bindings["<KP_Enter>"](object()) == "break"
    assert approvals == ["approved", "approved"]


def test_approval_applies_current_fields_and_files_in_one_click_without_popups(
    monkeypatch, tmp_path
):
    source = tmp_path / "Incoming" / "scan.pdf"
    source.parent.mkdir()
    source.write_bytes(b"pdf")
    result = {
        "source_file": str(source),
        "status": "needs_review",
        "proposed_destination": None,
        "proposed_filename": None,
    }
    corrected_destination = str(
        tmp_path / "Unit_91" / "004_Maintenance_Records" / "91_RP_07-07-2026_PG2.pdf"
    )
    corrected = dict(
        result,
        status="ready_for_review",
        proposed_destination=corrected_destination,
        proposed_filename="91_RP_07-07-2026_PG2.pdf",
        page_suffix="PG2",
    )
    approved = dict(corrected, status="approved", approved_destination=corrected_destination)
    confirmations = []
    success_popups = []
    correction_calls = []
    approval_inputs = []
    correction_audits = []
    monkeypatch.setattr(
        gui.messagebox,
        "askyesno",
        lambda *args, **kwargs: confirmations.append((args, kwargs)) or True,
    )
    monkeypatch.setattr(
        gui.messagebox,
        "showinfo",
        lambda *args, **kwargs: success_popups.append((args, kwargs)),
    )
    monkeypatch.setattr(
        gui,
        "apply_correction",
        lambda *args, **kwargs: correction_calls.append((args, kwargs)) or corrected,
    )
    monkeypatch.setattr(
        gui,
        "record_correction",
        lambda *args, **kwargs: correction_audits.append((args, kwargs)),
    )
    monkeypatch.setattr(
        gui,
        "approve_document",
        lambda candidate, **kwargs: approval_inputs.append((candidate, kwargs)) or approved,
    )
    monkeypatch.setattr(gui, "save_review_session", lambda *args, **kwargs: None)

    app = gui.DotReviewApp.__new__(gui.DotReviewApp)
    app._selected_result = lambda: result
    app.unit_var = _Value("091")
    app.type_var = _Value("RP")
    app.date_var = _Value("07/07/2026")
    app.page_var = _Value("pg2")
    app.audit_path = tmp_path / "audit.jsonl"
    app.unit_root = tmp_path
    app.farm_unit_root = tmp_path / "Farm Assets"
    app.database = tmp_path / "fleet.db"
    app.incoming = source.parent
    app.approved = tmp_path / "Approved"
    app.session_path = tmp_path / "active_review.json"
    app.model = _Model()
    app.model.results = [result]
    app.status_var = _Value()
    app.filter_var = _Value("All")
    advanced = []
    app._refresh_and_select_next = lambda prior, source_file: (
        app.filter_var.set("Active"), advanced.append((prior, source_file))
    )

    app.approve_selected()

    assert confirmations == []
    assert success_popups == []
    assert correction_calls[0][1]["page_suffix"] == "pg2"
    assert approval_inputs[0][0] == corrected
    assert approval_inputs[0][1]["approved_folder"] == app.approved
    assert correction_audits
    assert app.model.replaced == approved
    assert app.status_var.value == "Approved and copied: 91_RP_07-07-2026_PG2.pdf"
    assert advanced == [((str(source),), str(source))]


def test_mark_duplicate_archives_and_hides_selected_document_from_active_view(monkeypatch, tmp_path):
    source = tmp_path / "Incoming" / "duplicate.pdf"
    source.parent.mkdir()
    source.write_bytes(b"pdf")
    result = {"source_file": str(source), "status": "needs_review"}
    marked = dict(result, status="duplicate", duplicate_archived_file=str(tmp_path / "Processed" / "Duplicates" / source.name))
    confirmations = []
    saved = []
    monkeypatch.setattr(
        gui.messagebox,
        "askyesno",
        lambda *args, **kwargs: confirmations.append((args, kwargs)) or True,
    )
    monkeypatch.setattr(gui, "mark_duplicate_document", lambda *args, **kwargs: marked)
    monkeypatch.setattr(gui, "save_review_session", lambda *args, **kwargs: saved.append((args, kwargs)))

    app = gui.DotReviewApp.__new__(gui.DotReviewApp)
    app._selected_result = lambda: result
    app.unit_var = _Value("211")
    app.type_var = _Value("REG")
    app.date_var = _Value("08/31/2027")
    app.audit_path = tmp_path / "Review" / "audit.jsonl"
    app.unit_root = tmp_path / "DOT Binders"
    app.farm_unit_root = tmp_path / "Farm Assets"
    app.database = tmp_path / "fleet.db"
    app.incoming = source.parent
    app.processed = tmp_path / "Processed"
    app.session_path = tmp_path / "Review" / "active_review.json"
    app.model = _Model()
    app.model.results = [result]
    app.status_var = _Value()
    app.filter_var = _Value("All")
    advanced = []
    app._refresh_and_select_next = lambda prior, source_file: (
        app.filter_var.set("Active"), advanced.append((prior, source_file))
    )

    app.mark_selected_duplicate()

    assert len(confirmations) == 1
    assert app.model.replaced == marked
    assert saved
    assert app.filter_var.value == "Active"
    assert app.status_var.value == "Marked duplicate and archived: duplicate.pdf"
    assert advanced == [((str(source),), str(source))]


def test_mark_not_dot_archives_and_hides_selected_document_from_active_view(monkeypatch, tmp_path):
    source = tmp_path / "Incoming" / "unrelated.pdf"
    source.parent.mkdir()
    source.write_bytes(b"pdf")
    result = {"source_file": str(source), "status": "needs_review"}
    archived_path = tmp_path / "Exceptions" / "Not DOT" / source.name
    marked = dict(
        result,
        status="not_dot",
        not_dot_archived_file=str(archived_path),
        non_dot_classification="MVR_AUTH",
        non_dot_classification_label="MVR Auth",
    )
    calls = []
    saved = []
    monkeypatch.setattr(
        gui,
        "mark_not_dot_document",
        lambda *args, **kwargs: calls.append((args, kwargs)) or marked,
    )
    monkeypatch.setattr(
        gui,
        "save_review_session",
        lambda *args, **kwargs: saved.append((args, kwargs)),
    )

    app = gui.DotReviewApp.__new__(gui.DotReviewApp)
    app._selected_result = lambda: result
    app._selected_results = lambda: [result]
    app._choose_non_dot_classification = lambda _source_name: "MVR_AUTH"
    app.audit_path = tmp_path / "Review" / "audit.jsonl"
    app.incoming = source.parent
    app.exceptions = tmp_path / "Exceptions"
    app.session_path = tmp_path / "Review" / "active_review.json"
    app.model = _Model()
    app.model.results = [result]
    app.status_var = _Value()
    app.filter_var = _Value("All")
    advanced = []
    app._refresh_and_select_next = lambda prior, source_file: advanced.append((prior, source_file))

    app.mark_selected_not_dot()

    assert calls[0][1]["classification"] == "MVR_AUTH"
    assert calls[0][1]["exceptions_folder"] == app.exceptions
    assert app.model.replaced == marked
    assert saved
    assert app.filter_var.value == "All"
    assert app.status_var.value == "Classified as MVR Auth and removed from DOT workflow: unrelated.pdf"
    assert advanced == [((str(source),), str(source))]


def test_open_pdf_opens_archived_not_dot_document(monkeypatch, tmp_path):
    archive = tmp_path / "Exceptions" / "Not DOT" / "unrelated.pdf"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"pdf")
    opened = []
    monkeypatch.setattr(gui.os, "startfile", lambda path: opened.append(Path(path)))

    app = gui.DotReviewApp.__new__(gui.DotReviewApp)
    app._selected_result = lambda: {
        "source_file": str(tmp_path / "Incoming" / "unrelated.pdf"),
        "status": "not_dot",
        "not_dot_archived_file": str(archive),
    }
    app.incoming = tmp_path / "Incoming"
    app.exceptions = tmp_path / "Exceptions"

    app.open_pdf()

    assert opened == [archive]


def test_open_pdf_opens_archived_duplicate_document(monkeypatch, tmp_path):
    archive = tmp_path / "Processed" / "Duplicates" / "duplicate.pdf"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"pdf")
    opened = []
    errors = []
    monkeypatch.setattr(gui.os, "startfile", lambda path: opened.append(Path(path)))
    monkeypatch.setattr(
        gui.messagebox,
        "showerror",
        lambda *args, **kwargs: errors.append((args, kwargs)),
    )

    app = gui.DotReviewApp.__new__(gui.DotReviewApp)
    app._selected_result = lambda: {
        "source_file": str(tmp_path / "Incoming" / "duplicate.pdf"),
        "status": "duplicate",
        "duplicate_archived_file": str(archive),
    }
    app.incoming = tmp_path / "Incoming"
    app.processed = tmp_path / "Processed"
    app.exceptions = tmp_path / "Exceptions"

    app.open_pdf()

    assert errors == []
    assert opened == [archive]


def test_restore_selected_returns_archived_document_to_active_view(monkeypatch, tmp_path):
    source = tmp_path / "Incoming" / "duplicate.pdf"
    archived_path = tmp_path / "Processed" / "Duplicates" / source.name
    result = {
        "source_file": str(source),
        "status": "duplicate",
        "duplicate_archived_file": str(archived_path),
    }
    restored = dict(
        result,
        status="needs_review",
        reasons=["RESTORED_TO_ACTIVE_REVIEW"],
        duplicate_archived_file=None,
    )
    confirmations = []
    calls = []
    saved = []
    monkeypatch.setattr(
        gui.messagebox,
        "askyesno",
        lambda *args, **kwargs: confirmations.append((args, kwargs)) or True,
    )
    monkeypatch.setattr(
        gui,
        "restore_archived_document",
        lambda *args, **kwargs: calls.append((args, kwargs)) or restored,
    )
    monkeypatch.setattr(gui, "save_review_session", lambda *args, **kwargs: saved.append((args, kwargs)))

    app = gui.DotReviewApp.__new__(gui.DotReviewApp)
    app._selected_result = lambda: result
    app.audit_path = tmp_path / "Review" / "audit.jsonl"
    app.incoming = source.parent
    app.processed = tmp_path / "Processed"
    app.exceptions = tmp_path / "Exceptions"
    app.session_path = tmp_path / "Review" / "active_review.json"
    app.model = _Model()
    app.model.results = [result]
    app.status_var = _Value()
    app.filter_var = _Value("Duplicates")
    app._refresh = lambda: None
    app._select_source = lambda source_file: None

    app.restore_selected()

    assert len(confirmations) == 1
    assert calls[0][1]["processed_folder"] == app.processed
    assert calls[0][1]["exceptions_folder"] == app.exceptions
    assert app.model.replaced == restored
    assert saved
    assert app.filter_var.value == "Active"
    assert app.status_var.value == "Restored to Active review: duplicate.pdf"
