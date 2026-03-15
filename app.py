import os
import dashscope
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from dashscope import Generation
import sqlite3
import subprocess
import signal
import time
import re
import tempfile
import atexit
import datetime


def read_text_auto(path, offset=0):
    """统一解码函数（BOM 识别 + 严格回退）
    
    Args:
        path: 文件路径
        offset: 读取偏移量
        
    Returns:
        (text, new_offset): (读取的文本, 新的偏移量)
    """
    with open(path, 'rb') as f:
        raw = f.read()
    
    if raw.startswith(b'\xef\xbb\xbf'):
        text = raw.decode('utf-8-sig')
    elif raw.startswith(b'\xff\xfe') or raw.startswith(b'\xfe\xff'):
        text = raw.decode('utf-16')
    else:
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            text = raw.decode('gbk', errors='replace')
    
    if offset:
        return text[offset:], len(text)
    return text, len(text)


# 初始化Flask应用
app = Flask(__name__)
app.config['SECRET_KEY'] = 'simple-key'

# 启用 CORS（允许跨域访问）
CORS(app, resources={
    r"/*": {
        "origins": "*",
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"]
    }
})

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# 配置API密钥
dashscope.api_key = os.getenv('qinwen_api_key')

# 数据库路径
DB_PATH = os.path.join(os.path.dirname(__file__), 'ai_code_manager.db')

# 存储正在运行的代码执行进程
running_processes = {}

# 存储 Aider 上下文选择（key: (client_id, project_id) -> {file_id, relative_path, abs_path, updated_at}）
aider_contexts = {}


def modify_app_run(match_str):
    """修改app.run()调用，保留用户指定的host和port，只添加debug=False和use_reloader=False"""
    # 解析现有的参数
    params = {}
    
    # 提取host参数
    host_match = re.search(r'host\s*=\s*["\']([^"\']+)["\']', match_str)
    if host_match:
        params['host'] = host_match.group(1)
    else:
        params['host'] = '0.0.0.0'
    
    # 提取port参数
    port_match = re.search(r'port\s*=\s*(\d+)', match_str)
    if port_match:
        params['port'] = port_match.group(1)
    else:
        params['port'] = '5000'
    
    # 构建新的app.run()调用
    return f'app.run(debug=False, use_reloader=False, host="{params["host"]}", port={params["port"]})'


def get_db_connection():
    """获取数据库连接"""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def normalize_path(p):
    """规范化路径：abspath + normpath"""
    return os.path.abspath(os.path.normpath(p))


def is_under_root(path, root):
    """校验路径是否在根目录内，使用 commonpath"""
    path = normalize_path(path)
    root = normalize_path(root)
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def get_project_root(project_id):
    """从 DB 获取项目 root_path"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT root_path FROM projects WHERE id = ?', (project_id,))
        row = cursor.fetchone()
        return row['root_path'] if row else None
    finally:
        conn.close()


def get_file_by_id(file_id):
    """从 files 表获取文件记录"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM files WHERE id = ?', (file_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_client_id(data):
    """读取 client_id，默认为 'default'"""
    return data.get('client_id', 'default')


def sync_project_files(project_id, root_path):
    """
    同步项目文件到数据库
    - 扫描磁盘真实文件
    - upsert 到 files 表
    - 删除 DB 中已不存在的磁盘文件记录
    返回: {"ok": True/False, "file_info_list": [...], "real_count": n, "deleted_count": m, "error": "..."}
    """
    import os as os_module
    import time
    
    max_retries = 3
    for attempt in range(max_retries):
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            # 扫描目录，获取所有真实文件
            real_files = set()
            for dirpath, dirnames, filenames in os_module.walk(root_path):
                # 跳过隐藏目录和 __pycache__
                dirnames[:] = [d for d in dirnames if not d.startswith('.') and d != '__pycache__']
                
                for filename in filenames:
                    # 跳过隐藏文件
                    if filename.startswith('.'):
                        continue
                    
                    abs_path = os_module.path.join(dirpath, filename)
                    relative_path = os_module.path.relpath(abs_path, root_path)
                    file_type = filename.split('.')[-1].lower() if '.' in filename else ''
                    file_size = os_module.path.getsize(abs_path)
                    
                    real_files.add(abs_path)
                    
                    # 检查文件是否已存在
                    cursor.execute('SELECT id FROM files WHERE file_path = ?', (abs_path,))
                    existing_file = cursor.fetchone()
                    
                    if existing_file:
                        # 更新现有文件
                        cursor.execute('''
                            UPDATE files 
                            SET filename = ?, relative_path = ?, file_type = ?, file_size = ?
                            WHERE file_path = ?
                        ''', (filename, relative_path, file_type, file_size, abs_path))
                    else:
                        # 插入新文件
                        cursor.execute('''
                            INSERT INTO files (project_id, filename, file_path, relative_path, file_type, file_size)
                            VALUES (?, ?, ?, ?, ?, ?)
                        ''', (project_id, filename, abs_path, relative_path, file_type, file_size))
            
            # 删除 DB 里存在但磁盘不存在的记录
            cursor.execute('SELECT file_path FROM files WHERE project_id = ?', (project_id,))
            db_files = {row[0] for row in cursor.fetchall()}
            files_to_delete = db_files - real_files
            
            for file_path in files_to_delete:
                cursor.execute('DELETE FROM files WHERE file_path = ?', (file_path,))
            
            conn.commit()
            
            # 构建文件信息列表
            file_info_list = []
            cursor.execute('SELECT id, file_path, relative_path FROM files WHERE project_id = ?', (project_id,))
            for row in cursor.fetchall():
                file_info_list.append({
                    'file_id': row[0],
                    'file_path': row[1],
                    'relative_path': row[2]
                })
            
            return {
                'ok': True,
                'file_info_list': file_info_list,
                'real_count': len(real_files),
                'deleted_count': len(files_to_delete)
            }
            
        except sqlite3.OperationalError as e:
            if conn:
                try:
                    conn.rollback()
                except:
                    pass
                try:
                    conn.close()
                except:
                    pass
            
            error_msg = str(e)
            if 'database is locked' in error_msg and attempt < max_retries - 1:
                sleep_time = 0.2 * (attempt + 1)
                print(f"[SYNC] 数据库锁定，等待重试 (attempt {attempt + 1}/{max_retries}, sleep {sleep_time}s): project_id={project_id}, root_path={root_path}", file=sys.stderr)
                time.sleep(sleep_time)
                continue
            else:
                print(f"[SYNC] 文件同步失败: project_id={project_id}, root_path={root_path}, error={error_msg}", file=sys.stderr)
                return {'ok': False, 'error': error_msg}
                
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except:
                    pass
            error_msg = str(e)
            print(f"[SYNC] 文件同步失败: project_id={project_id}, root_path={root_path}, error={error_msg}", file=sys.stderr)
            return {'ok': False, 'error': error_msg}
        finally:
            if conn:
                try:
                    conn.close()
                except:
                    pass
    
    return {'ok': False, 'error': '同步失败：超过最大重试次数'}


