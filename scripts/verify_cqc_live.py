"""生产冒烟：合成样本、临时门户会话、原 Excel 真实 AI；仅清理本次生成的 OSS 对象。

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
parser.add_argument("--existing-only", action="store_true", help="只复验原 Excel 功能，跳过 CQC 规则检查")
parser.add_argument("--pcb-library-file", help="明确指定要正式导入的认证库 Excel；不自动清理正式库")
parser.add_argument("--check-library-permissions", action="store_true", help="验证独立授权名单、页面及管理 API，不变更认证库")
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
    allowed_ids = container_python("report-audit-platform", "import json\nfrom app.core.config import get_settings\nprint('SMOKE_JSON:'+json.dumps(get_settings().cqc_library.admin_user_ids.split(',')))")
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
allowed_ids=''' + repr(allowed_ids) + '''
if role=='admin':
 user=manager.get_user_by_id(int(allowed_ids[0]))
elif role.startswith('user:'):
 assert role[5:] in allowed_ids
 user=manager.get_user_by_id(int(role[5:]))
else:
 with get_connection_pool().get_cursor() as c:
  c.execute("SELECT id FROM users WHERE status='active' ORDER BY id")
  ids=[r['id'] for r in c.fetchall()]
 user=next(u for u in (manager.get_user_by_id(i) for i in ids)
           if str(u['id']) not in allowed_ids and bool(user_capabilities(u)['canManageModelConfig'])==(role=='other-admin'))
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


def upload(path, fields, cookie, csrf=True, values=None):
    boundary = "CqcSmoke" + secrets.token_hex(12)
    pieces = []
    for field, value in (values or {}).items():
        pieces.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"\r\n\r\n{value}\r\n'.encode())
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
    if args.check_library_permissions:
        allowed_ids = container_python('report-audit-platform', "import json\nfrom app.core.config import get_settings\nprint('SMOKE_JSON:'+json.dumps(get_settings().cqc_library.admin_user_ids.split(',')))")
        check(len(allowed_ids) == 3 and len(set(allowed_ids)) == 3, 'exactly three library managers configured')
        route = '/api/v1/cqc-library'
        code, body, _ = request(route, owner)
        check(code == 200, 'authorized library catalog')
        before = json.loads(body)
        cases = [(owner if index == 0 else session('user:' + uid), True)
                 for index, uid in enumerate(allowed_ids)]
        cases.extend([(session('other-admin'), False), (other, False)])
        for index, (cookie, allowed) in enumerate(cases):
            tag = f'library permission case {index + 1}'
            code, body, _ = request('/cqc-review', cookie)
            check(code == 200 and ('认证库管理</a>' in body.decode()) == allowed, tag + ' entry')
            check(request('/cqc-library', cookie)[0] == (200 if allowed else 403), tag + ' page')
            check(request(route, cookie)[0] == (200 if allowed else 403), tag + ' catalog')
            check(upload(route + '/upload', [('file', 'invalid-library.xlsx')], cookie,
                         values={'expected_revision': before['revision']})[0] == (422 if allowed else 403), tag + ' upload authorization')
            payload = json.dumps({'version': before['active'], 'expected_revision': before['revision']}).encode()
            check(request(route + '/activate', cookie, payload, 'application/json')[0] == (200 if allowed else 403), tag + ' activate authorization')
        check(json.loads(request(route, owner)[1]) == before, 'permission tests preserve library catalog')
    if args.pcb_library_file:
        route = '/api/v1/cqc-library'
        check(request('/cqc-library', owner)[0] == 200, 'library admin page')
        check(request('/cqc-library', other)[0] == 403, 'library member page denied')
        check(request(route, other)[0] == 403, 'library member API denied')
        code, body, _ = request(route, owner)
        check(code == 200, 'library catalog')
        state = json.loads(body)
        library_field = [('file', args.pcb_library_file)]
        values = {'expected_revision': state['revision']}
        check(upload(route + '/upload', library_field, other, values=values)[0] == 403, 'library member upload denied')
        check(upload(route + '/upload', library_field, owner, csrf=False, values=values)[0] == 403, 'library CSRF denied')
        code, body, _ = upload(route + '/upload', library_field, owner, values=values)
        result = json.loads(body)
        check(code == 200 and (result.get('published') or result.get('unchanged')), 'production library imported')
        code, body, _ = request(route, owner)
        library_state = json.loads(body)
        active = next(v for v in library_state['versions'] if v['version'] == library_state['active'])
        check(active['record_count'] == 8, 'production library eight records')
        values = {'expected_revision': library_state['revision']}
        code, body, _ = upload(route + '/upload', library_field, owner, values=values)
        check(code == 200 and json.loads(body).get('unchanged'), 'library duplicate unchanged')
        code, _, _ = upload(route + '/upload', [('file', 'invalid-library.xlsx')], owner, values=values)
        check(code == 422, 'library invalid Excel rejected')
        code, body, _ = request(route, owner)
        check(json.loads(body)['revision'] == library_state['revision'], 'library invalid upload preserves active version')
    portal_url = base.removesuffix("/report-audit") + "/neo/"
    with urllib.request.urlopen(urllib.request.Request(portal_url, headers={"Cookie": owner}), timeout=30) as portal:
        check(portal.status == 200 and "/login" not in portal.url, "portal return destination")
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
        check('href="/neo_hardware/neo/"' in body.decode() and '返回硬件门户' in body.decode(), "portal return " + path)
        check('CQC单报告审核' in body.decode() and 'CQC 变更审核' not in body.decode(), "CQC label " + path)
        if path.startswith('/cqc-'):
            check('AI 辅助分析' not in body.decode() and 'aiPanel' not in body.decode(), "no CQC AI panel " + path)
    for path in ("/static/js/auth.js", "/static/js/cqc.js", "/static/css/cqc-nav.css"):
        check(request(path, owner)[0] == 200, "static " + path)
    check(upload("/api/v1/cqc-review/analyze", [("file", "old.pdf")], owner, csrf=False)[0] == 403, "CQC CSRF denied")
    cqc_cases = () if args.existing_only else (("review", [("file", "old.pdf")]), ("compare", [("old_file", "old.pdf"), ("new_file", "new.pdf")]))
    if args.pcb_library_file:
        cqc_cases = (*cqc_cases, ("review", [("file", "pcb.pdf")]),
                     ("compare", [("old_file", "pcb.pdf"), ("new_file", "pcb.pdf")]))
    for mode, fields in cqc_cases:
        code, body, _ = upload(f"/api/v1/cqc-{mode}/analyze", fields, owner)
        check(code == 202, "CQC " + mode + " upload")
        task_id = json.loads(body)["task_id"]
        cleanup_keys.extend(f"uploads/cqc/{task_id}/{index:03d}-{name}" for index, (_, name) in enumerate(fields))
        key = f"results/cqc/{identity['owner_hash']}/{task_id}.json"
        cleanup_keys.append(key)
        route = f"/api/v1/cqc/tasks/{task_id}"
        job = wait_result(route, owner)
        check("ai" not in job, "CQC rules only " + mode)
        if fields[0][1] == 'pcb.pdf':
            if mode == 'review':
                pcb = job['result']['pcb_check']
                check(pcb['status'] == '通过' and pcb['summary']['passed'] == 1 and pcb['summary']['missing_records'] == 7,
                      'PCB recognized; unmatched library rows advisory only')
                check(pcb['database']['version'] == library_state['active'], 'PCB library version recorded')
            else:
                check(len(job['result']['safety_changes']['unchanged']) == 1, 'grouped PCB comparison recognized')
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
            check(any("box" in name for name in result["charts"]), "existing box chart")
        else:
            ai_result = result["details"]["ai_result"]
            check(ai_result.get("available") and not ai_result.get("error"), "existing real AI review")
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
