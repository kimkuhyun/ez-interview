console.log("✅ Stream page loaded");

// ========================================
// 전역 상태 관리
// ========================================
const state = {
  // Socket.IO 관련
  socket: null, // Socket.IO 연결 객체
  handlersRegistered: false, // 소켓 핸들러 중복 등록 방지 플래그

  // 세션 타이머
  sessionStart: null, // 면접 세션 시작 시각 (밀리초, Date.now())

  // STT 관련
  sttCurrentQuestion: null, // STT 세그먼트가 시작된 질문 ID (탭 전환 대비 고정)
  sttActive: false, // STT 활성화 여부
  finalText: "", // STT 최종 확정 텍스트 누적
  partialText: "", // STT 임시 인식 텍스트
  currentSTTDiv: null, // 현재 STT 메시지를 표시하는 DOM 요소

  // UI 상태
  currentTab: "", // 현재 활성화된 질문 탭 ID (q1, q2, ...)
  aiAutoGenerate: true, // AI 자동 질문 생성 ON/OFF

  // 질문 활성화 추적
  activationTimes: {}, // 각 질문 탭이 활성화된 시각 {q1: timestamp, q2: timestamp, ...}
  activationSent: {}, // 각 질문의 활성화 시각을 서버에 전송했는지 여부
};

// ========================================
// 유틸리티 함수
// ========================================
function getElapsedSeconds() {
  if (state.sessionStart === null) return 0;
  return Math.floor((Date.now() - state.sessionStart) / 1000);
}

function ensureSessionStart() {
  if (state.sessionStart === null) {
    state.sessionStart = Date.now();
    // 저장된 activation times를 offset으로 변환하여 전송
    Object.entries(state.activationTimes).forEach(([qid, ts]) => {
      if (!state.activationSent[qid] && ts) {
        const offset = Math.max(
          0,
          Math.floor((ts - state.sessionStart) / 1000)
        );
        console.log("[DEBUG] flush activation ->", qid, "offset", offset);
        sendQuestionActivation(qid, offset);
      }
    });
  }
}

function sendQuestionActivation(questionId, offset) {
  fetch("/question_activated", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question_id: questionId, offset }),
  })
    .then(() => {
      state.activationSent[questionId] = true;
    })
    .catch((err) => console.error("question_activated error", err));
}

// ██████████████████████████████████████████████████████████████████████
// ██                                                                  ██
// ██    SOCKET.IO 관련 코드 시작                                        ██
// ██    (STT 실시간 통신, WebSocket 연결, 이벤트 핸들러)                  ██
// ██                                                                  ██
// ██████████████████████████████████████████████████████████████████████
function ensureSocketConnected(callback) {
  if (typeof io === "undefined") {
    console.error("Socket.IO client not loaded");
    return;
  }

  if (!state.socket) {
    state.socket = io("http://127.0.0.1:5000");
  }

  if (callback) callback();
}

// ========================================
// Socket.IO 이벤트 핸들러 등록 (STT 전용)
// ========================================
function registerSocketHandlers() {
  if (!state.socket || state.handlersRegistered) return;
  state.handlersRegistered = true;

  // 기존 핸들러 제거 (중복 방지)
  if (state.socket.off) {
    state.socket.off("stt_text");
  }

  // STT 텍스트 수신 핸들러
  state.socket.on("stt_text", (data) => {
    const targetQ = state.sttCurrentQuestion || state.currentTab;
    const box = document.getElementById(targetQ);
    if (!box || !data.text) return;

    // STT 종료 신호 처리
    if (data.text === "stt_final_stop") {
      handleSTTFinalStop(targetQ, box);
      return;
    }

    // STT 메시지 div 생성
    if (!state.currentSTTDiv) {
      createSTTMessageDiv(box);
    }

    const newText = data.text.trim();
    if (data.final) {
      handleSTTFinal(newText, targetQ);
    } else {
      handleSTTPartial(newText);
    }

    box.scrollTop = box.scrollHeight;
  });
}