def init_database():
    """初始化数据库表结构"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # 创建项目表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                root_path TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # 创建文件表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                file_path TEXT NOT NULL,
                relative_path TEXT,
                file_type TEXT,
                file_size INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
            )
        ''')
        # 创建索引
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_project_id ON files(project_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_file_type ON files(file_type)')
        
        # 清理孤儿记录（引用不存在的project_id的文件记录）
        cursor.execute('DELETE FROM files WHERE project_id NOT IN (SELECT id FROM projects)')
        orphan_count = cursor.rowcount
        if orphan_count > 0:
            print(f"已清理 {orphan_count} 条孤儿记录")
        
        conn.commit()
        print("数据库初始化成功！")
    except Exception as e:
        print(f"数据库初始化失败：{e}")
    finally:
        conn.close()

# 存储用户生成状态和历史记录
user_generations = {}
user_histories = {}

# 初始化数据库
init_database()

# 创建默认项目目录
default_projects_dir = os.path.join(os.path.dirname(__file__), 'projects')
os.makedirs(default_projects_dir, exist_ok=True)
print(f"项目目录：{default_projects_dir}")

@app.route('/')
def index():
    """主页路由"""
    return render_template('index.html')

@socketio.on('ask_ai')
def handle_ask_ai(data):
    """处理AI对话请求"""
    user_id = request.sid
    user_input = data.get('message')

    if not user_input:
        emit('ai_response', {'content': '请输入问题'})
        return

    # 停止之前的生成
    if user_id in user_generations:
        user_generations[user_id]['stop'] = True

    # 初始化新的生成状态
    user_generations[user_id] = {'stop': False}

    # 初始化用户历史记录
    if user_id not in user_histories:
        user_histories[user_id] = []

    # 构建消息上下文
    messages = [{'role': 'system', 'content': "You are a helpful assistant."}]
    messages.extend(user_histories[user_id])
    messages.append({'role': 'user', 'content': user_input})

    # 调用AI模型生成响应
    responses = Generation.call(
        model="qwen-turbo",
        messages=messages,
        result_format='message',
        stream=True,
        incremental_output=True
    )

    full_answer = ""

    # 处理流式响应
    for res in responses:
        if user_id in user_generations and user_generations[user_id]['stop']:
            break

        if res.status_code == 200:
            content = res.output.choices[0].message.content
            full_answer += content
            safe_content = content.replace("\n", "\\n").replace("\r", "\\r")
            emit('ai_response', {'content': safe_content})
            socketio.sleep(0.05)
        else:
            emit('ai_response', {'content': '出错了'})
            break

    # 保存对话历史
    if user_id in user_generations and not user_generations[user_id]['stop']:
        user_histories[user_id].append({'role': 'user', 'content': user_input})
        user_histories[user_id].append({'role': 'assistant', 'content': full_answer})

        # 限制历史记录长度
        if len(user_histories[user_id]) > 10:
            user_histories[user_id] = user_histories[user_id][-10:]

    # 发送完成标记
    emit('ai_response', {'content': '[DONE]'})

@socketio.on('stop_generation')
def handle_stop_generation():
    """停止AI生成"""
    user_id = request.sid
    if user_id in user_generations:
        user_generations[user_id]['stop'] = True

@socketio.on('disconnect')
def handle_disconnect():
    """处理用户断开连接"""
    user_id = request.sid
    if user_id in user_generations:
        del user_generations[user_id]

@app.route('/api/projects', methods=['GET'])
def get_projects():
    """获取所有项目列表"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM projects ORDER BY updated_at DESC')
        projects = [dict(row) for row in cursor.fetchall()]
        return jsonify(projects)
    finally:
        conn.close()

@app.route('/api/projects', methods=['POST'])
def create_project():
    """创建新项目"""
    data = request.json
    project_name = data.get('name')
    root_path = data.get('root_path')
    description = data.get('description', '')
    
    if not project_name or not root_path:
        return jsonify({'error': '项目名称和路径不能为空'}), 400
    
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO projects (name, root_path, description) VALUES (?, ?, ?)',
            (project_name, root_path, description)
        )
        conn.commit()
        project_id = cursor.lastrowid
        return jsonify({'id': project_id, 'message': '项目创建成功'}), 201
    except sqlite3.IntegrityError:
        return jsonify({'error': '项目名称已存在'}), 400
    finally:
        conn.close()

@app.route('/api/projects/<int:project_id>', methods=['DELETE'])
def delete_project(project_id):
    """删除项目"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # 检查项目是否存在
        cursor.execute('SELECT * FROM projects WHERE id = ?', (project_id,))
        project = cursor.fetchone()
        
        if not project:
            return jsonify({'error': '项目不存在'}), 404
        
        # 获取项目中的所有文件
        cursor.execute('SELECT file_path FROM files WHERE project_id = ?', (project_id,))
        files = cursor.fetchall()
        
        # 删除本地文件
        for file in files:
            file_path = file[0]
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception as e:
                    print(f"删除本地文件失败: {e}")
        
        # 删除项目目录
        project_path = project['root_path']
        if os.path.exists(project_path):
            try:
                os.rmdir(project_path)
            except Exception as e:
                print(f"删除项目目录失败: {e}")
        
        # 删除项目（级联删除会删除关联的文件记录）
        cursor.execute('DELETE FROM projects WHERE id = ?', (project_id,))
        conn.commit()
        
        return jsonify({'message': '项目已删除'})
    finally:
        conn.close()

@app.route('/api/projects/<int:project_id>/files', methods=['GET'])
def get_project_files(project_id):
    """获取项目文件列表"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT * FROM files WHERE project_id = ? ORDER BY filename',
            (project_id,)
        )
        files = [dict(row) for row in cursor.fetchall()]
        return jsonify(files)
    finally:
        conn.close()

