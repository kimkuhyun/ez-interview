from flask import Blueprint, render_template, request, jsonify
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))
from agents.report_agent import (
    create_report_from_files, validate_and_save,
    get_report, apply_feedback
)
from pydantic import ValidationError

report_bp = Blueprint("report", __name__)
reports_bp = Blueprint("reports", __name__)

@report_bp.route("/panel/report")
def report_panel():
    return render_template("agents/report.html", report={})

@reports_bp.post("/generate")
def generate_from_txt():
    print("🔥 /reports/generate 호출됨!")
    data = request.form if request.form else request.get_json(silent=True) or {}
    print(f"📥 받은 데이터: {dict(data)}")

    try:
        # 1) axes_keys 문자열 → 리스트
        axes_keys_str = data.get("axes_keys", "")
        axes_keys = [k.strip() for k in axes_keys_str.split(",") if k.strip()]
        print(f"🔑 파싱된 axes_keys: {axes_keys}")
        if len(axes_keys) != 5:
            return jsonify({"error": "axes_keys는 쉼표로 구분된 5개"}), 400

        # 2) 리포트 생성 호출 (여기!! 인자 이름 복수형)
        print("🚀 create_report_from_files 호출 중...")
        rpt = create_report_from_files(
            resume_path=data["resume_path"],
            jd_path=data["jd_path"],
            log_path=data.get("log_path", ""),
            axes_keys=axes_keys,     # ← fix
        )
        print("✅ 리포트 생성 완료!")

        return render_template("agents/report.html", report=rpt)

    except KeyError as e:
        print(f"❌ KeyError: {e}")
        return jsonify({"error": "bad_request", "missing": str(e)}), 400
    except ValidationError as e:
        print(f"❌ ValidationError: {e}")
        return jsonify({"error": "validation_failed", "detail": e.errors()}), 422
    except Exception as e:
        print(f"❌ Exception: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": "failed", "detail": str(e)}), 500


@reports_bp.post("/validate-and-save")
def validate_then_save():
    data = request.get_json(silent=True) or {}
    try:
        rpt = validate_and_save(data)
        return jsonify(rpt), 201
    except ValidationError as e:
        return jsonify({"error":"validation_failed","detail":e.errors()}), 422

@reports_bp.get("/<rid>")
def read_report(rid: str):
    rpt = get_report(rid)
    if not rpt:
        return jsonify({"error":"not_found"}), 404
    return jsonify(rpt), 200

@reports_bp.post("/<rid>/feedback")
def patch_report(rid: str):
    patch = request.get_json(silent=True) or {}
    try:
        rpt = apply_feedback(rid, patch)
        if not rpt:
            return jsonify({"error":"not_found"}), 404
        return jsonify(rpt), 200
    except ValidationError as e:
        return jsonify({"error":"validation_failed","detail":e.errors()}), 422
