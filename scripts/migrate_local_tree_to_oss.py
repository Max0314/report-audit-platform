#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将旧报告数据目录灌入当前 ``OSS_PREFIX/legacy``。

默认只预演；生产迁移须显式使用 ``--upload --verify``。脚本不会删除源文件，
并生成可选 SHA-256 清单，便于迁移前后核对和回滚审计。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from app.object_store import ObjectStoreError, build_oss_store_from_env
from app.storage import safe_filename


def _files(root: Path):
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        yield path


def _key(prefix: str, relative: Path) -> str:
    parts = [safe_filename(part) for part in relative.parts]
    relative_id = hashlib.sha256(relative.as_posix().encode("utf-8")).hexdigest()[:16]
    parts[-1] = f"{relative_id}-{parts[-1]}"
    return f"{prefix.strip('/')}/{'/'.join(parts)}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, help="旧 data 目录")
    parser.add_argument("--key-prefix", default="legacy", help="OSS_PREFIX 下的迁移子前缀")
    parser.add_argument("--manifest", help="将本地 SHA-256 清单写到此文件（权限 600）")
    parser.add_argument("--upload", action="store_true", help="执行上传；缺省仅预演")
    parser.add_argument("--verify", action="store_true", help="上传后逐个读回并比对 SHA-256")
    args = parser.parse_args()

    load_dotenv()
    root = Path(args.root).resolve()
    if not root.is_dir() or root == Path(root.anchor):
        parser.error("--root 必须是明确存在的数据子目录")
    if args.verify and not args.upload:
        parser.error("--verify 必须与 --upload 同时使用")
    if not args.key_prefix.strip("/"):
        parser.error("--key-prefix 不能为空")

    store = build_oss_store_from_env()
    if args.upload and store is None:
        parser.error("上传需要 STORAGE_BACKEND=oss 和完整 OSS 配置")

    records = []
    total_bytes = 0
    root_digest = hashlib.sha256()
    uploaded = 0

    for path in _files(root):
        relative = path.relative_to(root)
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        size = len(data)
        key = _key(args.key_prefix, relative)
        records.append({"path": relative.as_posix(), "size": size, "sha256": digest, "key": key})
        total_bytes += size
        root_digest.update(relative.as_posix().encode("utf-8"))
        root_digest.update(b"\0")
        root_digest.update(digest.encode("ascii"))
        root_digest.update(b"\n")

        if args.upload:
            try:
                store.put_bytes(key, data)
                if args.verify:
                    remote = store.get_bytes(key)
                    if remote is None or hashlib.sha256(remote).hexdigest() != digest:
                        raise ObjectStoreError("读回校验不一致")
                uploaded += 1
            except ObjectStoreError as exc:
                print(
                    f"迁移在第 {len(records)} 个对象失败: {type(exc).__name__}",
                    file=sys.stderr,
                )
                return 2

    manifest = {
        "source_root": str(root),
        "file_count": len(records),
        "total_bytes": total_bytes,
        "tree_sha256": root_digest.hexdigest(),
        "objects": records,
    }
    if args.manifest:
        manifest_path = Path(args.manifest).resolve()
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(manifest_path, 0o600)
        except OSError:
            pass

    mode = "uploaded-and-verified" if args.upload and args.verify else "uploaded" if args.upload else "dry-run"
    print(
        json.dumps(
            {
                "mode": mode,
                "file_count": len(records),
                "uploaded": uploaded,
                "total_bytes": total_bytes,
                "tree_sha256": root_digest.hexdigest(),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
