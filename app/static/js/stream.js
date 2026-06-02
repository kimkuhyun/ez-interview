console.log("✅ Stream page loaded");

// ========================================
// 전역 상태 관리
// ========================================
const state = {
  // Socket.IO 관련
  socket: null, // Socket.IO 연결 객체
  socketReady: false, // Socket.IO 연결 완료 여부 (즉시 확인용)
  handlersRegistered: false, // 소켓 핸들러 중복 등록 방지 플래그

  // STT 관련
  sttCurrentQuestion: null, // STT 세그먼트가 시작된 질문 ID (탭 전환 대비 고정)
  sttActive: false, // STT 활성화 여부
  finalText: "", // STT 최종 확정 텍스트 누적
  partialText: "", // STT 임시 인식 텍스트
  currentSTTDiv: null, // 현재 STT 메시지를 표시하는 DOM 요소

  // UI 상태
  currentTab: "", // 현재 활성화된 질문 탭 ID (q1, q2, ...)
  aiAutoGenerate: true, // AI 자동 질문 생성 ON/OFF
  
  // 재생성 제어
  regenCount: 0, // 현재 답변에 대한 재생성 횟수
  maxRegenCount: 2, // 최대 재생성 횟수
};

// ========================================
// 유틸리티 함수
// ========================================
function sendQuestionActivation(questionId) {
  fetch("/question_activated", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question_id: questionId }),
  })
    .then((res) => res.json())
    .then((data) => {
      console.log(`[DEBUG] 질문 활성화 기록: ${questionId}, offset: ${data.offset_sec}초`);
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
    console.log("🔌 [STT 최적화] 웹소켓 연결 시작...");
    state.socket = io();
    
    // 🔒 연결 완료 전까지 STT 버튼 비활성화
    const sttBtn = document.getElementById("stt-btn");
    if (sttBtn) {
      sttBtn.disabled = true;
      sttBtn.textContent = "연결 중...";
    }
    
    // 연결 완료 시 플래그 설정
    state.socket.on("connect", () => {
      state.socketReady = true;
      console.log("✅ [STT 최적화] 웹소켓 연결 완료 (즉시 사용 가능)");
      
      // ✅ STT 버튼 활성화
      if (sttBtn) {
        sttBtn.disabled = false;
        sttBtn.textContent = "STT 시작";
      }
    });
    
    // 연결 끊김 시 플래그 해제
    state.socket.on("disconnect", () => {
      state.socketReady = false;
      console.warn("⚠️ [STT 최적화] 웹소켓 연결 끊김");
    });
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

    // ✅ 스크롤 자동 이동
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

        // 교정된 텍스트 저장 (서버에서 자동으로 offset 계산)
        return fetch("/send", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            text: correctedText,
            question_id: targetQ,
            role: "면접자",
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
  
  // ✅ 스크롤 자동 이동
  box.scrollTop = box.scrollHeight;

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
  
  // ✅ 스크롤 자동 이동
  const targetQ = state.sttCurrentQuestion || state.currentTab;
  const box = document.getElementById(targetQ);
  if (box) box.scrollTop = box.scrollHeight;
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

  // 질문 ID를 숫자순으로 정렬 (q1, q2, ..., q10, q11)
  const sortedEntries = Object.entries(res).sort((a, b) => {
    const numA = parseInt(a[0].replace('q', ''));
    const numB = parseInt(b[0].replace('q', ''));
    return numA - numB;
  });

  sortedEntries.forEach(([id, text], i) => {
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

  // 탭 활성화 시 서버에 기록 (서버에서 자동으로 offset 계산)
  sendQuestionActivation(tabId);
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
    // ✅ 웹소켓 연결 확인 (연결 안 됐으면 대기)
    if (!state.socketReady) {
      console.warn("⏳ [STT 최적화] 웹소켓 연결 대기 중... (재시도)");
      btn.textContent = "연결 중...";
      btn.disabled = true;
      
      // 연결 완료 대기 후 재시도 (최대 3초)
      const waitStart = Date.now();
      const checkInterval = setInterval(() => {
        if (state.socketReady) {
          clearInterval(checkInterval);
          btn.disabled = false;
          btn.textContent = "STT 시작";
          console.log("✅ [STT 최적화] 웹소켓 연결 완료, STT 재시작");
          toggleSTT(); // 다시 시도
        } else if (Date.now() - waitStart > 3000) {
          clearInterval(checkInterval);
          btn.disabled = false;
          btn.textContent = "STT 시작";
          state.sttActive = false;
          alert("웹소켓 연결 실패. 페이지를 새로고침하세요.");
        }
      }, 100);
      return;
    }

    // 🎬 최초 STT 시작 시 세션 시작
    fetch("/session_start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    })
      .then((res) => res.json())
      .then((data) => {
        console.log("🎬 세션 시작:", data);
      })
      .catch((err) => console.error("❌세션 시작 오류:", err));

    console.log("⚡ [STT 최적화] 웹소켓 즉시 전송 (연결 완료 상태)");
    state.socket.emit("stt_start");
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
  // 확인 대화상자
  if (!confirm("면접을 종료하시겠습니까?\nDB에 저장 후 리포트 페이지로 이동합니다.")) {
    return;
  }

  // STT 종료 신호 전송
  if (state.socket) state.socket.emit("stt_end");

  // 면접 종료 알림
  console.log("🎬 면접 종료 중...");

  // 부모 창(interview_session.html)에서 session_id 가져오기
  const sessionId = window.parent?.INTERVIEW_SESSION_ID || window.INTERVIEW_SESSION_ID;
  
  console.log("📍 Session ID:", sessionId);

  // 면접 로그를 DB에 저장하고 리포트 페이지로 이동
  fetch("/end_interview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId })
  })
    .then((res) => res.json())
    .then((data) => {
      console.log("✅ 면접 종료 완료:", data);

      // 오른쪽 패널만 리포트로 변경 (알림 없이 바로 이동)
      if (window.parent && window.parent.loadPanel) {
        // interview.html의 loadPanel 함수 호출
        window.parent.loadPanel('report');
      } else {
        console.warn("⚠️ loadPanel 함수를 찾을 수 없어 fallback 사용");
        // fallback: 전체 페이지 리다이렉트
        window.location.href = "/panel/report";
      }
    })
    .catch((err) => {
      console.error("❌ 면접 종료 오류:", err);
      alert("면접 종료 중 오류가 발생했습니다.\n콘솔을 확인하세요.");
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
  
  // 새 답변이면 재생성 카운트 초기화
  if (!isRegen) {
    state.regenCount = 0;
  }

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
          
          // 질문이 객체인 경우 (라벨 + 키워드 포함)
          if (typeof q === 'object' && q.label && q.question) {
            // 페르소나별 색상 설정
            const personaColors = {
              '검증자': '#e74c3c',
              '탐구자': '#3498db',
              '전환자': '#2ecc71'
            };
            const color = personaColors[q.persona] || '#95a5a6';
            
            // 라벨 요소
            const labelEl = document.createElement("div");
            labelEl.className = "intent-label";
            labelEl.textContent = q.label;
            labelEl.style.borderLeftColor = color;
            btn.appendChild(labelEl);
            
            // 질문 텍스트
            const questionEl = document.createElement("div");
            questionEl.className = "question-text";
            questionEl.textContent = q.question;
            btn.appendChild(questionEl);
            
            // 키워드 (있으면)
            if (q.keywords) {
              const keywordsEl = document.createElement("div");
              keywordsEl.className = "question-keywords";
              keywordsEl.textContent = "✓ " + q.keywords;
              btn.appendChild(keywordsEl);
            }
            
            btn.onclick = () => selectFollowup(q.question, box, wrapper);
          } else {
            // 기존 형식 (문자열)
            btn.textContent = typeof q === 'string' ? q : q.question || '';
            btn.onclick = () => selectFollowup(typeof q === 'string' ? q : q.question, box, wrapper);
          }
          
          wrapper.appendChild(btn);
        });

        // 재생성 카운트 증가
        if (isRegen) {
          state.regenCount++;
        }

        // 재생성 버튼
        const regen = document.createElement("button");
        regen.className = "followup-regen";
        regen.innerHTML = "↻";
        
        // 제한 도달 여부 확인
        if (state.regenCount >= state.maxRegenCount) {
          regen.title = `재생성 제한 (최대 ${state.maxRegenCount}회)`;
          regen.disabled = true;
          regen.style.opacity = "0.5";
          regen.style.cursor = "not-allowed";
        } else {
          regen.title = `질문 재생성 (${state.regenCount}/${state.maxRegenCount})`;
          regen.onclick = () => {
            wrapper.remove();
            sendToAI(fullText, true);
          };
        }
        
        wrapper.appendChild(regen);

        box.appendChild(wrapper);
        
        // ✅ 후속 질문 생성 후 스크롤 강제 이동 (약간의 딜레이 후)
        setTimeout(() => {
          box.scrollTop = box.scrollHeight;
        }, 100);
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
// 키보드 단축키 설정
// ========================================
function setupKeyboardShortcuts() {
  document.addEventListener("keydown", (e) => {
    // Ctrl + Space: STT 시작/중지
    if (e.ctrlKey && e.code === "Space") {
      e.preventDefault();
      const sttBtn = document.getElementById("stt-btn");
      if (sttBtn && !sttBtn.disabled) {
        toggleSTT();
        console.log("⌨️ [단축키] Ctrl + Space → STT 토글");
      }
    }
    
    // Ctrl + Shift + S: STT 시작/중지 (대체 단축키)
    if (e.ctrlKey && e.shiftKey && e.code === "KeyS") {
      e.preventDefault();
      const sttBtn = document.getElementById("stt-btn");
      if (sttBtn && !sttBtn.disabled) {
        toggleSTT();
        console.log("⌨️ [단축키] Ctrl + Shift + S → STT 토글");
      }
    }
  });
  
  console.log("⌨️ [단축키] 등록 완료: Ctrl + Space, Ctrl + Shift + S");
}

// ========================================
// 브라우저 콘솔 테스트용 함수
// ========================================
window.testAnswer = function(text) {
  /**
   * 브라우저 콘솔에서 면접자 답변 시뮬레이션
   * 
   * 사용법:
   *   testAnswer("저는 Spring Boot로 API를 개발했습니다")
   */
  console.log("🧪 [테스트 모드] 면접자 답변:", text);
  
  const box = document.getElementById(state.currentTab);
  if (!box) {
    console.error("❌ 현재 활성 탭을 찾을 수 없습니다.");
    return;
  }
  
  // AI 버튼 및 후속 질문 리스트 제거
  const aiBtn = box.querySelector(".inline-ai-btn");
  if (aiBtn) aiBtn.remove();
  
  const followup = box.querySelector(".followup-container");
  if (followup) followup.remove();
  
  // 면접자 답변 UI에 추가
  const msg = document.createElement("div");
  msg.className = "message user";
  msg.textContent = text;
  box.appendChild(msg);
  box.scrollTop = box.scrollHeight;
  
  // 서버에 저장
  fetch("/send", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text: text,
      question_id: state.currentTab,
      role: "면접자",
    }),
  })
    .then((res) => res.json())
    .then((data) => {
      console.log("✅ 답변 저장 완료:", data);
      
      // AI 자동 질문 생성
      if (state.aiAutoGenerate) {
        sendToAI(text.trim());
      } else {
        showAIButton(box, text);
      }
    })
    .catch((err) => console.error("❌ 답변 저장 오류:", err));
};

window.testQuestion = function(text) {
  /**
   * 브라우저 콘솔에서 면접관 질문 시뮬레이션
   * 
   * 사용법:
   *   testQuestion("구체적으로 어떤 기술을 사용하셨나요?")
   */
  console.log("🧪 [테스트 모드] 면접관 질문:", text);
  
  const box = document.getElementById(state.currentTab);
  if (!box) {
    console.error("❌ 현재 활성 탭을 찾을 수 없습니다.");
    return;
  }
  
  // AI 버튼 및 후속 질문 리스트 제거
  const aiBtn = box.querySelector(".inline-ai-btn");
  if (aiBtn) aiBtn.remove();
  
  const followup = box.querySelector(".followup-container");
  if (followup) followup.remove();
  
  // 면접관 질문 UI에 추가
  appendMessage(state.currentTab, "assistant", text);
  
  // 서버에 저장
  fetch("/send", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text,
      question_id: state.currentTab,
      role: "면접관",
    }),
  })
    .then((res) => res.json())
    .then((data) => console.log("✅ 질문 저장 완료:", data))
    .catch((err) => console.error("❌ 질문 저장 오류:", err));
};

