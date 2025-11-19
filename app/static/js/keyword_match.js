let currentPositionId = null;
let keywords = [];
let candidates = [];
let currentCandidateId = null;

function initKeywordMatch(positionId) {
  currentPositionId = positionId;
  loadPositionData();
}

async function loadPositionData() {
  const res = await fetch(`/api/positions/${currentPositionId}`);
  const data = await res.json();
  
  document.getElementById('positionTitle').textContent = `${data.name} - 키워드 설정`;
  keywords = data.keywords || [];
  renderKeywords();
}

function addKeyword() {
  const input = document.getElementById('keywordInput');
  const keyword = input.value.trim();
  
  if (!keyword) return;
  if (keywords.includes(keyword)) {
    alert('이미 추가된 키워드입니다.');
    return;
  }
  
  keywords.push(keyword);
  input.value = '';
  renderKeywords();
  saveKeywords();
}

function removeKeyword(keyword) {
  keywords = keywords.filter(k => k !== keyword);
  renderKeywords();
  saveKeywords();
}

function renderKeywords() {
  const container = document.getElementById('keywordsDisplay');
  
  if (keywords.length === 0) {
    container.innerHTML = '<span class="empty-text">키워드를 추가하거나 AI 추천을 받아보세요</span>';
    return;
  }
  
  container.innerHTML = keywords.map(kw => `
    <div class="keyword-tag">
      ${kw}
      <span class="keyword-remove" onclick="removeKeyword('${kw}')">×</span>
    </div>
  `).join('');
}

async function recommendKeywords() {
  const btn = event.target;
  btn.disabled = true;
  btn.textContent = '🤖 추천 중...';
  
  try {
    const res = await fetch(`/api/positions/${currentPositionId}/recommend-keywords`, {
      method: 'POST'
    });
    const data = await res.json();
    
    // 중복 제거하고 추가
    data.keywords.forEach(kw => {
      if (!keywords.includes(kw)) {
        keywords.push(kw);
      }
    });
    
    renderKeywords();
    saveKeywords();
  } catch (error) {
    alert('AI 추천 실패: ' + error.message);
  } finally {
    btn.disabled = false;
    btn.textContent = '🤖 AI 추천';
  }
}

async function saveKeywords() {
  await fetch(`/api/positions/${currentPositionId}/keywords`, {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({keywords})
  });
}

async function startMatching() {
  if (keywords.length === 0) {
    alert('키워드를 최소 1개 이상 추가해주세요.');
    return;
  }
  
  const btn = document.getElementById('matchBtn');
  btn.disabled = true;
  btn.textContent = '⏳ 매칭 중...';
  
  try {
    const res = await fetch(`/api/positions/${currentPositionId}/match`, {
      method: 'POST'
    });
    const data = await res.json();
    
    candidates = data.candidates || [];
    renderCandidates();
  } catch (error) {
    alert('매칭 실패: ' + error.message);
  } finally {
    btn.disabled = false;
    btn.textContent = '✓ 지원자 매칭 시작';
  }
}

