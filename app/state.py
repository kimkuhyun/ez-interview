from datetime import datetime

# Shared in-memory state for interview session
# This is intentionally simple (in-memory). For production, persist to DB.
state = {
    "interview_start_ts": None,
    "interview_end_ts": None,
    "interview_logs": []  # list of {question_id, followups: [{role, content, ts}], stt_end_times: []}
}


def now_iso():
    return datetime.utcnow().isoformat() + "Z"