window.testAI = function() {
  /**
   * 브라우저 콘솔에서 AI 후속 질문 강제 생성
   * 
   * 사용법:
   *   testAI()
   */
  console.log("🧪 [테스트 모드] AI 후속 질문 생성 강제 실행");
  
  const box = document.getElementById(state.currentTab);
  if (!box) {
    console.error("❌ 현재 활성 탭을 찾을 수 없습니다.");
    return;
  }
  
  // 마지막 면접자 답변 찾기
  const messages = box.querySelectorAll(".message.user");
  if (messages.length === 0) {
    console.error("❌ 면접자 답변이 없습니다. testAnswer()로 답변을 먼저 입력하세요.");
    return;
  }
  
  const lastAnswer = messages[messages.length - 1].textContent;
  console.log("📝 마지막 답변:", lastAnswer);
  
  sendToAI(lastAnswer.trim());
};

window.testHelp = function() {
  /**
   * 테스트 함수 사용법 출력
   */
  console.log(`
🧪 브라우저 콘솔 테스트 함수 사용법
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1️⃣ testAnswer("답변 내용")
   - 면접자 답변 시뮬레이션
   - AI 자동 질문 생성 (AI 자동 ON일 때)
   
   예시:
   testAnswer("저는 Spring Boot로 3년간 API를 개발했습니다")

2️⃣ testQuestion("질문 내용")
   - 면접관 질문 시뮬레이션
   - UI에 질문 추가
   
   예시:
   testQuestion("구체적으로 어떤 API를 개발하셨나요?")

3️⃣ testAI()
   - 마지막 답변 기준으로 AI 후속 질문 강제 생성
   
   예시:
   testAI()

4️⃣ testHelp()
   - 이 도움말 출력

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 빠른 테스트 시나리오:

testAnswer("Spring Boot로 API 개발했습니다")
// → AI가 자동으로 후속 질문 3개 생성

testQuestion("몇 년 경험하셨나요?")
testAnswer("3년입니다")
// → 또 AI가 후속 질문 생성

testAI()
// → 마지막 답변으로 다시 질문 생성
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  `);
};

// 초기 도움말 출력
console.log("🧪 테스트 모드 활성화됨! testHelp() 입력으로 사용법 확인");

// ========================================
// 초기화
// ========================================
loadtab();
ensureSocketConnected(registerSocketHandlers);
setupKeyboardShortcuts();

// 전역 함수로 노출 (HTML에서 호출)
window.toggleSTT = toggleSTT;
window.endInterview = endInterview;
window.sendMessage = sendMessage;
window.toggleAI = toggleAI;
