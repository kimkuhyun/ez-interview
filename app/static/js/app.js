// SPA 방식 탭 관리 (interview.html 방식)
(function() {
    let currentTab = 'candidates';

    // 탭 전환 함수
    async function switchTab(tabName) {
        console.log(`🔄 ${tabName} 탭 로드 시작`);
        
        try {
            const res = await fetch(`/api/admin/tabs/${tabName}`);
            console.log(`📥 /api/admin/tabs/${tabName} 응답: ${res.status}`);
            
            if (!res.ok) {
                throw new Error(`탭 로드 실패: ${res.status}`);
            }
            
            const html = await res.text();
            console.log(`📄 HTML 길이: ${html.length}자`);
            
            const tabContent = document.getElementById('tab-content');
            tabContent.innerHTML = html;
            console.log("✅ HTML 삽입 완료");

            // 삽입된 스크립트 실행 (interview.html 방식)
            const scripts = tabContent.querySelectorAll("script");
            console.log(`🔍 발견된 script 태그: ${scripts.length}개`);
            
            scripts.forEach((old, idx) => {
                try {
                    console.log(`   [${idx+1}] script 재실행 중...`);
                    const s = document.createElement("script");
                    if (old.src) {
                        s.src = old.src;
                        console.log(`      - 외부 스크립트: ${old.src}`);
                    } else {
                        s.textContent = old.textContent;
                        console.log(`      - 인라인 스크립트: ${old.textContent.length}자`);
                    }
                    document.body.appendChild(s);
                    old.remove();
                    console.log(`      ✅ 완료`);
                } catch (e) {
                    console.error(`      ❌ 에러: ${e.message}`);
                }
            });

            // 탭 활성화 상태 업데이트
            document.querySelectorAll('.tab-btn').forEach(btn => {
                if (btn.dataset.tab === tabName) {
                    btn.classList.remove('border-transparent', 'text-slate-500');
                    btn.classList.add('border-blue-600', 'text-blue-700', 'bg-blue-50/50');
                } else {
                    btn.classList.remove('border-blue-600', 'text-blue-700', 'bg-blue-50/50');
                    btn.classList.add('border-transparent', 'text-slate-500');
                }
            });

            currentTab = tabName;

            // URL 업데이트 (페이지 새로고침 없이)
            history.pushState({ tab: tabName }, '', `/admin/${tabName}`);
            
            console.log(`✅ ${tabName} 탭 로드 완료`);

        } catch (error) {
            console.error('❌ 탭 로드 에러:', error);
            alert('❌ 탭 로드 실패: ' + error.message);
        }
    }

    // 탭 버튼 이벤트 리스너
    document.addEventListener('DOMContentLoaded', () => {
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                switchTab(btn.dataset.tab);
            });
        });

        // 뒤로가기/앞으로가기 지원
        window.addEventListener('popstate', (event) => {
            if (event.state && event.state.tab) {
                switchTab(event.state.tab);
            }
        });

        // 초기 탭 로드
        switchTab(currentTab);
    });
})();