@app.route('/api/files', methods=['POST'])
def save_file():
    """保存文件（新建或更新）"""
    data = request.json
    project_id = data.get('project_id')
    file_id = data.get('file_id')
    filename = data.get('filename')
    file_path = data.get('file_path')
    content = data.get('content', '')
    file_type = data.get('file_type', '')
    relative_path = data.get('relative_path', '')
    
    # 验证必要参数
    if not project_id:
        return jsonify({'error': '缺少必要参数：project_id'}), 400
    if not filename:
        return jsonify({'error': '缺少必要参数：filename'}), 400
    
    # 验证参数类型
    try:
        project_id = int(project_id)
        if file_id:
            file_id = int(file_id)
    except ValueError:
        return jsonify({'error': '参数类型错误：project_id/file_id 必须是整数'}), 400
    
    conn = get_db_connection()
    try:
        # 验证项目是否存在
        cursor = conn.cursor()
        cursor.execute('SELECT id, root_path FROM projects WHERE id = ?', (project_id,))
        project = cursor.fetchone()
        
        if not project:
            return jsonify({'error': '项目不存在'}), 404
        
        root_path = project['root_path']
        
        # 如果提供了 file_id，按 file_id 更新
        if file_id:
            cursor.execute(
                'SELECT id, file_path, relative_path, filename FROM files WHERE id = ? AND project_id = ?',
                (file_id, project_id)
            )
            existing_file = cursor.fetchone()
            
            if not existing_file:
                return jsonify({'error': '文件不存在'}), 404
            
            # 使用数据库中的路径
            file_path = existing_file['file_path']
            if not relative_path:
                relative_path = existing_file['relative_path']
            if not filename:
                filename = existing_file['filename']
        else:
            # 新建文件：如果没有提供文件路径，自动生成
            if not file_path:
                if relative_path:
                    file_path = os.path.join(root_path, relative_path)
                else:
                    file_path = os.path.join(root_path, filename)
                    relative_path = filename
        
        # 确保目录存在
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        # 写入文件内容
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        # 计算文件大小
        file_size = len(content.encode('utf-8'))
        
        # 规范化路径
        abs_path = os.path.abspath(file_path)
        filename = os.path.basename(abs_path)
        relative_path = os.path.relpath(abs_path, root_path)
        
        cursor = conn.cursor()
        
        if file_id:
            # 更新现有文件（按 file_id）
            cursor.execute(
                '''UPDATE files 
                   SET filename = ?, file_path = ?, relative_path = ?, file_type = ?, file_size = ? 
                   WHERE id = ?''',
                (filename, abs_path, relative_path, file_type, file_size, file_id)
            )
            message = '文件更新成功'
        else:
            # 检查文件是否已存在（按 file_path）
            cursor.execute(
                'SELECT id FROM files WHERE file_path = ?',
                (abs_path,)
            )
            existing_file = cursor.fetchone()
            
            if existing_file:
                # 更新现有文件
                cursor.execute(
                    '''UPDATE files 
                       SET filename = ?, relative_path = ?, file_type = ?, file_size = ? 
                       WHERE file_path = ?''',
                    (filename, relative_path, file_type, file_size, abs_path)
                )
                file_id = existing_file['id']
                message = '文件更新成功'
            else:
                # 创建新文件记录
                cursor.execute(
                    '''INSERT INTO files (project_id, filename, file_path, relative_path, file_type, file_size)
                       VALUES (?, ?, ?, ?, ?, ?)''',
                    (project_id, filename, abs_path, relative_path, file_type, file_size)
                )
                conn.commit()
                file_id = cursor.lastrowid
                message = '文件保存成功'
        
        conn.commit()
        return jsonify({
            'id': file_id,
            'message': message,
            'file_path': abs_path,
            'relative_path': relative_path
        })
    except Exception as e:
        conn.rollback()
        return jsonify({'error': f'保存失败：{str(e)}'}), 500
    finally:
        conn.close()

@app.route('/api/files/<int:file_id>', methods=['GET'])
def get_file_content(file_id):
    """获取文件内容"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM files WHERE id = ?', (file_id,))
        file_info = cursor.fetchone()
        
        if not file_info:
            return jsonify({'error': '文件不存在'}), 404
        
        file_info = dict(file_info)
        
        try:
            # 读取文件内容
            with open(file_info['file_path'], 'r', encoding='utf-8') as f:
                content = f.read()
            file_info['content'] = content
            return jsonify(file_info)
        except FileNotFoundError:
            return jsonify({'error': '本地文件不存在'}), 404
    finally:
        conn.close()

@app.route('/api/files/<int:file_id>', methods=['DELETE'])
def delete_file(file_id):
    """删除文件"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM files WHERE id = ?', (file_id,))
        file_info = cursor.fetchone()
        
        if not file_info:
            return jsonify({'error': '文件不存在'}), 404
        
        # 删除本地文件
        file_path = file_info['file_path']
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception as e:
                print(f"删除本地文件失败: {e}")
        
        # 从数据库中删除文件记录
        cursor.execute('DELETE FROM files WHERE id = ?', (file_id,))
        conn.commit()
        
        return jsonify({'message': '文件已删除'})
    finally:
        conn.close()

