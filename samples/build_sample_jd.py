"""
샘플 채용 공고(JD) PDF 생성기.

가상의 회사 "Acme Tech" 백엔드 엔지니어 채용. 영어 + 일반적인 표현으로
실제 어느 회사에도 종속되지 않은 합성 JD. 이력서 매칭/면접 흐름 테스트용.
"""
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

OUT = Path(__file__).parent / "Sample_BackendEngineer_JD.pdf"

base = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=base["Heading1"], fontName="Helvetica-Bold",
                    fontSize=20, leading=24, spaceAfter=4)
H2 = ParagraphStyle("H2", parent=base["Heading2"], fontName="Helvetica-Bold",
                    fontSize=13, leading=17, spaceBefore=10, spaceAfter=4,
                    textColor=colors.HexColor("#1a365d"))
P = ParagraphStyle("P", parent=base["BodyText"], fontName="Helvetica",
                   fontSize=10.5, leading=15)

story = [
    Paragraph("Backend Engineer (Mid-level) — Acme Tech", H1),
    Paragraph("Acme Tech · Platform Team · Remote-friendly · Full-time", P),
    Spacer(1, 4),

    Paragraph("About Acme Tech", H2),
    Paragraph(
        "Acme Tech is a fictional company used for EZ-Interview sample data. "
        "We are migrating our monolithic e-commerce platform into a set of "
        "loosely-coupled domain services and are hiring a backend engineer to help "
        "stabilize order/payment APIs and grow the platform.",
        P),

    Paragraph("Responsibilities", H2),
    Paragraph(
        "• Design and implement REST APIs for the order/shipment domain using "
        "Python (FastAPI) or Node.js<br/>"
        "• Tune PostgreSQL queries — read execution plans, add indexes, "
        "and remove N+1 anti-patterns<br/>"
        "• Move side-effects (emails, settlement) into Celery / RabbitMQ workers<br/>"
        "• Write unit & integration tests (pytest), actively participate in code review<br/>"
        "• Investigate production incidents using logs, metrics, and traces",
        P),

    Paragraph("Requirements", H2),
    Paragraph(
        "• 2+ years building backend services in Python or another modern language<br/>"
        "• Hands-on REST API experience with FastAPI / Flask / Express<br/>"
        "• PostgreSQL or MySQL schema design experience<br/>"
        "• Comfortable with Git workflows, code review, and Docker<br/>"
        "• Good written English for async collaboration",
        P),

    Paragraph("Nice to Have", H2),
    Paragraph(
        "• Demonstrated performance optimization (e.g., N+1 fixes, index tuning)<br/>"
        "• Experience with Celery, RabbitMQ, Kafka or similar queues<br/>"
        "• AWS EC2/RDS/S3 basic operations<br/>"
        "• CI pipelines on GitHub Actions / GitLab CI<br/>"
        "• Side projects with RAG, vector DBs, or LLM tooling",
        P),

    Paragraph("Tech Stack", H2),
    Paragraph(
        "Python · FastAPI · SQLAlchemy · Pydantic · PostgreSQL · Redis · Celery · "
        "Docker Compose · AWS (EC2/RDS) · GitHub Actions · pytest",
        P),

    Paragraph("Benefits", H2),
    Paragraph(
        "• Flexible hours (10–11 AM start, 8h working day)<br/>"
        "• $10/day lunch stipend, quarterly $300 book budget<br/>"
        "• Generous PTO + sabbatical every 3 years",
        P),

    Spacer(1, 12),
    Paragraph(
        "This JD is synthetic sample data created for the EZ-Interview project. "
        "Any resemblance to a real company is coincidental.",
        ParagraphStyle("Sm", parent=base["BodyText"], fontName="Helvetica",
                       fontSize=9.5, leading=13,
                       textColor=colors.HexColor("#555")),
    ),
]

doc = SimpleDocTemplate(
    str(OUT), pagesize=A4,
    leftMargin=20*mm, rightMargin=20*mm,
    topMargin=18*mm, bottomMargin=18*mm,
    title="Sample JD — Backend Engineer",
)
doc.build(story)
print(f"OK -> {OUT.name} ({OUT.stat().st_size//1024} KB)")
