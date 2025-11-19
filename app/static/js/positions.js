let positions = [];
let uploadedFile = null;

async function loadPositions() {
  const res = await fetch('/api/positions');
  const data = await res.json();
  positions = data.positions || [];
  
  const tbody = document.getElementById('positionList');
  if (positions.length === 0) {
    tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:#94a3b8;padding:40px;">등록된 포지션이 없습니다.</td></tr>';
    return;
  }
  
  tbody.innerHTML = positions.map(p => `
    <tr>
      <td>
        <div class="position-name-cell">
          <strong>${p.name}</strong>
          <div class="delete-icon" onclick="deletePosition('${p.id}', event)">×</div>
        </div>
      </td>
      <td class="jd-file-cell" onclick="editJdFile('${p.id}')">
        <span class="edit-hint">클릭하여 파일 변경</span>
        ${p.jd_file ? p.jd_file.split('/').pop().split('\\').pop() : '-'}
      </td>
      <td>
        <span class="keyword-badge ${(p.keywords && p.keywords.length > 0) ? 'set' : 'not-set'}">
          ${(p.keywords && p.keywords.length > 0) ? p.keywords.length + '개 설정됨' : '미설정'}
        </span>
      </td>
      <td>
        <button class="btn-action" onclick="openKeywordModal('${p.id}')">
          키워드 설정 및 심사 시작
        </button>
      </td>
    </tr>
  `).join('');
}

function setupDragDrop() {
  const area = document.getElementById('fileUploadArea');
  const input = document.getElementById('jdFileInput');
  const placeholder = document.getElementById('uploadPlaceholder');
  const filename = document.getElementById('uploadFilename');
  
  area.onclick = () => input.click();
  
  ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(e => {
    area.addEventListener(e, evt => {
      evt.preventDefault();
      evt.stopPropagation();
    });
  });
  
  ['dragenter', 'dragover'].forEach(e => {
    area.addEventListener(e, () => area.classList.add('dragover'));
  });
  
  ['dragleave', 'drop'].forEach(e => {
    area.addEventListener(e, () => area.classList.remove('dragover'));
  });
  
  area.addEventListener('drop', e => {
    const files = e.dataTransfer.files;
    if (files.length) handleFile(files[0]);
  });
  
  input.addEventListener('change', e => {
    if (e.target.files.length) handleFile(e.target.files[0]);
  });
  
  function handleFile(file) {
    uploadedFile = file;
    placeholder.style.display = 'none';
    filename.style.display = 'block';
    filename.textContent = '✓ ' + file.name;
  }
}

function openModal() {
  document.getElementById('modal').classList.add('active');
  setupDragDrop();
}

function closeModal() {
  document.getElementById('modal').classList.remove('active');
  document.getElementById('positionName').value = '';
  document.getElementById('uploadPlaceholder').style.display = 'block';
  document.getElementById('uploadFilename').style.display = 'none';
  uploadedFile = null;
}

async function submitPosition() {
  const name = document.getElementById('positionName').value.trim();
  if (!name) return alert('포지션명을 입력하세요.');
  if (!uploadedFile) return alert('JD 파일을 업로드하세요.');
  
  const formData = new FormData();
  formData.append('name', name);
  formData.append('file', uploadedFile);
  
  const res = await fetch('/api/positions', {
    method: 'POST',
    body: formData
  });
  
  if (res.ok) {
    const data = await res.json();
    alert(`'${name}' 포지션이 등록되었습니다.`);
    closeModal();
    loadPositions();
  } else {
    alert('등록 실패');
  }
}

async function deletePosition(id, event) {
  event.stopPropagation();
  if (!confirm('정말 삭제하시겠습니까?')) return;
  
  const res = await fetch(`/api/positions/${id}`, {method: 'DELETE'});
  if (res.ok) {
    alert('삭제되었습니다.');
    loadPositions();
  } else {
    alert('삭제 실패');
  }
}

function editJdFile(id) {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.pdf,.docx,.txt';
  input.onchange = async e => {
    const file = e.target.files[0];
    if (!file) return;
    
    const formData = new FormData();
    formData.append('file', file);
    
    const res = await fetch(`/api/positions/${id}/jd`, {
      method: 'PUT',
      body: formData
    });
    
    if (res.ok) {
      alert('JD 파일이 변경되었습니다.');
      loadPositions();
    } else {
      alert('변경 실패');
    }
  };
  input.click();
}

function openKeywordModal(id) {
  // SPA 방식으로 키워드 매칭 화면 로드
  loadKeywordMatchView(id);
}

async function loadKeywordMatchView(positionId) {
  const rightPanel = document.querySelector('#right-panel') || document.querySelector('.tab-content');
  
  if (!rightPanel) {
    console.error('Right panel not found');
    return;
  }
  
  try {
    const res = await fetch(`/panel/keyword-match?position_id=${positionId}`);
    const html = await res.text();
    
    rightPanel.innerHTML = html;
    
    // ✅ position ID를 data attribute로 저장
    const container = rightPanel.querySelector('.keyword-match-container');
    if (container) {
      container.setAttribute('data-position-id', positionId);
    }
    
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
  } catch (error) {
    console.error('Failed to load keyword match view:', error);
  }
}

loadPositions();

// ✅ 탭이 활성화될 때마다 자동 새로고침
if (typeof MutationObserver !== 'undefined') {
  const observer = new MutationObserver(() => {
    const positionsTab = document.querySelector('[data-tab="positions"]');
    if (positionsTab && positionsTab.classList.contains('active')) {
      loadPositions();
    }
  });
  
  const tabContainer = document.querySelector('.tabs');
  if (tabContainer) {
    observer.observe(tabContainer, { 
      attributes: true, 
      subtree: true, 
      attributeFilter: ['class'] 
    });
  }
}