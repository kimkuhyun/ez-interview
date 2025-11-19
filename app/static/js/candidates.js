// 지원자 관리 페이지 JavaScript

// 전역 상태 관리 (중복 선언 방지)
if (!window.candidatesState) {
    window.candidatesState = {
        currentPage: 1,
        currentStatus: 'all',
        currentSearch: '',
        itemsPerPage: 10
    };
}

// 페이지 로드 시 데이터 가져오기 (즉시 실행)
(function initCandidates() {
    console.log('[Candidates] 초기화 시작');
    
    // 데이터 로드
    loadCandidates();
    
    // 필터 이벤트 리스너
    const statusFilter = document.getElementById('statusFilter');
    const searchInput = document.getElementById('searchInput');
    
    if (statusFilter) {
        statusFilter.addEventListener('change', () => {
            window.candidatesState.currentStatus = statusFilter.value;
            window.candidatesState.currentPage = 1;
            loadCandidates();
        });
        console.log('[Candidates] 상태 필터 이벤트 연결됨');
    }
    
    if (searchInput) {
        let searchTimeout;
        searchInput.addEventListener('input', (e) => {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => {
                window.candidatesState.currentSearch = e.target.value.trim();
                window.candidatesState.currentPage = 1;
                loadCandidates();
            }, 300);
        });
        console.log('[Candidates] 검색 입력 이벤트 연결됨');
    }
})();

// 지원자 데이터 로드
async function loadCandidates() {
    const state = window.candidatesState;
    console.log('[Candidates] 데이터 로드 시작:', { status: state.currentStatus, search: state.currentSearch, page: state.currentPage });
    
    try {
        const params = new URLSearchParams({
            status: state.currentStatus,
            search: state.currentSearch,
            page: state.currentPage,
            limit: state.itemsPerPage
        });
        
        const url = `/api/candidates?${params}`;
        console.log('[Candidates] 요청 URL:', url);
        
        const response = await fetch(url);
        const data = await response.json();
        
        console.log('[Candidates] 응답 데이터:', data);
        
        if (data.success) {
            renderCandidates(data.candidates);
            updatePagination(data.total, data.page, data.limit);
            updateCandidateCount(data.total);
            console.log('[Candidates] 렌더링 완료:', data.candidates.length, '명');
        } else {
            console.error('[Candidates] 로드 실패:', data.error);
            const tbody = document.getElementById('candidateTableBody');
            if (tbody) {
                tbody.innerHTML = `
                    <tr>
                        <td colspan="5" style="text-align: center; padding: 2rem; color: #ef4444;">
                            데이터 로드 실패: ${data.error || '알 수 없는 오류'}
                        </td>
                    </tr>
                `;
            }
        }
    } catch (error) {
        console.error('[Candidates] 오류:', error);
        const tbody = document.getElementById('candidateTableBody');
        if (tbody) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="5" style="text-align: center; padding: 2rem; color: #ef4444;">
                        네트워크 오류: ${error.message}
                    </td>
                </tr>
            `;
        }
    }
}

// 지원자 목록 렌더링
function renderCandidates(candidates) {
    const tbody = document.getElementById('candidateTableBody');
    if (!tbody) return;
    
    if (candidates.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="5" style="text-align: center; padding: 2rem; color: #9ca3af;">
                    검색 결과가 없습니다.
                </td>
            </tr>
        `;
        return;
    }
    
    tbody.innerHTML = candidates.map(candidate => {
        const statusBadge = getStatusBadge(candidate.status);
        const createdDate = candidate.created_at ? new Date(candidate.created_at).toLocaleDateString('ko-KR') : '-';
        const portfolioBadge = candidate.has_portfolio 
            ? '<span class="badge badge-success" style="font-size: 0.75rem;">제출 완료</span>' 
            : '<span class="badge badge-secondary" style="font-size: 0.75rem;">미제출</span>';
        
        return `
            <tr onclick="openCandidateDetail('${candidate.session_id}', '${escapeHtml(candidate.name)}')" style="cursor: pointer;">
                <td>${escapeHtml(candidate.name || '-')}</td>
                <td>${statusBadge}</td>
                <td>${escapeHtml(candidate.position || '-')}</td>
                <td>${createdDate}</td>
                <td style="text-align: center;">${portfolioBadge}</td>
            </tr>
        `;
    }).join('');
}

