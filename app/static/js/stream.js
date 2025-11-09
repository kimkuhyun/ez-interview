console.log("✅ Stream page loaded");

// Flask-SocketIO 연결: 안전 초기화
// 이유: 일부 환경에서 socket.io client 스크립트가 로드되지 않거나 순서 문제로
// `io`가 정의되지 않을 수 있어 이를 방어적으로 처리합니다.
let socket = null;
// Prevent double-registration of socket event handlers
let handlersRegistered = false;
function ensureSocketConnected(cb) {
  if (typeof io !== "undefined") {
    socket = io("http://127.0.0.1:5000");
    if (cb) cb();
    return;
  }

  // 동적 로드 시도
  const s = document.createElement("script");
  s.src =
    "https://cdn.jsdelivr.net/npm/socket.io-client@4.5.4/dist/socket.io.min.js";
  s.onload = () => {
    try {
      socket = io("http://127.0.0.1:5000");
      if (cb) cb();
    } catch (e) {
      console.error("socket.io init error:", e);
    }
  };
  s.onerror = () => console.error("Failed to load socket.io client script");
  document.head.appendChild(s);
}

function registerSocketHandlers() {
  if (!socket) return;

  // Avoid registering handlers multiple times
  if (handlersRegistered) return;
  handlersRegistered = true;

  // If any previous handlers exist (edge cases), remove them first
  try {
    if (socket.off) socket.off("stt_text");
  } catch (e) {
    /* ignore */
  }

  socket.on("stt_text", (data) => {
    const box = document.getElementById(currentTab);
    if (!box || !data.text) return;

    if (data.text === "stt_final_stop") {
      // Use the frozen question id (the tab where STT segment started).
      // If missing fall back to currentTab. Do NOT clear the frozen id
      // until after we've processed UI updates and sent final offsets.
      const targetQ =
        typeof sttCurrentQuestion !== "undefined" && sttCurrentQuestion
          ? sttCurrentQuestion
          : currentTab;
      const targetBox = document.getElementById(targetQ) || box;

      if (currentSTTDiv) {
        currentSTTDiv.classList.add("final");
        currentSTTDiv = null;
      }

      // AI inline button should appear in the tab that started the STT
      try {
        if (!aiAutoGenerate && finalText.trim().length > 0) {
          showAIButton(targetBox, finalText.trim());
        }
      } catch (e) {
        console.error("Error showing inline AI button:", e);
      }

      // 서버에 STT 종료 offset(초)만 기록하도록 알립니다. Use frozen question id.
      try {
        fetch("/stt_final_time", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            question_id: targetQ,
            offset: getElapsedSeconds(),
          }),
        }).catch((err) => console.error("stt_final_time error", err));
      } catch (e) {
        console.error("Error sending stt final time", e);
      }

      // finally clear the frozen question so next STT can set it anew
      sttCurrentQuestion = null;

      return;
    }

    if (!currentSTTDiv) {
      currentSTTDiv = document.createElement("div");
      currentSTTDiv.className = "message stt";
      box.appendChild(currentSTTDiv);
      // Freeze the question id for this STT segment (only once)
      if (!sttCurrentQuestion) {
        sttCurrentQuestion = currentTab;
        console.log(
          "[DEBUG] STT segment started, frozen question:",
          sttCurrentQuestion
        );
      }
    }

    const newText = data.text.trim();
    if (data.final) {
      finalText += (finalText ? " " : "") + newText;
      currentSTTDiv.textContent = finalText;

      // Ensure frozen question is set (defensive: don't overwrite existing freeze)
      if (!sttCurrentQuestion) sttCurrentQuestion = currentTab;
      // STT에서 최종 문장이 확정될 때마다 (면접자 발화 완료 시점)
      // 1) 면접자 발화(텍스트)를 서버의 /send로 저장합니다.
      // 2) STT 종료 시각(offset)을 /stt_final_time으로도 기록합니다.
      try {
        const offset = getElapsedSeconds();
        const targetQ =
          typeof sttCurrentQuestion !== "undefined" && sttCurrentQuestion
            ? sttCurrentQuestion
            : currentTab;
        console.log(
          "[DEBUG] STT final -> targetQ:",
          targetQ,
          "offset:",
          offset,
          "sttCurrentQuestion:",
          sttCurrentQuestion,
          "currentTab:",
          currentTab
        );
        // send the utterance (newText) as a 면접자 message bound to the frozen question
        fetch("/send", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            text: newText,
            question_id: targetQ,
            role: "면접자",
            offset,
          }),
        })
          .then((res) => res.json())
          .then((r) => console.log("saved interviewee utterance", r))
          .catch((err) => console.error("save utterance error", err));

        // also record stt final time (keeps historical timestamps)
        console.log("[DEBUG] send stt_final_time ->", targetQ, offset);
        fetch("/stt_final_time", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question_id: targetQ, offset }),
        }).catch((err) => console.error("stt_final_time error", err));
      } catch (e) {
        console.error("Error sending stt final data", e);
      }
    } else {
      partialText = newText;
      currentSTTDiv.textContent = finalText + " " + partialText;
    }

    box.scrollTop = box.scrollHeight;
  });
}

