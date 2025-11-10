from flask_socketio import emit
from flask import request
from app.stt.stt_worker import STTWorker

class SocketManager:
    """Socket.io 이벤트 관리 및 STT 세션 관리"""

    def __init__(self, socketio):
        self.socketio = socketio
        self.sessions = {}  # sid -> STTWorker
    
    def start_stt(self, sid):
        """특정 세션의 STT 시작"""
        if sid in self.sessions:
            return
        
        worker = STTWorker(self.socketio, target_sid=sid)
        self.sessions[sid] = worker
        print(f"🎙️ STT 시작 (sid: {sid})")
        worker.start()
    
    def stop_stt(self, sid):
        """특정 세션의 STT 중지"""
        worker = self.sessions.get(sid)
        if worker:
            text = worker.stop()
            del self.sessions[sid]
            print(f"🛑 STT 중지 (sid: {sid})")
            emit("stt_text", {"text": "stt_final_stop", "final": True}, to=sid)
            return text
    
    def end_session(self, sid):
        """세션 완전 종료"""
        if sid in self.sessions:
            self.stop_stt(sid)
        print(f"✅ 면접 세션 종료 (sid: {sid})")
        emit("stt_end_confirm", {"message": "session ended"}, to=sid)
    
    def cleanup_disconnected(self, sid):
        """연결 끊긴 세션 정리"""
        if sid in self.sessions:
            self.stop_stt(sid)
            print(f"🔌 연결 끊김 처리 (sid: {sid})")

def register_socket_events(socketio):
    """Socket.io 이벤트 핸들러 등록"""
    manager = SocketManager(socketio)
    
    @socketio.on("connect")
    def handle_connect():
        print(f"🔗 새 연결: {request.sid}")
    
    @socketio.on("disconnect")
    def handle_disconnect():
        manager.cleanup_disconnected(request.sid)
    
    @socketio.on("stt_start")
    def handle_stt_start():
        manager.start_stt(request.sid)
    
    @socketio.on("stt_stop")
    def handle_stt_stop():
        manager.stop_stt(request.sid)
    
    @socketio.on("stt_end")
    def handle_stt_end():
        manager.end_session(request.sid)