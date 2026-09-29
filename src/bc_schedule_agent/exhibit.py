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
    issue_schedule,
    schedule_hash,
)
from bc_schedule_agent.models import ComposeResult, OvertimeProposal, PlacedShift


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
        return (
            f"{actor} approved overtime {subj.get('proposal_id')} "
            f"at {ev.get('timestamp')}."
        )
    if kind == "ot_refused":
        return (
            f"{actor} refused overtime {subj.get('proposal_id')} "
            f"(returned to composer)."
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


def render_pdf_bytes(model: ScheduleModel, chain: AuditChain) -> bytes:
    """Minimal single-page PDF: roster + legal posture + prose appendix."""
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

    # PDF content stream (Helvetica, top-down).
    y = 780
    content_parts: list[str] = ["BT", "/F1 9 Tf", "14 TL"]
    for line in lines:
        content_parts.append(f"1 0 0 1 40 {y} Tm ({_pdf_escape(line)}) Tj")
        y -= 11
        if y < 40:
            break
    content_parts.append("ET")
    stream = "\n".join(content_parts).encode("latin-1", errors="replace")

    objects: list[bytes] = []
    objects.append(b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n")
    objects.append(b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n")
    objects.append(
        b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>endobj\n"
    )
    objects.append(
        f"4 0 obj<< /Length {len(stream)} >>stream\n".encode("ascii")
        + stream
        + b"\nendstream\nendobj\n"
    )
    objects.append(
        b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n"
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


def render_xlsx_bytes(model: ScheduleModel) -> bytes:
    """Minimal OOXML workbook: shifts + OT lines + legal posture row."""
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

    def cell_xml(ref: str, value: str) -> str:
        return (
            f'<c r="{ref}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
        )

    sheet_rows: list[str] = []
    for r_idx, row in enumerate(rows, start=1):
        cells = []
        for c_idx, value in enumerate(row):
            col = ""
            n = c_idx + 1
            while n:
                n, rem = divmod(n - 1, 26)
                col = chr(65 + rem) + col
            cells.append(cell_xml(f"{col}{r_idx}", value))
        sheet_rows.append(f'<row r="{r_idx}">{"".join(cells)}</row>')

    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="schedule" sheetId="1" r:id="rId1"/></sheets>'
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
        "</Types>"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
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
) -> ExhibitBundle:
    """Issue the schedule and write PDF + XLSX + audit.json under out_dir."""
    if any(e.kind == "rule_refuse" for e in chain.events):
        raise ExportBlocked("export blocked: rule_refuse present on chain")
    assert_no_pending_ot(result.ot_proposals)

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

    return ExhibitBundle(
        model=model,
        issue=issue,
        paths=ExhibitPaths(pdf=pdf_path, xlsx=xlsx_path, audit_json=audit_path),
        pdf_bytes=pdf_bytes,
        xlsx_bytes=xlsx_bytes,
        audit=audit,
    )
