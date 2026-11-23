"""The three rate documents, one per version, each in a different format.

v1: PDF rate notice   - wide table (regions x categories) with boilerplate and footnotes
v2: Excel rate sheet  - long table, merged title cells, notes row, different column and
                        category labels ("Children (0-18)", "ABD / Disabled", ...), blank rows
v3: Email (plain text) - prose amendment mentioning only the changed cells, one with an end date
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from data_gen import spec

XLSX_CATEGORY_LABELS = {
    "Child": "Children (0-18)",
    "Adult": "Adults (19-64)",
    "Disabled": "ABD / Disabled",
    "Expansion": "Medicaid Expansion Adults",
}


def _money(d) -> str:
    return f"${d:,.2f}"


def write_pdf(path: Path) -> None:
    styles = getSampleStyleSheet()
    # invariant=1: no creation timestamp or random document ID, so the same content gives the same bytes.
    doc = SimpleDocTemplate(str(path), pagesize=letter, title="CY2026 Capitation Rate Notice", invariant=1)
    story = [
        Paragraph(spec.AGENCY, styles["Title"]),
        Paragraph("Medicaid Managed Care Program &mdash; Calendar Year 2026 Capitation Rate Notice", styles["Heading2"]),
        Paragraph(f"Notice date: {spec.PUBLISHED['v1']:%B %d, %Y} &nbsp;&nbsp; Notice No. WDHS-MC-2026-01", styles["Normal"]),
        Spacer(1, 10),
        Paragraph(
            "Pursuant to the managed care contract, the Department hereby publishes the monthly capitation "
            "rates (per member per month, PMPM) payable to contracted health plans. The rates below are "
            "<b>effective January 1, 2026</b> and remain in effect until amended by the Department. Payments "
            "are issued monthly by electronic funds transfer with an accompanying remittance advice.",
            styles["Normal"],
        ),
        Spacer(1, 12),
        Paragraph("Table 1. Monthly capitation rates by region and eligibility category (PMPM)", styles["Heading4"]),
    ]
    header = ["Region", "Child", "Adult", "Disabled", "Expansion*"]
    rows = [header] + [[r] + [_money(spec.RATES_V1[(r, c)]) for c in spec.CATEGORIES] for r in spec.REGIONS]
    table = Table(rows, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
    ]))
    story += [
        table,
        Spacer(1, 12),
        Paragraph("* Expansion rates apply to adults eligible under the expansion group and remain subject to federal approval.", styles["Italic"]),
        Paragraph("Rates exclude maternity kick payments and directed payments, which are issued separately.", styles["Italic"]),
        Paragraph("Questions regarding this notice may be directed to the Office of Managed Care Finance.", styles["Normal"]),
    ]
    doc.build(story)


def write_xlsx(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Rates Eff 04-01-2026"
    ws.merge_cells("A1:D1")
    ws["A1"] = f"{spec.AGENCY.upper()} - MANAGED CARE RATE SHEET"
    ws["A1"].font = Font(bold=True, size=13)
    ws["A1"].alignment = Alignment(horizontal="center")
    ws.merge_cells("A2:D2")
    ws["A2"] = (f"Revised rates effective 04/01/2026 (prospective). Published {spec.PUBLISHED['v2']:%m/%d/%Y}. "
                "Rows marked R have changed from the prior rate period.")
    ws["A2"].font = Font(italic=True)
    ws.append([])
    ws.append(["Rgn", "Eligibility Group", "Eff. Date", "Monthly Cap Rate ($)", "Chg"])
    for cell in ws[4]:
        cell.font = Font(bold=True)
    for region in spec.REGIONS:
        for cat in spec.CATEGORIES:
            changed = (region, cat) in spec.V2_CHANGES
            ws.append([region.upper(), XLSX_CATEGORY_LABELS[cat], "04/01/2026",
                       float(spec.RATES_V2[(region, cat)]), "R" if changed else ""])
        ws.append([])   # blank separator row between regions
    ws.append(["Notes:"])
    ws.append(["1. Rates are PMPM and exclude directed payments."])
    ws.append(["2. Cells without an R flag carry forward unchanged."])
    for col, width in zip("ABCDE", (8, 28, 12, 22, 6)):
        ws.column_dimensions[col].width = width
    for row in ws.iter_rows(min_row=5, min_col=4, max_col=4):
        for cell in row:
            if isinstance(cell.value, float):
                cell.number_format = "#,##0.00"
    wb.save(path)
    _pin_timestamps(path, f"{spec.PUBLISHED['v2']:%Y-%m-%d}T00:00:00Z")


def _pin_timestamps(path: Path, stamp: str) -> None:
    """openpyxl stamps 'now' into the file and its zip entries. Pin both so regenerating the data gives
    byte-identical files (the document's sha256 is its identity for idempotent ingestion)."""
    with zipfile.ZipFile(path) as z:
        entries = [(i.filename, z.read(i.filename)) for i in z.infolist()]
    when = tuple(int(x) for x in re.split(r"[-T:Z]", stamp)[:6])
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries:
            if name == "docProps/core.xml":
                data = re.sub(rb"(<dcterms:(?:created|modified)[^>]*>)[^<]*", rb"\g<1>" + stamp.encode(), data)
            z.writestr(zipfile.ZipInfo(name, date_time=when), data, compress_type=zipfile.ZIP_DEFLATED)


def email_text() -> str:
    na, sd = spec.V3_ROWS
    return f"""From: Office of Managed Care Finance <mcfinance@westlake-dhs.example>
To: Plan Finance Contacts
Date: Wed, {spec.PUBLISHED['v3']:%d %b %Y} 16:42:00 -0500
Subject: Rate amendment - CY2026 capitation (Notice WDHS-MC-2026-01A)

Good afternoon,

Following the actuarial review of the North region adult population, the Department
is amending the CY2026 capitation rates as follows.

Effective retroactively to January 1, 2026, the North Adult rate is revised from
$400.00 to {_money(na['pmpm'])} PMPM. This rate remains in effect until further notice.

In addition, for the period January 1 through March 31, 2026 only, the South Disabled
(ABD) rate is revised from $1,150.00 to {_money(sd['pmpm'])} PMPM. The South Disabled rate
published in the April 1 rate sheet is unchanged.

All other rates in Notice WDHS-MC-2026-01 and the April 1, 2026 rate sheet remain
unchanged. Retroactive differences for prior months will be paid through a future
remittance as a retro adjustment.

Please reply to confirm receipt.

Regards,
Managed Care Finance
Westlake Department of Health Services
(This message and any attachments are intended for the named recipients only.)
"""


def write_email(path: Path) -> None:
    path.write_text(email_text(), encoding="utf-8")


def write_all(out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "v1": out_dir / "v1_rate_notice_CY2026.pdf",
        "v2": out_dir / "v2_rate_sheet_eff_2026-04-01.xlsx",
        "v3": out_dir / "v3_rate_amendment_email.txt",
    }
    write_pdf(files["v1"])
    write_xlsx(files["v2"])
    write_email(files["v3"])
    return files
