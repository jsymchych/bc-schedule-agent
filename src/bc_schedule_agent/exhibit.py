"""In-memory schedule model → PDF + XLSX + audit.json exhibits (Wave D)."""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from bc_schedule_agent.audit import AuditChain, AuditEvent
from bc_schedule_agent.export import (
    LEGAL_POSTURE,
    STATUTE_URL,
    ExportBlocked,
    IssueResult,
    assert_no_pending_ot,
    gate_snapshot_hash,
    issue_schedule,
    schedule_hash,
)
from bc_schedule_agent.history import WeekHistoryStore, week_dirname
from bc_schedule_agent.models import ComposeResult, OvertimeProposal, PlacedShift
from bc_schedule_agent.replay import (
    REPLAY_INPUTS_FILENAME,
    ReplayInputs,
    with_gate_snapshot,
    write_replay_envelope,
)


@dataclass(frozen=True)
class ScheduleModel:
    """One in-memory schedule. PDF and XLSX are renders of this model only."""

    decision_id: str
    ruleset_version: str
    ruleset_hash: str
    week_start: date
    placed: tuple[PlacedShift, ...]
    ot_proposals: tuple[OvertimeProposal, ...]
    schedule_hash: str

    @classmethod
    def from_compose(
        cls,
        result: ComposeResult,
        *,
        decision_id: str,
        ruleset_version: str,
        ruleset_hash: str,
        week_start: date,
    ) -> ScheduleModel:
        assert_no_pending_ot(result.ot_proposals)
        return cls(
            decision_id=decision_id,
            ruleset_version=ruleset_version,
            ruleset_hash=ruleset_hash,
            week_start=week_start,
            placed=tuple(result.placed),
            ot_proposals=tuple(result.ot_proposals),
            schedule_hash=schedule_hash(result.placed),
        )


@dataclass(frozen=True)
class ExhibitPaths:
    pdf: Path
    xlsx: Path
    audit_json: Path
    replay_inputs: Path | None = None


@dataclass
class ExhibitBundle:
    model: ScheduleModel
    issue: IssueResult
    paths: ExhibitPaths
    pdf_bytes: bytes
    xlsx_bytes: bytes
    audit: dict[str, Any] = field(repr=False)


def event_to_prose(event: AuditEvent) -> str:
    """One drawer sentence from an audit event (same chain as audit.json)."""
    kind = event.kind
    actor = event.actor
    subj = event.subject
    ev = event.evidence
    if kind == "ingest":
        sheet = subj.get("sheet", "input")
        return f"{actor} ingested {sheet} (hash {ev.get('input_hash', '')})."
    if kind == "parse":
        return (
            f"{actor} stored coverage demand {subj.get('demand_id')} "
            f"from {ev.get('source')} ({subj.get('shifts')} shifts)."
        )
    if kind == "place":
        return (
            f"{actor} placed {subj.get('employee')} on {subj.get('date')} "
            f"{ev.get('start')}-{ev.get('end')} "
            f"({ev.get('worked_hours')}h, regime {ev.get('regime')})."
        )
    if kind == "rule_refuse":
        return (
            f"{actor} refused {subj.get('shift_id')}: {ev.get('detail')} "
            f"(s.{ev.get('section')})."
        )
    if kind == "rule_pass":
        return (
            f"{actor} passed {subj.get('employee')} on {subj.get('date')}: "
            f"{ev.get('detail', 'ok')}."
        )
    if kind == "ot_proposed":
        return (
            f"{actor} proposed overtime for {subj.get('employee')} on "
            f"{subj.get('date')}: {ev.get('hours')}h at {ev.get('multiplier')}x "
            f"(s.{ev.get('section')})."
        )
    if kind == "ot_approved":
        reason = ev.get("reason") or ""
        if reason:
            return (
                f"{actor} approved overtime {subj.get('proposal_id')} "
                f"at {ev.get('timestamp')}: {reason}."
            )
        return (
            f"{actor} approved overtime {subj.get('proposal_id')} "
            f"at {ev.get('timestamp')}."
        )
    if kind == "ot_refused":
        return (
            f"{actor} refused overtime {subj.get('proposal_id')} "
            f"(returned to composer)."
        )
    if kind == "ot_unavoidable":
        return (
            f"{actor} marked overtime unavoidable for {subj.get('shift_id')} "
            f"({ev.get('reason_unavoidable', 'residual coverage')})."
        )
    if kind == "repair":
        return (
            f"{actor} repaired placement for {subj.get('shift_id')}: "
            f"{ev.get('detail', 'reassigned')}."
        )
    if kind == "timeoff_decided":
        return (
            f"{actor} {ev.get('decision')}d time-off {subj.get('request_id')} "
            f"→ {ev.get('status')}."
        )
    if kind == "packet_accepted":
        return ev.get("audit_sentence") or (
            f"{actor} accepted averaging packet {subj.get('packet_id')}."
        )
    if kind == "packet_rejected":
        sentence = ev.get("audit_sentence")
        if isinstance(sentence, str) and sentence.strip():
            return sentence
        missing = ev.get("missing_terms") or []
        if isinstance(missing, list) and missing:
            parts: list[str] = []
            for item in missing:
                if isinstance(item, dict):
                    term = item.get("term", "term")
                    section = item.get("section", "")
                    parts.append(f"{term} (s.{section})" if section else str(term))
                else:
                    parts.append(str(item))
            return (
                f"{actor} rejected averaging packet {subj.get('packet_id')}: "
                + "; ".join(parts)
                + "."
            )
        return (
            f"{actor} rejected averaging packet {subj.get('packet_id')}: "
            f"{ev.get('detail', 'required term missing')}."
        )
    if kind == "issued":
        return (
            f"{actor} issued decision {subj.get('decision_id')} "
            f"schedule_hash={ev.get('schedule_hash')}."
        )
    if kind == "repair":
        return f"{actor} repaired {subj.get('shift_id', 'schedule')}: {ev.get('detail', '')}."
    return f"{actor} recorded {kind}."


