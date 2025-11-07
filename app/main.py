from __future__ import annotations
from pathlib import Path
from dataclasses import dataclass, field
import pathlib
from typing import List, Any, Dict, Optional
from flask_socketio import SocketIO

from dotenv import load_dotenv
from flask import Flask, render_template

from app.routes.question_routes import question_bp
from app.routes.stream_routes import stream_bp
from app.routes.report_routes import report_bp,reports_bp
from app.routes.stt_socket import register_stt_events

import os

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
print("🔑 Loaded API KEY:", os.getenv("OPENAI_API_KEY"))

#state - 면접 진행 저장 
@dataclass
class InterviewState:
    pass
_state = InterviewState()

# app
BASE_DIR = Path(__file__).parent
app = Flask(
    __name__,
    template_folder = str(BASE_DIR / "templates")
)
app.config['TEMPLATES_AUTO_RELOAD'] = True

app.register_blueprint(question_bp)
app.register_blueprint(stream_bp)
app.register_blueprint(report_bp)
app.register_blueprint(reports_bp, url_prefix="/reports")

socketio = SocketIO(app, cors_allowed_origins="*")

register_stt_events(socketio)

@app.route("/")
def load_interview_home():
    return render_template("interview.html")

# 엔트리
if __name__ == "__main__":
    # app.run(debug=True)
    socketio.run(app, debug=True) # socket 사용 이유로 변경 (기존 코드에 문제 X)