// 지원자 상세 보기 (PDF 모달)
if (!window.candidatePdfState) {
    window.candidatePdfState = {
        currentFiles: [],
        currentName: ''
    };
}

async function openCandidateDetail(sessionId, name) {
    console.log('[Candidates] 지원자 상세 보기:', sessionId, name);
    
    try {
        // API로 지원자 파일 정보 조회
        const response = await fetch(`/api/candidates/${sessionId}/files`);
        const data = await response.json();
        
        if (!data.success) {
            alert('파일 정보를 불러올 수 없습니다.');
            return;
        }
        
        if (data.files.length === 0) {
            alert('제출된 서류가 없습니다.');
            return;
        }
        
        // 전역 객체에 저장
        window.candidatePdfState.currentFiles = data.files;
        window.candidatePdfState.currentName = name;
        
        // PDF 모달 열기
        const modal = document.getElementById('pdfModal');
        const modalTitle = document.getElementById('pdfModalTitle');
        const pdfTabs = document.getElementById('pdfTabs');
        const pdfViewer = document.getElementById('pdfViewer');
        
        if (!modal || !modalTitle || !pdfViewer || !pdfTabs) {
            console.error('[Candidates] 모달 요소를 찾을 수 없습니다.');
            return;
        }
        
        modalTitle.textContent = `${name} - 지원 서류`;
        
        // 파일이 여러 개인 경우 탭 표시
        if (data.files.length > 1) {
            pdfTabs.style.display = 'flex';
            pdfTabs.style.gap = '0.5rem';
            pdfTabs.style.paddingTop = '0.75rem';
            
            const resumeFile = data.files.find(f => f.type === 'resume');
            const portfolioFile = data.files.find(f => f.type === 'portfolio');
            
            let tabsHtml = '';
            if (resumeFile) {
                tabsHtml += `<button class="pdf-tab active" onclick="switchPdfFile('${resumeFile.file_path}', this)">📄 이력서</button>`;
            }
            if (portfolioFile) {
                tabsHtml += `<button class="pdf-tab ${!resumeFile ? 'active' : ''}" onclick="switchPdfFile('${portfolioFile.file_path}', this)">📁 포트폴리오</button>`;
            }
            
            pdfTabs.innerHTML = tabsHtml;
            
            // 첫 번째 파일 표시
            pdfViewer.src = `/api/files/${data.files[0].file_path}`;
        } else {
            // 파일이 1개만 있는 경우 탭 숨김
            pdfTabs.style.display = 'none';
            const fileType = data.files[0].type === 'resume' ? '이력서' : '포트폴리오';
            modalTitle.textContent = `${name} - ${fileType}`;
            pdfViewer.src = `/api/files/${data.files[0].file_path}`;
        }
        
        // 모달 열기
        modal.classList.add('active');
        
    } catch (error) {
        console.error('[Candidates] 파일 로드 오류:', error);
        alert('파일을 불러오는 중 오류가 발생했습니다.');
    }
}

// PDF 파일 전환 (탭 클릭 시)
function switchPdfFile(filePath, tabElement) {
    const pdfViewer = document.getElementById('pdfViewer');
    if (pdfViewer) {
        pdfViewer.src = `/api/files/${filePath}`;
    }
    
    // 탭 활성화 상태 변경
    const allTabs = document.querySelectorAll('.pdf-tab');
    allTabs.forEach(tab => tab.classList.remove('active'));
    if (tabElement) {
        tabElement.classList.add('active');
    }
}

