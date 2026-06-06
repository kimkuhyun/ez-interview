"""
공개 샘플 이력서를 PDF 로 빌드.

데이터 출처: JSON Resume — 공식 schema repo 의 `sample.resume.json`
             https://github.com/jsonresume/resume-schema (MIT License)
주인공 "Richard Hendriks" 는 HBO Silicon Valley 시즌 1 의 패러디 가상 인물.

사용법:
  pip install reportlab requests
  python samples/build_sample_resume.py
  # → samples/Richard Hendriks_이력서.pdf 생성

EZ-Interview 업로드 API 규약: 파일명을 `{이름}_이력서.pdf` 로 맞춰야 한다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.request import urlopen

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

SAMPLE_URL = (
    "https://raw.githubusercontent.com/jsonresume/resume-schema/master/"
    "sample.resume.json"
)
OUT = Path(__file__).parent / "Richard Hendriks_이력서.pdf"


def fetch_sample() -> dict:
    with urlopen(SAMPLE_URL, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def make_styles():
    base = getSampleStyleSheet()
    return {
        "H1": ParagraphStyle("H1", parent=base["Heading1"], fontName="Helvetica-Bold",
                             fontSize=22, leading=26, spaceAfter=4),
        "H2": ParagraphStyle("H2", parent=base["Heading2"], fontName="Helvetica-Bold",
                             fontSize=13, leading=17, spaceBefore=10, spaceAfter=4,
                             textColor=colors.HexColor("#1a365d")),
        "P":  ParagraphStyle("P",  parent=base["BodyText"], fontName="Helvetica",
                             fontSize=10.5, leading=15),
        "Sm": ParagraphStyle("Sm", parent=base["BodyText"], fontName="Helvetica",
                             fontSize=9.5, leading=13,
                             textColor=colors.HexColor("#555")),
    }


def build(data: dict) -> list:
    s = make_styles()
    out = []

    b = data["basics"]
    out += [
        Paragraph(b["name"], s["H1"]),
        Paragraph(b.get("label", ""), s["Sm"]),
        Paragraph(
            f"📧 {b.get('email','')} &nbsp;|&nbsp; 📱 {b.get('phone','')} &nbsp;|&nbsp; "
            f"🌐 {b.get('url','')}",
            s["Sm"],
        ),
        Spacer(1, 4),
        Paragraph("Summary", s["H2"]),
        Paragraph(b.get("summary", ""), s["P"]),
    ]

    if data.get("work"):
        out += [Paragraph("Experience", s["H2"])]
        rows = [["Period", "Role / Highlights"]]
        for w in data["work"]:
            period = f"{w.get('startDate','')} ~ {w.get('endDate','')}"
            highlights = "<br/>".join(f"• {h}" for h in w.get("highlights", []))
            body = (
                f"<b>{w.get('position','')}</b> · {w.get('name','')} "
                f"({w.get('location','')})<br/>{w.get('summary','')}<br/>{highlights}"
            )
            rows.append([period, Paragraph(body, s["P"])])
        t = Table(rows, colWidths=[40 * mm, 125 * mm])
        t.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
            ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.lightgrey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        out += [t]

    if data.get("education"):
        out += [Paragraph("Education", s["H2"])]
        for e in data["education"]:
            out += [Paragraph(
                f"<b>{e.get('institution','')}</b> — {e.get('studyType','')} "
                f"in {e.get('area','')} "
                f"({e.get('startDate','')} ~ {e.get('endDate','')}, "
                f"GPA {e.get('score','')})",
                s["P"],
            )]

    if data.get("skills"):
        out += [Paragraph("Skills", s["H2"])]
        lines = []
        for sk in data["skills"]:
            kw = ", ".join(sk.get("keywords", []))
            lines.append(f"<b>{sk.get('name','')}</b> ({sk.get('level','')}): {kw}")
        out += [Paragraph("<br/>".join(lines), s["P"])]

    if data.get("projects"):
        out += [Paragraph("Projects", s["H2"])]
        lines = []
        for p in data["projects"]:
            kw = ", ".join(p.get("keywords", []))
            lines.append(
                f"<b>{p.get('name','')}</b> — {p.get('description','')} "
                f"<br/>&nbsp;&nbsp;tags: {kw}"
            )
        out += [Paragraph("<br/><br/>".join(lines), s["P"])]

    if data.get("languages"):
        out += [Paragraph("Languages", s["H2"])]
        out += [Paragraph(
            ", ".join(
                f"{lg.get('language')} ({lg.get('fluency','')})"
                for lg in data["languages"]
            ),
            s["P"],
        )]

    out += [
        Spacer(1, 10),
        Paragraph(
            "데이터 출처: JSON Resume 공식 schema sample.resume.json "
            "(MIT License, jsonresume/resume-schema). "
            "Richard Hendriks 는 가상 인물.",
            s["Sm"],
        ),
    ]
    return out


def main() -> None:
    try:
        data = fetch_sample()
    except Exception as e:
        print(f"[fetch failed] {e}", file=sys.stderr)
        sys.exit(1)

    doc = SimpleDocTemplate(
        str(OUT), pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title="JSON Resume Sample — Richard Hendriks",
    )
    doc.build(build(data))
    print(f"OK -> {OUT.name} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
