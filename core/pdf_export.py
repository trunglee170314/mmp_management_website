from io import BytesIO
from pathlib import Path
import re
from xml.sax.saxutils import escape, quoteattr

from django.conf import settings
from django.db.models import Prefetch
from django.utils import timezone
from django.utils.text import slugify
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    CondPageBreak,
    KeepTogether,
    LongTable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .models import Meeting, MeetingTask, TaskBoard
from .services import frozen_meeting_entry_actions, meeting_entries_action_groups, previous_meeting_completed_after, task_snapshot


GREEN = HexColor("#005C3D")
GREEN_DARK = HexColor("#063D2E")
GREEN_SOFT = HexColor("#EAF4EE")
INK = HexColor("#252B27")
MUTED = HexColor("#6D766F")
LINE = HexColor("#DCE5DF")
PAPER_SOFT = HexColor("#F6F8F6")
RED = HexColor("#B84E48")
AMBER = HexColor("#A96925")


def _register_fonts():
    candidates = [
        Path(settings.BASE_DIR) / "assets" / "core" / "fonts",
        Path("/usr/share/fonts/truetype/dejavu"),
        Path("/usr/local/share/fonts"),
    ]
    for folder in candidates:
        regular = folder / "DejaVuSans.ttf"
        bold = folder / "DejaVuSans-Bold.ttf"
        if regular.exists() and bold.exists():
            if "MMPDejaVu" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("MMPDejaVu", str(regular)))
                pdfmetrics.registerFont(TTFont("MMPDejaVu-Bold", str(bold)))
            return "MMPDejaVu", "MMPDejaVu-Bold"
    return "Helvetica", "Helvetica-Bold"


FONT_REGULAR, FONT_BOLD = _register_fonts()


def _safe_text(value, empty="-"):
    text = str(value).strip() if value is not None else ""
    return escape(text or empty).replace("\n", "<br/>")


def _paragraph(value, style, empty="-"):
    return Paragraph(_safe_text(value, empty=empty), style)


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "MMPTitle", parent=base["Title"], fontName=FONT_BOLD, fontSize=22,
            leading=27, textColor=INK, alignment=TA_LEFT, spaceAfter=3 * mm,
        ),
        "eyebrow": ParagraphStyle(
            "MMPEyebrow", parent=base["Normal"], fontName=FONT_BOLD, fontSize=8,
            leading=10, textColor=GREEN, tracking=1.3, spaceAfter=2 * mm,
        ),
        "body": ParagraphStyle(
            "MMPBody", parent=base["BodyText"], fontName=FONT_REGULAR, fontSize=9,
            leading=13, textColor=INK, wordWrap="CJK",
        ),
        "body_bold": ParagraphStyle(
            "MMPBodyBold", parent=base["BodyText"], fontName=FONT_BOLD, fontSize=9,
            leading=13, textColor=INK, wordWrap="CJK",
        ),
        "small": ParagraphStyle(
            "MMPSmall", parent=base["BodyText"], fontName=FONT_REGULAR, fontSize=7.5,
            leading=10, textColor=MUTED, wordWrap="CJK",
        ),
        "small_bold": ParagraphStyle(
            "MMPSmallBold", parent=base["BodyText"], fontName=FONT_BOLD, fontSize=7.5,
            leading=10, textColor=INK, wordWrap="CJK",
        ),
        "meta_label": ParagraphStyle(
            "MMPMetaLabel", parent=base["BodyText"], fontName=FONT_BOLD, fontSize=6.5,
            leading=8, textColor=MUTED, tracking=.7, spaceAfter=1.2 * mm,
        ),
        "task_title": ParagraphStyle(
            "MMPTaskTitle", parent=base["Heading2"], fontName=FONT_BOLD, fontSize=12,
            leading=16, textColor=INK, wordWrap="CJK",
        ),
        "section": ParagraphStyle(
            "MMPSection", parent=base["Heading3"], fontName=FONT_BOLD, fontSize=9,
            leading=12, textColor=GREEN_DARK, spaceBefore=2 * mm, spaceAfter=2 * mm,
        ),
        "center": ParagraphStyle(
            "MMPCenter", parent=base["BodyText"], fontName=FONT_BOLD, fontSize=8,
            leading=10, textColor=INK, alignment=TA_CENTER,
        ),
        "right_small": ParagraphStyle(
            "MMPRightSmall", parent=base["BodyText"], fontName=FONT_REGULAR, fontSize=7.5,
            leading=10, textColor=MUTED, alignment=TA_RIGHT,
        ),
    }