// PDF 모달 닫기
function closePdfModal() {
    const modal = document.getElementById('pdfModal');
    const pdfViewer = document.getElementById('pdfViewer');
    
    if (modal) {
        modal.classList.remove('active');
    }
    
    if (pdfViewer) {
        pdfViewer.src = '';
    }
}

// 상태 배지 생성
function getStatusBadge(status) {
    const badges = {
        'pending': '<span class="badge badge-warning">서류 대기</span>',
        'screening': '<span class="badge badge-info">서류 심사 중</span>',
        'interview_pending': '<span class="badge badge-primary">면접 대기</span>',
        'interview_in_progress': '<span class="badge badge-info">면접 진행 중</span>',
        'hired': '<span class="badge badge-success">최종 합격</span>',
        'rejected': '<span class="badge badge-danger">최종 불합격</span>'
    };
    
    return badges[status] || `<span class="badge badge-secondary">${status}</span>`;
}

// 페이지네이션 업데이트
function updatePagination(total, page, limit) {
    const totalPages = Math.ceil(total / limit);
    const paginationContainer = document.getElementById('pagination');
    
    if (!paginationContainer || totalPages <= 1) {
        if (paginationContainer) paginationContainer.innerHTML = '';
        return;
    }
    
    let html = '<div class="pagination">';
    
    // 이전 버튼 (항상 표시, 1페이지일 때는 비활성화)
    const prevDisabled = page <= 1 ? 'disabled' : '';
    html += `<button onclick="goToPage(${page - 1})" class="btn btn-sm btn-secondary" ${prevDisabled}>이전</button>`;
    
    // 페이지 번호
    const startPage = Math.max(1, page - 2);
    const endPage = Math.min(totalPages, page + 2);
    
    for (let i = startPage; i <= endPage; i++) {
        const activeClass = i === page ? 'btn-primary' : 'btn-secondary';
        html += `<button onclick="goToPage(${i})" class="btn btn-sm ${activeClass}">${i}</button>`;
    }
    
    // 다음 버튼 (항상 표시, 마지막 페이지일 때는 비활성화)
    const nextDisabled = page >= totalPages ? 'disabled' : '';
    html += `<button onclick="goToPage(${page + 1})" class="btn btn-sm btn-secondary" ${nextDisabled}>다음</button>`;
    
    html += '</div>';
    paginationContainer.innerHTML = html;
}

// 페이지 이동
function goToPage(page) {
    window.candidatesState.currentPage = page;
    loadCandidates();
}

// 지원자 수 업데이트
function updateCandidateCount(total) {
    const countElement = document.getElementById('candidateCount');
    if (countElement) {
        countElement.textContent = total;
    }
}

// HTML 이스케이프
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// 필터링 함수 (기존 함수 유지 - 호환성)
function filterCandidates() {
    loadCandidates();
}

// ============================================
// 이력서 업로드 모달
// ============================================

if (!window.uploadState) {
    window.uploadState = {
        selectedFiles: [],
        position: ''
    };
}

function openUploadModal() {
    const modal = document.getElementById('uploadModal');
    if (modal) {
        modal.classList.add('active');
        resetUploadModal();
    }
}

function closeUploadModal() {
    const modal = document.getElementById('uploadModal');
    if (modal) {
        modal.classList.remove('active');
        resetUploadModal();
    }
}

function resetUploadModal() {
    window.uploadState.selectedFiles = [];
    window.uploadState.position = '';
    
    const folderInput = document.getElementById('folderInput');
    const fileListContainer = document.getElementById('fileListContainer');
    const fileList = document.getElementById('fileList');
    const positionInput = document.getElementById('positionInput');
    const uploadSubmitBtn = document.getElementById('uploadSubmitBtn');
    const uploadProgress = document.getElementById('uploadProgress');
    
    if (folderInput) folderInput.value = '';
    if (fileListContainer) fileListContainer.style.display = 'none';
    if (fileList) fileList.innerHTML = '';
    if (positionInput) positionInput.value = '';
    if (uploadSubmitBtn) uploadSubmitBtn.disabled = true;
    if (uploadProgress) uploadProgress.style.display = 'none';
}

