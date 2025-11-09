from flask_socketio import emit
from app.stt.stt_worker import STTWorker
from app.config.config import Config

class STTSessionManager:
    """🎙️ 면접 STT 세션 관리 (시간 로직 제거 버전)"""
    def __init__(self, socketio):
        self.socketio = socketio
        self.worker = None
        self.client_id = Config.RTZR_CLIENT_ID
        self.client_secret = Config.RTZR_CLIENT_SECRET

    def start_stt(self):
        """STT 시작"""
        if not self.worker:
            self.worker = STTWorker(self.socketio, self.client_id, self.client_secret)
        print("🎙️ STT 시작 요청 수신")
        self.worker.start()

    def stop_stt(self):
        """STT 중지"""
        if self.worker:
            self.worker.stop()
        print("🛑 STT 중지 요청 수신")
        emit("stt_text", {"text": "stt_final_stop", "final": True})

    def end_session(self):
        """면접 완전 종료"""
        print("✅ 면접 세션 종료됨")
        self.worker = None
        emit("stt_end_confirm", {"message": "session ended"}, broadcast=True)


def register_stt_events(socketio):
    """SocketIO 이벤트 등록"""
    manager = STTSessionManager(socketio)

    @socketio.on("stt_start")
    def handle_stt_start():
        manager.start_stt()

    @socketio.on("stt_stop")
    def handle_stt_stop():
        manager.stop_stt()

    @socketio.on("stt_end")
    def handle_stt_end():
        manager.end_session()
