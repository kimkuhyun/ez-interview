console.log("Stream page loaded");

// 샘플 데이터
const res = {
    q1: "자기소개를 해주세요.",
    q2: "가장 어려웠던 프로젝트는 무엇인가요?",
    q3: "팀 내에서 갈등을 어떻게 해결하셨나요?",
    q4: "5년 뒤 본인의 커리어 목표는 무엇인가요?",
    q5: "최근 관심있는 기술 트렌드는 무엇인가요?"
};

let currentTab = '';

// 질문 탭 및 초기 질문 셋팅
function loadtab() {
    tabBar = document.getElementById("tab-bar");
    chatArea = document.getElementById("chat-area");
    Object.entries(res).forEach(([id, text], i) => {
        const tab = Object.assign(document.createElement("div"), {
            className: "tab",
            textContent: id.toUpperCase(),
            onclick: () => switchTab(id, tabBar.children[i]),
        });
        tabBar.appendChild(tab);

        const box = Object.assign(document.createElement("div"), {
            id, className: "chat-box",
            style: "display:none",
        });
        chatArea.appendChild(box);

        appendMessage(id, "user", text);
        if (i === 0) switchTab(id, tab);
    });
}

loadtab();

function switchTab(tabId, el) {
    document.querySelectorAll('.chat-box').forEach(div => div.style.display = 'none');
    document.getElementById(tabId).style.display = 'flex';
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    el.classList.add('active');
    currentTab = tabId;
}

// 메시지 관련 함수
function appendMessage(tab, role, text) {
    const box = document.getElementById(tab);
    if (!box) return console.warn(`[WARN] chatBox(${tab}) 없음`);
    const msg = document.createElement("div");
    msg.className = `message ${role}`;
    msg.textContent = text;
    box.appendChild(msg);
    box.scrollTop = box.scrollHeight;
}

async function sendMessage() {
    const input = document.getElementById("user-input");
    const text = input.value.trim();
    if (!text || !currentTab) return;
    appendMessage(currentTab, "user", text);
    input.value = "";

    const res = await fetch("/send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text })
    });
    const data = await res.json();
    appendMessage(currentTab, "assistant", data.reply);
}

let sttActive = false;

function toggleSTT() {
    const indicator = document.getElementById('stt-indicator');
    const btn = document.getElementById('stt-btn');

    sttActive = !sttActive;

    if (sttActive) {
        indicator.classList.add('active');
        btn.textContent = 'STT 중지';
        console.log('🎙️ STT 시작됨');
    } else {
        indicator.classList.remove('active');
        btn.textContent = 'STT 시작';
        console.log('🔇 STT 종료됨');
    }
}