def run_aider_mode(execution_id, project_id, prompt, target_relative_path, source, client_id='default', use_selected_context=False):
    """运行 Aider 模式（AI 代码编辑）"""
    import os as os_module
    import sys
    
    # 获取项目信息
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('SELECT root_path FROM projects WHERE id = ?', (project_id,))
        project = cursor.fetchone()
        
        if not project:
            return jsonify({'error': f'项目不存在: project_id={project_id}'}), 400
        
        root_path = project[0]
        
        # 校验项目 ID
        if not project_id:
            return jsonify({'error': 'project_id 不能为空'}), 400
        
        # 校验提示信息
        if not prompt or not prompt.strip():
            return jsonify({'error': 'prompt 不能为空'}), 400
        
        # 计算目标文件的绝对路径
        if use_selected_context:
            # 使用选中的上下文
            if target_relative_path:
                # 使用 commonpath 校验路径是否在项目根目录内
                target_abs = os_module.path.abspath(os_module.path.join(root_path, target_relative_path))
                
                # 校验路径是否在项目根目录内（防止路径逃逸）
                if not os_module.path.commonpath([root_path, target_abs]) == root_path:
                    return jsonify({'error': f'目标路径必须在项目根目录内: {target_relative_path}'}), 400
                
                # 校验路径是否存在
                if not os_module.path.exists(target_abs):
                    return jsonify({'error': f'目标文件不存在: {target_relative_path}'}), 400
                
                # 校验是否为文件
                if not os_module.path.isfile(target_abs):
                    return jsonify({'error': f'目标路径必须是文件: {target_relative_path}'}), 400
            else:
                # 没有指定目标文件，从上下文中获取
                context_key = (client_id, project_id)
                context = aider_contexts.get(context_key)
                if context:
                    target_abs = context['abs_path']
                    # 校验路径是否存在
                    if not os_module.path.exists(target_abs):
                        return jsonify({'error': f'上下文文件不存在: {context["relative_path"]}'}), 400
                else:
                    # 没有上下文，Aider 可以编辑项目内任意文件
                    target_abs = None
        else:
            # 不使用上下文，直接按"无绑定模式"运行
            target_abs = None
        
        # 创建临时文件来存储输出
        temp_output_path = os_module.path.join(tempfile.gettempdir(), f'aider_output_{execution_id}.txt')
        
        # 检查 API Key（兼容 OPENAI_API_KEY / DEEPSEEK_API_KEY）
        api_key = (
            os_module.environ.get('OPENAI_API_KEY')
            or os_module.environ.get('DEEPSEEK_API_KEY')
            or ''
        ).strip()
        
        if not api_key:
            return jsonify({'error': '未设置 OPENAI_API_KEY 或 DEEPSEEK_API_KEY 环境变量'}), 400
        
        # 清理环境变量
        clean_env = os_module.environ.copy()
        env_vars_to_remove = ['WERKZEUG_RUN_MAIN', 'WERKZEUG_SERVER_FD', 'FLASK_RUN_FROM_CLI']
        for var in env_vars_to_remove:
            clean_env.pop(var, None)
        
        # 强制统一到 OpenAI 兼容 DeepSeek
        clean_env['OPENAI_API_KEY'] = api_key
        clean_env['OPENAI_API_BASE'] = 'https://api.deepseek.com'
        clean_env['PYTHONUNBUFFERED'] = '1'  # 确保 Python 输出不被缓冲
        clean_env['PYTHONIOENCODING'] = 'utf-8'  # 解决 Windows 终端 GBK 编码问题
        clean_env.pop('AIDER_MODEL', None)  # 避免被旧环境变量覆盖成 deepseek-chat
        
        # 使用子进程执行 Aider
        try:
            # 构建 aider 命令
            aider_cmd = [
                sys.executable, '-m', 'aider',
                '--message', prompt,
                '--yes-always',  # 自动确认所有提示
                '--no-gitignore',  # 不使用 .gitignore
                '--exit',  # 完成后自动退出
                '--no-fancy-input',
                '--no-pretty',
                '--no-show-model-warnings',
                '--no-git',
                '--model', 'openai/deepseek-chat',
                '--openai-api-base', 'https://api.deepseek.com',
                '--openai-api-key', api_key,
            ]
            
            # 只有当 target_abs 存在时，才添加 --file 参数
            if target_abs:
                aider_cmd.extend(['--file', target_abs])
            
            # 打开临时文件用于写入输出（使用行缓冲，读取时多编码兼容）
            temp_output_file = open(temp_output_path, 'w', encoding='utf-8-sig', buffering=1)
            
            # 设置工作目录（Windows 下使用 CREATE_NEW_PROCESS_GROUP）
            if os_module.name == 'nt':
                process = subprocess.Popen(
                    aider_cmd,
                    cwd=root_path,
                    stdout=temp_output_file,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                    env=clean_env,
                    bufsize=1  # 行缓冲
                )
            else:
                process = subprocess.Popen(
                    aider_cmd,
                    cwd=root_path,
                    stdout=temp_output_file,
                    stderr=subprocess.STDOUT,
                    env=clean_env,
                    bufsize=1  # 行缓冲
                )
            
            # 关闭文件句柄（子进程会保持打开）
            temp_output_file.close()
            
            # 存储进程引用和临时文件路径
            running_processes[execution_id] = {
                'process': process,
                'temp_output_path': temp_output_path,
                'temp_code_path': None,
                'temp_user_code_path': None,  # Aider 模式不使用 temp_user_code_path（避免删除真实项目文件）
                'task_type': 'aider',
                'is_web_service': False,
                'start_time': time.time(),
                'run_mode': 'aider',
                'cwd': root_path,
                'script_path': target_abs if target_relative_path else None,
                'project_id': project_id,
                'source': source,
                'prompt': prompt,
                'target_relative_path': target_relative_path
            }
            
            # 启动后台线程：进程结束即同步
            import threading
            def sync_after_aider_finish():
                """Aider 进程结束后自动同步文件"""
                try:
                    process.wait()
                    print(f"[DEBUG] Aider 进程结束，开始同步项目 {project_id} 的文件")
                    result = sync_project_files(project_id, root_path)
                    if result['ok']:
                        print(f"[DEBUG] Aider 后同步完成: 新增/更新 {result['real_count']} 个文件，删除 {result['deleted_count']} 个文件")
                    else:
                        print(f"[DEBUG] Aider 后同步失败: {result.get('error', '未知错误')}", file=sys.stderr)
                except Exception as e:
                    print(f"[DEBUG] Aider 后同步线程异常: {str(e)}", file=sys.stderr)
            
            sync_thread = threading.Thread(target=sync_after_aider_finish, daemon=True)
            sync_thread.start()
            
            # 立即返回，让 Aider 在后台运行
            output_message = f'🤖 Aider 已启动（后台运行中）'
            if target_relative_path:
                output_message += f'\n📄 目标文件: {target_relative_path}'
            
            return jsonify({
                'output': output_message,
                'execution_id': execution_id,
                'running': True,
                'is_web_service': False,
                'run_mode': 'aider',
                'target_abs_path': target_abs
            })
                
        except Exception as e:
            # 清理临时文件
            try:
                os_module.unlink(temp_output_path)
            except:
                pass
            
            # 清理进程引用
            if execution_id in running_processes:
                del running_processes[execution_id]
            
            return jsonify({'error': f"启动失败: {str(e)}"}), 500
    finally:
        conn.close()

def run_project_file(execution_id, script_path, cwd, project_id, source):
    """运行项目文件（project模式）"""
    import os as os_module
    import sys
    
    # 路径校验
    validation_result = validate_project_paths(script_path, cwd, project_id)
    if validation_result:
        return validation_result
    
    # 创建临时文件来存储输出
    temp_output = tempfile.NamedTemporaryFile(mode='w+', delete=False, encoding='utf-8')
    temp_output_path = temp_output.name
    temp_output.close()
    
    # 清理环境变量，避免子进程继承Flask的WERKZEUG_*变量导致WinError 10038
    clean_env = os_module.environ.copy()
    env_vars_to_remove = ['WERKZEUG_RUN_MAIN', 'WERKZEUG_SERVER_FD', 'FLASK_RUN_FROM_CLI']
    for var in env_vars_to_remove:
        clean_env.pop(var, None)
    # 设置 Python 输出编码为 UTF-8，解决 Windows 终端 GBK 编码问题
    clean_env['PYTHONIOENCODING'] = 'utf-8'
    
    # 使用子进程执行项目文件（Windows上设置进程组以便终止整个进程树）
    try:
        # 打开临时文件用于写入输出
        temp_output_file = open(temp_output_path, 'w', encoding='utf-8-sig')
        
        if os_module.name == 'nt':
            # Windows: 创建新进程组
            process = subprocess.Popen(
                [sys.executable, '-u', script_path],
                cwd=cwd,
                stdout=temp_output_file,
                stderr=subprocess.STDOUT,  # 将 stderr 重定向到 stdout
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                env=clean_env
            )
        else:
            # Unix/Linux
            process = subprocess.Popen(
                [sys.executable, '-u', script_path],
                cwd=cwd,
                stdout=temp_output_file,
                stderr=subprocess.STDOUT,  # 将 stderr 重定向到 stdout
                preexec_fn=os_module.setsid,
                env=clean_env
            )
        
        # 关闭文件句柄（子进程会保持打开）
        temp_output_file.close()
        
        # 检测是否为Web服务
        is_web_service = detect_web_service(script_path)
        
        # 存储进程引用和临时文件路径
        running_processes[execution_id] = {
            'process': process,
            'temp_output_path': temp_output_path,
            'temp_code_path': None,  # project模式不需要临时代码文件
            'temp_user_code_path': None,  # project模式直接使用项目文件，不保存到temp_user_code_path
            'task_type': 'service' if is_web_service else 'task',
            'is_web_service': is_web_service,
            'start_time': time.time(),
            'run_mode': 'project',
            'cwd': cwd,
            'script_path': script_path,
            'project_id': project_id,
            'source': source
        }
        
        # 立即返回，让代码在后台运行
        if is_web_service:
            output_message = '🌐 Web服务已启动（后台运行中）'
            service_url = extract_service_url(script_path)
        else:
            output_message = '✅ 代码开始执行（后台运行中）'
            service_url = None
        
        return jsonify({
            'output': output_message,
            'execution_id': execution_id,
            'running': True,
            'is_web_service': is_web_service,
            'service_url': service_url,
            'run_mode': 'project',
            'script_path': script_path,
            'cwd': cwd
        })
            
    except Exception as e:
        # 清理临时文件
        try:
            os_module.unlink(temp_output_path)
        except:
            pass
        
        # 清理进程引用
        if execution_id in running_processes:
            del running_processes[execution_id]
        
        return jsonify({'error': f"启动失败: {str(e)}"}), 500

