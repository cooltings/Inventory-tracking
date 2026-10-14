"""PDF test report for a device."""
import datetime as dt
from html import escape
from pathlib import Path

from PySide6.QtCore import QMarginsF
from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter, QTextDocument

from . import APP_NAME
from .checklist import (CUSTOM_SECTION, FAIL, NA, PASS, SECTIONS, is_custom, label_of,
                        summarise)
from .db import device_title, num

COLOURS = {  # background, text
    PASS: ("#dcfce7", "#166534"),
    FAIL: ("#fee2e2", "#991b1b"),
    NA: ("#e5e7eb", "#374151"),
    "": ("#fef9c3", "#854d0e"),
}
LABEL_BG = "#f3f4f6"
LINE = "#c9ced6"


def _e(value) -> str:
    return escape(str(value if value not in (None, "") else ""))


def _date(text) -> str:
    try:
        return dt.date.fromisoformat(str(text)[:10]).strftime("%d %b %Y")
    except (TypeError, ValueError):
        return str(text or "")


def _battery(device) -> str:
    pct = device.get("battery_health")
    if not pct:
        return "Not recorded"
    colour = "#166534" if pct >= 85 else "#a16207" if pct >= 80 else "#991b1b"
    text = f"<b><span style='color:{colour}'>{int(pct)}%</span></b>"
    cycles = int(num(device.get("battery_cycles")))
    return text + (f" &nbsp;({cycles} cycles)" if cycles else "")


def _pill(result) -> str:
    bg, fg = COLOURS.get(result, COLOURS[""])
    return f"<span style='color:{fg}'><b>{_e(result or 'Not tested')}</b></span>"


def _grid(pairs) -> str:
    """Two label/value pairs per row."""
    rows = []
    for i in range(0, len(pairs), 2):
        cells = ""
        for label, value in pairs[i:i + 2]:
            cells += (f"<td width='20%' bgcolor='{LABEL_BG}'><b>{_e(label)}</b></td>"
                      f"<td width='30%'>{value if value else '<span style=\"color:#888\">-</span>'}</td>")
        if len(pairs[i:i + 2]) == 1:
            cells += "<td width='20%'></td><td width='30%'></td>"
        rows.append(f"<tr>{cells}</tr>")
    return (f"<table width='100%' border='1' cellspacing='0' cellpadding='5' "
            f"style='border-color:{LINE}; border-style:solid;'>{''.join(rows)}</table>")


def _wide(label, value_html) -> str:
    return (f"<table width='100%' border='1' cellspacing='0' cellpadding='5' "
            f"style='border-color:{LINE}; border-style:solid;'><tr>"
            f"<td width='20%' bgcolor='{LABEL_BG}'><b>{_e(label)}</b></td>"
            f"<td width='80%'>{value_html}</td></tr></table>")


def sections_for(results: dict) -> list:
    """[(section name, [(key, label)])] for the standard tests plus any custom ones."""
    out = [(name, [(key, label) for key, label, _ in items]) for name, items in SECTIONS]
    custom = [(key, label_of(key)) for key in results if is_custom(key)]
    if custom:
        out.append((CUSTOM_SECTION, sorted(custom, key=lambda kv: kv[1].lower())))
    return out


