// 지원자 관리 페이지 JavaScript

// 필터링 함수
function filterCandidates() {
    const statusFilter = document.getElementById('statusFilter').value;
    const positionFilter = document.getElementById('positionFilter').value;
    const rows = document.querySelectorAll('#candidateTableBody tr[data-status]');
    
    let visibleCount = 0;
    
    rows.forEach(row => {
        const status = row.getAttribute('data-status');
        const position = row.getAttribute('data-position');
        
        let showRow = true;
        
        if (statusFilter !== 'all' && status !== statusFilter) {
            showRow = false;
        }
        
        if (positionFilter !== 'all' && position !== positionFilter) {
            showRow = false;
        }
        
        row.style.display = showRow ? '' : 'none';
        if (showRow) visibleCount++;
    });
    
    document.getElementById('candidateCount').textContent = visibleCount;
}

// 이력서 업로드 모달
function openUploadModal() {
    openModal('uploadModal');
}

function closeUploadModal() {
    closeModal('uploadModal');
    // 입력 필드 초기화
    document.getElementById('newCandidateName').value = '';
    document.getElementById('newCandidateResumeFile').value = '';
    document.getElementById('newCandidatePositionId').value = '';
    document.getElementById('newCandidateResumeContent').value = '';
}

// 새 지원자 등록
async function submitNewCandidate() {
    const name = document.getElementById('newCandidateName').value.trim();
    const resumeFile = document.getElementById('newCandidateResumeFile').value.trim();
    const positionId = document.getElementById('newCandidatePositionId').value;
    const resumeContent = document.getElementById('newCandidateResumeContent').value.trim();
    
    if (!name || !resumeFile || !positionId) {
        alert('이름, 이력서 파일명, 지원 포지션을 모두 입력해주세요.');
        return;
    }
    
    try {
        const result = await apiRequest('/api/candidates', 'POST', {
            name: name,
            resume_file: resumeFile,
            position_id: parseInt(positionId),
            resume_content: resumeContent || '새로 업로드된 지원자입니다. 이력서 내용 목업.'
        });
        
        alert(`'${name}' 님의 이력서가 성공적으로 업로드되었습니다.`);
        closeUploadModal();
        location.reload();
    } catch (error) {
        // 에러는 apiRequest에서 처리됨
    }
}