def build_audit_document(
    chain: AuditChain,
    *,
    model: ScheduleModel,
) -> dict[str, Any]:
    """audit.json: same chain the PDF appendix renders as prose."""
    chain.verify_links()
    events = [
        {
            "event_id": e.event_id,
            "timestamp": e.timestamp,
            "prev_event_id": e.prev_event_id,
            "actor": e.actor,
            "kind": e.kind,
            "subject": e.subject,
            "evidence": e.evidence,
            "prose": event_to_prose(e),
        }
        for e in chain.events
    ]
    return {
        "decision_id": model.decision_id,
        "ruleset_version": model.ruleset_version,
        "ruleset_hash": model.ruleset_hash,
        "schedule_hash": model.schedule_hash,
        "week_start": model.week_start.isoformat(),
        "legal_posture": LEGAL_POSTURE,
        "statute_url": STATUTE_URL,
        "hours_and_multipliers_only": True,
        "events": events,
    }


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _wrap_pdf_line(text: str, width: int = 96) -> list[str]:
    """Wrap long lines so statute URL / posture are not truncated."""
    if len(text) <= width:
        return [text]
    parts: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= width:
            parts.append(remaining)
            break
        cut = remaining.rfind(" ", 0, width + 1)
        if cut <= 0:
            cut = width
        parts.append(remaining[:cut])
        remaining = remaining[cut:].lstrip()
    return parts


def _paginate_pdf_lines(
    lines: list[str],
    *,
    y_top: int = 780,
    y_floor: int = 40,
    line_height: int = 11,
) -> list[list[str]]:
    """Split wrapped lines into pages so the audit appendix is never truncated."""
    pages: list[list[str]] = []
    current: list[str] = []
    y = y_top
    for line in lines:
        if current and y < y_floor:
            pages.append(current)
            current = []
            y = y_top
        current.append(line)
        y -= line_height
    if current:
        pages.append(current)
    return pages or [[]]


def _pdf_page_stream(page_lines: list[str], *, y_top: int = 780) -> bytes:
    y = y_top
    parts: list[str] = ["BT", "/F1 9 Tf", "14 TL"]
    for line in page_lines:
        parts.append(f"1 0 0 1 40 {y} Tm ({_pdf_escape(line)}) Tj")
        y -= 11
    parts.append("ET")
    return "\n".join(parts).encode("latin-1", errors="replace")