function handleFolderSelect(event) {
    const files = Array.from(event.target.files);
    const pdfFiles = files.filter(file => file.name.toLowerCase().endsWith('.pdf'));
    
    console.log('[Upload] 선택된 PDF 파일:', pdfFiles.length, '개');
    
    if (pdfFiles.length === 0) {
        alert('PDF 파일이 없습니다. PDF 파일이 포함된 폴더를 선택해주세요.');
        return;
    }
    
    window.uploadState.selectedFiles = pdfFiles;
    
    // 파일 목록 표시
    const fileListContainer = document.getElementById('fileListContainer');
    const fileList = document.getElementById('fileList');
    const fileCount = document.getElementById('fileCount');
    const uploadSubmitBtn = document.getElementById('uploadSubmitBtn');
    
    if (fileCount) fileCount.textContent = pdfFiles.length;
    
    renderFileList();
    
    if (fileListContainer) fileListContainer.style.display = 'block';
    if (uploadSubmitBtn) uploadSubmitBtn.disabled = false;
}

function removeFileFromList(index) {
    // 배열에서 해당 파일 제거
    window.uploadState.selectedFiles.splice(index, 1);
    
    console.log('[Upload] 파일 제거:', index, '남은 파일:', window.uploadState.selectedFiles.length);
    
    // UI 업데이트
    const fileList = document.getElementById('fileList');
    const fileCount = document.getElementById('fileCount');
    const fileListContainer = document.getElementById('fileListContainer');
    const uploadSubmitBtn = document.getElementById('uploadSubmitBtn');
    
    if (window.uploadState.selectedFiles.length === 0) {
        // 파일이 없으면 목록 숨김
        if (fileListContainer) fileListContainer.style.display = 'none';
        if (uploadSubmitBtn) uploadSubmitBtn.disabled = true;
    } else {
        // 파일 목록 다시 렌더링
        if (fileCount) fileCount.textContent = window.uploadState.selectedFiles.length;
        renderFileList();
    }
}

function renderFileList() {
    const fileList = document.getElementById('fileList');
    if (!fileList) return;
    
    fileList.innerHTML = window.uploadState.selectedFiles.map((file, idx) => `
        <div id="file-item-${idx}" style="display: flex; align-items: center; padding: 0.5rem; border-bottom: 1px solid #f1f5f9; gap: 0.5rem;">
            <span style="color: #64748b; font-size: 0.875rem; min-width: 30px;">${idx + 1}.</span>
            <span style="flex: 1; font-size: 0.875rem; color: #1e293b; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${file.name}</span>
            <button 
                id="file-btn-${idx}"
                onclick="removeFileFromList(${idx})" 
                class="btn btn-sm" 
                style="padding: 0.25rem 0.5rem; font-size: 0.75rem; background-color: #ef4444; color: white; border: none; min-width: 32px;"
                title="제거"
            >
                ✕
            </button>
        </div>
    `).join('');
}

function updateFileItemStatus(index, status) {
    const btn = document.getElementById(`file-btn-${index}`);
    if (!btn) return;
    
    if (status === 'loading') {
        // 로딩 중 - 회전하는 스피너 (배경 제거)
        btn.innerHTML = '<span style="display: inline-block; animation: spin 1s linear infinite; font-size: 1rem;">◐</span>';
        btn.style.backgroundColor = 'transparent';
        btn.style.color = '#3b82f6';
        btn.style.border = 'none';
        btn.disabled = true;
        btn.onclick = null;
    } else if (status === 'success') {
        // 성공 - 체크 표시
        btn.innerHTML = '✓';
        btn.style.backgroundColor = '#10b981';
        btn.style.color = 'white';
        btn.disabled = true;
        btn.onclick = null;
    } else if (status === 'error') {
        // 실패 - X 표시
        btn.innerHTML = '✗';
        btn.style.backgroundColor = '#ef4444';
        btn.style.color = 'white';
        btn.disabled = true;
        btn.onclick = null;
    }
}

