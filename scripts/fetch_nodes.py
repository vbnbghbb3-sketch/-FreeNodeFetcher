"""
FreeNodeFetcher - 免费代理节点抓取脚本
从多个 GitHub 开源订阅源聚合节点，输出 V2rayN (base64 txt) 和 Clash (yaml) 格式
无需第三方依赖，仅使用 Python 标准库

功能:
  - 节点抓取 + 去重 + 分块输出
  - 运行时自动检测源可用性
  - 不可用源自动替换为新发现的活跃源
  - 自动更新脚本自身的源列表
"""

import base64
import json
import os
import re
import ssl
import sys
import urllib.request
import urllib.error
import concurrent.futures
from datetime import datetime
from pathlib import Path

# ============================================================
# 节点源配置
# ============================================================
GITHUB_MIRRORS = [
    "https://gh-proxy.com/",
    "https://ghps.cc/",
]

# 节点源: (name, raw_url, encoding)
#   encoding: "base64" | "plain"
SOURCES = [
    ("V2RayAggregator", "https://raw.githubusercontent.com/mahdibland/V2RayAggregator/master/sub/sub_merge.txt", "plain"),
    ("ermaozi", "https://raw.githubusercontent.com/ermaozi/get_subscribe/main/subscribe/v2ray.txt", "base64"),
    ("ssrsub-v2ray", "https://raw.githubusercontent.com/ssrsub/ssr/master/v2ray", "base64"),
    ("ripaojiedian", "https://raw.githubusercontent.com/ripaojiedian/freenode/main/sub", "base64"),
    ("NoMoreWalls", "https://raw.githubusercontent.com/peasoft/NoMoreWalls/master/list.txt", "base64"),
    ("mfuu", "https://raw.githubusercontent.com/mfuu/v2ray/master/v2ray", "base64"),
    ("Pawdroid", "https://raw.githubusercontent.com/Pawdroid/Free-servers/main/sub", "base64"),
]

VALID_SCHEMES = ("vmess", "vless", "ss", "ssr", "trojan", "hysteria", "hysteria2", "tuic", "wireguard")
NODES_PER_FILE = 100
OUTPUT_DIR = "nodes"