def report_html(db, device_id) -> str:
    device = db.get("devices", device_id)
    if not device:
        raise ValueError("Device not found")
    results = db.device_tests(device_id)
    meta = db.test_meta(device_id)
    counts = summarise(results)
    business = db.setting("report_business").strip()
    title = device_title({**device, "grade": ""}).replace(" - Faulty / Spares or Repair", "")

    # ---- overall verdict
    if not counts["done"]:
        bg, fg, verdict = COLOURS[""][0], COLOURS[""][1], "NOT TESTED"
    elif counts["fail"]:
        bg, fg = COLOURS[FAIL]
        verdict = f"{counts['fail']} TEST{'S' if counts['fail'] != 1 else ''} FAILED"
    elif counts["untested"]:
        bg, fg = COLOURS[""]
        verdict = f"INCOMPLETE: {counts['untested']} NOT TESTED"
    else:
        bg, fg = COLOURS[PASS]
        verdict = "ALL TESTS PASSED"

    imei = device.get("imei") or ""
    parts = [f"""
<html><body style="font-family: 'Segoe UI', Arial, Helvetica, sans-serif; font-size: 9pt; color:#1f2937;">
<table width="100%" cellspacing="0" cellpadding="0"><tr>
  <td><span style="font-size:20pt; font-weight:bold;">Device Test Report</span><br/>
      <span style="font-size:11pt; color:#4b5563;">{_e(business)}</span></td>
  <td align="right"><span style="color:#6b7280;">Report date</span><br/>
      <b>{dt.date.today().strftime('%d %b %Y')}</b></td>
</tr></table>
<hr/>
<p style="font-size:13pt; margin-top:4px; margin-bottom:6px;"><b>{_e(title)}</b></p>
<table width="100%" cellspacing="0" cellpadding="8"><tr><td align="center" bgcolor="{bg}">
  <span style="font-size:14pt; font-weight:bold; color:{fg};">{verdict}</span><br/>
  <span style="color:{fg};">Passed <b>{counts['pass']}</b> &nbsp;·&nbsp; Failed <b>{counts['fail']}</b>
  &nbsp;·&nbsp; N/A <b>{counts['na']}</b> &nbsp;·&nbsp; Not tested <b>{counts['untested']}</b>
  &nbsp;(of {counts['total']} checks)</span>
</td></tr></table>
<p style="margin:2px;"></p>
"""]

    # ---- device details
    pairs = [
        ("Brand", _e(device.get("brand"))), ("Model", _e(device.get("model"))),
        ("Model number", _e(device.get("model_number"))), ("Colour", _e(device.get("colour"))),
        ("Storage", _e(device.get("storage"))), ("RAM", _e(device.get("ram"))),
        ("IMEI", f"<b>{_e(imei)}</b>" if imei else ""), ("Serial number", f"<b>{_e(device.get('serial'))}</b>"
                                                       if device.get("serial") else ""),
        ("Network", _e(device.get("network"))), ("OS version", _e(device.get("os_version"))),
        ("Battery health", _battery(device)), ("Cosmetic condition", _e(device.get("grade"))),
        ("SKU", _e(device.get("sku"))), ("Quantity", ""),
        ("Tested by", _e(meta.get("tested_by"))), ("Date tested", _e(_date(meta.get("tested_at")))),
    ]
    pairs = [p for p in pairs if p[0] != "Quantity"]
    parts.append("<p style='margin:6px 0 3px 0; font-size:11pt;'><b>Device details</b></p>")
    parts.append(_grid(pairs))
    if device.get("cosmetic_notes"):
        parts.append("<p style='margin:2px;'></p>")
        parts.append(_wide("Cosmetic notes", _e(device["cosmetic_notes"]).replace("\n", "<br/>")))
    if device.get("faults"):
        parts.append("<p style='margin:2px;'></p>")
        parts.append(_wide("Recorded faults", _e(device["faults"])))
    if meta.get("notes"):
        parts.append("<p style='margin:2px;'></p>")
        parts.append(_wide("Tester notes", _e(meta["notes"]).replace("\n", "<br/>")))

    # ---- failed items
    sections = sections_for(results)
    failed = [(name, label, results[key].get("note", "")) for name, items in sections
              for key, label in items if results.get(key, {}).get("result") == FAIL]
    if failed:
        parts.append("<p style='margin:10px 0 3px 0; font-size:11pt; color:#991b1b;'>"
                     "<b>Failed items</b></p>")
        rows = "".join(
            f"<tr><td width='34%'>{_e(label)}<br/><span style='color:#6b7280; font-size:8pt;'>"
            f"{_e(name)}</span></td><td width='66%'>{_e(note) or '<span style=\"color:#888\">No note</span>'}</td></tr>"
            for name, label, note in failed)
        parts.append(f"<table width='100%' border='1' cellspacing='0' cellpadding='5' "
                     f"style='border-color:{LINE}; border-style:solid;' bgcolor='#fff5f5'>{rows}</table>")

    # ---- full results
    parts.append("<p style='page-break-before:always;'></p>")
    parts.append("<p style='margin:0 0 4px 0; font-size:13pt;'><b>Detailed test results</b></p>")
    parts.append(f"<p style='margin:0 0 6px 0; color:#6b7280;'>{_e(title)} &nbsp;·&nbsp; "
                 f"IMEI {_e(imei) or '-'} &nbsp;·&nbsp; Serial {_e(device.get('serial')) or '-'}</p>")
    for name, items in sections:
        rows = ""
        for key, label in items:
            row = results.get(key, {})
            result, note = row.get("result", ""), row.get("note", "")
            bg, fg = COLOURS.get(result, COLOURS[""])
            rows += (f"<tr><td width='46%'>{_e(label)}</td>"
                     f"<td width='13%' align='center' bgcolor='{bg}'><span style='color:{fg}'>"
                     f"<b>{_e(result or 'Not tested')}</b></span></td>"
                     f"<td width='41%'>{_e(note)}</td></tr>")
        parts.append(f"<p style='margin:8px 0 2px 0; font-size:10.5pt;'><b>{_e(name)}</b></p>"
                     f"<table width='100%' border='1' cellspacing='0' cellpadding='4' "
                     f"style='border-color:{LINE}; border-style:solid;'>"
                     f"<tr bgcolor='{LABEL_BG}'><td width='46%'><b>Test</b></td>"
                     f"<td width='13%' align='center'><b>Result</b></td>"
                     f"<td width='41%'><b>Notes</b></td></tr>{rows}</table>")

    parts.append(f"<p style='margin-top:12px; color:#6b7280; font-size:8pt;'>Generated by "
                 f"{escape(APP_NAME)} on {dt.datetime.now().strftime('%d %b %Y %H:%M')}. "
                 f"Results reflect the tester's checks on the date shown.</p></body></html>")
    return "".join(parts)


def default_filename(device) -> str:
    ident = device.get("imei") or device.get("serial") or ""
    name = f"TestReport-{device.get('sku') or 'device'}" + (f"-{ident}" if ident else "")
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in name) + ".pdf"


def write_pdf(db, device_id, path) -> Path:
    path = Path(path)
    doc = QTextDocument()
    doc.setHtml(report_html(db, device_id))
    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QPageSize.A4))
    writer.setPageMargins(QMarginsF(14, 14, 14, 14), QPageLayout.Millimeter)
    writer.setTitle(f"Device Test Report - {db.get('devices', device_id).get('sku')}")
    writer.setCreator(APP_NAME)
    doc.print_(writer)
    return path
