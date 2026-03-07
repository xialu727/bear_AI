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
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # 启用外键约束
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


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
    except ValueError:
        return jsonify({'error': '参数类型错误：project_id 必须是整数'}), 400
    
    # 如果没有提供文件路径，自动生成
    if not file_path:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('SELECT root_path FROM projects WHERE id = ?', (project_id,))
            project = cursor.fetchone()
            
            if not project:
                return jsonify({'error': '项目不存在'}), 404
            
            root_path = project['root_path']
            file_path = os.path.join(root_path, filename)
            
            if not relative_path:
                relative_path = filename
        finally:
            conn.close()
    
    conn = get_db_connection()
    try:
        # 验证项目是否存在
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM projects WHERE id = ?', (project_id,))
        if not cursor.fetchone():
            return jsonify({'error': '项目不存在'}), 404
        
        # 确保目录存在
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        # 写入文件内容
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        # 计算文件大小
        file_size = len(content.encode('utf-8'))
        
        cursor = conn.cursor()
        
        # 检查文件是否已存在
        cursor.execute(
            'SELECT id FROM files WHERE file_path = ?',
            (file_path,)
        )
        existing_file = cursor.fetchone()
        
        if existing_file:
            # 更新现有文件
            cursor.execute(
                '''UPDATE files 
                   SET filename = ?, relative_path = ?, file_type = ?, file_size = ? 
                   WHERE file_path = ?''',
                (filename, relative_path, file_type, file_size, file_path)
            )
            file_id = existing_file['id']
            message = '文件更新成功'
        else:
            # 创建新文件记录
            cursor.execute(
                '''INSERT INTO files (project_id, filename, file_path, relative_path, file_type, file_size)
                   VALUES (?, ?, ?, ?, ?, ?)''',
                (project_id, filename, file_path, relative_path, file_type, file_size)
            )
            conn.commit()
            file_id = cursor.lastrowid
            message = '文件保存成功'
        
        conn.commit()
        return jsonify({
            'id': file_id,
            'message': message,
            'file_path': file_path
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
    
    # 使用子进程执行项目文件（Windows上设置进程组以便终止整个进程树）
    try:
        if os_module.name == 'nt':
            # Windows: 创建新进程组
            process = subprocess.Popen(
                [sys.executable, '-u', script_path],
                cwd=cwd,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                env=clean_env
            )
        else:
            # Unix/Linux
            process = subprocess.Popen(
                [sys.executable, '-u', script_path],
                cwd=cwd,
                preexec_fn=os_module.setsid,
                env=clean_env
            )
        
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
    
    # 检查 script_path 是否在 cwd 内
    script_abs = os_module.path.abspath(script_path)
    cwd_abs = os_module.path.abspath(cwd)
    
    if not script_abs.startswith(cwd_abs):
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
            
            project_root = project[0]
            project_root_abs = os_module.path.abspath(project_root)
            
            if not cwd_abs.startswith(project_root_abs):
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
    run_mode = data.get('run_mode', 'snippet')  # snippet 或 project
    code = data.get('code', '')
    language = data.get('language', 'python')
    script_path = data.get('script_path', '')
    cwd = data.get('cwd', '')
    project_id = data.get('project_id', '')
    source = data.get('source', 'editor')  # editor 或 chat
    
    # 生成唯一的执行ID
    execution_id = str(uuid.uuid4())
    
    # Project 模式：直接运行项目文件
    if run_mode == 'project':
        return run_project_file(execution_id, script_path, cwd, project_id, source)
    
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
        temp_output = tempfile.NamedTemporaryFile(mode='w+', delete=False, encoding='utf-8')
        temp_output_path = temp_output.name
        temp_output.close()
        
        # 创建临时文件来存储用户代码
        temp_user_code = tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8', suffix='.py')
        temp_user_code_path = temp_user_code.name
        
        # 重写代码，将输出重定向到临时文件
        wrapped_code = f'''import sys
import io

# 重定向stdout和stderr到临时文件
with open(r"{temp_output_path}", 'w', encoding='utf-8') as f:
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
                    with open(temp_output_path, 'r', encoding='utf-8') as f:
                        output = f.read()
                    if not output:
                        output = "⏹️ 代码已停止"
                except:
                    output = "⏹️ 代码已停止"
            
            # 清理临时文件
            try:
                if temp_output_path and os_module.path.exists(temp_output_path):
                    os_module.unlink(temp_output_path)
                if temp_code_path and os_module.path.exists(temp_code_path):
                    os_module.unlink(temp_code_path)
                if temp_user_code_path and os_module.path.exists(temp_user_code_path):
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
            # 进程已结束，读取输出并清理
            try:
                with open(temp_output_path, 'r', encoding='utf-8') as f:
                    output = f.read()
                if not output:
                    output = "✅ 代码执行完成（无输出）"
            except:
                output = "✅ 代码执行完成（无输出）"
            
            # 清理临时文件
            try:
                if temp_output_path and os_module.path.exists(temp_output_path):
                    os_module.unlink(temp_output_path)
                if proc_info['temp_code_path'] and os_module.path.exists(proc_info['temp_code_path']):
                    os_module.unlink(proc_info['temp_code_path'])
                if 'temp_user_code_path' in proc_info and proc_info['temp_user_code_path'] and os_module.path.exists(proc_info['temp_user_code_path']):
                    os_module.unlink(proc_info['temp_user_code_path'])
                input_file = temp_output_path + '.input'
                if os_module.path.exists(input_file):
                    os_module.unlink(input_file)
            except:
                pass
            
            # 清理进程引用
            del running_processes[execution_id]
            
            return jsonify({'output': output, 'running': False})
        else:
            # 进程还在运行，持续读取当前输出
            try:
                # 获取offset参数（用于增量读取）
                offset = data.get('offset', 0)
                
                with open(temp_output_path, 'r', encoding='utf-8') as f:
                    # 跳过已读取的内容
                    if offset > 0:
                        f.seek(offset)
                    # 读取新增内容
                    output = f.read()
                    # 返回新的offset（文件当前位置）
                    new_offset = f.tell()
                
                # 如果没有新输出，返回空字符串
                if not output:
                    output = ""
                    new_offset = offset
            except:
                output = ""
                new_offset = data.get('offset', 0)
            
            return jsonify({'output': output, 'running': True, 'offset': new_offset})
    
    return jsonify({'error': '未找到正在运行的代码'}), 404


if __name__ == '__main__':
    """启动应用"""
    socketio.run(app, debug=False, host='0.0.0.0', port=5000)
