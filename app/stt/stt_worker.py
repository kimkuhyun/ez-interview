import threading, time
import grpc, pyaudio, requests
from app.stt import vito_stt_client_pb2 as pb
from app.stt import vito_stt_client_pb2_grpc as pb_grpc


class STTWorker:
    def __init__(self, socketio, client_id, client_secret):
        self.socketio = socketio
        self.client_id = client_id
        self.client_secret = client_secret
        self.running = False
        self.thread = None
        self.buffer = []

    # -------- Access Token --------
    def _get_token(self):
        resp = requests.post(
            "https://openapi.vito.ai/v1/authenticate",
            data={"client_id": self.client_id, "client_secret": self.client_secret},
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    # -------- Core STT Loop --------
    def _run(self):
        token = self._get_token()
        creds = grpc.composite_channel_credentials(
            grpc.ssl_channel_credentials(),
            grpc.access_token_call_credentials(token)
        )
        stub = pb_grpc.OnlineDecoderStub(
            grpc.secure_channel("grpc-openapi.vito.ai:443", creds)
        )

        config = pb.DecoderConfig(
            sample_rate=16000,
            encoding=pb.DecoderConfig.AudioEncoding.LINEAR16,
            use_itn=True,           # ✅ ITN: "일이삼" → "123" 변환
            use_disfluency_filter=False,  # ✅ 더듬거림 필터 OFF (자연스러운 대화 유지)
            use_profanity_filter=False,   # ✅ 욕설 필터 OFF (면접에선 불필요)
            domain="GENERAL",       # ✅ GENERAL: 일반 대화 / CALL: 전화 통화
        )

        p = pyaudio.PyAudio()
        stream = p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=16000,
            input=True,
            frames_per_buffer=1600,
        )

        def req_iter():
            yield pb.DecoderRequest(streaming_config=config)
            while self.running:
                data = stream.read(1600, exception_on_overflow=False)
                yield pb.DecoderRequest(audio_content=data)

        try:
            for resp in stub.Decode(req_iter()):
                if not self.running:
                    break
                for result in resp.results:
                    text = result.alternatives[0].text
                    if text:
                        self.buffer.append(text)
                        self.socketio.emit(
                            "stt_text",
                            {"text": text, "final": result.is_final}
                        )
        except Exception as e:
            print(f"❌ STT 오류: {e}")
        finally:
            stream.stop_stream()
            stream.close()
            p.terminate()
            self.running = False
            # ⚠️ stt_final_stop은 stt_socket.py의 stop_stt()에서만 emit
            # 여기서는 emit하지 않음 (중복 방지)


    # -------- Start / Stop Control --------
    def start(self):
        if self.running:
            print("⚠️ 이미 STT 실행 중입니다.")
            return
        self.running = True
        self.buffer.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)
        return " ".join(self.buffer)
