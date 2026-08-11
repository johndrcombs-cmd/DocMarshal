from pathlib import Path

from dotdocs import gui


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


class _Widget:
    def __init__(self):
        self.bindings = {}

    def bind(self, sequence, callback):
        self.bindings[sequence] = callback


def test_document_type_choices_include_distinct_registration_documents():
    assert gui.DOCUMENT_TYPE_CHOICES == (
        "DOT",
        "RP",
        "REG",
        "TITLE",
        "CERTORIGIN",
        "INS",
    )


def test_fixed_dark_theme_declares_complete_glass_palette():
    assert gui.DARK_THEME == {
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


def test_docmarshal_branding_declares_product_name_and_icon():
    assert gui.APP_NAME == "DocMarshal"
    assert gui.APP_ICON_PATH.name == "docmarshal.ico"
    assert gui.APP_ICON_PATH.is_file()


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
        lambda candidate, **kwargs: approval_inputs.append(candidate) or approved,
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
    app.session_path = tmp_path / "active_review.json"
    app.model = _Model()
    app.status_var = _Value()
    app._refresh = lambda: None
    app._select_source = lambda source_file: None

    app.approve_selected()

    assert confirmations == []
    assert success_popups == []
    assert correction_calls[0][1]["page_suffix"] == "pg2"
    assert approval_inputs == [corrected]
    assert correction_audits
    assert app.model.replaced == approved
    assert app.status_var.value == "Approved and copied: 91_RP_07-07-2026_PG2.pdf"


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
    app._refresh = lambda: None

    app.mark_selected_duplicate()

    assert len(confirmations) == 1
    assert app.model.replaced == marked
    assert saved
    assert app.filter_var.value == "Active"
    assert app.status_var.value == "Marked duplicate and archived: duplicate.pdf"


def test_mark_not_dot_archives_and_hides_selected_document_from_active_view(monkeypatch, tmp_path):
    source = tmp_path / "Incoming" / "unrelated.pdf"
    source.parent.mkdir()
    source.write_bytes(b"pdf")
    result = {"source_file": str(source), "status": "needs_review"}
    archived_path = tmp_path / "Exceptions" / "Not DOT" / source.name
    marked = dict(result, status="not_dot", not_dot_archived_file=str(archived_path))
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
    app.audit_path = tmp_path / "Review" / "audit.jsonl"
    app.incoming = source.parent
    app.exceptions = tmp_path / "Exceptions"
    app.session_path = tmp_path / "Review" / "active_review.json"
    app.model = _Model()
    app.model.results = [result]
    app.status_var = _Value()
    app.filter_var = _Value("All")
    app._refresh = lambda: None

    app.mark_selected_not_dot()

    assert len(confirmations) == 1
    assert calls[0][1]["exceptions_folder"] == app.exceptions
    assert app.model.replaced == marked
    assert saved
    assert app.filter_var.value == "Active"
    assert app.status_var.value == "Removed from DOT workflow: unrelated.pdf"


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