def _meta_cell(label, value, styles):
    return [
        Paragraph(escape(label.upper()), styles["meta_label"]),
        _paragraph(value, styles["body_bold"]),
    ]


def _summary_card(value, label, styles):
    value_style = ParagraphStyle(
        f"Summary{label}", parent=styles["center"], fontSize=15, leading=18,
        textColor=GREEN_DARK,
    )
    label_style = ParagraphStyle(
        f"SummaryLabel{label}", parent=styles["meta_label"], alignment=TA_CENTER,
    )
    return Table(
        [[Paragraph(escape(str(value)), value_style)], [Paragraph(escape(label.upper()), label_style)]],
        colWidths=[39 * mm],
        style=TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, 0), 3 * mm),
            ("BOTTOMPADDING", (0, 1), (-1, 1), 3 * mm),
        ]),
    )


def _action_table(items, styles):
    if not items:
        return Table(
            [[Paragraph("No action items.", styles["small"])]],
            colWidths=[176 * mm],
            style=TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), PAPER_SOFT),
                ("BOX", (0, 0), (-1, -1), .6, LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 3 * mm),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 2.5 * mm),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5 * mm),
            ]),
        )

    rows = [[
        Paragraph("STATUS", styles["meta_label"]),
        Paragraph("ACTION ITEM", styles["meta_label"]),
        Paragraph("ASSIGNEE", styles["meta_label"]),
        Paragraph("DUE DATE", styles["meta_label"]),
    ]]
    for item in items:
        status = "Completed" if item.is_completed else "Open"
        due = item.due_date.strftime("%d %b %Y") if item.due_date else "-"
        rows.append([
            Paragraph(status, styles["small_bold"]),
            _paragraph(item.content, styles["body"]),
            _paragraph(item.assignee or "Unassigned", styles["small"]),
            Paragraph(due, styles["small"]),
        ])
    table = LongTable(rows, colWidths=[21 * mm, 91 * mm, 39 * mm, 25 * mm], repeatRows=1, splitByRow=1)
    commands = [
        ("BACKGROUND", (0, 0), (-1, 0), PAPER_SOFT),
        ("BOX", (0, 0), (-1, -1), .6, LINE),
        ("INNERGRID", (0, 0), (-1, -1), .35, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2.2 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2.2 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 2 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2 * mm),
    ]
    for row_number, item in enumerate(items, start=1):
        commands.append(("TEXTCOLOR", (0, row_number), (0, row_number), GREEN if item.is_completed else AMBER))
    table.setStyle(TableStyle(commands))
    return table


def _page_decorator(meeting):
    def draw(canvas, doc):
        canvas.saveState()
        width, height = A4
        canvas.setTitle(f"{meeting.title} - MMP management")
        canvas.setAuthor("MMP management")
        if meeting.status == Meeting.Status.DRAFT:
            # Use a deliberately pale solid colour instead of PDF transparency.
            # This renders consistently in browsers and offline PDF readers.
            canvas.setFillColor(HexColor("#E7F0EA"))
            canvas.setFont(FONT_BOLD, 46)
            canvas.translate(width / 2, height / 2)
            canvas.rotate(32)
            canvas.drawCentredString(0, 0, "DRAFT")
            canvas.rotate(-32)
            canvas.translate(-width / 2, -height / 2)

        canvas.setFillColor(GREEN_DARK)
        canvas.setFont(FONT_BOLD, 8)
        canvas.drawString(17 * mm, height - 13 * mm, "MMP WORKSPACE")
        canvas.setFillColor(MUTED)
        canvas.setFont(FONT_REGULAR, 7.5)
        canvas.drawRightString(width - 17 * mm, height - 13 * mm, meeting.meeting_date.strftime("%d %b %Y").upper())
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(.6)
        canvas.line(17 * mm, height - 16 * mm, width - 17 * mm, height - 16 * mm)

        canvas.line(17 * mm, 13 * mm, width - 17 * mm, 13 * mm)
        canvas.setFillColor(MUTED)
        canvas.setFont(FONT_REGULAR, 7)
        footer_title = meeting.title[:70]
        canvas.drawString(17 * mm, 8.5 * mm, footer_title)
        canvas.drawRightString(width - 17 * mm, 8.5 * mm, f"Page {doc.page}")
        canvas.restoreState()
    return draw


