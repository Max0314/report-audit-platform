"""生产冒烟：合成样本、临时门户会话、真实 AI；仅清理本次生成的 OSS 对象。

在部署主机运行：python3 scripts/verify_cqc_live.py --base-url HTTPS入口 --fixtures /tmp/合成样本目录
样本通过 tests.cqc_fixtures.write_fixtures 在本地生成；本脚本不输出 Cookie、Key 或原文。
"""
import argparse
import json
from pathlib import Path
import secrets
import subprocess
import time
import urllib.error
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument("--base-url", required=True)
parser.add_argument("--fixtures", required=True)
args = parser.parse_args()
base = args.base_url.rstrip("/")
fixtures = Path(args.fixtures)
sessions, cleanup_keys = [], []


def container_python(container, code):
    result = subprocess.run(["docker", "exec", "-i", container, "python", "-"], input=code,
                            text=True, capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError("Verification helper failed; sensitive output suppressed")
    return json.loads(next(line[11:] for line in result.stdout.splitlines() if line.startswith("SMOKE_JSON:")))


def session(role):
    data = container_python("stack-htmlsystm", '''
import os,json
from server.neo_user_key import get_shared_user_manager
from server.auth.capabilities import user_capabilities
from server.auth.login_service import issue_session_for_user
from server.auth.session_index import MAX_SESSIONS_PER_USER
from server.db_adapter import get_connection_pool
from server.auth.signed_session import verify_token,AUTH_TOKEN_COOKIE
manager=get_shared_user_manager()
role=''' + repr(role) + '''
if role=='admin':
 user=manager.get_user_by_username(os.environ['SUPER_ADMIN_USERNAME'])
else:
 with get_connection_pool().get_cursor() as c:
  c.execute("SELECT id FROM users WHERE status='active' ORDER BY id")
  ids=[r['id'] for r in c.fetchall()]
 user=next(u for u in (manager.get_user_by_id(i) for i in ids) if not user_capabilities(u)['canManageModelConfig'])
assert user and user.get('status')=='active'
with get_connection_pool().get_cursor() as c:
 c.execute('SELECT COUNT(*) AS n FROM auth_session_index WHERE user_id=%s',(user['id'],))
 assert c.fetchone()['n'] < MAX_SESSIONS_PER_USER
body,headers=issue_session_for_user(user,secure=False)
cookie='; '.join(h.split(';')[0] for h in headers if not h.startswith('session_id='))
token=next(h.split(';')[0].split('=',1)[1] for h in headers if h.startswith(AUTH_TOKEN_COOKIE+'='))
print('SMOKE_JSON:'+json.dumps({'cookie':cookie,'jti':verify_token(token)['jti']}))
''')
    sessions.append(data)
    return data["cookie"]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def request(path, cookie="", data=None, content_type=None, csrf=True):
    headers = {}
    if cookie:
        headers["Cookie"] = cookie + "; csrf_token=cqc-smoke-token"
    if csrf:
        headers["X-CSRF-Token"] = "cqc-smoke-token"
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(base + path, headers=headers, data=data)
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=240) as response:
            return response.status, response.read(), response.headers
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers


def check(condition, label):
    if not condition:
        raise RuntimeError(label + " failed")
    print(label + ": passed", flush=True)


def upload(path, fields, cookie, csrf=True):
    boundary = "CqcSmoke" + secrets.token_hex(12)
    pieces = []
    for field, filename in fields:
        pieces.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode(),
                       (fixtures / filename).read_bytes(), b"\r\n"])
    pieces.append(f"--{boundary}--\r\n".encode())
    return request(path, cookie, b"".join(pieces), "multipart/form-data; boundary=" + boundary, csrf)


def wait_result(path, cookie):
    for _ in range(150):
        code, body, _ = request(path, cookie)
        check(code == 200, "poll response") if code != 200 else None
        job = json.loads(body)
        if job["status"] == "completed":
            return job
        if job["status"] in ("failed", "error"):
            raise RuntimeError("Synthetic task failed")
        time.sleep(2)
    raise RuntimeError("Synthetic task timed out")