function renderCandidates() {
  const container = document.getElementById('candidatesList');
  const count = document.getElementById('candidateCount');
  
  count.textContent = `${candidates.length}명`;
  
  if (candidates.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path>
          <circle cx="9" cy="7" r="4"></circle>
          <path d="M23 21v-2a4 4 0 0 0-3-3.87"></path>
          <path d="M16 3.13a4 4 0 0 1 0 7.75"></path>
        </svg>
        <p>매칭된 지원자가 없습니다</p>
      </div>
    `;
    return;
  }
  
  container.innerHTML = candidates.map(c => `
    <div class="candidate-card" id="card-${c.session_id}" data-session-id="${c.session_id}">
      <div class="candidate-info" onclick="openCandidateModal('${c.session_id}')">
        <div class="candidate-name">${c.name}</div>
        <div class="candidate-meta">
          ${c.matched_keywords ? c.matched_keywords.map(kw => `<span class="meta-tag">${kw}</span>`).join('') : ''}
        </div>
      </div>
      <div class="match-score" onclick="openCandidateModal('${c.session_id}')">${c.score}%</div>
      <div class="card-actions">
        <button class="btn-card-pass" onclick="updateCandidateStatusInList('${c.session_id}', 'interview_pending', event)">✓ 합격</button>
        <button class="btn-card-reject" onclick="updateCandidateStatusInList('${c.session_id}', 'rejected', event)">× 불합격</button>
      </div>
    </div>
  `).join('');
}

async function openCandidateModal(sessionId) {
  currentCandidateId = sessionId;
  const candidate = candidates.find(c => c.session_id === sessionId);
  
  document.getElementById('modalCandidateName').textContent = candidate.name;
  
  // 이력서 로드
  const resumeFrame = document.getElementById('resumeFrame');
  resumeFrame.src = `/api/positions/candidates/${sessionId}/resume`;
  
  // 포트폴리오 확인
  const hasPortfolio = candidate.has_portfolio;
  if (hasPortfolio) {
    document.getElementById('portfolioTab').style.display = 'block';
    document.getElementById('portfolioFrame').src = `/api/positions/candidates/${sessionId}/portfolio`;
  } else {
    document.getElementById('portfolioTab').style.display = 'none';
  }
  
  document.getElementById('candidateModal').classList.add('active');
}

function closeCandidateModal() {
  const modal = document.getElementById('candidateModal');
  const modalContent = modal.querySelector('.modal-content');
  
  // border 스타일 초기화
  modalContent.style.borderColor = '';
  modalContent.style.borderWidth = '';
  modalContent.style.borderStyle = '';
  
  modal.classList.remove('active');
  currentCandidateId = null;
}

function switchTab(tab) {
  document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
  document.querySelectorAll('.document-viewer').forEach(viewer => viewer.classList.remove('active'));
  
  event.target.classList.add('active');
  document.getElementById(tab + 'Content').classList.add('active');
}

async function updateCandidateStatusInList(sessionId, status, event) {
  event.stopPropagation(); // 카드 클릭 이벤트 방지
  
  const card = document.getElementById(`card-${sessionId}`);
  const passBtn = card.querySelector('.btn-card-pass');
  const rejectBtn = card.querySelector('.btn-card-reject');
  
  // 버튼 비활성화
  passBtn.disabled = true;
  rejectBtn.disabled = true;
  
  try {
    const res = await fetch(`/api/positions/candidates/${sessionId}/status`, {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({status})
    });
    
    const data = await res.json();
    
    if (res.ok && data.success) {
      // ✅ 성공 피드백
      const isPass = status === 'interview_pending';
      const feedbackColor = isPass ? '#10b981' : '#ef4444';
      const feedbackText = isPass ? '합격' : '불합격';
      
      // 카드에 피드백 표시
      card.style.borderColor = feedbackColor;
      card.style.borderWidth = '3px';
      card.style.backgroundColor = isPass ? '#ecfdf5' : '#fef2f2';
      card.style.transition = 'all 0.3s';
      
      // 버튼 영역에 결과 표시
      const actions = card.querySelector('.card-actions');
      actions.innerHTML = `
        <div style="color: ${feedbackColor}; font-weight: 700; font-size: 14px;">
          ${feedbackText}
        </div>
      `;
      
      console.log(`✅ DB 업데이트 확인: ${data.name} -> ${data.status}`);
      
      // 2초 후 목록에서 제거
      setTimeout(() => {
        card.style.opacity = '0';
        card.style.transform = 'translateX(-20px)';
        setTimeout(() => {
          candidates = candidates.filter(c => c.session_id !== sessionId);
          renderCandidates();
        }, 300);
      }, 2000);
      
    } else {
      alert('상태 변경 실패: ' + (data.error || '알 수 없는 오류'));
      passBtn.disabled = false;
      rejectBtn.disabled = false;
    }
  } catch (error) {
    alert('상태 변경 실패: ' + error.message);
    passBtn.disabled = false;
    rejectBtn.disabled = false;
  }
}

async function updateCandidateStatus(status) {
  if (!currentCandidateId) return;
  
  const passBtn = document.querySelector('.btn-pass');
  const rejectBtn = document.querySelector('.btn-reject');
  const modalContent = document.querySelector('.modal-content');
  
  // 버튼 비활성화
  passBtn.disabled = true;
  rejectBtn.disabled = true;
  
  try {
    const res = await fetch(`/api/positions/candidates/${currentCandidateId}/status`, {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({status})
    });
    
    const data = await res.json();
    
    if (res.ok && data.success) {
      // ✅ 성공 피드백 (파란색 또는 빨간색)
      const isPass = status === 'interview_pending';
      const feedbackColor = isPass ? '#10b981' : '#ef4444';
      const feedbackText = isPass ? '합격 처리되었습니다' : '불합격 처리되었습니다';
      
      // 모달에 피드백 표시
      modalContent.style.borderColor = feedbackColor;
      modalContent.style.borderWidth = '4px';
      modalContent.style.borderStyle = 'solid';
      modalContent.style.transition = 'all 0.3s';
      
      // 피드백 메시지 표시
      const feedback = document.createElement('div');
      feedback.style.cssText = `
        position: absolute;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%);
        background: ${feedbackColor};
        color: white;
        padding: 24px 48px;
        border-radius: 12px;
        font-size: 18px;
        font-weight: 700;
        box-shadow: 0 10px 40px rgba(0,0,0,0.3);
        z-index: 10;
        animation: fadeIn 0.3s;
      `;
      feedback.textContent = feedbackText;
      modalContent.style.position = 'relative';
      modalContent.appendChild(feedback);
      
      console.log(`✅ DB 업데이트 확인: ${data.name} -> ${data.status}`);
      
      // 1.5초 후 모달 닫고 목록 업데이트
      setTimeout(() => {
        closeCandidateModal();
        candidates = candidates.filter(c => c.session_id !== currentCandidateId);
        renderCandidates();
      }, 1500);
      
    } else {
      alert('상태 변경 실패: ' + (data.error || '알 수 없는 오류'));
      passBtn.disabled = false;
      rejectBtn.disabled = false;
    }
  } catch (error) {
    alert('상태 변경 실패: ' + error.message);
    passBtn.disabled = false;
    rejectBtn.disabled = false;
  }
}

function closeKeywordView() {
  // SPA 방식으로 포지션 목록으로 돌아가기
  const rightPanel = document.querySelector('#right-panel') || document.querySelector('.tab-content');
  
  if (!rightPanel) {
    window.location.reload();
    return;
  }
  
  fetch('/panel/positions')
    .then(res => res.text())
    .then(html => {
      rightPanel.innerHTML = html;
      
      // 스크립트 재실행
      const scripts = rightPanel.querySelectorAll('script');
      scripts.forEach(old => {
        const s = document.createElement('script');
        if (old.src) {
          s.src = old.src;
        } else {
          s.textContent = old.textContent;
        }
        document.body.appendChild(s);
        old.remove();
      });
      
      // positions.js의 loadPositions 호출
      if (typeof loadPositions === 'function') {
        loadPositions();
      }
    });
}

// 초기화
(function() {
  const c = document.querySelector('.keyword-match-container');
  if (c) {
    const id = c.getAttribute('data-position-id');
    if (id && id !== 'null') initKeywordMatch(id);
  }
})();

// 재진입 감지
if (window.MutationObserver) {
  new MutationObserver(() => {
    const c = document.querySelector('.keyword-match-container');
    if (c && c.offsetParent) {
      const id = c.getAttribute('data-position-id');
      if (id && id !== 'null' && id !== currentPositionId) {
        currentPositionId = id;
        loadPositionData();
      }
    }
  }).observe(document.body, { childList: true, subtree: true });
}
