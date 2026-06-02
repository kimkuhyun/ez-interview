// 공통 유틸리티 함수

// 모달 열기/닫기 애니메이션
function openModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.classList.add('active');
        document.body.style.overflow = 'hidden';
    }
}

function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.classList.remove('active');
        document.body.style.overflow = '';
    }
}

// 상태 레이블 맵
const STATUS_LABELS = {
    'all': '전체 상태',
    'pending': '서류 대기',
    'interview_pending': '면접 대기',
    'hired': '최종 합격',
    'rejected': '최종 불합격'
};

// AJAX 요청 헬퍼
async function apiRequest(url, method = 'GET', data = null) {
    const options = {
        method: method,
        headers: {
            'Content-Type': 'application/json',
        }
    };

    if (data && method !== 'GET') {
        options.body = JSON.stringify(data);
    }

    try {
        const response = await fetch(url, options);
        const result = await response.json();
        
        if (!response.ok) {
            throw new Error(result.error || '요청 실패');
        }
        
        return result;
    } catch (error) {
        console.error('API 요청 오류:', error);
        alert(error.message);
        throw error;
    }
}

// 데이터 새로고침
async function refreshData() {
    if (confirm('데이터를 초기 상태로 새로고침하시겠습니까?')) {
        try {
            await apiRequest('/api/refresh', 'POST');
            alert('데이터가 초기 상태로 새로고침되었습니다.');
            location.reload();
        } catch (error) {
            // 에러는 apiRequest에서 처리됨
        }
    }
}