try:
    check(request("/health")[0] == 200, "public health")
    check(request("/api/v1/cqc/tasks/" + "0" * 32)[0] == 401, "anonymous API denied")
    check(request("/cqc-review")[0] == 302, "anonymous page redirected")
    owner, other = session("admin"), session("member")
    identity = container_python("report-audit-platform", '''
import asyncio,json,hashlib
from app.auth import _verify
user,ok=asyncio.run(_verify(''' + repr(owner) + '''))
assert ok and user
key=user.get('userKey') or user.get('userid') or user.get('id') or user.get('username')
print('SMOKE_JSON:'+json.dumps({'owner_hash':hashlib.sha256(str(key).encode()).hexdigest()}))
''')
    code, _, headers = request("/", owner)
    check(code == 302 and headers.get("Location", "").endswith("/report-audit/review"), "homepage removed")
    for path in ("/review", "/analysis", "/cqc-review", "/cqc-compare"):
        code, body, _ = request(path, owner)
        check(code == 200 and body.count(b'class="nav-link') == 4, "four tabs " + path)
    for path in ("/static/js/auth.js", "/static/js/cqc.js", "/static/css/cqc-nav.css"):
        check(request(path, owner)[0] == 200, "static " + path)
    check(upload("/api/v1/cqc-review/analyze", [("file", "old.pdf")], owner, csrf=False)[0] == 403, "CQC CSRF denied")
    for mode, fields in (("review", [("file", "old.pdf")]), ("compare", [("old_file", "old.pdf"), ("new_file", "new.pdf")])):
        code, body, _ = upload(f"/api/v1/cqc-{mode}/analyze", fields, owner)
        check(code == 202, "CQC " + mode + " upload")
        task_id = json.loads(body)["task_id"]
        cleanup_keys.extend(f"uploads/cqc/{task_id}/{index:03d}-{name}" for index, (_, name) in enumerate(fields))
        key = f"results/cqc/{identity['owner_hash']}/{task_id}.json"
        cleanup_keys.append(key)
        route = f"/api/v1/cqc/tasks/{task_id}"
        job = wait_result(route, owner)
        print(json.dumps({"mode": mode, "ai_status": job["ai"]["status"], "model": job["ai"].get("model"),
                          "failure_kind": job["ai"].get("failure_kind"), "findings": len(job["ai"]["findings"])}, ensure_ascii=False), flush=True)
        check(job["ai"]["status"] == "completed" and job["ai"]["model"] == "deepseek/deepseek-v4-pro", "CQC real AI " + mode)
        check(bool(job["result"]["warnings"]), "missing data warnings " + mode)
        check(request(route, other)[0] == 404 and request(route + "/export", other)[0] == 404, "CQC owner isolation " + mode)
        code, body, _ = request(route + "/export", owner)
        check(code == 200 and body.startswith(b"PK"), "CQC export " + mode)
        archived = container_python("report-audit-platform", "import json\nfrom app.storage import get_store\nd=get_store().get_bytes(" + repr(key) + ")\nprint('SMOKE_JSON:'+json.dumps({'archived':bool(d) and json.loads(d)['status']=='completed'}))")
        check(archived["archived"], "CQC OSS result " + mode)
    # 回归原有 Excel 功能，使用真实配置，不修改旧路由。
    for mode in ("review", "analysis"):
        field = "file" if mode == "review" else "files"
        code, body, _ = upload(f"/api/v1/{mode}/upload", [(field, "synthetic.xlsx")], owner)
        check(code == 200, "existing " + mode + " upload")
        task_id = json.loads(body)["task_id"]
        filename = "synthetic.xlsx" if mode == "review" else "001-synthetic.xlsx"
        cleanup_keys.append(f"uploads/{mode}/{task_id}/{filename}")
        wait_result(f"/api/v1/{mode}/status/{task_id}", owner)
        code, body, _ = request(f"/api/v1/{mode}/result/{task_id}", owner)
        check(code == 200, "existing " + mode + " result")
        result = json.loads(body)
        if mode == "analysis":
            check(result["summary"]["total_records"] == 3 and bool(result["charts"]), "existing aggregation and charts")
        else:
            code, body, _ = request(f"/api/v1/review/export/{task_id}", owner)
            check(code == 200 and body.startswith(b"PK"), "existing review export")
        check(request(f"/api/v1/{mode}/result/{task_id}", other)[0] == 404, "existing owner isolation " + mode)
finally:
    try:
        if cleanup_keys:
            cleaned = container_python("report-audit-platform", "import json\nfrom app.storage import get_store\ns=get_store()\nkeys=" + repr(cleanup_keys) + "\nfor k in keys: s.delete(k)\nprint('SMOKE_JSON:'+json.dumps({'deleted':len(keys)}))")
            print("Synthetic OSS objects removed: " + str(cleaned["deleted"]), flush=True)
    finally:
        for data in sessions:
            container_python("stack-htmlsystm", "from server.auth.session_index import revoke_jti\nimport json\nrevoke_jti(" + repr(data["jti"]) + ")\nprint('SMOKE_JSON:{\"revoked\":true}')")
        print("Temporary sessions revoked", flush=True)