def render_pdf_bytes(model: ScheduleModel, chain: AuditChain) -> bytes:
    """Paginated PDF: roster + legal posture + full prose audit appendix."""
    raw_lines: list[str] = [
        "BC Schedule Exhibit",
        f"Decision id: {model.decision_id}",
        f"Ruleset: {model.ruleset_version}",
        f"Schedule hash: {model.schedule_hash}",
        f"Week start (Sunday): {model.week_start.isoformat()}",
        "Decision support under the Employment Standards Act, not legal advice.",
        f"Statute: {STATUTE_URL}",
        "",
        "Shifts (hours only - not a paycheque):",
    ]
    for p in sorted(model.placed, key=lambda s: (s.date, s.employee, s.shift_id)):
        raw_lines.append(
            f"  {p.date.isoformat()} {p.employee} "
            f"{p.start.isoformat(timespec='minutes')}-"
            f"{p.end.isoformat(timespec='minutes')} "
            f"{p.worked_hours}h [{p.shift_id}]"
        )
    if model.ot_proposals:
        raw_lines.append("Overtime / premiums (hours x multiplier):")
        for o in model.ot_proposals:
            raw_lines.append(
                f"  {o.date.isoformat()} {o.employee} "
                f"{o.hours}h x {o.multiplier} s.{o.section} ({o.status})"
            )
    raw_lines.append("")
    raw_lines.append("Audit appendix (same chain as audit.json):")
    for event in chain.events:
        raw_lines.append(f"  - {event_to_prose(event)}")

    lines: list[str] = []
    for line in raw_lines:
        lines.extend(_wrap_pdf_line(line))

    page_line_groups = _paginate_pdf_lines(lines)
    streams = [_pdf_page_stream(group) for group in page_line_groups]
    page_count = len(streams)

    # Object layout: 1=Catalog, 2=Pages, then (Page, Contents) pairs, then Font.
    font_obj_num = 3 + (page_count * 2)
    kids_refs = " ".join(f"{3 + (i * 2)} 0 R" for i in range(page_count))

    objects: list[bytes] = []
    objects.append(b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n")
    objects.append(
        f"2 0 obj<< /Type /Pages /Kids [{kids_refs}] /Count {page_count} >>endobj\n".encode(
            "ascii"
        )
    )
    for i, stream in enumerate(streams):
        page_num = 3 + (i * 2)
        content_num = page_num + 1
        objects.append(
            (
                f"{page_num} 0 obj<< /Type /Page /Parent 2 0 R "
                f"/MediaBox [0 0 612 792] /Contents {content_num} 0 R "
                f"/Resources << /Font << /F1 {font_obj_num} 0 R >> >> >>endobj\n"
            ).encode("ascii")
        )
        objects.append(
            f"{content_num} 0 obj<< /Length {len(stream)} >>stream\n".encode("ascii")
            + stream
            + b"\nendstream\nendobj\n"
        )
    objects.append(
        (
            f"{font_obj_num} 0 obj<< /Type /Font /Subtype /Type1 "
            f"/BaseFont /Helvetica >>endobj\n"
        ).encode("ascii")
    )

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(out.tell())
        out.write(obj)
    xref_pos = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    out.write(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.write(f"{off:010d} 00000 n \n".encode("ascii"))
    out.write(
        f"trailer<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n".encode("ascii")
    )
    return out.getvalue()


def _col_letters(index: int) -> str:
    """1-based column index → A, B, … Z, AA, …"""
    col = ""
    n = index
    while n:
        n, rem = divmod(n - 1, 26)
        col = chr(65 + rem) + col
    return col


def _sheet_xml(rows: list[list[str]]) -> str:
    def cell_xml(ref: str, value: str) -> str:
        return f'<c r="{ref}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'

    sheet_rows: list[str] = []
    for r_idx, row in enumerate(rows, start=1):
        cells = [
            cell_xml(f"{_col_letters(c_idx)}{r_idx}", value)
            for c_idx, value in enumerate(row, start=1)
        ]
        sheet_rows.append(f'<row r="{r_idx}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
    )


def render_xlsx_bytes(model: ScheduleModel) -> bytes:
    """OOXML workbook: schedule sheet + OT sheet (approver + reason columns)."""
    headers = [
        "decision_id",
        "ruleset_version",
        "schedule_hash",
        "employee",
        "date",
        "start",
        "end",
        "worked_hours",
        "shift_id",
        "ot_hours",
        "ot_multiplier",
        "ot_section",
        "ot_status",
        "legal_posture",
        "statute_url",
    ]
    rows: list[list[str]] = [headers]
    ot_by_shift = {o.shift_id: o for o in model.ot_proposals if o.shift_id}
    for p in sorted(model.placed, key=lambda s: (s.date, s.employee, s.shift_id)):
        ot = ot_by_shift.get(p.shift_id)
        rows.append(
            [
                model.decision_id,
                model.ruleset_version,
                model.schedule_hash,
                p.employee,
                p.date.isoformat(),
                p.start.isoformat(timespec="minutes"),
                p.end.isoformat(timespec="minutes"),
                str(p.worked_hours),
                p.shift_id,
                "" if ot is None else str(ot.hours),
                "" if ot is None else str(ot.multiplier),
                "" if ot is None else ot.section,
                "" if ot is None else ot.status,
                LEGAL_POSTURE,
                STATUTE_URL,
            ]
        )
    if not model.placed:
        rows.append(
            [
                model.decision_id,
                model.ruleset_version,
                model.schedule_hash,
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                LEGAL_POSTURE,
                STATUTE_URL,
            ]
        )

    ot_headers = [
        "proposal_id",
        "employee",
        "date",
        "hours",
        "multiplier",
        "section",
        "status",
        "shift_id",
        "human_name",
        "timestamp",
        "reason",
    ]
    ot_rows: list[list[str]] = [ot_headers]
    for o in sorted(
        model.ot_proposals,
        key=lambda p: (p.date, p.employee, p.proposal_id),
    ):
        ot_rows.append(
            [
                o.proposal_id,
                o.employee,
                o.date.isoformat(),
                str(o.hours),
                str(o.multiplier),
                o.section,
                o.status,
                o.shift_id or "",
                o.decided_by or "",
                o.decided_at or "",
                o.reason or "",
            ]
        )

    sheet1 = _sheet_xml(rows)
    sheet2 = _sheet_xml(ot_rows)
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<sheets>"
        '<sheet name="schedule" sheetId="1" r:id="rId1"/>'
        '<sheet name="ot" sheetId="2" r:id="rId2"/>'
        "</sheets>"
        "</workbook>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    wb_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet2.xml"/>'
        "</Relationships>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/worksheets/sheet2.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        zf.writestr("xl/worksheets/sheet1.xml", sheet1)
        zf.writestr("xl/worksheets/sheet2.xml", sheet2)
    return buf.getvalue()


def build_pdf_exhibit(
    result: ComposeResult,
    *,
    decision_id: str,
    ruleset_version: str,
    ruleset_hash: str = "",
    week_start: date | None = None,
    chain: AuditChain | None = None,
) -> dict[str, Any]:
    """PDF render of the in-memory schedule. Refuses while OT is pending."""
    assert_no_pending_ot(result.ot_proposals)
    model = ScheduleModel(
        decision_id=decision_id,
        ruleset_version=ruleset_version,
        ruleset_hash=ruleset_hash,
        week_start=week_start or date.min,
        placed=tuple(result.placed),
        ot_proposals=tuple(result.ot_proposals),
        schedule_hash=schedule_hash(result.placed),
    )
    chain = chain or AuditChain()
    pdf = render_pdf_bytes(model, chain)
    return {
        "format": "pdf",
        "decision_id": decision_id,
        "ruleset_version": ruleset_version,
        "schedule_hash": model.schedule_hash,
        "bytes": pdf,
        "legal_posture": LEGAL_POSTURE,
        "statute_url": STATUTE_URL,
        "downloadable": True,
    }


def build_xlsx_exhibit(
    result: ComposeResult,
    *,
    decision_id: str,
    ruleset_version: str,
    ruleset_hash: str = "",
    week_start: date | None = None,
) -> dict[str, Any]:
    """XLSX render of the in-memory schedule. Refuses while OT is pending."""
    assert_no_pending_ot(result.ot_proposals)
    model = ScheduleModel(
        decision_id=decision_id,
        ruleset_version=ruleset_version,
        ruleset_hash=ruleset_hash,
        week_start=week_start or date.min,
        placed=tuple(result.placed),
        ot_proposals=tuple(result.ot_proposals),
        schedule_hash=schedule_hash(result.placed),
    )
    xlsx = render_xlsx_bytes(model)
    return {
        "format": "xlsx",
        "decision_id": decision_id,
        "ruleset_version": ruleset_version,
        "schedule_hash": model.schedule_hash,
        "bytes": xlsx,
        "rows": [
            {
                "shift_id": p.shift_id,
                "employee": p.employee,
                "date": p.date.isoformat(),
                "start": p.start.isoformat(timespec="minutes"),
                "end": p.end.isoformat(timespec="minutes"),
                "worked_hours": p.worked_hours,
            }
            for p in result.placed
        ],
        "ot_lines": [
            {
                "proposal_id": p.proposal_id,
                "employee": p.employee,
                "date": p.date.isoformat(),
                "hours": p.hours,
                "multiplier": p.multiplier,
                "section": p.section,
                "status": p.status,
            }
            for p in result.ot_proposals
        ],
        "legal_posture": LEGAL_POSTURE,
        "statute_url": STATUTE_URL,
        "downloadable": True,
    }


def write_exhibits(
    result: ComposeResult,
    *,
    chain: AuditChain,
    decision_id: str,
    ruleset_version: str,
    ruleset_hash: str,
    week_start: date,
    out_dir: Path,
    actor: str = "agent",
    timestamp: str | None = None,
    history_root: Path | None = None,
    history_max_weeks: int | None = None,
    parameter_shelf_id: str | None = None,
    history_prior_week_starts: list[str] | None = None,
    replay_inputs: ReplayInputs | None = None,
) -> ExhibitBundle:
    """Issue the schedule and write PDF + XLSX + audit.json under out_dir.

    When history_root is set, append the issued week to the rolling history shelf.
    Draft-only compose paths never call this — history stays issue-only.
    Copies parameter_shelf_id onto the week record when provided.
    When replay_inputs is provided, write replay_inputs.json beside exhibits
    and into the history week dir (Wave C envelope).
    """
    if any(e.kind == "rule_refuse" for e in chain.events):
        raise ExportBlocked("export blocked: rule_refuse present on chain")
    assert_no_pending_ot(result.ot_proposals)

    prior_starts = list(history_prior_week_starts or [])
    store: WeekHistoryStore | None = None
    if history_root is not None:
        kwargs: dict[str, Any] = {"root": Path(history_root)}
        if history_max_weeks is not None:
            kwargs["history_max_weeks"] = history_max_weeks
        store = WeekHistoryStore(**kwargs)
        if history_prior_week_starts is None:
            prior_starts = [r.week_start for r in store.load_index().rows]

    model = ScheduleModel.from_compose(
        result,
        decision_id=decision_id,
        ruleset_version=ruleset_version,
        ruleset_hash=ruleset_hash,
        week_start=week_start,
    )
    issue = issue_schedule(
        result,
        chain=chain,
        decision_id=decision_id,
        ruleset_version=ruleset_version,
        ruleset_hash=ruleset_hash,
        actor=actor,
        timestamp=timestamp,
        parameter_shelf_id=parameter_shelf_id,
        history_prior_week_starts=prior_starts,
    )
    if issue.schedule_hash != model.schedule_hash:
        raise ExportBlocked("issued schedule hash diverged from model")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_bytes = render_pdf_bytes(model, chain)
    xlsx_bytes = render_xlsx_bytes(model)
    audit = build_audit_document(chain, model=model)

    pdf_path = out_dir / f"{decision_id}.pdf"
    xlsx_path = out_dir / f"{decision_id}.xlsx"
    audit_path = out_dir / "audit.json"
    pdf_path.write_bytes(pdf_bytes)
    xlsx_path.write_bytes(xlsx_bytes)
    audit_path.write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    envelope_path: Path | None = None
    envelope_inputs: ReplayInputs | None = None
    if replay_inputs is not None:
        envelope_inputs = with_gate_snapshot(replay_inputs, issue.gate_snapshot)
        envelope_path = write_replay_envelope(
            out_dir / REPLAY_INPUTS_FILENAME, envelope_inputs
        )

    exhibit_paths: dict[str, str] = {
        "pdf": str(pdf_path),
        "xlsx": str(xlsx_path),
        "audit_json": str(audit_path),
    }
    if envelope_path is not None:
        exhibit_paths["replay_inputs"] = str(envelope_path)

    if store is not None:
        store.ensure_layout()
        week_path = store.weeks_dir / week_dirname(week_start, decision_id)
        week_path.mkdir(parents=True, exist_ok=True)
        # Durable envelope lives in the week dir for dual-plane reopen.
        if envelope_inputs is not None:
            week_envelope = write_replay_envelope(
                week_path / REPLAY_INPUTS_FILENAME, envelope_inputs
            )
            exhibit_paths["replay_inputs"] = str(week_envelope)
        store.record_issue(
            week_start=week_start,
            decision_id=decision_id,
            exhibit_paths=exhibit_paths,
            schedule_hash=issue.schedule_hash,
            gate_snapshot_hash=gate_snapshot_hash(issue.gate_snapshot),
            parameter_shelf_id=parameter_shelf_id,
            issued_at=timestamp,
        )

    return ExhibitBundle(
        model=model,
        issue=issue,
        paths=ExhibitPaths(
            pdf=pdf_path,
            xlsx=xlsx_path,
            audit_json=audit_path,
            replay_inputs=envelope_path,
        ),
        pdf_bytes=pdf_bytes,
        xlsx_bytes=xlsx_bytes,
        audit=audit,
    )
