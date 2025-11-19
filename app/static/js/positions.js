// 포지션 관리 및 서류 전형 페이지 JavaScript

let currentPositionId = null;
let currentKeywords = [];
let matchingCandidates = [];
let currentReviewIndex = -1;

// 뷰 전환
function showListView() {
    document.getElementById('positionListView').style.display = 'block';
    document.getElementById('keywordView').style.display = 'none';
    document.getElementById('reviewView').style.display = 'none';
    currentPositionId = null;
    currentKeywords = [];
    matchingCandidates = [];
    currentReviewIndex = -1;
}

function showKeywordView(positionId) {
    currentPositionId = positionId;
    
    // 포지션 정보 가져오기
    apiRequest(`/api/positions/${positionId}`)
        .then(position => {
            currentKeywords = position.keywords || [];
            
            document.getElementById('positionListView').style.display = 'none';
            document.getElementById('keywordView').style.display = 'block';
            document.getElementById('reviewView').style.display = 'none';
            
            document.getElementById('currentPositionName').textContent = position.name;
            document.getElementById('keywordPositionName').textContent = position.name;
            document.getElementById('keywordPositionJD').textContent = position.jd_file;
            
            updateKeywordsDisplay();
            
            if (currentKeywords.length === 0) {
                extractKeywords();
            } else {
                document.getElementById('llmStatus').textContent = '이미 키워드가 설정되어 있습니다. 다시 추천 받으시려면 버튼을 누르세요.';
                renderLLMKeywords(currentKeywords);
            }
            
            // pending 상태 지원자 수 업데이트
            updatePendingCount();
        });
}

function updateKeywordsDisplay() {
    const display = currentKeywords.length > 0 
        ? currentKeywords.join(', ') 
        : '키워드 미설정';
    document.getElementById('currentKeywordsDisplay').textContent = display;
    
    renderSelectedKeywords();
}

function updatePendingCount() {
    apiRequest('/api/candidates?status=pending')
        .then(data => {
            document.getElementById('pendingCount').textContent = data.candidates.length;
        });
}

// LLM 키워드 추출 (목업)
function extractKeywords() {
    if (!currentPositionId) return;
    
    document.getElementById('llmStatus').textContent = '키워드 추출 중... (LLM API 호출 시뮬레이션)';
    document.getElementById('llmKeywordsContainer').innerHTML = '<span class="text-[11px] text-slate-500/80">추천 중...</span>';
    
    setTimeout(() => {
        apiRequest(`/api/positions/${currentPositionId}/extract-keywords`, 'POST')
            .then(data => {
                document.getElementById('llmStatus').textContent = '추천 완료. 클릭하여 최종 키워드에 추가하거나 직접 입력하세요.';
                renderLLMKeywords(data.keywords);
            });
    }, 800);
}

function renderLLMKeywords(keywords) {
    const container = document.getElementById('llmKeywordsContainer');
    
    if (keywords.length === 0) {
        container.innerHTML = '<span class="text-[11px] text-slate-500/80">추천 키워드가 없습니다.</span>';
        return;
    }
    
    container.innerHTML = keywords.map(kw => {
        const disabled = currentKeywords.includes(kw) ? 'opacity-60 cursor-not-allowed' : 'cursor-pointer hover:bg-blue-200';
        return `<button onclick="addKeywordFromLLM('${kw}')" class="inline-flex items-center rounded-full bg-blue-100 px-3 py-1 text-[11px] font-medium text-blue-800 shadow-sm ${disabled} transition-colors">${kw}</button>`;
    }).join('');
}

function renderSelectedKeywords() {
    const container = document.getElementById('selectedKeywords');
    
    if (currentKeywords.length === 0) {
        container.innerHTML = '<span class="text-xs text-slate-500">키워드를 추가하여 심사 매칭률을 높이세요.</span>';
        return;
    }
    
    container.innerHTML = currentKeywords.map(kw => `
        <span class="inline-flex items-center rounded-full bg-indigo-100 px-3 py-1 text-xs font-medium text-indigo-800">
            ${kw}
            <button type="button" onclick="removeKeyword('${kw}')" class="ml-2 inline-flex h-4 w-4 flex-shrink-0 items-center justify-center rounded-full text-indigo-400 hover:bg-indigo-200 hover:text-indigo-500 transition-colors">
                <svg class="h-3 w-3" viewBox="0 0 20 20" fill="currentColor">
                    <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd" />
                </svg>
            </button>
        </span>
    `).join('');
}

function addKeywordFromLLM(keyword) {
    if (currentKeywords.includes(keyword)) return;
    addKeyword(keyword);
}

function addKeyword(keyword) {
    const kw = keyword || document.getElementById('keywordInput').value.trim();
    const errorEl = document.getElementById('keywordError');
    
    errorEl.style.display = 'none';
    
    if (kw.length < 2) {
        errorEl.textContent = '키워드는 2자 이상 입력해야 합니다.';
        errorEl.style.display = 'block';
        return;
    }
    
    if (currentKeywords.includes(kw)) {
        document.getElementById('keywordInput').value = '';
        return;
    }
    
    currentKeywords.push(kw);
    updateKeywordsDisplay();
    document.getElementById('keywordInput').value = '';
}

function removeKeyword(keyword) {
    currentKeywords = currentKeywords.filter(k => k !== keyword);
    updateKeywordsDisplay();
}

