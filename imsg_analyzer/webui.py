"""Local web console for imessage-exporter transcripts.

    python3 -m imsg_analyzer.webui [export_dir]

Binds to 127.0.0.1 only. Serves a single-page UI to:
- list conversations in an export directory (imessage-exporter -f txt output)
- trigger exports via the imessage-exporter binary (needs Full Disk Access)
- view per-conversation stats / condensed transcript
- run an LLM analysis with a user-supplied OpenAI-compatible API key

The API key lives only in the browser page memory for the duration of one
request; transcripts and keys never leave the machine except to the LLM
endpoint the user configures.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .parser import parse_file
from .render import dump_json, render_condensed, render_stats_md
from .stats import compute_stats

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
FALLBACK_TEMPLATE = (
    "# 通用聊天分析\n\n基于以下统计数据与聊天转写，输出一份有证据支撑的分析报告："
    "先给总体结论，再用数据分点论证，引用转写内容时注明时间戳，"
    "最后给可行建议。无证据的推断需标注为推测。\n"
)

EXPORTER_TIMEOUT = 900  # seconds for a full export
LLM_TIMEOUT = 300


class ConvStore:
    """Parse-once cache for every txt in the export directory."""

    def __init__(self, export_dir: Path):
        self.dir = export_dir
        self._lock = threading.Lock()
        self._cache: dict[str, dict] = {}

    def _load(self, path: Path) -> dict:
        messages = parse_file(str(path))
        stats = compute_stats(messages, name=path.stem)
        condensed = render_condensed(messages)
        return {"mtime": path.stat().st_mtime, "messages": messages,
                "stats": stats, "condensed": condensed}

    def get(self, stem: str) -> dict | None:
        path = self.dir / f"{stem}.txt"
        if not path.exists():
            return None
        with self._lock:
            entry = self._cache.get(stem)
            if entry and entry["mtime"] == path.stat().st_mtime:
                return entry
            entry = self._load(path)
            self._cache[stem] = entry
            return entry

    def list_conversations(self) -> list[dict]:
        items = []
        for path in sorted(self.dir.glob("*.txt")):
            entry = self.get(path.stem)
            if not entry:
                continue
            stats = entry["stats"]
            items.append({
                "name": path.stem,
                "total": stats.get("total_messages", 0),
                "start": stats.get("date_start"),
                "end": stats.get("date_end"),
                "top_day": (stats.get("per_day_top") or [[None, 0]])[0][0],
            })
        items.sort(key=lambda x: x["total"], reverse=True)
        return items

    def invalidate(self) -> None:
        with self._lock:
            self._cache.clear()


def load_templates() -> dict[str, str]:
    templates: dict[str, str] = {}
    if PROMPTS_DIR.exists():
        for path in sorted(PROMPTS_DIR.glob("*.md")):
            templates[path.stem] = path.read_text(encoding="utf-8")
    if not templates:
        templates["generic"] = FALLBACK_TEMPLATE
    return templates


def call_llm(base_url: str, api_key: str, model: str,
             system: str, user: str) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0.5,
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"LLM API 返回 {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接 LLM API: {exc.reason}") from exc
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"LLM API 响应格式异常: {json.dumps(data)[:500]}") from exc


def run_export(export_dir: Path, target: str | None) -> str:
    exe = shutil.which("imessage-exporter")
    if not exe:
        raise RuntimeError("未找到 imessage-exporter，请先: brew install imessage-exporter")
    cmd = [exe, "-f", "txt", "-o", str(export_dir)]
    if target:
        cmd += ["-t", target]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=EXPORTER_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("导出超时，可改为在终端手动执行") from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        hint = err[-1] if err else "未知错误"
        if "contains existing" in hint:
            raise RuntimeError(
                f"导出目录已有 txt 数据（{export_dir}），imessage-exporter 不覆盖已有导出。"
                "已有数据仍可正常浏览分析；如需重新导出，先清空该目录或用终端导出到新目录")
        if "full disk access" in hint.lower() or "unable to open database" in hint.lower():
            hint += "（需给运行本程序的终端 App 开启完全磁盘访问权限并重启终端）"
        raise RuntimeError(f"导出失败: {hint}")
    return proc.stdout.strip() or "导出完成"


class Handler(BaseHTTPRequestHandler):
    store: ConvStore
    templates: dict[str, str]

    def log_message(self, fmt, *args):  # quiet access logs
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: dict, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _fail(self, exc: Exception, code: int = 400) -> None:
        self._json({"error": str(exc)}, code)

    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path
        if route == "/":
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif route == "/api/list":
            try:
                self._json({"dir": str(self.store.dir),
                            "conversations": self.store.list_conversations(),
                            "templates": list(self.templates)})
            except Exception as exc:
                self._fail(exc, 500)
        elif route == "/api/conversation":
            stem = unquote(parse_qs(parsed.query).get("name", [""])[0])
            entry = self.store.get(stem)
            if not entry:
                self._fail("会话不存在", 404)
                return
            stats = entry["stats"]
            condensed = entry["condensed"]
            self._json({
                "name": stem,
                "stats_md": render_stats_md(stats),
                "stats_json": json.loads(dump_json(stats)),
                "condensed_chars": len(condensed),
                "condensed_preview": condensed[:3000],
            })
        elif route == "/api/transcript":
            stem = unquote(parse_qs(parsed.query).get("name", [""])[0])
            entry = self.store.get(stem)
            if not entry:
                self._fail("会话不存在", 404)
                return
            self._send(200, entry["condensed"].encode("utf-8"),
                       "text/plain; charset=utf-8")
        else:
            self._fail("not found", 404)

    def do_POST(self):
        route = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except json.JSONDecodeError:
            self._fail("请求体不是合法 JSON")
            return
        try:
            if route == "/api/export":
                msg = run_export(self.store.dir, body.get("target"))
                self.store.invalidate()
                self._json({"message": msg,
                            "conversations": self.store.list_conversations()})
            elif route == "/api/llm":
                name = body.get("name", "")
                entry = self.store.get(name)
                if not entry:
                    self._fail("会话不存在", 404)
                    return
                tpl_id = body.get("template", "")
                system = self.templates.get(tpl_id) or next(iter(self.templates.values()))
                api_key = body.get("api_key", "")
                if not api_key:
                    self._fail("请填写 API Key")
                    return
                user = (f"# 统计数据\n{render_stats_md(entry['stats'])}\n\n"
                        f"# 聊天转写\n{entry['condensed']}")
                text = call_llm(body.get("base_url", "https://api.openai.com/v1"),
                                api_key, body.get("model", "gpt-4o-mini"),
                                system, user)
                self._json({"text": text})
            else:
                self._fail("not found", 404)
        except Exception as exc:
            self._fail(exc, 500)


PAGE = r"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>iMessage 分析台</title>
<style>
:root{--bg:#101418;--panel:#171d24;--line:#2a3340;--fg:#d8e0ea;--dim:#8b98a8;--acc:#5ab0ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
 font:14px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
header{display:flex;align-items:center;gap:12px;padding:10px 16px;border-bottom:1px solid var(--line);flex-wrap:wrap}
header h1{font-size:16px;margin:0}
.sub{color:var(--dim);font-size:12px}
button{background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:6px;
 padding:5px 12px;cursor:pointer;font-size:13px}button:hover{border-color:var(--acc)}
button:disabled{opacity:.5;cursor:default}
main{display:flex;height:calc(100vh - 53px)}
aside{width:300px;border-right:1px solid var(--line);overflow-y:auto;flex-shrink:0}
#search{width:100%;padding:8px 12px;background:var(--panel);border:none;border-bottom:1px solid var(--line);
 color:var(--fg);outline:none}
.conv{padding:8px 12px;cursor:pointer;border-bottom:1px solid var(--line)}
.conv:hover{background:var(--panel)}.conv.on{background:var(--panel);border-left:2px solid var(--acc)}
.conv .n{font-size:13px}.conv .m{color:var(--dim);font-size:11px}
section{flex:1;overflow:auto;padding:16px}
nav{display:flex;gap:8px;margin-bottom:12px}
nav button.on{border-color:var(--acc);color:var(--acc)}
pre{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px;
 overflow:auto;white-space:pre-wrap;word-break:break-word;max-height:calc(100vh - 190px);margin:8px 0}
table{border-collapse:collapse;margin:8px 0}
td,th{border:1px solid var(--line);padding:4px 10px;font-size:13px}
h1{font-size:18px}h2{font-size:15px;border-bottom:1px solid var(--line);padding-bottom:4px}
.row{display:flex;gap:8px;align-items:center;margin:6px 0}
.cfg{display:flex;gap:8px;align-items:center;flex-wrap:wrap;background:var(--panel);
 border:1px solid var(--line);border-radius:8px;padding:10px}
.cfg input,.cfg select{background:var(--bg);color:var(--fg);border:1px solid var(--line);
 border-radius:6px;padding:5px 8px;font-size:13px}
#base{width:220px}#model{width:140px}#key{width:180px}
.hint{color:var(--dim);font-size:11px;width:100%}
#status,#aiStatus{color:var(--dim);font-size:12px}
a{color:var(--acc);text-decoration:none}
</style></head><body>
<header>
  <h1>iMessage 分析台</h1>
  <span class="sub" id="dir"></span>
  <button id="btnExport">⟳ 运行全量导出</button>
  <button id="btnRefresh">刷新列表</button>
  <span id="status"></span>
</header>
<main>
<aside><input id="search" placeholder="搜索会话…"><div id="list"></div></aside>
<section>
  <div id="empty" style="color:var(--dim)">← 选择左侧会话开始分析</div>
  <div id="detail" hidden>
    <nav>
      <button data-tab="stats" class="on">统计</button>
      <button data-tab="transcript">转写</button>
      <button data-tab="ai">AI 分析</button>
    </nav>
    <div id="tab-stats" class="tab"></div>
    <div id="tab-transcript" class="tab" hidden>
      <div class="row">
        <button id="btnCopyT">复制全文</button>
        <a id="dlT" href="#" download="transcript.txt">下载 .txt</a>
        <span class="sub" id="tmeta"></span>
      </div>
      <pre id="transcriptBox"></pre>
    </div>
    <div id="tab-ai" class="tab" hidden>
      <div class="cfg">
        模板 <select id="tpl"></select>
        API 地址 <input id="base" value="https://api.openai.com/v1">
        模型 <input id="model" value="gpt-4o-mini" placeholder="模型名">
        API Key <input id="key" type="password" placeholder="sk-…">
        <button id="btnRun">开始分析</button>
        <span class="hint">密钥仅保存在本页内存中，随请求直接发往你填写的 API 地址；本服务不落盘、不上传第三方。
长会话（转写 >10 万字符）可能超出模型上下文。</span>
      </div>
      <div id="aiStatus" style="margin:8px 0"></div>
      <pre id="aiOut" hidden></pre>
    </div>
  </div>
</section>
</main>
<script>
const $=id=>document.getElementById(id);
let CONVS=[],CURRENT=null;
function esc(s){return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")}
function md2html(src){
  const lines=src.split("\n");let html=[],i=0;
  while(i<lines.length){
    const l=lines[i];
    if(l.startsWith("```")){let buf=[];i++;
      while(i<lines.length&&!lines[i].startsWith("```")){buf.push(lines[i]);i++}
      i++;html.push("<pre>"+esc(buf.join("\n"))+"</pre>");continue}
    if(l.startsWith("|")){
      let rows=[];
      while(i<lines.length&&lines[i].startsWith("|")){rows.push(lines[i]);i++}
      let t="<table>";
      rows.forEach((r,j)=>{
        if(/^\|[\s:\-|]+\|?$/.test(r))return;
        const cells=r.replace(/^\||\|$/g,"").split("|").map(c=>esc(c.trim()));
        t+="<tr>"+cells.map(c=>j===0?"<th>"+c+"</th>":"<td>"+c+"</td>").join("")+"</tr>"});
      html.push(t+"</table>");continue}
    if(/^#{1,3}\s/.test(l)){const n=l.match(/^#+/)[0].length;
      html.push(`<h${n+1}>`+esc(l.replace(/^#+\s*/,""))+`</h${n+1}>`);i++;continue}
    if(/^- /.test(l)){let buf=[];
      while(i<lines.length&&/^- /.test(lines[i])){buf.push(lines[i].replace(/^- /,""));i++}
      html.push("<ul>"+buf.map(x=>"<li>"+inline(x)+"</li>").join("")+"</ul>");continue}
    html.push(l.trim()===""?"":`<p>${inline(l)}</p>`);i++;
  }
  function inline(s){return esc(s).replace(/\*\*(.+?)\*\*/g,"<b>$1</b>")}
  return html.join("\n");
}
async function api(path,opt){
  const r=await fetch(path,opt);
  const data=await r.json().catch(()=>({error:"响应解析失败"}));
  if(!r.ok||data.error)throw new Error(data.error||r.status);
  return data;
}
function status(s){$("status").textContent=s||""}
function loadList(keep){
  api("/api/list").then(d=>{
    $("dir").textContent="数据目录: "+d.dir;
    CONVS=d.conversations;
    $("tpl").innerHTML=d.templates.map(t=>`<option value="${t}">${t}</option>`).join("");
    renderList();
    if(keep&&CONVS.some(c=>c.name===keep))openConv(keep);
    status(`共 ${CONVS.length} 个会话`);
  }).catch(e=>status("加载失败: "+e.message));
}
function renderList(){
  const q=$("search").value.trim().toLowerCase();
  $("list").innerHTML="";
  CONVS.filter(c=>!q||c.name.toLowerCase().includes(q)).forEach(c=>{
    const div=document.createElement("div");
    div.className="conv"+(c.name===CURRENT?" on":"");
    const n=document.createElement("div");n.className="n";n.textContent=c.name;
    const m=document.createElement("div");m.className="m";
    m.textContent=`${c.total} 条 · ${c.start||"?"} ~ ${c.end||"?"}`;
    div.append(n,m);div.onclick=()=>openConv(c.name);$("list").append(div);
  });
}
async function openConv(name){
  CURRENT=name;renderList();
  $("empty").hidden=true;$("detail").hidden=false;
  status("加载 "+name+" …");
  try{
    const d=await api("/api/conversation?name="+encodeURIComponent(name));
    $("tab-stats").innerHTML=md2html(d.stats_md);
    $("tmeta").textContent=`共 ${d.condensed_chars} 字符`;
    status("");
    const tr=await fetch("/api/transcript?name="+encodeURIComponent(name)).then(r=>r.text());
    $("transcriptBox").textContent=tr;
    $("dlT").href="data:text/plain;charset=utf-8,"+encodeURIComponent(tr);
    $("dlT").download=name+".condensed.txt";
  }catch(e){status("加载失败: "+e.message)}
}
async function runExport(){
  if(!confirm("运行 imessage-exporter 全量导出？可能需要几分钟。"))return;
  $("btnExport").disabled=true;status("导出中…");
  try{
    const d=await api("/api/export",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});
    CONVS=d.conversations;renderList();status("导出完成，共 "+CONVS.length+" 个会话");
  }catch(e){status("导出失败: "+e.message)}
  $("btnExport").disabled=false;
}
async function runAI(){
  const btn=$("btnRun");btn.disabled=true;
  $("aiStatus").textContent="分析中…（长会话可能需要一两分钟）";$("aiOut").hidden=true;
  try{
    const d=await api("/api/llm",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({name:CURRENT,template:$("tpl").value,base_url:$("base").value.trim(),
        model:$("model").value.trim(),api_key:$("key").value})});
    $("aiOut").textContent=d.text;$("aiOut").hidden=false;$("aiStatus").textContent="完成";
  }catch(e){$("aiStatus").textContent="失败: "+e.message}
  btn.disabled=false;
}
document.querySelectorAll("nav button").forEach(b=>b.onclick=()=>{
  document.querySelectorAll("nav button").forEach(x=>x.classList.remove("on"));
  b.classList.add("on");
  ["stats","transcript","ai"].forEach(t=>$("tab-"+t).hidden=t!==b.dataset.tab);
});
$("btnExport").onclick=runExport;
$("btnRefresh").onclick=()=>loadList(CURRENT);
$("search").oninput=renderList;
$("btnRun").onclick=runAI;
$("btnCopyT").onclick=()=>{navigator.clipboard.writeText($("transcriptBox").textContent);
  $("btnCopyT").textContent="已复制";setTimeout(()=>$("btnCopyT").textContent="复制全文",1200)};
loadList();
</script></body></html>
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="imsg-webui", description="本地 Web 控制台：浏览/导出/分析 iMessage 聊天记录")
    ap.add_argument("export_dir", nargs="?", default="~/imessage_export",
                    help="imessage-exporter 导出目录（默认 ~/imessage_export）")
    ap.add_argument("-p", "--port", type=int, default=8631)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args(argv)

    export_dir = Path(args.export_dir).expanduser()
    export_dir.mkdir(parents=True, exist_ok=True)

    Handler.store = ConvStore(export_dir)
    Handler.templates = load_templates()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"iMessage 分析台: {url}   (数据目录: {export_dir})  Ctrl+C 退出")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
