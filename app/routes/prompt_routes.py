from flask import Blueprint, render_template, request, jsonify
import os
import shutil
from datetime import datetime

prompt_bp = Blueprint('prompt', __name__)

# 원본 프롬프트 디렉토리 (읽기 전용)
ORIGINAL_PROMPT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'agents', 'report_prompt')
# 작업 디렉토리 (uploads/report_prompt - 수정 가능)
WORK_PROMPT_DIR = os.path.join('uploads', 'report_prompt')
# 백업 디렉토리
BACKUP_DIR = os.path.join(WORK_PROMPT_DIR, 'backups')

# 디렉토리 생성
os.makedirs(WORK_PROMPT_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

PROMPT_FILES = {
    'comp_agent': 'comp_agent.md',
    'quality_agent': 'quality_agent.md',
    'summary_agent': 'summary_agent.md',
    'final_agent': 'final_agent.md',
    'optimized_agent': 'optimized_agent.md',
    'queryPlan_agent': 'queryPlan_agent.md',
    'global_rules': 'global_rules.md'
}

def init_work_prompt_files():
    """원본 프롬프트를 작업 디렉토리로 복사 (없을 때만)"""
    for filename in PROMPT_FILES.values():
        original_path = os.path.join(ORIGINAL_PROMPT_DIR, filename)
        work_path = os.path.join(WORK_PROMPT_DIR, filename)
        
        # 작업 파일이 없으면 원본 복사
        if not os.path.exists(work_path) and os.path.exists(original_path):
            shutil.copy2(original_path, work_path)

# 초기화
init_work_prompt_files()

@prompt_bp.route('/prompts')
def prompts_page():
    """프롬프트 편집 페이지"""
    file_name = request.args.get('file', '')
    return render_template('admin/prompts.html', initial_file=file_name)

@prompt_bp.route('/api/prompts')
def list_prompts():
    """프롬프트 파일 목록"""
    return jsonify(list(PROMPT_FILES.keys()))

@prompt_bp.route('/api/prompts/<name>')
def get_prompt(name):
    """프롬프트 내용 조회 (작업 디렉토리에서)"""
    if name not in PROMPT_FILES:
        return jsonify({'error': 'Prompt not found'}), 404
    
    file_path = os.path.join(WORK_PROMPT_DIR, PROMPT_FILES[name])
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return jsonify({'name': name, 'content': content})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@prompt_bp.route('/api/prompts/<name>', methods=['POST'])
def update_prompt(name):
    """프롬프트 내용 수정 (작업 디렉토리에서, 사용자 지정 백업 파일명)"""
    if name not in PROMPT_FILES:
        return jsonify({'error': 'Prompt not found'}), 404
    
    content = request.json.get('content', '')
    custom_backup_name = request.json.get('backup_name', '').strip()
    file_path = os.path.join(WORK_PROMPT_DIR, PROMPT_FILES[name])
    
    # 백업 파일명 결정
    if custom_backup_name:
        # 사용자 지정 이름 사용
        backup_filename = custom_backup_name if custom_backup_name.endswith('.backup') else f"{custom_backup_name}.backup"
    else:
        # 기본: 타임스탬프 백업 파일명
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_filename = f"{PROMPT_FILES[name].replace('.md', '')}_{timestamp}.md.backup"
    
    backup_path = os.path.join(BACKUP_DIR, backup_filename)
    
    # 중복 파일명 체크
    if os.path.exists(backup_path):
        return jsonify({'error': f'백업 파일 "{backup_filename}"이(가) 이미 존재합니다'}), 400
    
    try:
        # 새 내용 저장
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        # 저장된 내용을 백업
        with open(backup_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        return jsonify({'success': True, 'backup': backup_filename})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@prompt_bp.route('/api/prompts/<name>/restore', methods=['POST'])
def restore_original(name):
    """원본 프롬프트로 복원"""
    if name not in PROMPT_FILES:
        return jsonify({'error': 'Prompt not found'}), 404
    
    original_path = os.path.join(ORIGINAL_PROMPT_DIR, PROMPT_FILES[name])
    work_path = os.path.join(WORK_PROMPT_DIR, PROMPT_FILES[name])
    
    try:
        if os.path.exists(original_path):
            shutil.copy2(original_path, work_path)
            return jsonify({'success': True, 'message': '원본으로 복원되었습니다'})
        else:
            return jsonify({'error': '원본 파일을 찾을 수 없습니다'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@prompt_bp.route('/api/prompts/<name>/backups')
def list_backups(name):
    """백업 목록 조회 - 모든 백업 파일 표시"""
    if name not in PROMPT_FILES:
        return jsonify({'error': 'Prompt not found'}), 404
    
    try:
        # 모든 .backup 파일 표시 (사용자가 이름 변경한 경우도 포함)
        backups = [f for f in os.listdir(BACKUP_DIR) if f.endswith('.backup')]
        backups.sort(reverse=True)  # 최신 순
        return jsonify({'backups': backups})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@prompt_bp.route('/api/prompts/<name>/backups/<backup_file>')
def get_backup_content(name, backup_file):
    """백업 파일 내용 조회"""
    if name not in PROMPT_FILES:
        return jsonify({'error': 'Prompt not found'}), 404
    
    backup_path = os.path.join(BACKUP_DIR, backup_file)
    
    try:
        if os.path.exists(backup_path):
            with open(backup_path, 'r', encoding='utf-8') as f:
                content = f.read()
            return jsonify({'content': content, 'filename': backup_file})
        else:
            return jsonify({'error': '백업 파일을 찾을 수 없습니다'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@prompt_bp.route('/api/prompts/<name>/backups/<backup_file>', methods=['DELETE'])
def delete_backup(name, backup_file):
    """백업 파일 삭제"""
    if name not in PROMPT_FILES:
        return jsonify({'error': 'Prompt not found'}), 404
    
    backup_path = os.path.join(BACKUP_DIR, backup_file)
    
    try:
        if os.path.exists(backup_path):
            os.remove(backup_path)
            return jsonify({'success': True, 'message': '백업 파일이 삭제되었습니다'})
        else:
            return jsonify({'error': '백업 파일을 찾을 수 없습니다'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@prompt_bp.route('/api/prompts/<name>/backups/<backup_file>/rename', methods=['POST'])
def rename_backup(name, backup_file):
    """백업 파일 이름 변경"""
    if name not in PROMPT_FILES:
        return jsonify({'error': 'Prompt not found'}), 404
    
    new_name = request.json.get('new_name', '').strip()
    if not new_name:
        return jsonify({'error': '새 파일명을 입력하세요'}), 400
    
    # .backup 확장자 강제
    if not new_name.endswith('.backup'):
        new_name += '.backup'
    
    old_path = os.path.join(BACKUP_DIR, backup_file)
    new_path = os.path.join(BACKUP_DIR, new_name)
    
    try:
        if not os.path.exists(old_path):
            return jsonify({'error': '백업 파일을 찾을 수 없습니다'}), 404
        
        if os.path.exists(new_path):
            return jsonify({'error': '같은 이름의 파일이 이미 존재합니다'}), 400
        
        os.rename(old_path, new_path)
        return jsonify({'success': True, 'new_name': new_name, 'message': '파일명이 변경되었습니다'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