def _task_story(entry, number, meeting, previous_completed_after, styles, action_groups=None):
    task = entry.task
    is_frozen = meeting.status == Meeting.Status.FINALIZED and entry.snapshot.get("snapshot_version", 0) >= 2
    snapshot = entry.snapshot if is_frozen else (task_snapshot(task) if task else entry.snapshot or {})
    title = snapshot.get("title") or (task.title if task else "Archived task")
    scope = snapshot.get("scope") or (task.scope_label if task else "-")
    assignee = snapshot.get("assignee") or (str(task.assignee) if task and task.assignee else "Unassigned")
    status = snapshot.get("status") or (task.get_status_display() if task else "Unavailable")
    boards = snapshot.get("boards") or []
    boards_text = ", ".join(boards) if boards else "No boards"
    link_url = snapshot.get("link_url") or snapshot.get("redmine_url") or (task.link_url if task else "")
    due_date = snapshot.get("due_date_label") or (task.due_date.strftime("%d %b %Y") if task and task.due_date else "-")

    number_box = Table(
        [[Paragraph(str(number), styles["center"])]], colWidths=[9 * mm], rowHeights=[9 * mm],
        style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), GREEN_SOFT),
            ("BOX", (0, 0), (-1, -1), .6, HexColor("#BED8C9")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ]),
    )
    task_header = Table(
        [[number_box, _paragraph(title, styles["task_title"])]],
        colWidths=[13 * mm, 163 * mm],
        style=TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]),
    )
    meta = Table(
        [[
            _meta_cell("Scope", scope, styles),
            _meta_cell("Assignee", assignee, styles),
            _meta_cell("Status", status, styles),
            _meta_cell("Due Date", due_date, styles),
            _meta_cell("Boards", boards_text, styles),
        ]],
        colWidths=[31 * mm, 39 * mm, 28 * mm, 31 * mm, 47 * mm],
        style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), PAPER_SOFT),
            ("BOX", (0, 0), (-1, -1), .6, LINE),
            ("INNERGRID", (0, 0), (-1, -1), .35, LINE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 3 * mm),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3 * mm),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5 * mm),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5 * mm),
        ]),
    )
    header_parts = [task_header, Spacer(1, 2.5 * mm), meta]
    if link_url:
        link = f'<link href={quoteattr(link_url)} color="#005C3D"><u>Open Link</u></link>'
        header_parts.extend([Spacer(1, 1.8 * mm), Paragraph(link, styles["small_bold"])])

    # Do not start a task at the foot of a page unless there is enough room for
    # its identity, metadata and at least the first content section.
    parts = [CondPageBreak(72 * mm), KeepTogether(header_parts), Spacer(1, 3 * mm)]
    parts.append(Paragraph("PROGRESS", styles["section"]))
    progress_value = snapshot.get("weekly_progress", entry.weekly_progress) if is_frozen else entry.weekly_progress
    progress_box = Table(
        [[_paragraph(progress_value, styles["body"], empty="No progress update recorded.")]],
        colWidths=[176 * mm],
        style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.white),
            ("BOX", (0, 0), (-1, -1), .6, LINE),
            ("LEFTPADDING", (0, 0), (-1, -1), 3 * mm),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3 * mm),
            ("TOPPADDING", (0, 0), (-1, -1), 3 * mm),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3 * mm),
        ]),
    )
    parts.append(progress_box)

    if is_frozen:
        open_previous, recent_completed, new_actions = frozen_meeting_entry_actions(entry)
        previous_actions = open_previous + recent_completed
    elif task:
        open_previous, recent_completed, new_actions = action_groups or ([], [], [])
        previous_actions = open_previous + recent_completed
    else:
        previous_actions = []
        new_actions = []

    parts.extend([
        CondPageBreak(28 * mm),
        Paragraph("PREVIOUS ACTION ITEMS", styles["section"]),
        _action_table(previous_actions, styles),
        CondPageBreak(28 * mm),
        Paragraph("NEW ACTION ITEMS", styles["section"]),
        _action_table(new_actions, styles),
        Spacer(1, 6 * mm),
    ])
    return parts


