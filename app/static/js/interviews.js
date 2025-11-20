// 면접 관리 페이지 JavaScript

// 전역 상태 관리
if (!window.interviewsState) {
    window.interviewsState = {
        interviews: []
    };
}

// 페이지 로드 시 데이터 가져오기
(function initInterviews() {
    console.log('[Interviews] 초기화 시작');
    loadInterviews();
})();

// 면접 목록 로드
async function loadInterviews() {
    console.log('[Interviews] 데이터 로드 시작');
    
    try {
        const response = await fetch('/api/interviews/pending');
        const data = await response.json();
        
        console.log('[Interviews] 응답 데이터:', data);
        
        if (data.success) {
            window.interviewsState.interviews = data.interviews;
            renderInterviews(data.interviews);
            console.log('[Interviews] 렌더링 완료:', data.interviews.length, '건');
        } else {
            console.error('[Interviews] 로드 실패:', data.error);
            showError(data.error || '데이터를 불러올 수 없습니다.');
        }
    } catch (error) {
        console.error('[Interviews] 오류:', error);
        showError('네트워크 오류: ' + error.message);
    }
}

// 면접 목록 렌더링
function renderInterviews(interviews) {
    const tbody = document.getElementById('interviews-tbody');
    if (!tbody) return;
    
    if (interviews.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="5" style="text-align: center; padding: 2rem; color: #9ca3af;">
                    대기 중인 면접이 없습니다.
                </td>
            </tr>
        `;
        return;
    }
    
    tbody.innerHTML = interviews.map(interview => {
        const statusBadge = getInterviewStatusBadge(interview.status);
        
        return `
            <tr>
                <td>${escapeHtml(interview.name || '-')}</td>
                <td>${escapeHtml(interview.position || '-')}</td>
                <td>
                    <input 
                        type="datetime-local" 
                        value="${interview.interview_at ? interview.interview_at.slice(0, 16) : ''}"
                        onchange="updateInterviewDate('${interview.session_id}', this.value)"
                        style="padding: 0.5rem; border: 1px solid #e2e8f0; border-radius: 0.375rem; font-size: 0.875rem;"
                    />
                </td>
                <td>${statusBadge}</td>
                <td>
                    <button 
                        class="btn btn-sm primary" 
                        onclick="startInterview('${interview.session_id}', '${escapeHtml(interview.name)}', '${interview.jd_id}', '${escapeHtml(interview.position)}')"
                        style="padding: 0.5rem 1rem; font-size: 0.875rem;"
                    >
                        면접 시작
                    </button>
                </td>
            </tr>
        `;
    }).join('');
}

// 면접 상태 배지
function getInterviewStatusBadge(status) {
    const badges = {
        'interview_pending': '<span class="badge badge-warning">대기</span>',
        'interview_in_progress': '<span class="badge badge-info">진행 중</span>',
        'passed': '<span class="badge badge-success">합격</span>',
        'rejected': '<span class="badge badge-danger">불합격</span>'
    };
    
    return badges[status] || `<span class="badge badge-secondary">${status}</span>`;
}

// 면접 일시 변경
async function updateInterviewDate(sessionId, newDate) {
    console.log('[Interviews] 면접 일시 변경:', sessionId, newDate);
    
    if (!newDate) {
        alert('면접 일시를 선택해주세요.');
        return;
    }
    
    try {
        const response = await fetch(`/api/interviews/${sessionId}/schedule`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                interview_at: newDate
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            console.log('[Interviews] 면접 일시 저장 완료');
            // 새로고침 없이 조용히 저장
        } else {
            console.error('[Interviews] 면접 일시 저장 실패:', data.error);
            alert('면접 일시 저장에 실패했습니다: ' + (data.error || '알 수 없는 오류'));
        }
    } catch (error) {
        console.error('[Interviews] 오류:', error);
        alert('네트워크 오류: ' + error.message);
    }
}

// 면접 시작
function startInterview(sessionId, name, jdId, position) {
    console.log('[Interviews] 면접 시작:', sessionId, name, jdId, position);
    
    if (confirm(`${name}님의 면접을 시작하시겠습니까?`)) {
        window.location.href = `/panel/interview?session_id=${sessionId}&name=${encodeURIComponent(name)}&jd_id=${jdId}&position=${encodeURIComponent(position)}`;
    }
}

// 에러 표시
function showError(message) {
    const tbody = document.getElementById('interviews-tbody');
    if (tbody) {
        tbody.innerHTML = `
            <tr>
                <td colspan="5" style="text-align: center; padding: 2rem; color: #ef4444;">
                    ${escapeHtml(message)}
                </td>
            </tr>
        `;
    }
}

// HTML 이스케이프
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}