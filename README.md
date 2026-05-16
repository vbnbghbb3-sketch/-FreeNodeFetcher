# FreeNodeFetcher

每日自动聚合免费代理节点，支持 V2rayN 和 Clash。

## 原理

### 整体架构

```
┌──────────────┐    每日定时     ┌──────────────────┐
│  GitHub       │ ──────────▶  │  GitHub Actions    │
│  Actions      │   cron job   │  执行 Python 脚本  │
└──────────────┘               └────────┬─────────┘
                                         │
                           ┌─────────────▼─────────────┐
                           │  fetch_nodes.py            │
                           │                            │
                           │  1. 健康检查 (并发)         │
                           │  2. 死源自动搜索替换         │
                           │  3. 从所有源获取节点         │
                           │  4. 去重                    │
                           │  5. 输出文件                 │
                           └─────────────┬─────────────┘
                                         │
                           ┌─────────────▼─────────────┐
                           │  nodes/                    │
                           │  ├── v2rayn_0.txt          │
                           │  ├── v2rayn_1.txt          │
                           │  ├── clash_0.yaml          │
                           │  └── index.html            │
                           └─────────────┬─────────────┘
                                         │
                           ┌─────────────▼─────────────┐
                           │  GitHub Pages              │
                           │  提供订阅 URL               │
                           └───────────────────────────┘
```

### 节点获取原理

1. **多源聚合** — 从多个 GitHub 开源仓库抓取免费节点
2. **格式识别** — 识别 `vmess://`、`vless://`、`ss://`、`trojan://`、`hysteria2://` 等协议链接
3. **Base64 解码** — 部分源使用 Base64 编码，脚本自动解码
4. **镜像降级** — 主源不通时自动通过 GitHub 镜像代理（`gh-proxy.com`）重试
5. **去重** — 按完整链接去重，避免重复节点
6. **分块输出** — 每 100 个节点一个文件，避免单文件过大

### 健康检查 + 自动发现

每次运行时：

1. **并发测试** — 同时检查所有节点源是否可达（约 10 秒完成）
2. **标记死源** — 超时或返回空内容的源标记为不可用
3. **自动搜索** — 通过 GitHub API 搜索最近活跃的仓库
4. **候选测试** — 对搜索结果尝试常见文件名（`v2ray.txt`、`sub`、`nodes.txt` 等）
5. **替换更新** — 找到可用新源后替换死源，并更新脚本自身的源列表

### 节点来源

| 源 | 节点数 | 协议 |
|---|---|---|
| [V2RayAggregator](https://github.com/mahdibland/V2RayAggregator) | ~5000 | ss/vmess/trojan/ssr |
| [NoMoreWalls](https://github.com/peasoft/NoMoreWalls) | ~200 | vmess/vless/ss |
| [ssrsub](https://github.com/ssrsub/ssr) | ~140 | hysteria2/vmess/trojan/vless/ss |
| [mfuu](https://github.com/mfuu/v2ray) | ~70 | ss/vmess/trojan |
| [ermaozi](https://github.com/ermaozi/get_subscribe) | ~25 | vless/ss/trojan |
| [Pawdroid](https://github.com/Pawdroid/Free-servers) | ~20 | 混合 |
| [ripaojiedian](https://github.com/ripaojiedian/freenode) | ~15 | ss/vmess/trojan |

## 使用方法

### 方式一：GitHub Pages 订阅（推荐）

1. **Fork 本仓库**

2. **启用 GitHub Pages**
   - 仓库设置 → Pages → Source → `main` 分支 `/nodes` 目录 → Save

3. **启用 GitHub Actions**
   - Actions 标签页 → 启用工作流
   - 每天北京时间 09:00 自动运行
   - 也可手动触发：Actions → Fetch Free Nodes → Run workflow

4. **使用订阅链接**
   ```
   V2rayN: https://你的用户名.github.io/FreeNodeFetcher/v2rayn_0.txt
   Clash:  https://你的用户名.github.io/FreeNodeFetcher/clash_0.yaml
   ```

5. **V2rayN 导入**
   - 右键托盘图标 → 订阅分组 → 订阅分组设置
   - 新增 → 粘贴订阅 URL → 确定
   - 订阅分组 → 更新全部订阅

### 方式二：本地一键导入（Windows）

双击 `v2rayn.bat`，脚本会自动：
1. 并发测试所有节点源的可用性
2. 有死源时自动搜索新源替换
3. 从所有存活源获取节点
4. 去重后复制到剪贴板
5. 启动 V2rayN
6. 右键托盘图标 → 服务器 → 从剪贴板批量导入

#### 前置要求

- Windows 系统
- Python 3.10+（仅健康检查+自动发现需要）
- V2rayN 已安装

#### 自定义 V2rayN 路径

编辑 `v2rayn_add_sub.ps1`，修改第 215 行：

```powershell
$v2raynExe = "你的v2rayN.exe路径"
```

## 项目结构

```
FreeNodeFetcher/
├── .github/workflows/fetch.yml   # GitHub Actions 定时任务
├── scripts/
│   └── fetch_nodes.py            # Python 节点抓取脚本
├── nodes/                        # 输出目录（自动更新）
│   ├── v2rayn_0.txt              # V2rayN base64 订阅
│   ├── clash_0.yaml              # Clash YAML 订阅
│   └── index.html                # GitHub Pages 首页
├── v2rayn.bat                    # 一键导入入口
├── v2rayn_add_sub.ps1            # PowerShell 主脚本
└── README.md
```

## 本地运行

```bash
# 抓取节点（无需第三方依赖）
python scripts/fetch_nodes.py

# 一键导入（Windows）
双击 v2rayn.bat
```

## 自定义节点源

编辑 `scripts/fetch_nodes.py` 中的 `SOURCES` 列表：

```python
SOURCES = [
    ("名称", "https://raw.githubusercontent.com/.../file.txt", "base64"),  # 或 "plain"
]
```

编辑 `v2rayn_add_sub.ps1` 中的 `$sources` 数组：

```powershell
$sources = @(
    @{name="名称"; url="https://raw.githubusercontent.com/.../file.txt"; enc="base64"}
)
```

## 注意事项

- 免费节点可用性不保证，节点可能会失效
- 请遵守当地法律法规
- 仅供学习研究使用
