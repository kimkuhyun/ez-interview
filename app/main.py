from __future__ import annotations
from pathlib import Path
from dataclasses import dataclass, field
import pathlib
from typing import List, Any, Dict, Optional

from dotenv import load_dotenv
from flask import Flask, render_template

load_dotenv(Path(__file__).resolve().parent / ".env")


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

@app.route("/")
def load_interview_home():
    return render_template("interview_home.html")

# 라우트


# 엔트리
if __name__ == "__main__":
    app.run(debug=True)