def build_meeting_pdf(meeting):
    styles = _styles()
    output = BytesIO()
    doc = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=17 * mm,
        leftMargin=17 * mm,
        topMargin=22 * mm,
        bottomMargin=18 * mm,
        title=f"{meeting.title} - MMP management",
        author="MMP management",
        subject="Meeting Minutes",
    )
    entries = list(
        meeting.task_entries.select_related("task", "task__assignee")
        .prefetch_related("task__scopes")
        .prefetch_related(Prefetch(
            "task__board_links",
            queryset=TaskBoard.objects.filter(released_at__isnull=True).select_related("board"),
            to_attr="_snapshot_board_links",
        ))
        .order_by("position")
    )
    reviewed = sum(
        1 for entry in entries
        if entry.snapshot.get("review_state", entry.review_state) != MeetingTask.ReviewState.PENDING
    )
    if meeting.status == Meeting.Status.FINALIZED:
        open_actions = sum(
            1
            for entry in entries
            for item in entry.snapshot.get("new_actions", [])
            if not item.get("is_completed")
        )
    else:
        open_actions = meeting.action_items.filter(is_completed=False).count()
    previous_completed_after = previous_meeting_completed_after(meeting)
    action_groups = (
        meeting_entries_action_groups(
            meeting, entries, completed_after=previous_completed_after,
        )
        if meeting.status != Meeting.Status.FINALIZED
        else {}
    )

    status_color = GREEN if meeting.status == Meeting.Status.FINALIZED else AMBER
    status_label = meeting.get_status_display().upper()
    status_style = ParagraphStyle(
        "MMPStatusBadge", parent=styles["center"], textColor=status_color,
    )
    status_badge = Table(
        [[Paragraph(status_label, status_style)]],
        colWidths=[26 * mm],
        style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), GREEN_SOFT if meeting.status == Meeting.Status.FINALIZED else HexColor("#FFF0DC")),
            ("BOX", (0, 0), (-1, -1), .6, status_color),
            ("TEXTCOLOR", (0, 0), (-1, -1), status_color),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 1.7 * mm),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1.7 * mm),
        ]),
    )
    story = [
        Paragraph("MEETING MINUTES", styles["eyebrow"]),
        Table(
            [[_paragraph(meeting.title, styles["title"]), status_badge]],
            colWidths=[150 * mm, 26 * mm],
            style=TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]),
        ),
        Spacer(1, 2 * mm),
        Table(
            [[
                _meta_cell("Meeting Date", meeting.meeting_date.strftime("%d %b %Y"), styles),
                _meta_cell("Host", meeting.host, styles),
                _meta_cell("Minute Writer", meeting.minute_taker, styles),
                _meta_cell("Created By", meeting.created_by or "System", styles),
            ]],
            colWidths=[44 * mm] * 4,
            style=TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), PAPER_SOFT),
                ("BOX", (0, 0), (-1, -1), .6, LINE),
                ("INNERGRID", (0, 0), (-1, -1), .35, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3 * mm),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3 * mm),
                ("TOPPADDING", (0, 0), (-1, -1), 3 * mm),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3 * mm),
            ]),
        ),
        Spacer(1, 4 * mm),
        Table(
            [[
                _summary_card(len(entries), "Tasks", styles),
                _summary_card(reviewed, "Reviewed", styles),
                _summary_card(open_actions, "Open Action Items", styles),
                _summary_card(timezone.localtime().strftime("%H:%M"), "Generated", styles),
            ]],
            colWidths=[44 * mm] * 4,
            style=TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), GREEN_SOFT),
                ("BOX", (0, 0), (-1, -1), .6, HexColor("#C7DDD0")),
                ("INNERGRID", (0, 0), (-1, -1), .35, HexColor("#C7DDD0")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]),
        ),
        Spacer(1, 7 * mm),
    ]

    for index, entry in enumerate(entries, start=1):
        story.extend(_task_story(
            entry,
            index,
            meeting,
            previous_completed_after,
            styles,
            action_groups=action_groups.get(entry.pk),
        ))
        if index < len(entries):
            story.append(Spacer(1, 1 * mm))
    if not entries:
        story.append(Paragraph("No tasks were included in this Meeting Minute.", styles["body"]))

    decorator = _page_decorator(meeting)
    doc.build(story, onFirstPage=decorator, onLaterPages=decorator)
    return output.getvalue()


def meeting_pdf_filename(meeting):
    safe_title = slugify(meeting.title).replace("-", "_") or "meeting_minute"
    safe_title = re.sub(r"[^a-zA-Z0-9_]", "", safe_title)
    return f"{safe_title}_{meeting.meeting_date.isoformat()}.pdf"