// STT 종료 신호 처리
function handleSTTFinalStop(targetQ, box) {
  const targetBox = document.getElementById(targetQ) || box;

  // STT Div 참조 저장 (나중에 업데이트하기 위해)
  const sttDiv = state.currentSTTDiv;

  if (sttDiv) {
    sttDiv.classList.add("final");
  }

  // ✅ STT 종료 시 LLM 후처리 후 저장
  if (state.finalText.trim().length > 0) {
    const originalText = state.finalText.trim();

    console.log("[DEBUG] STT 후처리 시작:", originalText);

    // LLM 교정 요청
    fetch("/correct_stt", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: originalText }),
    })
      .then((res) => res.json())
      .then((data) => {
        const correctedText = data.corrected || originalText;

        console.log("[DEBUG] 교정 결과:", {
          original: originalText,
          corrected: correctedText,
          changed: data.changed,
        });

        // 교정된 텍스트로 UI 업데이트
        if (sttDiv && data.changed) {
          sttDiv.textContent = correctedText;
          sttDiv.classList.add("corrected");
          console.log("✅ UI 업데이트 완료:", correctedText);
        }

        // 교정된 텍스트 저장
        const offset = getElapsedSeconds();
        return fetch("/send", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            text: correctedText,
            question_id: targetQ,
            role: "면접자",
            offset,
          }),
        });
      })
      .then((res) => res.json())
      .then((r) => {
        console.log("✅ 면접자 발화 저장 완료", r);

        // AI 자동 생성이 OFF일 때 인라인 버튼 표시
        if (!state.aiAutoGenerate) {
          const correctedText = sttDiv?.textContent || state.finalText.trim();
          showAIButton(targetBox, correctedText);
        }
      })
      .catch((err) => {
        console.error("❌ STT 후처리 오류:", err);
        // 오류 시 원본 텍스트로 저장
        fetch("/send", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            text: originalText,
            question_id: targetQ,
            role: "면접자",
            offset: getElapsedSeconds(),
          }),
        });
      });
  }

  // ⚠️ STT 상태 초기화 (다음 발화를 위해)
  state.currentSTTDiv = null;
  state.finalText = "";
  state.partialText = "";
  state.sttCurrentQuestion = null;

  console.log("[DEBUG] STT 상태 초기화 완료");
}

// STT 메시지 div 생성 및 질문 ID 고정
function createSTTMessageDiv(box) {
  state.currentSTTDiv = document.createElement("div");
  state.currentSTTDiv.className = "message stt";
  box.appendChild(state.currentSTTDiv);

  // STT 세그먼트 시작 시 질문 ID 고정
  if (!state.sttCurrentQuestion) {
    state.sttCurrentQuestion = state.currentTab;
    console.log(
      "[DEBUG] STT segment started, frozen question:",
      state.sttCurrentQuestion
    );
  }
}

// STT 최종 확정 텍스트 처리
function handleSTTFinal(newText, targetQ) {
  // 최종 텍스트 누적 (아직 저장하지 않음)
  state.finalText += (state.finalText ? " " : "") + newText;
  state.currentSTTDiv.textContent = state.finalText;

  console.log(
    "[DEBUG] STT final (누적) -> targetQ:",
    targetQ,
    "finalText:",
    state.finalText,
    "sttCurrentQuestion:",
    state.sttCurrentQuestion
  );

  // ⚠️ 여기서는 저장하지 않음 (stt_final_stop에서만 저장)
}

// STT 부분 텍스트 처리
function handleSTTPartial(newText) {
  state.partialText = newText;
  state.currentSTTDiv.textContent = state.finalText + " " + state.partialText;
}

// ██████████████████████████████████████████████████████████████████████
// ██                                                                  ██
// ██    SOCKET.IO 관련 코드 끝                                        ██
// ██                                                                  ██
// ██████████████████████████████████████████████████████████████████████

// ██████████████████████████████████████████████████████████████████████
// ██                                                                  ██
// ██    UI 관련 코드 시작                                             ██
// ██    (탭, 메시지, AI 질문 생성, 사용자 입력)                       ██
// ██                                                                  ██
// ██████████████████████████████████████████████████████████████████████