// 서류 심사 시작
function startReview() {
    if (!currentPositionId) {
        alert('포지션을 먼저 선택하세요.');
        return;
    }
    
    if (currentKeywords.length === 0) {
        alert('최소한 하나 이상의 키워드를 설정해야 심사를 진행할 수 있습니다.');
        return;
    }
    
    // 키워드 저장
    apiRequest(`/api/positions/${currentPositionId}/keywords`, 'PUT', {
        keywords: currentKeywords
    }).then(() => {
        // 매칭 지원자 조회
        return apiRequest(`/api/positions/${currentPositionId}/matching-candidates`, 'POST', {
            keywords: currentKeywords
        });
    }).then(data => {
        matchingCandidates = data.candidates;
        
        if (matchingCandidates.length === 0) {
            alert('설정된 키워드와 매칭되는 지원자가 없습니다. 키워드를 수정해주세요.');
            return;
        }
        
        currentReviewIndex = 0;
        showReviewView();
    });
}

function showReviewView() {
    document.getElementById('keywordView').style.display = 'none';
    document.getElementById('reviewView').style.display = 'block';
    
    // 포지션 정보 표시
    apiRequest(`/api/positions/${currentPositionId}`)
        .then(position => {
            document.getElementById('reviewPositionName').textContent = position.name;
            document.getElementById('totalReviewCount').textContent = matchingCandidates.length;
            document.getElementById('totalReviewCount2').textContent = matchingCandidates.length;
            
            loadCandidateForReview();
        });
}

function loadCandidateForReview() {
    if (currentReviewIndex < 0 || currentReviewIndex >= matchingCandidates.length) {
        document.getElementById('reviewCandidateCard').style.display = 'none';
        document.getElementById('reviewCompleteMessage').style.display = 'block';
        document.getElementById('reviewMessage').style.display = 'block';
        document.getElementById('reviewMessage').className = 'p-3 mb-4 rounded-md text-sm font-medium bg-indigo-100 text-indigo-700';
        document.getElementById('reviewMessage').textContent = '모든 심사가 완료되었습니다! 포지션 목록으로 돌아가세요.';
        return;
    }
    
    const candidate = matchingCandidates[currentReviewIndex];
    
    document.getElementById('reviewCandidateCard').style.display = 'block';
    document.getElementById('reviewCompleteMessage').style.display = 'none';
    document.getElementById('currentReviewNumber').textContent = currentReviewIndex + 1;
    
    document.getElementById('reviewCandidateName').textContent = candidate.name;
    document.getElementById('reviewCandidateScore').textContent = candidate.score;
    document.getElementById('reviewCandidateResume').textContent = candidate.resume_url;
    document.getElementById('reviewCandidateContent').textContent = candidate.resume_content;
    
    // 상태 배지
    const statusBadge = document.getElementById('reviewCandidateStatusBadge');
    const statusClass = candidate.status === 'pending' ? 'bg-yellow-100 text-yellow-800' :
                       candidate.status === 'interview_pending' ? 'bg-indigo-100 text-indigo-800' :
                       'bg-red-100 text-red-800';
    statusBadge.className = `inline-flex items-center ml-3 rounded-full px-3 py-0.5 text-xs font-semibold ${statusClass}`;
    statusBadge.textContent = STATUS_LABELS[candidate.status];
    
    document.getElementById('reviewerNotes').value = '';
    document.getElementById('reviewMessage').style.display = 'none';
}

function reviewAction(action) {
    if (currentReviewIndex < 0 || currentReviewIndex >= matchingCandidates.length) {
        alert('심사할 지원자를 먼저 선택하거나 다음 지원자를 로드해주세요.');
        return;
    }
    
    const candidate = matchingCandidates[currentReviewIndex];
    const newStatus = action === 'pass' ? 'interview_pending' : 'rejected';
    
    apiRequest(`/api/candidates/${candidate.id}/status`, 'PUT', {
        status: newStatus,
        position_id: currentPositionId
    }).then(() => {
        // 로컬 상태 업데이트
        matchingCandidates[currentReviewIndex].status = newStatus;
        
        // 메시지 표시
        const messageEl = document.getElementById('reviewMessage');
        messageEl.style.display = 'block';
        messageEl.className = `p-3 mb-4 rounded-md text-sm font-medium ${action === 'pass' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`;
        messageEl.textContent = `${candidate.name} 지원자가 '서류 ${action === 'pass' ? '합격' : '불합격'}' 처리되었습니다.`;
        
        // 다음 지원자로 이동
        setTimeout(() => {
            currentReviewIndex++;
            loadCandidateForReview();
        }, 700);
    });
}

// 새 포지션 등록 모달
function openNewPositionModal() {
    openModal('newPositionModal');
}

function closeNewPositionModal() {
    closeModal('newPositionModal');
    document.getElementById('newPositionName').value = '';
    document.getElementById('newPositionFileName').value = '';
}

async function submitNewPosition() {
    const name = document.getElementById('newPositionName').value.trim();
    const fileName = document.getElementById('newPositionFileName').value.trim();
    
    if (!name || !fileName) {
        alert('포지션명과 JD 파일명을 모두 입력해야 합니다.');
        return;
    }
    
    try {
        await apiRequest('/api/positions', 'POST', {
            name: name,
            jd_file: fileName
        });
        
        alert(`'${name}' 포지션이 등록되었습니다. 이제 키워드를 설정해 주세요.`);
        closeNewPositionModal();
        location.reload();
    } catch (error) {
        // 에러는 apiRequest에서 처리됨
    }
}