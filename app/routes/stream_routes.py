from flask import Blueprint, render_template, request, jsonify

stream_bp = Blueprint("stream", __name__)

@stream_bp.route("/panel/stream")
def stream_panel():
    return render_template("agents/stream.html")