def validate_project_paths(script_path, cwd, project_id):
    """校验项目路径（project模式）"""
    import os as os_module
    
    # 检查路径是否为绝对路径
    if not os_module.path.isabs(script_path):
        return jsonify({'error': 'script_path 必须是绝对路径'}), 400
    
    if not os_module.path.isabs(cwd):
        return jsonify({'error': 'cwd 必须是绝对路径'}), 400
    
    # 检查路径是否存在
    if not os_module.path.exists(script_path):
        return jsonify({'error': f'script_path 不存在: {script_path}'}), 400
    
    if not os_module.path.isfile(script_path):
        return jsonify({'error': f'script_path 必须是文件: {script_path}'}), 400
    
    if not os_module.path.exists(cwd):
        return jsonify({'error': f'cwd 不存在: {cwd}'}), 400
    
    if not os_module.path.isdir(cwd):
        return jsonify({'error': f'cwd 必须是目录: {cwd}'}), 400
    
    # 规范化路径
    script_abs = os_module.path.normpath(os_module.path.abspath(script_path))
    cwd_abs = os_module.path.normpath(os_module.path.abspath(cwd))
    
    # 检查 script_path 是否在 cwd 内（使用 commonpath 防止路径绕过）
    try:
        common = os_module.path.commonpath([script_abs, cwd_abs])
        if common != cwd_abs:
            return jsonify({'error': 'script_path 必须在 cwd 目录内'}), 400
    except ValueError:
        return jsonify({'error': 'script_path 必须在 cwd 目录内'}), 400
    
    # 如果提供了 project_id，校验 cwd 是否属于该项目
    if project_id:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute('SELECT root_path FROM projects WHERE id = ?', (project_id,))
            project = cursor.fetchone()
            
            if not project:
                return jsonify({'error': f'项目不存在: project_id={project_id}'}), 400
            
            project_root = os_module.path.normpath(os_module.path.abspath(project[0]))
            
            # 检查 cwd 是否在 project_root 内（使用 commonpath 防止路径绕过）
            try:
                common = os_module.path.commonpath([cwd_abs, project_root])
                if common != project_root:
                    return jsonify({'error': 'cwd 不属于指定项目'}), 400
            except ValueError:
                return jsonify({'error': 'cwd 不属于指定项目'}), 400
        finally:
            conn.close()
    
    return None  # 校验通过

def detect_web_service(script_path):
    """检测是否为Web服务"""
    import re
    
    try:
        with open(script_path, 'r', encoding='utf-8') as f:
            code = f.read()
        
        web_frameworks = [
            r'app\.run\(',  # Flask
            r'uvicorn\.run\(',  # FastAPI/Starlette
            r'serve\(',  # aiohttp
            r'make_server\(',  # WSGI
            r'HTTPServer\(',  # http.server
            r'TCPServer\(',  # socket server
            r'werkzeug\.run_simple\(',  # Werkzeug
            r'cherrypy\.quickstart\(',  # CherryPy
            r'bottle\.run\(',  # Bottle
            r'pyramid\.main\(',  # Pyramid
            r'tornado\.web\.Application\(',  # Tornado
            r'gunicorn\.run\(',  # Gunicorn
        ]
        
        for pattern in web_frameworks:
            if re.search(pattern, code):
                return True
        
        return False
    except:
        return False

def extract_service_url(script_path):
    """从代码中提取服务URL"""
    import re
    
    try:
        with open(script_path, 'r', encoding='utf-8') as f:
            code = f.read()
        
        service_host = '0.0.0.0'
        service_port = '5000'
        
        host_match = re.search(r'host\s*=\s*["\']([^"\']+)["\']', code)
        if host_match:
            service_host = host_match.group(1)
            if service_host == '0.0.0.0':
                service_host = 'localhost'
        
        port_match = re.search(r'port\s*=\s*(\d+)', code)
        if port_match:
            service_port = port_match.group(1)
        
        return f'http://{service_host}:{service_port}'
    except:
        return None

