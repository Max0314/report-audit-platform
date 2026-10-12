"""对同一型号的两份 CQC 报告进行可追溯的版本差异比较。"""

import io
import re
from datetime import datetime

from pypdf import PdfReader

from app.services.cqc_report import analyze_cqc_pdf, normalize_text
from app.services.cqc_image_compare import compare_photo_images


def normalized_model(value: str) -> str:
    return re.sub(r"\s+", "", value or "").casefold()


def normalized_part(value: str) -> str:
    return re.sub(r"\s+", "", value or "").casefold()


def model_variants(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[、，,;；]+", value or "") if item.strip()]


def report_identity(pages: list[str]) -> dict:
    cover = pages[0] if pages else ""
    report_number = re.search(r"报告编号\s*[:：]\s*(C-\d+-\d+CQC-\d+)", cover)
    application_number = re.search(r"申请编号\s*[:：]\s*(V\d{4}CQC\d+-\d+)", cover)
    return {
        "report_number": report_number.group(1) if report_number else None,
        "application_number": application_number.group(1) if application_number else None,
    }


def compare_safety_components(old_components: list[dict], new_components: list[dict]) -> dict:
    old_unmatched = set(range(len(old_components)))
    new_unmatched = set(range(len(new_components)))
    matched = []

    def pair_unique(match_condition):
        for old_index in sorted(old_unmatched):
            candidates = [new_index for new_index in sorted(new_unmatched)
                          if match_condition(old_components[old_index], new_components[new_index])]
            if len(candidates) == 1:
                new_index = candidates[0]
                if sum(match_condition(old_components[i], new_components[new_index])
                       for i in old_unmatched) != 1:
                    continue
                old_unmatched.remove(old_index)
                new_unmatched.remove(new_index)
                matched.append((old_index, new_index))

    pair_unique(lambda old, new: normalized_part(old["name"]) == normalized_part(new["name"])
                and normalized_part(old["model"]) == normalized_part(new["model"]))
    pair_unique(lambda old, new: bool(old["certificate"] and new["certificate"])
                and normalized_part(old["certificate"]) == normalized_part(new["certificate"])
                and normalized_part(old["name"]) == normalized_part(new["name"]))
    pair_unique(lambda old, new: old["part_number"] == new["part_number"]
                and normalized_part(old["name"]) == normalized_part(new["name"]))

    modified = []
    unchanged = []
    for old_index, new_index in sorted(matched):
        old = old_components[old_index]
        new = new_components[new_index]
        changed_fields = [field for field in ("name", "model", "certificate", "specification",
                                              "manufacturer", "factory", "standard", "remark")
                          if normalized_part(old.get(field)) != normalized_part(new.get(field))]
        if not changed_fields and normalized_part(old.get("raw")) != normalized_part(new.get("raw")):
            changed_fields.append("raw")
        if changed_fields:
            change = {"old": old, "new": new, "changed_fields": changed_fields}
            if "model" in changed_fields and "PCB" in new["name"]:
                old_models = model_variants(old["model"])
                new_models = model_variants(new["model"])
                change["model_added"] = [item for item in new_models
                                         if normalized_part(item) not in {normalized_part(model) for model in old_models}]
                change["model_removed"] = [item for item in old_models
                                           if normalized_part(item) not in {normalized_part(model) for model in new_models}]
            modified.append(change)
        else:
            unchanged.append({"old": old, "new": new})

    ambiguous_old, ambiguous_new = set(), set()
    for oi in old_unmatched:
        for ni in new_unmatched:
            old, new = old_components[oi], new_components[ni]
            if (old.get("certificate") and normalized_part(old["certificate"]) == normalized_part(new.get("certificate"))):
                ambiguous_old.add(oi)
                ambiguous_new.add(ni)
    return {
        "added": [new_components[index] for index in sorted(new_unmatched - ambiguous_new)],
        "removed": [old_components[index] for index in sorted(old_unmatched - ambiguous_old)],
        "modified": modified,
        "unchanged": unchanged,
        "ambiguous": {"old": [old_components[i] for i in sorted(ambiguous_old)],
                      "new": [new_components[i] for i in sorted(ambiguous_new)]},
    }


def compare_cqc_pdfs(old_contents: bytes, new_contents: bytes,
                     old_filename: str, new_filename: str) -> dict:
    old_report = analyze_cqc_pdf(old_contents)
    new_report = analyze_cqc_pdf(new_contents)
    old_model = old_report["fields"]["样品型号"]["value"]
    new_model = new_report["fields"]["样品型号"]["value"]
    if not old_model or not new_model:
        raise ValueError("至少一份报告未能识别样品型号，无法进行同型号对比")
    if normalized_model(old_model) != normalized_model(new_model):
        raise ValueError(f"两份报告的样品型号不一致：{old_model} / {new_model}")

    old_reader = PdfReader(io.BytesIO(old_contents))
    new_reader = PdfReader(io.BytesIO(new_contents))
    old_pages = [page.extract_text() or "" for page in old_reader.pages]
    new_pages = [page.extract_text() or "" for page in new_reader.pages]
    old_metadata = {"filename": old_filename, "model": old_model, "page_count": len(old_pages),
                    **report_identity(old_pages)}
    new_metadata = {"filename": new_filename, "model": new_model, "page_count": len(new_pages),
                    **report_identity(new_pages)}
    fields = [
        {"name": name, "old": old_report["fields"][name], "new": new_report["fields"][name],
         "changed": normalize_text(old_report["fields"][name]["value"] or "")
                    != normalize_text(new_report["fields"][name]["value"] or "")}
        for name in old_report["fields"]
    ]

    safety = compare_safety_components(old_report["safety_components"], new_report["safety_components"])
    warnings = ["旧版：" + w for w in old_report.get("warnings", [])]
    warnings.extend("新版：" + w for w in new_report.get("warnings", []))
    if safety["ambiguous"]["old"]:
        warnings.append("部分关键件共用证书且无法唯一配对，已列为待人工匹配，不计入确定增减")
    if not old_report["safety_components"] or not new_report["safety_components"]:
        warnings.append("至少一份报告未能识别安全关键件清单，无法可靠判断关键件增减")
        safety = {"added": [], "removed": [], "modified": [], "unchanged": [], "ambiguous": {"old": [], "new": []}}
    old_date = old_report["fields"]["完成日期"]["value"]
    new_date = new_report["fields"]["完成日期"]["value"]
    if old_metadata["report_number"] == new_metadata["report_number"] and old_date == new_date:
        warnings.append("两份报告编号及完成日期相同；旧版/新版仅按上传位置判定，不代表报告实际签发先后")
    elif old_date and new_date:
        try:
            if datetime.strptime(old_date, "%Y年%m月%d日") > datetime.strptime(new_date, "%Y年%m月%d日"):
                warnings.append("标记为旧版的报告完成日期晚于新版，请检查上传顺序")
        except ValueError:
            warnings.append("报告完成日期格式不一致，无法自动确认旧版与新版的时间顺序")

    photo_comparison = compare_photo_images(old_reader, new_reader, old_pages, new_pages)
    warnings.extend(photo_comparison["warnings"])

    return {
        "old_report": old_metadata,
        "new_report": new_metadata,
        "fields": fields,
        "safety_changes": safety,
        "photo_comparison": photo_comparison,
        "declared_changes": {"old": old_report["changes"], "new": new_report["changes"]},
        "warnings": warnings,
        "summary": {"field_changes": sum(field["changed"] for field in fields),
                    "added_components": len(safety["added"]),
                    "removed_components": len(safety["removed"]),
                    "modified_components": len(safety["modified"]),
                    "unchanged_components": len(safety["unchanged"]),
                    "changed_photos": len(photo_comparison["changes"]),
                    "slightly_changed_photos": len(photo_comparison["format_only"])},
    }
