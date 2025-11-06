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
            err = {"status":"failed","code":"bad_request","message":"axes_keys는 쉼표로 구분된 5개"}
            wants_html = (data.get("format") == "html")
            return (render_template("agents/report.html", report=err), 200) if wants_html else (jsonify(err), 400)

        # 2) 리포트 생성 호출 (여기!! 인자 이름 복수형)
        print("🚀 create_report_from_files 호출 중...")
        rpt = create_report_from_files(
            resume_path=data["resume_path"],
            jd_path=data["jd_path"],
            log_path=data.get("log_path", ""),
            axes_keys=axes_keys,
        )
        print("✅ 리포트 생성 완료!")
        
        # 에러 응답 체크
        if rpt.get("status") == "failed":
            print(f"❌ 리포트 생성 실패: [{rpt.get('code')}] {rpt.get('message')}")
            if rpt.get("details"):
                print(f"   상세: {rpt.get('details')}")
            wants_html = (data.get("format") == "html")
            return (render_template("agents/report.html", report=rpt), 200) if wants_html else (jsonify(rpt), 200)
        
        # 디버깅: talkSummary 구조 확인
        if rpt.get("talkSummary") and rpt["talkSummary"].get("items"):
            print(f"📋 talkSummary.items 개수: {len(rpt['talkSummary']['items'])}")
            for idx, item in enumerate(rpt["talkSummary"]["items"]):  # 전체 출력
                print(f"  [{idx}] 주제: {item.get('주제', 'N/A')}")
        else:
            print("⚠️ talkSummary.items가 비어있거나 없음")
        
        # 응답 모드: html 또는 json
        wants_html = (data.get("format") == "html")
        return (render_template("agents/report.html", report=rpt), 200) if wants_html else (jsonify(rpt), 200)

    except KeyError as e:
        print(f"❌ KeyError: {e}")
        err = {"status":"failed","code":"bad_request","message":f"필수 파라미터 누락: {e}","details":{}}
        wants_html = (data.get("format") == "html")
        return (render_template("agents/report.html", report=err), 200) if wants_html else (jsonify(err), 200)
    except ValidationError as e:
        print(f"❌ ValidationError: {e}")
        err = {"status":"failed","code":"validation_failed","message":"입력 검증 실패","details":e.errors()}
        wants_html = (data.get("format") == "html")
        return (render_template("agents/report.html", report=err), 200) if wants_html else (jsonify(err), 200)
    except Exception as e:
        print(f"❌ Exception: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        err = {"status":"failed","code":"failed","message":str(e)}
        wants_html = (data.get("format") == "html")
        return (render_template("agents/report.html", report=err), 200) if wants_html else (jsonify(err), 200)


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
