"""认证库不可变快照与版本目录。生产写通 OSS；当前部署为单进程写入。"""
import hashlib
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.storage import get_store, safe_filename
from app.services.cqc_pcb import parse_database, comparable_value, FIELD_LABELS

ROOT = "libraries/cqc-pcb"
CATALOG = ROOT + "/catalog.json"
LOCAL_ROOT = Path("data/library-development")
_lock = threading.RLock()


class LibraryConflict(ValueError):
    pass


def read_bytes(key):
    store = get_store()
    if store:
        return store.get_bytes(key)
    if os.getenv("SERVER_ENV", "").lower() == "production":
        raise RuntimeError("生产认证库必须使用 OSS")
    path = LOCAL_ROOT / key
    return path.read_bytes() if path.exists() else None


def write_bytes(key, value, content_type="application/json"):
    store = get_store()
    if store:
        store.put_bytes(key, value, content_type)
        return
    if os.getenv("SERVER_ENV", "").lower() == "production":
        raise RuntimeError("生产认证库必须使用 OSS")
    path = LOCAL_ROOT / key
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_bytes(value)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def encoded(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def catalog():
    value = read_bytes(CATALOG)
    return json.loads(value) if value else {"revision": 0, "active": None, "versions": [], "events": []}


def version_snapshot(version):
    if not isinstance(version, str) or len(version) != 64 or any(c not in "0123456789abcdef" for c in version):
        raise ValueError("无效的认证库版本")
    value = read_bytes(f"{ROOT}/{version}/snapshot.json")
    if not value:
        raise ValueError("认证库版本文件不可用")
    return json.loads(value)


def current_snapshot():
    # 目录与快照均持久化；读到旧指针仍能使用不可变旧快照。
    try:
        active = catalog()["active"]
        return version_snapshot(active) if active else {"message": "尚未配置认证库，PCB 核对未完成"}
    except Exception:
        return {"message": "认证库暂时不可用，PCB 核对未完成；其他规则检查正常执行"}


def difference(old, new):
    before = {comparable_value("certificate", r["certificate"]): r for r in old}
    after = {comparable_value("certificate", r["certificate"]): r for r in new}
    fields = ("name", "model", "specification", "manufacturer", "factory", "standard", "certificate")
    modified = sum(any(comparable_value(f, before[k][f]) != comparable_value(f, after[k][f])
                       for f in fields) for k in before.keys() & after.keys())
    return {"added": len(after.keys() - before.keys()), "removed": len(before.keys() - after.keys()),
            "modified": modified, "unchanged": len(before.keys() & after.keys()) - modified}


def publish(contents, filename, actor, expected_revision):
    records, errors = parse_database(contents)
    if errors:
        return {"published": False, "errors": errors[:100], "error_count": len(errors)}
    source_sha256 = hashlib.sha256(contents).hexdigest()
    with _lock:
        state = catalog()
        if state["revision"] != expected_revision:
            raise LibraryConflict("认证库已由其他操作更新，请刷新后重试")
        previous = version_snapshot(state["active"]) if state["active"] else {"records": [], "metadata": {}}
        old = previous["records"]
        # 版本标识包含导入前版本，重复导入旧文件也必须基于当前库合并，不能恢复旧快照。
        version = hashlib.sha256(encoded([state["active"], source_sha256, safe_filename(filename)])).hexdigest()
        merged = {}
        for record in old:
            source = record.get("source") or {
                "filename": previous["metadata"].get("filename"), "version": state["active"],
                "sheet": record["sheet"], "row": record["row"],
            }
            merged[comparable_value("certificate", record["certificate"])] = {**record, "source": source}
        for record in records:
            key = comparable_value("certificate", record["certificate"])
            if key not in merged or difference([merged[key]], [record])["modified"]:
                merged[key] = {**record, "source": {
                    "filename": safe_filename(filename), "version": version,
                    "sheet": record["sheet"], "row": record["row"],
                }}
        merged_records = list(merged.values())
        if len(merged_records) > 1000:
            raise ValueError("增量合并后认证库超过 1000 条，未更新；请联系管理员处理")
        if sum(len(record.get(field) or "") for record in merged_records for field in FIELD_LABELS) > 1000000:
            raise ValueError("增量合并后认证库文本超过 100 万字符，未更新；请联系管理员处理")
        changes = difference(old, merged_records)
        if not changes["added"] and not changes["modified"]:
            return {"published": False, "unchanged": True, "revision": state["revision"],
                    "version": state["active"], "changes": changes}
        if not any(v["version"] == version for v in state["versions"]):
            if len(state["versions"]) >= 1000:
                raise ValueError("认证库版本数量已达上限，请联系管理员归档")
            metadata = {"version": version, "filename": safe_filename(filename), "sheet": "CQC认证信息",
                        "record_count": len(merged_records), "uploaded_at": datetime.now(timezone.utc).isoformat(),
                        "uploaded_by": actor, "import_mode": "incremental", "import_count": len(records),
                        "source_sha256": source_sha256, "parent_version": state["active"]}
            # 所有文件成功后，最后一个目录对象 PUT 同时提交当前版本和历史记录。
            write_bytes(f"{ROOT}/{version}/source.xlsx", contents,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            write_bytes(f"{ROOT}/{version}/snapshot.json", encoded({"metadata": metadata, "records": merged_records}))
            state["versions"].append(metadata)
        else:
            version_snapshot(version)
        activate_state(state, version, actor, "upload")
        return {"published": True, "version": version, "revision": state["revision"], "changes": changes}


def activate_state(state, version, actor, action):
    state["active"] = version
    state["revision"] += 1
    state["events"].append({"revision": state["revision"], "version": version, "action": action,
                            "actor": actor, "at": datetime.now(timezone.utc).isoformat()})
    write_bytes(CATALOG, encoded(state))


def activate(version, actor, expected_revision):
    with _lock:
        state = catalog()
        if state["revision"] != expected_revision:
            raise LibraryConflict("认证库已更新，请刷新后重试")
        if not any(v["version"] == version for v in state["versions"]):
            raise ValueError("认证库版本不存在")
        version_snapshot(version)
        if state["active"] != version:
            activate_state(state, version, actor, "activate")
        return {"active": version, "revision": state["revision"]}