let sttActive = false;
let currentTab = "";
let finalText = ""; // 전체 누적 텍스트
let partialText = ""; // STT 임시 문장
let currentSTTDiv = null;
let aiAutoGenerate = true; // 기본값: 자동 생성 ON
// 세션 타이머: 최초 STT 시작 시점(밀리초)
let sessionStart = null;

function ensureSessionStart() {
  if (sessionStart === null) sessionStart = Date.now();
  // When session starts, flush any recorded activation times into offsets
  try {
    if (activationTimes) {
      Object.entries(activationTimes).forEach(([qid, ts]) => {
        if (!activationSent[qid] && ts) {
          const offset = Math.floor((ts - sessionStart) / 1000);
          // don't send negative offsets
          const safeOffset = offset >= 0 ? offset : 0;
          console.log("[DEBUG] flush activation ->", qid, "offset", safeOffset);
          fetch("/question_activated", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ question_id: qid, offset: safeOffset }),
          })
            .then(() => {
              activationSent[qid] = true;
            })
            .catch((err) => console.error("question_activated error", err));
        }
      });
    }
  } catch (e) {
    console.error("Error flushing activationTimes on session start", e);
  }
}

// activationTimes: when a question tab becomes active, record wall-clock ms
const activationTimes = {};
// activationSent: whether activation offset has been sent to server
const activationSent = {};

function getElapsedSeconds() {
  if (sessionStart === null) return 0;
  return Math.floor((Date.now() - sessionStart) / 1000);
}

// ------------------------------
// 초기 탭 구성
// ------------------------------
function loadtab() {
  const tabBar = document.getElementById("tab-bar");
  const chatArea = document.getElementById("chat-area");

  Object.entries(res).forEach(([id, text], i) => {
    const tab = Object.assign(document.createElement("div"), {
      className: "tab",
      textContent: id.toUpperCase(),
      onclick: () => switchTab(id, tabBar.children[i]),
    });
    tabBar.appendChild(tab);

    const box = Object.assign(document.createElement("div"), {
      id,
      className: "chat-box",
      style: "display:none",
    });
    chatArea.appendChild(box);

    appendMessage(id, "assistant", text);
    if (i === 0) switchTab(id, tab);
  });
}
loadtab();

function switchTab(tabId, el) {
  document
    .querySelectorAll(".chat-box")
    .forEach((div) => (div.style.display = "none"));
  document.getElementById(tabId).style.display = "flex";
  document
    .querySelectorAll(".tab")
    .forEach((t) => t.classList.remove("active"));
  el.classList.add("active");
  currentTab = tabId;
  // 기록: 이 탭(질문)이 활성화된 wall-clock 시각을 저장
  try {
    activationTimes[tabId] = Date.now();
    // 만약 세션이 이미 시작된 상태라면 즉시 offset을 전송
    if (sessionStart !== null && !activationSent[tabId]) {
      const offset = getElapsedSeconds();
      console.log("[DEBUG] immediate activation ->", tabId, "offset", offset);
      fetch("/question_activated", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question_id: tabId, offset }),
      })
        .then(() => {
          activationSent[tabId] = true;
        })
        .catch((err) => console.error("question_activated error", err));
    }
  } catch (e) {
    console.error("Error recording activation time", e);
  }
}

function appendMessage(tab, role, text) {
  const box = document.getElementById(tab);
  // send the utterance (newText) as a 면접자 message bound to the frozen question

  const msg = document.createElement("div");
  msg.className = `message ${role}`;
  msg.textContent = text;
  box.appendChild(msg);
  box.scrollTop = box.scrollHeight;

  // ✅ 면접자(user)가 답변을 하면 — 면접관(assistant) 쪽에 버튼 표시
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
    btn.remove(); // 클릭 시 제거
    sendToAI(lastAnswer.trim()); // AI 질문 생성 요청
  };

  // ✅ 면접관 쪽(오른쪽)에 배치
  btn.style.alignSelf = "flex-end";
  box.appendChild(btn);
  box.scrollTop = box.scrollHeight;
}