async function submitUpload() {
    const positionInput = document.getElementById('positionInput');
    const position = positionInput ? positionInput.value.trim() : '';
    
    if (!position) {
        alert('지원 포지션을 입력해주세요.');
        return;
    }
    
    if (window.uploadState.selectedFiles.length === 0) {
        alert('업로드할 파일을 선택해주세요.');
        return;
    }
    
    const uploadProgress = document.getElementById('uploadProgress');
    const progressBar = document.getElementById('progressBar');
    const progressText = document.getElementById('progressText');
    const uploadSubmitBtn = document.getElementById('uploadSubmitBtn');
    
    if (uploadSubmitBtn) uploadSubmitBtn.disabled = true;
    if (uploadProgress) uploadProgress.style.display = 'block';
    
    const totalFiles = window.uploadState.selectedFiles.length;
    let successCount = 0;
    let failCount = 0;
    
    for (let i = 0; i < totalFiles; i++) {
        const file = window.uploadState.selectedFiles[i];
        
        // 현재 파일 로딩 상태로 변경
        updateFileItemStatus(i, 'loading');
        
        // 진행률 업데이트
        if (progressText) progressText.textContent = `${i + 1} / ${totalFiles}`;
        if (progressBar) progressBar.style.width = `${((i + 1) / totalFiles) * 100}%`;
        
        try {
            const formData = new FormData();
            formData.append('file', file);
            formData.append('position', position);
            
            const response = await fetch('/api/candidates/upload', {
                method: 'POST',
                body: formData
            });
            
            const result = await response.json();
            
            if (result.success) {
                successCount++;
                updateFileItemStatus(i, 'success');
                console.log(`[Upload] 성공: ${file.name}`);
            } else {
                failCount++;
                updateFileItemStatus(i, 'error');
                console.error(`[Upload] 실패: ${file.name}`, result.error);
                // 첫 번째 에러만 상세히 표시
                if (failCount === 1) {
                    console.error(`[Upload] 첫 번째 에러 상세:`, result);
                }
            }
        } catch (error) {
            failCount++;
            updateFileItemStatus(i, 'error');
            console.error(`[Upload] 오류: ${file.name}`, error);
        }
    }
    
    // 완료 메시지 UI 표시
    const uploadProgressDiv = document.getElementById('uploadProgress');
    if (uploadProgressDiv) {
        let resultColor = successCount > 0 && failCount === 0 ? '#10b981' : '#3b82f6';
        if (failCount > 0 && successCount === 0) resultColor = '#ef4444';
        
        uploadProgressDiv.innerHTML = `
            <div style="background-color: ${resultColor}; border-radius: 0.5rem; padding: 1.5rem; color: white; text-align: center;">
                <div style="font-size: 2rem; margin-bottom: 0.5rem;">${successCount > 0 && failCount === 0 ? '✓' : '📊'}</div>
                <div style="font-weight: 600; font-size: 1.125rem; margin-bottom: 0.5rem;">업로드 완료</div>
                <div style="font-size: 0.875rem; margin-bottom: 1rem;">
                    성공: ${successCount}개 / 실패: ${failCount}개
                    ${failCount > 0 ? '<br><span style="font-size: 0.75rem; opacity: 0.9;">콘솔(F12)에서 실패 정보 확인 가능</span>' : ''}
                </div>
                <button 
                    onclick="closeUploadModalAndRefresh()" 
                    class="btn" 
                    style="background-color: white; color: ${resultColor}; font-weight: 600; padding: 0.5rem 1.5rem;"
                >
                    확인
                </button>
            </div>
        `;
    }
}

function closeUploadModalAndRefresh() {
    closeUploadModal();
    loadCandidates();
}