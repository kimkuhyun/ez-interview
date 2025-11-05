from flask import Blueprint, render_template, request, jsonify

report_bp = Blueprint("report", __name__)

@report_bp.route("/panel/report")
def report_panel():
    return render_template("agents/report.html")