@app.route('/api/run', methods=['POST'])
def run_code():
    """运行代码（支持snippet和project两种模式）"""
    import re
    import io
    import sys
    import builtins
    import multiprocessing
    import uuid
    import tempfile
    import os as os_module
    
    data = request.json
    run_mode = data.get('run_mode', 'snippet')  # snippet、project 或 aider
    code = data.get('code', '')
    language = data.get('language', 'python')
    script_path = data.get('script_path', '')
    cwd = data.get('cwd', '')
    project_id = data.get('project_id', '')
    source = data.get('source', 'editor')  # editor 或 chat
    prompt = data.get('prompt', '')  # Aider 模式的提示
    target_relative_path = data.get('target_relative_path', '')  # Aider 模式的目标文件相对路径
    use_selected_context = data.get('use_selected_context', False)  # 是否使用选中的 Aider 上下文
    
    # 生成唯一的执行ID
    execution_id = str(uuid.uuid4())
    
    # Project 模式：直接运行项目文件
    if run_mode == 'project':
        return run_project_file(execution_id, script_path, cwd, project_id, source)
    
    # Aider 模式：使用 Aider 编辑代码
    if run_mode == 'aider':
        client_id = get_client_id(data)
        return run_aider_mode(execution_id, project_id, prompt, target_relative_path, source, client_id, use_selected_context)
    
    # Snippet 模式：运行代码片段（原有逻辑）
    if not code:
        return jsonify({'error': '代码不能为空'}), 400
    
    if language == 'python':
        # 检测Python代码中的import语句
        imports = re.findall(r'^import\s+(\w+)|^from\s+(\w+)', code, re.MULTILINE)
        required_packages = set([imp for imp in imports for imp in imp if imp])
        
        # 移除标准库
        standard_libs = {'os', 'sys', 'json', 'math', 're', 'datetime', 'time', 'random', 'collections', 'itertools', 'functools', 'typing', 'string', 'copy', 'decimal', 'fractions', 'hashlib', 'base64', 'uuid', 'secrets', 'statistics', 'pathlib', 'tempfile', 'shutil', 'glob', 'fnmatch', 'pickle', 'csv', 'sqlite3', 'html', 'xml', 'email', 'urllib', 'http', 'ftplib', 'poplib', 'imaplib', 'smtplib', 'telnetlib', 'socket', 'ssl', 'hashlib', 'hmac', 'queue', 'threading', 'multiprocessing', 'concurrent', 'asyncio', 'signal', 'atexit', 'traceback', 'warnings', 'contextlib', 'abc', 'enum', 'dataclasses', 'types', 'inspect', 'importlib', 'pkgutil', 'warnings'}
        required_packages = required_packages - standard_libs
        
        # 检查并安装缺失的库
        missing_packages = []
        for pkg in required_packages:
            try:
                __import__(pkg)
            except ImportError:
                missing_packages.append(pkg)
        
        if missing_packages:
            for pkg in missing_packages:
                try:
                    subprocess.run(['pip', 'install', pkg], capture_output=True, text=True, timeout=30)
                except subprocess.TimeoutExpired:
                    return jsonify({'error': f'安装库 {pkg} 超时'}), 500
                except Exception as e:
                    return jsonify({'error': f'安装库 {pkg} 失败: {str(e)}'}), 500
        
        # 检测是否为Web服务
        is_web_service = False
        web_frameworks = [
            r'app\.run\(',  # Flask
            r'uvicorn\.run\(',  # FastAPI/Starlette
            r'serve\(',  # aiohttp
            r'make_server\(',  # WSGI
            r'HTTPServer\(',  # http.server
            r'TCPServer\(',  # socket server
            r'werkzeug\.run_simple\(',  # Werkzeug
            r'cherrypy\.quickstart\(',  # CherryPy
            r'bottle\.run\(',  # Bottle
            r'pyramid\.main\(',  # Pyramid
            r'tornado\.web\.Application\(',  # Tornado
            r'gunicorn\.run\(',  # Gunicorn
        ]
        
        for pattern in web_frameworks:
            if re.search(pattern, code):
                is_web_service = True
                break
        
        # 如果是Web服务，自动添加debug=False和use_reloader=False
        if is_web_service:
            code = re.sub(r'app\.run\([^)]*\)', lambda m: modify_app_run(m.group()), code)
            code = re.sub(r'uvicorn\.run\([^)]*\)', 'uvicorn.run(app, host="0.0.0.0", log_level="info")', code)
            code = re.sub(r'HTTPServer\([^)]*\)', 'HTTPServer(("0.0.0.0", 8000), handler)', code)
        
        # 创建临时文件来存储输出
        temp_output = tempfile.NamedTemporaryFile(mode='w+', delete=False, encoding='utf-8-sig')
        temp_output_path = temp_output.name
        temp_output.close()
        
        # 创建临时文件来存储用户代码
        temp_user_code = tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8', suffix='.py')
        temp_user_code_path = temp_user_code.name
        
        # 重写代码，将输出重定向到临时文件
        wrapped_code = f'''import sys
import io

# 重定向stdout和stderr到临时文件
with open(r"{temp_output_path}", 'w', encoding='utf-8-sig') as f:
    sys.stdout = f
    sys.stderr = f
    
    # 拦截input函数，支持交互式输入
    original_input = __builtins__.input
    def custom_input(prompt=''):
        input_file = r"{temp_output_path}.input"
        # 输出提示信息并立即刷新
        print(f"⚠️ 等待用户输入: {{prompt}}", flush=True)
        
        # 等待输入文件出现（最多等待30秒）
        import os as os_local
        import time
        for _ in range(300):
            if os_local.path.exists(input_file):
                with open(input_file, 'r', encoding='utf-8') as f:
                    user_input = f.read()
                os_local.unlink(input_file)
                return user_input
            time.sleep(0.1)
        
        # 超时返回空字符串
        print(f"⚠️ 输入超时", flush=True)
        return ""
    __builtins__.input = custom_input
    
    try:
        # 直接运行用户代码文件
        exec_globals = {{
            '__builtins__': __builtins__,
            '__name__': '__main__',
            '__file__': r"{temp_user_code_path}",
            '__doc__': None
        }}
        
        # 动态导入用户代码中使用的所有标准库
        required_packages = {list(required_packages)}
        for pkg in required_packages:
            try:
                exec_globals[pkg] = __import__(pkg)
            except ImportError:
                pass
        
        # 使用runpy直接运行用户代码文件
        import runpy
        runpy.run_path(r"{temp_user_code_path}", run_name='__main__', init_globals=exec_globals)
    except Exception as e:
        print(f"错误: {{str(e)}}", file=sys.stderr)
    finally:
        sys.stdout = sys.__stdout__
        __builtins__.input = original_input
'''
        
        # 将包装代码写入临时文件
        temp_code = tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8', suffix='.py')
        temp_code_path = temp_code.name
        temp_code.write(wrapped_code)
        temp_code.close()
        
        # 将用户代码写入另一个临时文件（用于执行）
        temp_user_code.write(code)
        temp_user_code.close()
        
        # 清理环境变量，避免子进程继承Flask的WERKZEUG_*变量导致WinError 10038
        clean_env = os_module.environ.copy()
        env_vars_to_remove = ['WERKZEUG_RUN_MAIN', 'WERKZEUG_SERVER_FD', 'FLASK_RUN_FROM_CLI']
        for var in env_vars_to_remove:
            clean_env.pop(var, None)
        clean_env['PYTHONIOENCODING'] = 'utf-8'  # 解决 Windows 终端 GBK 编码问题
        
        # 使用子进程执行代码（Windows上设置进程组以便终止整个进程树）
        try:
            if os_module.name == 'nt':
                # Windows: 创建新进程组
                process = subprocess.Popen(
                    [sys.executable, temp_code_path],
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                    env=clean_env
                )
            else:
                # Unix/Linux
                process = subprocess.Popen(
                    [sys.executable, temp_code_path],
                    preexec_fn=os_module.setsid,
                    env=clean_env
                )
            
            # 存储进程引用和临时文件路径
            running_processes[execution_id] = {
                'process': process,
                'temp_output_path': temp_output_path,
                'temp_code_path': temp_code_path,
                'temp_user_code_path': temp_user_code_path,
                'task_type': 'service' if is_web_service else 'task',  # task: 短任务, service: 长期服务
                'is_web_service': is_web_service,
                'start_time': time.time()
            }
            
            # 立即返回，让代码在后台运行
            if is_web_service:
                output_message = '🌐 Web服务已启动（后台运行中）'
                # 提取用户代码中的host和port
                service_host = '0.0.0.0'
                service_port = '5000'
                
                host_match = re.search(r'host\s*=\s*["\']([^"\']+)["\']', code)
                if host_match:
                    service_host = host_match.group(1)
                    if service_host == '0.0.0.0':
                        service_host = 'localhost'
                
                port_match = re.search(r'port\s*=\s*(\d+)', code)
                if port_match:
                    service_port = port_match.group(1)
                
                service_url = f'http://{service_host}:{service_port}'
            else:
                output_message = '✅ 代码开始执行（后台运行中）'
                service_url = None
            
            return jsonify({
                'output': output_message,
                'execution_id': execution_id,
                'running': True,
                'is_web_service': is_web_service,
                'service_url': service_url
            })
                
        except Exception as e:
            # 清理临时文件
            try:
                os_module.unlink(temp_output_path)
                os_module.unlink(temp_code_path)
                os_module.unlink(temp_user_code_path)
                input_file = temp_output_path + '.input'
                if os_module.path.exists(input_file):
                    os_module.unlink(input_file)
            except:
                pass
            
            # 清理进程引用
            if execution_id in running_processes:
                del running_processes[execution_id]
            
            return jsonify({'output': f"错误: {str(e)}", 'execution_id': execution_id})
    
    elif language == 'javascript':
        # 执行JavaScript代码（使用浏览器执行，不需要Node.js）
        return jsonify({
            'output': 'JavaScript代码将在浏览器中执行\n请在浏览器控制台查看结果',
            'browser_exec': True,
            'code': code
        })
    
    else:
        return jsonify({'error': f'不支持的语言: {language}'}), 400