// ========================================
// 탭 및 메시지 UI 함수
// ========================================
function loadtab() {
  const tabBar = document.getElementById("tab-bar");
  const chatArea = document.getElementById("chat-area");

  Object.entries(res).forEach(([id, text], i) => {
    const tab = document.createElement("div");
    tab.className = "tab";
    tab.textContent = id.toUpperCase();
    tab.onclick = () => switchTab(id, tabBar.children[i]);
    tabBar.appendChild(tab);

    const box = document.createElement("div");
    box.id = id;
    box.className = "chat-box";
    box.style.display = "none";
    chatArea.appendChild(box);

    appendMessage(id, "assistant", text);
    if (i === 0) switchTab(id, tab);
  });
}

function switchTab(tabId, el) {
  document
    .querySelectorAll(".chat-box")
    .forEach((div) => (div.style.display = "none"));
  document.getElementById(tabId).style.display = "flex";
  document
    .querySelectorAll(".tab")
    .forEach((t) => t.classList.remove("active"));
  el.classList.add("active");
  state.currentTab = tabId;

  // 탭 활성화 시각 기록
  state.activationTimes[tabId] = Date.now();

  // 세션이 시작된 상태라면 즉시 전송
  if (state.sessionStart !== null && !state.activationSent[tabId]) {
    const offset = getElapsedSeconds();
    console.log("[DEBUG] immediate activation ->", tabId, "offset", offset);
    sendQuestionActivation(tabId, offset);
  }
}

function appendMessage(tab, role, text) {
  const box = document.getElementById(tab);
  const msg = document.createElement("div");
  msg.className = `message ${role}`;
  msg.textContent = text;
  box.appendChild(msg);
  box.scrollTop = box.scrollHeight;

  // 면접자가 답변하면 AI 버튼 표시
  if (role === "user") {
    showAIButton(box, text);
  }
}

function showAIButton(box, lastAnswer) {
  console.log("수동모드에서 ai 버튼 생성");

  // 중복 방지
  const oldBtn = box.querySelector(".inline-ai-btn");
  if (oldBtn) oldBtn.remove();

  const btn = document.createElement("button");
  btn.className = "inline-ai-btn";
  btn.innerHTML = "AI 질문 생성하기";
  btn.onclick = () => {
    btn.remove();
    sendToAI(lastAnswer.trim());
  };

  btn.style.alignSelf = "flex-end";
  box.appendChild(btn);
  box.scrollTop = box.scrollHeight;
}

// ========================================
// STT 제어 함수
// ========================================
function toggleSTT() {
  const indicator = document.getElementById("stt-indicator");
  const btn = document.getElementById("stt-btn");
  state.sttActive = !state.sttActive;

  if (state.sttActive) {
    ensureSessionStart();
    if (state.socket) state.socket.emit("stt_start");
    indicator.classList.add("active");
    btn.textContent = "STT 중지";
    state.finalText = "";
    state.partialText = "";
  } else {
    if (state.socket) state.socket.emit("stt_stop");
    indicator.classList.remove("active");
    btn.textContent = "STT 시작";

    // AI 자동 질문 생성
    if (state.aiAutoGenerate && state.finalText.trim().length > 0) {
      sendToAI(state.finalText.trim());
    } else {
      console.log("🤖 AI 자동 질문 생성 비활성화 상태입니다.");
    }
  }
}