# ============================================================
# 网络请求
# ============================================================
def fetch_url(url: str, timeout: int = 20) -> str | None:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/plain, */*",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
        return resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return None


def fetch_json(url: str, timeout: int = 8) -> dict | list | None:
    """获取 JSON 数据"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    headers = {"User-Agent": "Mozilla/5.0"}
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
        return json.loads(resp.read().decode("utf-8", errors="ignore"))
    except Exception:
        return None


# ============================================================
# 节点解析
# ============================================================
def parse_nodes(raw_text: str) -> list[str]:
    nodes = []
    for line in raw_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        scheme = line.split("://")[0].lower() if "://" in line else ""
        if scheme in VALID_SCHEMES:
            nodes.append(line)
    return nodes


def fetch_source(name: str, url: str, encoding: str) -> list[str]:
    """从单个源获取节点，失败时自动尝试镜像"""
    print(f"  {name} ... ", end="", flush=True)
    raw = fetch_url(url)

    if raw is None and "raw.githubusercontent.com" in url:
        for mirror in GITHUB_MIRRORS:
            raw = fetch_url(mirror + url)
            if raw is not None:
                break

    if raw is None:
        print("FAIL")
        return []

    if encoding == "base64":
        try:
            raw = base64.b64decode(raw).decode("utf-8", errors="ignore")
        except Exception:
            pass

    nodes = parse_nodes(raw)
    print(f"{len(nodes)} nodes")
    return nodes


def test_source(url: str) -> tuple[bool, int]:
    """测试源是否可用，返回 (是否可用, 节点数)"""
    raw = fetch_url(url, timeout=6)
    if raw is None and "raw.githubusercontent.com" in url:
        for mirror in GITHUB_MIRRORS:
            raw = fetch_url(mirror + url, timeout=6)
            if raw is not None:
                break
    if raw is None:
        return False, 0

    try:
        decoded = base64.b64decode(raw).decode("utf-8", errors="ignore")
    except Exception:
        decoded = raw

    count = len(parse_nodes(decoded))
    return count > 0, count


# ============================================================
# 源健康检查 + 自动发现
# ============================================================
# 候选文件名：搜索 GitHub 仓库时尝试的文件名
_CANDIDATE_FILES = [
    "v2ray.txt", "v2ray", "sub", "sub.txt", "nodes.txt", "nodes",
    "list.txt", "v2rayn.txt", "trojan.txt", "ss.txt", "data.txt",
    "sub_merge.txt", "vmess.txt",
]


def discover_new_sources(dead_names: set[str], known_urls: set[str]) -> list[tuple[str, str, str]]:
    """
    通过 GitHub API 搜索新的活跃节点源。
    返回 [(name, url, encoding), ...]
    """
    print("\n  Searching GitHub for new sources...")

    # 搜索关键词（只搜一次，减少耗时）
    api_url = "https://api.github.com/search/repositories?q=free+v2ray+nodes+in:name&sort=updated&order=desc&per_page=6"
    data = fetch_json(api_url, timeout=8)
    if not data or "items" not in data:
        print("  GitHub API unavailable, skipping discovery")
        return []

    candidate_repos = set()
    for item in data["items"]:
        repo = item["full_name"]
        pushed = item.get("pushed_at", "")
        if pushed and pushed > "2026-02-01":
            candidate_repos.add(repo)

    if not candidate_repos:
        print("  No active repos found")
        return []

    print(f"  Testing {len(candidate_repos)} repos...")

    # 对每个候选仓库，尝试常见文件名
    new_sources = []
    for repo in candidate_repos:
        if len(new_sources) >= 2:
            break
        for fname in _CANDIDATE_FILES[:6]:  # 只试前 6 个文件名
            for branch in ("main", "master"):
                url = f"https://raw.githubusercontent.com/{repo}/{branch}/{fname}"
                if url in known_urls:
                    continue
                ok, count = test_source(url)
                if ok and count >= 5:
                    name = f"{repo.split('/')[1]}-{fname.replace('.', '_')}"
                    print(f"  + NEW: {name} ({count} nodes)")
                    new_sources.append((name, url, "base64"))
                    break
            if len(new_sources) >= 2:
                break

    return new_sources


def health_check_and_discover(current_sources: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """
    并发检查所有源的健康状态，替换死源为新发现的源。
    返回更新后的源列表。
    """
    print("[Health] Checking sources (concurrent)...")
    alive = []
    dead = set()
    known_urls = {url for _, url, _ in current_sources}

    # 并发测试所有源
    with concurrent.futures.ThreadPoolExecutor(max_workers=7) as pool:
        futures = {
            pool.submit(test_source, url): (name, url, enc)
            for name, url, enc in current_sources
        }
        try:
            for future in concurrent.futures.as_completed(futures, timeout=15):
                name, url, enc = futures[future]
                try:
                    ok, count = future.result(timeout=1)
                    if ok:
                        print(f"  OK   {name} ({count} nodes)")
                        alive.append((name, url, enc))
                    else:
                        print(f"  DEAD {name}")
                        dead.add(name)
                except Exception:
                    print(f"  DEAD {name} (timeout)")
                    dead.add(name)
        except concurrent.futures.TimeoutError:
            # 超时未完成的源默认视为可用（大文件下载慢是正常的）
            for future, (name, url, enc) in futures.items():
                if future.done():
                    try:
                        ok, count = future.result(timeout=0)
                        if ok and (name, url, enc) not in alive:
                            alive.append((name, url, enc))
                    except Exception:
                        if (name, url, enc) not in alive:
                            dead.add(name)
                else:
                    print(f"  SKIP {name} (slow, keep as-is)")
                    alive.append((name, url, enc))

    if not dead:
        print("[Health] All sources OK")
        return current_sources

    print(f"\n[Health] {len(dead)} dead source(s): {', '.join(dead)}")
    replacements = discover_new_sources(dead, known_urls)

    if replacements:
        print(f"[Health] Added {len(replacements)} new source(s)")
        alive.extend(replacements)
    else:
        print("[Health] No replacements found, keeping remaining sources")

    return alive


# ============================================================
# 自动更新脚本自身的 SOURCES
# ============================================================
def update_script_sources(new_sources: list[tuple[str, str, str]]):
    """将更新后的源列表写回脚本文件"""
    script_path = os.path.abspath(__file__)

    # 生成新的 SOURCES 块
    lines = ["SOURCES = ["]
    for name, url, enc in new_sources:
        lines.append(f'    ("{name}", "{url}", "{enc}"),')
    lines.append("]")
    new_block = "\n".join(lines)

    with open(script_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 替换 SOURCES = [...] 块
    pattern = r"^SOURCES = \[.*?\]"
    new_content = re.sub(pattern, new_block, content, count=1, flags=re.DOTALL | re.MULTILINE)

    if new_content != content:
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        print("[Health] Script sources updated")


# ============================================================
# 格式转换
# ============================================================
def to_v2rayn_base64(nodes: list[str]) -> str:
    raw = "\r\n".join(nodes)
    return base64.b64encode(raw.encode("utf-8")).decode("ascii")


def to_clash_yaml(nodes: list[str]) -> str:
    lines = ["proxies:"]
    for node in nodes:
        scheme = node.split("://")[0].lower()
        proxy = _CONVERTERS.get(scheme, lambda n: None)(node)
        if proxy:
            lines.append(proxy)
    return "\n".join(lines)


def _vmess_to_clash(node):
    try:
        data = json.loads(base64.b64decode(node.replace("vmess://", "")).decode("utf-8", errors="ignore"))
        name = data.get("ps", "vmess").replace(" ", "_")
        addr = data.get("add", "")
        port = data.get("port", 443)
        uuid = data.get("id", "")
        if not addr or not uuid:
            return None
        p = f'  - name: "{name}"\n    type: vmess\n    server: {addr}\n    port: {port}\n    uuid: {uuid}\n    alterId: {data.get("aid", 0)}\n    cipher: auto\n    network: {data.get("net", "tcp")}'
        if data.get("tls") == "tls":
            p += "\n    tls: true"
        if data.get("host"):
            p += f"\n    servername: {data['host']}"
        if data.get("net") == "ws":
            p += f'\n    ws-opts:\n      path: {data.get("path", "/")}\n      headers:\n        Host: {data.get("host", "")}'
        return p
    except Exception:
        return None


def _ss_to_clash(node):
    try:
        from urllib.parse import unquote, urlparse
        p = urlparse(node)
        name = unquote(p.fragment) or f"ss-{p.hostname}"
        if not p.hostname:
            return None
        try:
            ui = base64.b64decode(p.username + "==").decode()
        except Exception:
            ui = p.username or ""
        if ":" in ui:
            method, pw = ui.split(":", 1)
        else:
            method, pw = "aes-256-gcm", ui
        return f'  - name: "{name}"\n    type: ss\n    server: {p.hostname}\n    port: {p.port}\n    cipher: {method}\n    password: {pw}'
    except Exception:
        return None


def _trojan_to_clash(node):
    try:
        from urllib.parse import unquote, urlparse
        p = urlparse(node)
        name = unquote(p.fragment) or f"trojan-{p.hostname}"
        sni = ""
        if p.query:
            params = dict(x.split("=", 1) for x in p.query.split("&") if "=" in x)
            sni = params.get("sni", params.get("peer", ""))
        s = f'  - name: "{name}"\n    type: trojan\n    server: {p.hostname}\n    port: {p.port}\n    password: {p.username}\n    udp: true'
        if sni:
            s += f"\n    sni: {sni}"
        return s
    except Exception:
        return None


def _vless_to_clash(node):
    try:
        from urllib.parse import unquote, urlparse
        p = urlparse(node)
        name = unquote(p.fragment) or f"vless-{p.hostname}"
        params = {}
        if p.query:
            params = dict(x.split("=", 1) for x in p.query.split("&") if "=" in x)
        s = f'  - name: "{name}"\n    type: vless\n    server: {p.hostname}\n    port: {p.port}\n    uuid: {p.username}\n    udp: true'
        if params.get("security") == "tls":
            s += "\n    tls: true"
            if params.get("sni"):
                s += f"\n    servername: {params['sni']}"
        return s
    except Exception:
        return None


def _hy2_to_clash(node):
    try:
        from urllib.parse import unquote, urlparse
        p = urlparse(node)
        name = unquote(p.fragment) or f"hy2-{p.hostname}"
        params = {}
        if p.query:
            params = dict(x.split("=", 1) for x in p.query.split("&") if "=" in x)
        return f'  - name: "{name}"\n    type: hysteria2\n    server: {p.hostname}\n    port: {p.port}\n    password: {p.username or params.get("auth", "")}\n    udp: true'
    except Exception:
        return None


_CONVERTERS = {
    "vmess": _vmess_to_clash,
    "ss": _ss_to_clash,
    "trojan": _trojan_to_clash,
    "vless": _vless_to_clash,
    "hysteria2": _hy2_to_clash,
}


# ============================================================
# 文件输出
# ============================================================
def write_v2rayn_files(chunks, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    for i, chunk in enumerate(chunks):
        with open(os.path.join(output_dir, f"v2rayn_{i}.txt"), "w", encoding="utf-8") as f:
            f.write(to_v2rayn_base64(chunk))
    print(f"  V2rayN: {len(chunks)} files")


def write_clash_files(chunks, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    for i, chunk in enumerate(chunks):
        with open(os.path.join(output_dir, f"clash_{i}.yaml"), "w", encoding="utf-8") as f:
            f.write(to_clash_yaml(chunk))
    print(f"  Clash: {len(chunks)} files")


def write_index(output_dir, v2rayn_count, clash_count, total_nodes, sources_info):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    v2rayn_links = "\n".join(f'      <a href="v2rayn_{i}.txt">v2rayn_{i}.txt</a>' for i in range(v2rayn_count))
    clash_links = "\n".join(f'      <a href="clash_{i}.yaml">clash_{i}.yaml</a>' for i in range(clash_count))

    source_rows = "\n".join(
        f"      <tr><td>{name}</td><td>{count}</td></tr>"
        for name, count in sources_info
    )

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Free Node Fetcher</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;max-width:680px;margin:40px auto;padding:0 20px;color:#333}}
h1{{border-bottom:2px solid #0366d6;padding-bottom:8px}}
.info{{background:#f6f8fa;border-radius:6px;padding:12px 16px;margin:16px 0}}
.links a{{display:block;padding:6px 0;color:#0366d6;text-decoration:none}}
.links a:hover{{text-decoration:underline}}
table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ddd;padding:6px 10px;text-align:left}}
th{{background:#f6f8fa}}
</style></head><body>
<h1>Free Node Fetcher</h1>
<div class="info">Nodes: <strong>{total_nodes}</strong> | Updated: <strong>{now}</strong></div>
<h2>V2rayN</h2><div class="links">{v2rayn_links}</div>
<h2>Clash</h2><div class="links">{clash_links}</div>
<h2>Sources</h2><table><tr><th>Source</th><th>Nodes</th></tr>{source_rows}</table>
</body></html>"""

    with open(os.path.join(output_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)


# ============================================================
# 主流程
# ============================================================
def main():
    print("=" * 50)
    print(" FreeNodeFetcher")
    print("=" * 50)
    print()

    # 1. 健康检查 + 替换死源
    updated_sources = health_check_and_discover(SOURCES)
    if updated_sources != SOURCES:
        update_script_sources(updated_sources)
    print()

    # 2. 获取节点
    print("[Fetch] Collecting nodes...")
    all_nodes = []
    sources_info = []
    for name, url, enc in updated_sources:
        nodes = fetch_source(name, url, enc)
        all_nodes.extend(nodes)
        if nodes:
            sources_info.append((name, len(nodes)))

    # 3. 去重
    print(f"\n[Dedup] {len(all_nodes)} raw -> ", end="")
    seen = set()
    unique = []
    for n in all_nodes:
        if n not in seen:
            seen.add(n)
            unique.append(n)
    print(f"{len(unique)} unique")

    if not unique:
        print("\nNo nodes collected, exiting")
        return

    # 4. 输出
    print(f"\n[Output] Writing files...")
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    output_dir = os.path.join(script_dir, OUTPUT_DIR)
    chunks = [unique[i : i + NODES_PER_FILE] for i in range(0, len(unique), NODES_PER_FILE)]

    write_v2rayn_files(chunks, output_dir)
    write_clash_files(chunks, output_dir)
    write_index(output_dir, len(chunks), len(chunks), len(unique), sources_info)

    print(f"\nDone! {len(unique)} nodes -> {output_dir}")


if __name__ == "__main__":
    main()