@app.route('/api/stop', methods=['POST'])
def stop_code():
    """停止正在运行的代码"""
    import os as os_module
    
    data = request.json
    execution_id = data.get('execution_id')
    
    if execution_id and execution_id in running_processes:
        proc_info = running_processes[execution_id]
        process = proc_info['process']
        temp_output_path = proc_info['temp_output_path']
        temp_code_path = proc_info['temp_code_path']
        temp_user_code_path = proc_info.get('temp_user_code_path')
        
        try:
            # 检查进程是否还在运行
            if process.poll() is not None:
                # 进程已经结束，直接清理
                output = "⏹️ 代码已停止（进程已结束）"
            else:
                # 进程还在运行，需要停止
                if os_module.name == 'nt':
                    # Windows: 发送Ctrl+Break信号终止整个进程组
                    try:
                        # 先检查进程是否还在运行
                        if process.poll() is None:
                            process.send_signal(signal.CTRL_BREAK_EVENT)
                            process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        # 如果信号失败，强制杀死
                        if process.poll() is None:
                            process.kill()
                            process.wait(timeout=2)
                    except OSError as e:
                        # 忽略WinError 10038（进程已结束）和其他预期的错误
                        if hasattr(e, 'winerror') and e.winerror != 10038:
                            raise
                    
                    # Windows 兜底：使用 taskkill 强制终止进程树
                    if process.poll() is None:
                        try:
                            subprocess.run(['taskkill', '/T', '/F', '/PID', str(process.pid)], 
                                       capture_output=True, timeout=5)
                        except:
                            pass
                else:
                    # Unix/Linux: 终止整个进程组
                    try:
                        os_module.killpg(os_module.getpgid(process.pid), signal.SIGTERM)
                        process.wait(timeout=2)
                    except:
                        if process.poll() is None:
                            process.kill()
                            process.wait(timeout=2)
                
                # 读取输出文件
                try:
                    output, _ = read_text_auto(temp_output_path, 0)
                    if not output:
                        output = "⏹️ 代码已停止"
                except:
                    output = "⏹️ 代码已停止"
            
            # Aider 模式：手动停止后也执行一次文件同步
            run_mode = proc_info.get('run_mode', 'snippet')
            if run_mode == 'aider':
                project_id = proc_info.get('project_id')
                cwd = proc_info.get('cwd', '')
                if project_id and cwd:
                    print(f"[DEBUG] Aider 手动停止，开始同步项目 {project_id} 的文件")
                    sync_result = sync_project_files(project_id, cwd)
                    if sync_result['ok']:
                        print(f"[DEBUG] Aider 手动停止后同步完成: 新增/更新 {sync_result['real_count']} 个文件，删除 {sync_result['deleted_count']} 个文件")
                        # 在输出中追加同步信息
                        output += f"\n\n✅ 已同步 {sync_result['real_count']} 个文件到数据库"
                    else:
                        print(f"[DEBUG] Aider 手动停止后同步失败: {sync_result.get('error', '未知错误')}", file=sys.stderr)
            
            # 清理临时文件（只删除临时文件，不删除项目真实文件）
            try:
                if temp_output_path and os_module.path.exists(temp_output_path):
                    os_module.unlink(temp_output_path)
                if temp_code_path and os_module.path.exists(temp_code_path):
                    os_module.unlink(temp_code_path)
                # temp_user_code_path 只在 snippet 模式下是临时文件，project 和 aider 模式不使用
                if run_mode == 'snippet' and temp_user_code_path and os_module.path.exists(temp_user_code_path):
                    os_module.unlink(temp_user_code_path)
                input_file = temp_output_path + '.input'
                if os_module.path.exists(input_file):
                    os_module.unlink(input_file)
            except:
                pass
            
            # 清理进程引用
            del running_processes[execution_id]
            
            return jsonify({'message': '代码已停止', 'output': output})
        except Exception as e:
            return jsonify({'error': f'停止失败: {str(e)}'}), 500
    
    return jsonify({'error': '未找到正在运行的代码'}), 404


@app.route('/api/input', methods=['POST'])
def handle_input():
    """处理交互式输入"""
    import os as os_module
    
    data = request.json
    execution_id = data.get('execution_id')
    user_input = data.get('input', '')
    
    if execution_id and execution_id in running_processes:
        proc_info = running_processes[execution_id]
        temp_output_path = proc_info['temp_output_path']
        input_file = temp_output_path + '.input'
        
        # 将用户输入写入输入文件
        try:
            with open(input_file, 'w', encoding='utf-8') as f:
                f.write(user_input)
            return jsonify({'message': '输入已发送'})
        except Exception as e:
            return jsonify({'error': f'发送输入失败: {str(e)}'}), 500
    
    return jsonify({'error': '未找到正在运行的代码'}), 404