function endInterview() {
  // STT 종료 신호 전송
  if (state.socket) state.socket.emit("stt_end");

  // 면접 로그를 백엔드로 전송하여 콘솔 출력
  fetch("/end_interview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  })
    .then((res) => res.json())
    .then((data) => {
      console.log("✅ 면접 종료 완료:", data);
    })
    .catch((err) => {
      console.error("❌ 면접 종료 오류:", err);
    });

  // UI 초기화
  const indicator = document.getElementById("stt-indicator");
  const btn = document.getElementById("stt-btn");
  indicator.classList.remove("active");
  btn.textContent = "STT 시작";
  state.sttActive = false;
}

// ========================================
// AI 질문 생성 함수
// ========================================
function sendToAI(fullText, isRegen = false) {
  const box = document.getElementById(state.currentTab);
  const loader = showThinking(box);

  fetch("/ai_followup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text: fullText,
      question_id: state.currentTab,
      regen: isRegen,
    }),
  })
    .then((res) => res.json())
    .then((data) => {
      loader.remove();

      const oldWrapper = box.querySelector(".followup-container");
      if (oldWrapper) oldWrapper.remove();

      if (data.questions && Array.isArray(data.questions)) {
        const wrapper = document.createElement("div");
        wrapper.className = "followup-container";

        data.questions.forEach((q) => {
          const btn = document.createElement("button");
          btn.className = "followup-btn";
          btn.textContent = q;
          btn.onclick = () => selectFollowup(q, box, wrapper);
          wrapper.appendChild(btn);
        });

        const regen = document.createElement("button");
        regen.className = "followup-regen";
        regen.innerHTML = "↻";
        regen.title = "질문 재생성";
        regen.onclick = () => {
          wrapper.remove();
          sendToAI(fullText, true);
        };
        wrapper.appendChild(regen);

        box.appendChild(wrapper);
        box.scrollTop = box.scrollHeight;
      }
    })
    .catch((err) => {
      console.error("❌ AI 요청 오류:", err);
      loader.remove();
    });
}

function selectFollowup(question, box, wrapper) {
  wrapper.remove();

  const msg = document.createElement("div");
  msg.className = "message assistant";
  msg.textContent = question;
  box.appendChild(msg);
  box.scrollTop = box.scrollHeight;

  fetch("/send", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text: question,
      question_id: state.currentTab,
      role: "면접관",
      offset: getElapsedSeconds(),
    }),
  })
    .then((res) => res.json())
    .then((data) => console.log("✅ 선택된 질문 로그 저장 완료:", data))
    .catch((err) => console.error("❌ 선택 질문 로그 오류:", err));
}

function showThinking(box) {
  const thinking = document.createElement("div");
  thinking.className = "ai-thinking";
  thinking.innerHTML = `
    <span>AI가 후속 질문을 생성하고 있습니다</span>
    <span class="dot1">.</span>
    <span class="dot2">.</span>
    <span class="dot3">.</span>
  `;
  box.appendChild(thinking);
  box.scrollTop = box.scrollHeight;
  return thinking;
}

// ========================================
// 사용자 입력 전송
// ========================================
function sendMessage() {
  const input = document.getElementById("user-input");
  const text = input.value.trim();
  if (!text) return;

  const box = document.getElementById(state.currentTab);

  // AI 버튼 및 후속 질문 리스트 제거
  const aiBtn = box.querySelector(".inline-ai-btn");
  if (aiBtn) aiBtn.remove();

  const followup = box.querySelector(".followup-container");
  if (followup) followup.remove();

  appendMessage(state.currentTab, "assistant", text);
  input.value = "";

  fetch("/send", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text,
      question_id: state.currentTab,
      role: "면접관",
      offset: getElapsedSeconds(),
    }),
  })
    .then((res) => res.json())
    .then((data) => console.log("✅ 면접관 질문 로그 저장:", data))
    .catch((err) => console.error("❌ 로그 전송 오류:", err));
}

function toggleAI() {
  state.aiAutoGenerate = !state.aiAutoGenerate;
  const btn = document.getElementById("ai-toggle-btn");

  if (state.aiAutoGenerate) {
    btn.textContent = "AI 자동 질문: ON";
    btn.classList.remove("off");
    btn.classList.add("on");
  } else {
    btn.textContent = "AI 자동 질문: OFF";
    btn.classList.remove("on");
    btn.classList.add("off");
  }
}

// ========================================
// 초기화
// ========================================
loadtab();
ensureSocketConnected(registerSocketHandlers);

// 전역 함수로 노출 (HTML에서 호출)
window.toggleSTT = toggleSTT;
window.endInterview = endInterview;
window.sendMessage = sendMessage;
window.toggleAI = toggleAI;
