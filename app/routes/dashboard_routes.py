from flask import Blueprint, render_template, Response
from pathlib import Path

dashboard_bp = Blueprint('dashboard', __name__)

# 탭 HTML 제공 API
@dashboard_bp.route("/api/admin/tabs/<tab_name>")
def get_tab_content(tab_name):
    try:
        # render_template_string을 사용하여 템플릿 렌더링
        from flask import render_template_string
        
        BASE_DIR = Path(__file__).parent.parent
        tab_file = BASE_DIR / "templates" / "admin" / "tabs" / f"{tab_name}.html"
        
        if not tab_file.exists():
            return Response("탭을 찾을 수 없습니다.", status=404)
        
        with open(tab_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Flask 템플릿 엔진으로 렌더링하여 {{ url_for() }} 처리
        rendered_content = render_template_string(content)
        
        return Response(rendered_content.strip(), mimetype='text/html')
    except Exception as e:
        print(f"탭 로드 오류: {e}")
        return Response(f"오류: {str(e)}", status=500)

@dashboard_bp.route("/panel/positions")
def get_positions_panel():
    """포지션 목록 패널"""
    return render_template("admin/tabs/positions.html")

@dashboard_bp.route("/panel/keyword-match")
def get_keyword_match_panel():
    """키워드 매칭 패널"""
    return render_template("admin/tabs/keyword_match.html")