// ------------------------------
// sttCurrentQuestion: the question tab that started the current STT segment
let sttCurrentQuestion = null;
// 🎙️ STT 토글
// ------------------------------
function toggleSTT() {
  const indicator = document.getElementById("stt-indicator");
  const btn = document.getElementById("stt-btn");
  sttActive = !sttActive;

  if (sttActive) {
    ensureSessionStart();
    if (socket) socket.emit("stt_start");
    indicator.classList.add("active");
    btn.textContent = "STT 중지";
    finalText = "";
    partialText = "";
  } else {
    if (socket) socket.emit("stt_stop");
    indicator.classList.remove("active");
    btn.textContent = "STT 시작";

    // ✅ AI 자동 질문 생성 여부 확인
    if (aiAutoGenerate && finalText.trim().length > 0) {
      sendToAI(finalText.trim());
    } else {
      console.log("🤖 AI 자동 질문 생성 비활성화 상태입니다.");
    }
  }
}

// ------------------------------
// 🛑 면접 종료
// ------------------------------
function endInterview() {
  if (socket) socket.emit("stt_end");
  alert("면접이 종료되었습니다!");

  const indicator = document.getElementById("stt-indicator");
  const btn = document.getElementById("stt-btn");
  indicator.classList.remove("active");
  btn.textContent = "STT 시작";
  sttActive = false;
}

// ------------------------------
// 🎧 STT 실시간 수신 처리
// ------------------------------
// 핸들러는 socket이 준비된 뒤 등록합니다.
ensureSocketConnected(registerSocketHandlers);

// ------------------------------
// 🧠 AI Follow-up 요청
// ------------------------------
function sendToAI(fullText, isRegen = false) {
  const box = document.getElementById(currentTab);
  const loader = showThinking(box); // 🧠 시작 시 애니메이션 표시

  fetch("/ai_followup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text: fullText,
      question_id: currentTab,
      regen: isRegen,
    }),
  })
    .then((res) => res.json())
    .then((data) => {
      loader.remove(); // ✅ 응답 수신 시 제거

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

// ✅ 사용자가 버튼 중 하나 선택 시 처리
function selectFollowup(question, box, wrapper) {
  // 버튼 UI 제거
  wrapper.remove();

  // 채팅창에 면접관 메시지로 추가
  const msg = document.createElement("div");
  msg.className = "message assistant";
  msg.textContent = question;
  box.appendChild(msg);
  box.scrollTop = box.scrollHeight;

  // 서버에 로그로 전송 (/send)
  fetch("/send", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text: question,
      question_id: currentTab,
      role: "면접관",
      offset: getElapsedSeconds(),
    }),
  })
    .then((res) => res.json())
    .then((data) => console.log("✅ 선택된 질문 로그 저장 완료:", data))
    .catch((err) => console.error("❌ 선택 질문 로그 오류:", err));
}

// ------------------------------
// 🧾 사용자 입력 전송 (로그만 기록)
// ------------------------------
function sendMessage() {
  const input = document.getElementById("user-input");
  const text = input.value.trim();
  if (!text) return;

  const box = document.getElementById(currentTab);

  // ✅ 면접관이 직접 질문하면 버튼 제거
  const aiBtn = box.querySelector(".inline-ai-btn");
  if (aiBtn) aiBtn.remove();

  // ✅ 후속 질문 리스트가 떠있는 상태에서 사용자가 직접 입력하면
  // 후속 질문 리스트를 제거하고 사용자의 메시지를 위로 노출시킵니다.
  const followup = box.querySelector(".followup-container");
  if (followup) followup.remove();

  appendMessage(currentTab, "assistant", text); // 면접관 질문 (파란색)
  input.value = "";

  fetch("/send", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text,
      question_id: currentTab,
      role: "면접관",
      offset: getElapsedSeconds(),
    }),
  })
    .then((res) => res.json())
    .then((data) => console.log("✅ 면접관 질문 로그 저장:", data))
    .catch((err) => console.error("❌ 로그 전송 오류:", err));
}

window.sendMessage = sendMessage;

// 🎬 AI 질문 생성 중 표시
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

function toggleAI() {
  aiAutoGenerate = !aiAutoGenerate;
  const btn = document.getElementById("ai-toggle-btn");

  if (aiAutoGenerate) {
    btn.textContent = "AI 자동 질문: ON";
    btn.classList.remove("off");
    btn.classList.add("on");
  } else {
    btn.textContent = "AI 자동 질문: OFF";
    btn.classList.remove("on");
    btn.classList.add("off");
  }
}