@app.route('/api/output', methods=['POST'])
def get_output():
    """获取正在运行代码的输出"""
    import os as os_module
    
    data = request.json
    execution_id = data.get('execution_id')
    
    if execution_id and execution_id in running_processes:
        proc_info = running_processes[execution_id]
        process = proc_info['process']
        temp_output_path = proc_info['temp_output_path']
        
        # 检查进程是否还在运行
        if process.poll() is not None:
            # 进程已结束，读取增量输出并清理
            try:
                # 获取offset参数（用于增量读取）
                offset = data.get('offset', 0)
                
                # 尝试多种编码读取输出文件（增量）
                output = None
                new_offset = offset
                try:
                    output, new_offset = read_text_auto(temp_output_path, offset)
                    print(f"[DEBUG] 成功使用自动编码读取输出（增量完成）")
                except Exception as e:
                    print(f"[DEBUG] 读取输出文件失败: {e}")
                    output = ""
                    new_offset = offset
                
                # 调试信息
                print(f"[DEBUG] Aider 输出文件路径: {temp_output_path}")
                print(f"[DEBUG] Aider 增量输出长度: {len(output)}")
                print(f"[DEBUG] Aider 增量输出前500字符: {output[:500]}")
            except Exception as e:
                print(f"[DEBUG] 读取 Aider 输出文件失败: {e}")
                output = f"❌ 读取输出失败: {e}"
                new_offset = data.get('offset', 0)
            
            # Aider 模式：扫描目录落库同步（双保险，后台线程已同步，这里再次同步确保前端获取最新）
            file_info_list = []
            if proc_info.get('run_mode') == 'aider':
                project_id = proc_info.get('project_id')
                root_path = proc_info.get('cwd', '')
                
                if project_id and root_path:
                    result = sync_project_files(project_id, root_path)
                    if result['ok']:
                        file_info_list = result['file_info_list']
                        print(f"[DEBUG] /api/output 同步完成: 新增/更新 {result['real_count']} 个文件，删除 {result['deleted_count']} 个文件")
                        # 将文件信息添加到响应中
                        if output:
                            output += f'\n\n✅ Aider 完成，已同步 {result["real_count"]} 个文件'
                        else:
                            output = f'✅ Aider 完成，已同步 {result["real_count"]} 个文件'
                    else:
                        print(f"[DEBUG] /api/output 同步失败: {result.get('error', '未知错误')}", file=sys.stderr)
            
            # 清理临时文件（只删除临时文件，不删除项目真实文件）
            try:
                if temp_output_path and os_module.path.exists(temp_output_path):
                    os_module.unlink(temp_output_path)
                if proc_info['temp_code_path'] and os_module.path.exists(proc_info['temp_code_path']):
                    os_module.unlink(proc_info['temp_code_path'])
                # temp_user_code_path 只在 snippet 模式下是临时文件，project 和 aider 模式不使用
                if proc_info.get('run_mode') == 'snippet' and 'temp_user_code_path' in proc_info and proc_info['temp_user_code_path'] and os_module.path.exists(proc_info['temp_user_code_path']):
                    os_module.unlink(proc_info['temp_user_code_path'])
                input_file = temp_output_path + '.input'
                if os_module.path.exists(input_file):
                    os_module.unlink(input_file)
            except:
                pass
            
            # 清理进程引用
            del running_processes[execution_id]
            
            # 构建响应
            response_data = {'output': output, 'running': False, 'offset': new_offset}
            
            # Aider 模式：添加文件信息
            if proc_info.get('run_mode') == 'aider' and 'file_info_list' in locals() and file_info_list:
                response_data['changed_files'] = file_info_list
                # 不设置 primary_file_id，让前端自己选择
            
            return jsonify(response_data)
        else:
            # 进程还在运行，持续读取当前输出
            try:
                # 获取offset参数（用于增量读取）
                offset = data.get('offset', 0)
                
                # 尝试多种编码读取输出文件
                output = None
                new_offset = offset
                try:
                    output, new_offset = read_text_auto(temp_output_path, offset)
                    print(f"[DEBUG] 成功使用自动编码读取输出（增量）")
                except Exception as e:
                    print(f"[DEBUG] 读取输出文件失败: {e}")
                    output = ""
                    new_offset = offset
                
                # 如果没有新输出，返回空字符串
                if not output:
                    output = ""
                    new_offset = offset
            except Exception as e:
                print(f"[DEBUG] 读取 Aider 输出文件失败: {e}")
                output = ""
                new_offset = data.get('offset', 0)
            
            return jsonify({'output': output, 'running': True, 'offset': new_offset})
    
    return jsonify({'error': '未找到正在运行的代码'}), 404


@app.route('/api/aider/context/select', methods=['POST'])
def select_aider_context():
    """选择 Aider 上下文文件"""
    data = request.get_json()
    client_id = data.get('client_id', 'default')
    project_id = data.get('project_id')
    file_id = data.get('file_id')

    if not project_id or not file_id:
        return jsonify({'error': '缺少必要参数'}), 400

    # 统一转换为 int 类型
    try:
        project_id = int(project_id)
    except (TypeError, ValueError):
        return jsonify({'error': 'project_id 必须是整数'}), 400

    try:
        file_id = int(file_id)
    except (TypeError, ValueError):
        return jsonify({'error': 'file_id 必须是整数'}), 400

    project_root = get_project_root(project_id)
    if not project_root:
        return jsonify({'error': '项目不存在'}), 404

    file_record = get_file_by_id(file_id)
    if not file_record:
        return jsonify({'error': '文件不存在'}), 404

    if file_record['project_id'] != project_id:
        return jsonify({'error': '文件不属于该项目'}), 400

    file_path = file_record['file_path']
    if not is_under_root(file_path, project_root):
        return jsonify({'error': '文件路径不在项目根目录内'}), 400

    context_key = (client_id, project_id)
    aider_contexts[context_key] = {
        'file_id': file_id,
        'relative_path': file_record['relative_path'],
        'abs_path': file_path,
        'updated_at': datetime.datetime.now().isoformat()
    }

    return jsonify({
        'ok': True,
        'selected': {
            'file_id': file_id,
            'relative_path': file_record['relative_path'],
            'file_path': file_path
        }
    })


@app.route('/api/aider/context', methods=['GET'])
def get_aider_context():
    """查询 Aider 上下文文件"""
    client_id = request.args.get('client_id', 'default')
    project_id = request.args.get('project_id')

    if not project_id:
        return jsonify({'error': '缺少必要参数'}), 400

    # 统一转换为 int 类型
    try:
        project_id = int(project_id)
    except (TypeError, ValueError):
        return jsonify({'error': 'project_id 必须是整数'}), 400

    context_key = (client_id, project_id)
    context = aider_contexts.get(context_key)

    if context:
        return jsonify({
            'ok': True,
            'selected': {
                'file_id': context['file_id'],
                'relative_path': context['relative_path'],
                'file_path': context['abs_path']
            }
        })
    else:
        return jsonify({'ok': True, 'selected': None})


@app.route('/api/aider/context/clear', methods=['POST'])
def clear_aider_context():
    """清空 Aider 上下文文件"""
    data = request.get_json()
    client_id = data.get('client_id', 'default')
    project_id = data.get('project_id')

    if not project_id:
        return jsonify({'error': '缺少必要参数'}), 400

    # 统一转换为 int 类型
    try:
        project_id = int(project_id)
    except (TypeError, ValueError):
        return jsonify({'error': 'project_id 必须是整数'}), 400

    context_key = (client_id, project_id)
    if context_key in aider_contexts:
        del aider_contexts[context_key]

    return jsonify({'ok': True})


def cleanup_aider_processes():
    """清理所有 Aider 子进程（Flask 退出时调用）"""
    import os as os_module
    for execution_id, proc_info in list(running_processes.items()):
        if proc_info.get('run_mode') == 'aider':
            process = proc_info.get('process')
            if process and process.poll() is None:
                try:
                    if os_module.name == 'nt':
                        # Windows: 使用 taskkill 强制终止进程树
                        subprocess.run(['taskkill', '/T', '/F', '/PID', str(process.pid)],
                                   capture_output=True, timeout=5)
                    else:
                        # Unix/Linux: 终止进程组
                        os_module.killpg(os_module.getpgid(process.pid), signal.SIGTERM)
                except:
                    pass


if __name__ == '__main__':
    """启动应用"""
    # 注册退出清理函数
    atexit.register(cleanup_aider_processes)
    
    socketio.run(app, debug=False, host='0.0.0.0', port=5000)
