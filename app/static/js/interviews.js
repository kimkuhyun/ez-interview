// 면접 관리 페이지 JavaScript

let currentInterviewCandidateId = null;
let currentInterviewCandidateName = '';

// 면접 예정일 변경
async function updateInterviewDate(candidateId, newDate) {
    try {
        await apiRequest(`/api/interviews/${candidateId}/date`, 'PUT', {
            scheduled_date: newDate
        });
        console.log(`지원자 ${candidateId}의 면접 예정일이 ${newDate}로 변경되었습니다.`);
    } catch (error) {
        // 에러는 apiRequest에서 처리됨
    }
}

// 면접 결과 모달
function openInterviewResultModal(candidateId, candidateName) {
    currentInterviewCandidateId = candidateId;
    currentInterviewCandidateName = candidateName;
    
    document.getElementById('interviewCandidateName').textContent = candidateName;
    document.getElementById('interviewCandidateNameText').textContent = candidateName;
    
    openModal('interviewResultModal');
}

function closeInterviewResultModal() {
    closeModal('interviewResultModal');
    currentInterviewCandidateId = null;
    currentInterviewCandidateName = '';
}

// 면접 결과 제출
async function submitInterviewResult(result) {
    if (!currentInterviewCandidateId) {
        alert('지원자 정보를 찾을 수 없습니다.');
        return;
    }
    
    const newStatus = result; // 'hired' or 'rejected'
    const statusLabel = STATUS_LABELS[newStatus];
    
    try {
        await apiRequest(`/api/candidates/${currentInterviewCandidateId}/status`, 'PUT', {
            status: newStatus
        });
        
        alert(`'${currentInterviewCandidateName}' 님의 최종 상태가 '${statusLabel}'으로 업데이트되었습니다.`);
        closeInterviewResultModal();
        location.reload();
    } catch (error) {
        // 에러는 apiRequest에서 처리됨
    }
}