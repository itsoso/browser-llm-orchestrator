#!/usr/bin/env python3
"""
重建 index.html 的布局：左侧菜单 + 右侧内容
"""

from pathlib import Path

# 读取原文件
original_file = Path("templates/index.html")
content = original_file.read_text(encoding='utf-8')

# 分割文件内容
lines = content.split('\n')

# 找到关键位置
body_start = None
script_start = None

for i, line in enumerate(lines):
    if '<body class="bg-gray-50">' in line:
        body_start = i
    if i > 200 and line.strip() == '<script>':
        script_start = i
        break

print(f"Body starts at line: {body_start}")
print(f"Script starts at line: {script_start}")

# 提取 head 部分（包括样式）
head_content = '\n'.join(lines[:body_start])

# 提取 script 和结束部分
script_content = '\n'.join(lines[script_start:])

# 创建新的 body 内容
new_body = '''<body class="bg-gray-50">
    <!-- 左侧菜单 -->
    <aside class="sidebar">
        <div class="sidebar-header">
            <h1 class="text-xl font-bold">🤖 LLM Orchestrator</h1>
            <p class="text-xs text-blue-200 mt-1">多 LLM 决策系统</p>
        </div>
        
        <nav class="mt-4">
            <div class="menu-item active" onclick="showTab('warmup')">
                <span class="menu-item-icon">🔥</span>
                <span>站点预热</span>
            </div>
            <div class="menu-item" onclick="showTab('chatlog')">
                <span class="menu-item-icon">💬</span>
                <span>Chatlog 自动化</span>
            </div>
            <div class="menu-item" onclick="showTab('recap')">
                <span class="menu-item-icon">📊</span>
                <span>每日复盘</span>
            </div>
            <div class="menu-item" onclick="showTab('logs')">
                <span class="menu-item-icon">📝</span>
                <span>日志查看</span>
            </div>
            <div class="menu-item" onclick="showTab('config')">
                <span class="menu-item-icon">⚙️</span>
                <span>配置管理</span>
            </div>
            <div class="menu-item" onclick="showTab('templates')">
                <span class="menu-item-icon">🎨</span>
                <span>模板管理</span>
            </div>
        </nav>
    </aside>

    <!-- 主内容区域 -->
    <main class="main-content">
        <!-- 顶部状态栏 -->
        <div class="status-bar">
            <div class="flex items-center space-x-6">
                <div class="flex items-center">
                    <span id="driver-status-dot" class="status-dot status-stopped"></span>
                    <span class="text-sm font-medium">Driver Server</span>
                    <span id="driver-status-text" class="ml-2 text-sm text-gray-600">已停止</span>
                </div>
                <div class="text-sm text-gray-600">
                    <span id="driver-config-sites">站点: -</span>
                </div>
            </div>
            <div class="flex items-center space-x-3">
                <button onclick="startDriver()" id="start-driver-btn" class="text-sm px-3 py-1 bg-green-600 text-white rounded hover:bg-green-700">
                    ▶️ 启动
                </button>
                <button onclick="stopDriver()" id="stop-driver-btn" class="text-sm px-3 py-1 bg-red-600 text-white rounded hover:bg-red-700">
                    ⏹ 停止
                </button>
                <button onclick="refreshStatus()" class="text-sm px-3 py-1 text-blue-600 hover:text-blue-700">
                    🔄 刷新
                </button>
            </div>
        </div>

        <!-- Tab 内容区域 -->
        <div class="tab-contents">
'''

# 从原文件中提取所有 tab 内容
# 找到所有 tab-content 的开始和结束
tab_content_start = []
for i, line in enumerate(lines):
    if 'id="tab-warmup"' in line or \
       'id="tab-chatlog"' in line or \
       'id="tab-recap"' in line or \
       'id="tab-logs"' in line or \
       'id="tab-config"' in line or \
       'id="tab-templates"' in line:
        tab_content_start.append(i)

print(f"Found {len(tab_content_start)} tabs")

# 提取每个 tab 的内容
for start_line in tab_content_start:
    # 找到这个 tab 的结束位置（下一个 </div> 在同级）
    depth = 0
    end_line = start_line
    for i in range(start_line, script_start):
        line = lines[i]
        if '<div' in line:
            depth += line.count('<div')
        if '</div>' in line:
            depth -= line.count('</div>')
            if depth == 0:
                end_line = i + 1
                break
    
    # 提取这个 tab 的内容
    tab_lines = lines[start_line:end_line]
    
    # 修改hidden类，默认只显示第一个tab
    if start_line == tab_content_start[0]:
        # 第一个 tab（warmup）默认显示
        for j, line in enumerate(tab_lines):
            if 'class="tab-content hidden"' in line:
                tab_lines[j] = line.replace('class="tab-content hidden"', 'class="tab-content"')
    
    new_body += '\n'.join(tab_lines) + '\n\n'

# 添加结束标签
new_body += '''        </div>
    </main>

'''

# 合并所有内容
new_content = head_content + '\n' + new_body + script_content

# 写入新文件
output_file = Path("templates/index.html")
output_file.write_text(new_content, encoding='utf-8')

print(f"✅ 新布局已生成！")
print(f"   - Head 部分: {body_start} 行")
print(f"   - Body 部分: 新生成")
print(f"   - Script 部分: {len(lines) - script_start} 行")
