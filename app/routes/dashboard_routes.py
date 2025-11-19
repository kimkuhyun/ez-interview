from flask import Blueprint, render_template, Response
from pathlib import Path

dashboard_bp = Blueprint('dashboard', __name__)

@dashboard_bp.route("/admin")
def admin_dashboard():
    return render_template("base.html")

# 탭 HTML 제공 API
@dashboard_bp.route("/api/admin/tabs/<tab_name>")
def get_tab_content(tab_name):
    try:
        # templates/admin/tabs/ 경로에서 HTML 파일 읽기
        BASE_DIR = Path(__file__).parent.parent
        tab_file = BASE_DIR / "templates" / "admin" / "tabs" / f"{tab_name}.html"
        
        if not tab_file.exists():
            return Response("탭을 찾을 수 없습니다.", status=404)
        
        with open(tab_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        return Response(content.strip(), mimetype='text/html')
    except Exception as e:
        print(f"탭 로드 오류: {e}")
        return Response(f"오류: {str(e)}", status=500)
