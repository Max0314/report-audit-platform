"""从单份 CQC 变更报告中提取样品信息和明确记载的变更。"""

import io
import re
from datetime import datetime

from pypdf import PdfReader
from pypdf.generic import ContentStream


FIELD_NAMES = ("样品名称", "样品型号", "收样日期", "完成日期")
FIELD_ENDINGS = FIELD_NAMES + (
    "样品数量", "样品来源", "委托人", "委托人地址", "生产者",
    "生产者地址", "生产企业", "生产企业地址", "试验依据标准",
    "试验结论", "安全主检", "安全审核", "EMC 主检", "备注",
)
FIELD_END_PATTERN = "|".join(re.escape(name) for name in FIELD_ENDINGS)
CHANGE_ROW_PATTERN = re.compile(
    r"[（(]\s*(\d+)\s*[）)]\s*(.*?)"
    r"(?=[（(]\s*\d+\s*[）)]|\n\s*(?:安全试验|EMC\s*试验|变更结论)|\Z)",
    re.S,
)
COMPONENT_ROW_PATTERN = re.compile(
    r"^([1-9]\d?-\d+|3)\s+(.*?)(?=^[1-9]\d?-\d+\s+|^3\s*/\s*/|\Z)",
    re.M | re.S,
)
PHOTO_SECTION_PATTERN = re.compile(
    r"(?m)^[ \t]*样[ \t]*品[ \t]*照[ \t]*片(?:[ \t]*[（(][ \t]*(?:安全|EMC)[ \t]*[）)])?"
)
PHOTO_PLACEHOLDER_PATTERN = re.compile(
    r"(?:图片|照片|贴图|图)\s*(?:待补|待贴|待插入|未贴|未插入|缺失)"
    r"|(?:待补|待贴|待插入|缺少)\s*(?:图片|照片|贴图|图)"
)


def normalize_text(text: str) -> str:
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    return re.sub(r"\s+", " ", text).strip()


def extract_fields(pages: list[str]) -> dict:
    fields = {}
    for field in FIELD_NAMES:
        fields[field] = {"value": None, "page": None}
        pattern = re.compile(
            rf"(?:^|\n)\s*{re.escape(field)}\s*[:：]\s*(.*?)"
            rf"(?=\n\s*(?:{FIELD_END_PATTERN})\s*[:：]|\Z)",
            re.S,
        )
        for page_number, text in enumerate(pages[:6], 1):
            match = pattern.search(text)
            if match:
                fields[field] = {"value": normalize_text(match.group(1)), "page": page_number}
                break
    return fields


def extract_changes(pages: list[str]) -> tuple[list[dict], int | None]:
    import bisect
    offsets, position = [], 0
    for text in pages:
        offsets.append(position)
        position += len(text) + 1
    full_text = "\n".join(pages)
    for heading in re.finditer("变更确认表", full_text):
        end = full_text.find("变更结论", heading.end())
        next_heading = full_text.find("变更确认表", heading.end())
        if end < 0:
            continue
        if 0 <= next_heading < end and not CHANGE_ROW_PATTERN.search(full_text[heading.end():next_heading]):
            continue
        table = full_text[heading.end():end]
        page_number = bisect.bisect_right(offsets, heading.start())
        changes = []
        for match in CHANGE_ROW_PATTERN.finditer(table):
            number, raw_row = match.groups()
            row_page = bisect.bisect_right(offsets, heading.end() + match.start())
            end_page = bisect.bisect_right(offsets, heading.end() + match.end() - 1)
            row = normalize_text(raw_row)
            if "详见原报告" in row:
                item, current = row.split("详见原报告", 1)
                before = "详见原报告"
            elif row.startswith("更新认证实施规则"):
                item = "更新认证实施规则"
                rules = re.findall(r"CQC\d{2}-\d{6}-\d{4}《[^》]*》", row)
                before = rules[0] if len(rules) >= 2 else "未明确记载"
                current = rules[1] if len(rules) >= 2 else row[len(item):]
            elif "RJ45" in row and "传输速率" in row:
                item = "变更 RJ45 端口传输速率"
                speeds = re.findall(r"\d+(?:/\d+)+\s*Mbps", row, re.I)
                before = speeds[0] if len(speeds) >= 2 else "未明确记载"
                current = speeds[1] if len(speeds) >= 2 else row
            elif row.startswith("增加产品名称"):
                item = "增加产品名称"
                names = list(re.finditer(r"10Gbit/s", row, re.I))
                before = row[names[0].start():names[1].start()].strip() if len(names) >= 2 else "未明确记载"
                current = row[names[1].start():].strip() if len(names) >= 2 else row[len(item):]
            else:
                item, current, before = row, "", "未明确记载"
            item = item.strip()
            category = (
                "新增" if re.search(r"增加|新增", item) else
                "减少" if re.search(r"减少|删除|取消", item) else "其他变更"
            )
            changes.append({
                "number": number,
                "category": category,
                "item": item,
                "before": before,
                "after": current.strip() or "未明确记载",
                "page": row_page,
                "pages": list(range(row_page, end_page + 1)),
                "evidence": row,
            })
        if changes:
            return changes, page_number
    return [], None


def extract_component_rows(pages: list[str], start_page: int = 1) -> list[dict]:
    rows = []
    for page_number, text in enumerate(pages, start_page):
        if "-P-S" not in text and "安全关键件清单" not in text:
            continue
        for part_number, body in COMPONENT_ROW_PATTERN.findall(text):
            rows.append({"part_number": part_number, "body": body.strip(), "page": page_number})
    return rows


def extract_component_details(body: str, part_number: str) -> dict:
    text = normalize_text(body)
    standards = list(dict.fromkeys(re.findall(r"GB(?:/T)?\s*\d+(?:\.\d+)?-\d{4}", text)))
    standard_start = re.search(r"GB(?:/T)?\s*\d", text)
    specification_match = None
    if part_number.startswith("1-"):
        specification_match = re.search(r"输入[:：].*?[)）]", text)
    elif part_number.startswith("2-"):
        specification_match = re.search(r"V-0(?:级)?", text)
    elif part_number == "3" or part_number.startswith("3-"):
        specification_match = re.search(r"ABS.*?试验|ABS", text)
    elif part_number.startswith("4-"):
        specification_match = re.search(r"波长[:：].*?Class\s*1", text, re.I)

    companies = []
    if specification_match and standard_start:
        manufacturer_section = text[specification_match.end():standard_start.start()]
        companies = re.findall(r"[\u4e00-\u9fff]{2,45}?有限公司", manufacturer_section)
    return {
        "specification": specification_match.group() if specification_match else None,
        "manufacturer": companies[0] if companies else None,
        "factory": companies[1] if len(companies) > 1 else None,
        "standard": "; ".join(standards) if standards else None,
    }


def extract_safety_components(pages: list[str]) -> list[dict]:
    start = next((index for index, text in enumerate(pages) if "安全关键件清单" in text), None)
    if start is None:
        return []
    end = next(
        (index for index in range(start + 1, len(pages))
         if "产品电气原理图" in pages[index] or "电磁兼容关键件清单" in pages[index]),
        len(pages),
    )
    components = []
    for row in extract_component_rows(pages[start:end], start + 1):
        part_number = row["part_number"]
        body = row["body"]
        if part_number.startswith("1-"):
            name = "开关电源适配器" if re.search(r"开关电源适\s*配器", body) else "电源适配器"
            model_match = re.search(r"(?:开关电源适\s*配器|电源适配器)\s+(\S+)", body)
        elif part_number.startswith("2-"):
            name = "印制板基材（PCB）"
            model_match = re.search(
                r"^(?:/\s*/\s*)?(?:印制板基材\s*[（(]PCB[）)])?\s*(.*?)\s+V-0(?:\s*级)?(?=\s|$)",
                body, re.S,
            )
        elif part_number == "3" or part_number.startswith("3-"):
            name = "外壳"
            model_match = re.search(r"^(?:/\s*/\s*)?(?:外壳\s+)?(\S+)", body)
        else:
            laser_match = re.search(r"激光\s*模块\s*[（(]([^）)]*)[）)]", body)
            name = f"激光模块（{laser_match.group(1).strip()}）" if laser_match else "激光模块"
            model_match = re.search(
                r"^(?:/\s*/\s*)?(?:激光\s*模块(?:\s*[（(][^）)]*[）)])?\s+)?(.*?)\s*波长",
                body, re.S,
            )

        model = normalize_text(model_match.group(1)) if model_match else "未识别"
        model = re.sub(r"-\s+(?=[A-Za-z0-9])", "-", model)
        certificate_match = re.search(r"证书号\s*[:：]\s*(CQC\s*)?(\d[\d\s]{6,24})", body)
        certificate = None
        if certificate_match:
            certificate = (certificate_match.group(1) or "").strip() + re.sub(r"\s+", "", certificate_match.group(2))
        remark_match = re.search(r"原报告确认|经核查[，,]?\s*无需\s*试验|[（(](?:主用|备用)[）)]", body)
        components.append({
            "part_number": part_number,
            "name": name,
            "model": model,
            "certificate": certificate,
            "remark": normalize_text(remark_match.group()) if remark_match else None,
            "page": row["page"],
            "raw": body,
            **extract_component_details(body, part_number),
        })
    return components


def enrich_changes(pages: list[str], changes: list[dict], safety_components: list[dict] | None = None) -> list[str]:
    rows = extract_component_rows(pages)
    if safety_components is None:
        safety_components = extract_safety_components(pages)
    warnings = []
    for change in changes:
        change["components"] = []
        if change["category"] != "新增":
            continue
        if "激光模块" in change["item"]:
            candidates = [
                row for row in rows
                if row["part_number"].startswith("4-")
                and "激光模块" in normalize_text(row["body"][:50])
                and "原报告确认" not in row["body"]
                and re.search(r"经核查|本次增加|新增", row["body"])
            ]
            if len(candidates) != 1:
                warnings.append("激光模块未能在安全关键件清单中唯一定位，请人工核对粗体项")
                continue
            row = candidates[0]
            match = re.search(
                r"激光\s*模块\s*[（(]\s*([^）)]*)[）)]\s*(.*?)\s*波长",
                row["body"], re.S,
            )
            if not match:
                warnings.append(f"激光模块第 {row['page']} 页型号未能解析，请人工核对")
                continue
            model = re.sub(r"\s+", "", match.group(2))
            change["components"].append({
                "part_number": row["part_number"],
                "name": f"激光模块（{match.group(1).strip()}）",
                "model": model,
                "certificate": None,
                "page": row["page"],
                "evidence": normalize_text(row["body"]),
            })
        elif "电源适配器" in change["item"]:
            certificate_match = re.search(r"证书号\s*[:：]\s*(\d{14,20})", change["after"])
            certificate = certificate_match.group(1) if certificate_match else None
            if not certificate:
                warnings.append("新增电源适配器的变更说明未提取到明确证书号，无法按证书匹配；型号等其他文本不能视为证书号")
                continue
            candidates = [part for part in safety_components if certificate and part["certificate"] == certificate]
            if len(candidates) != 1:
                warnings.append("新增电源适配器未能按证书号在安全关键件清单中唯一定位")
                continue
            part = candidates[0]
            change["components"].append({
                "part_number": part["part_number"], "name": part["name"],
                "model": part["model"], "certificate": part["certificate"],
                "page": part["page"], "evidence": normalize_text(part["raw"]),
            })
        elif "主板方案" in change["item"]:
            board_candidates = []
            for page_number, text in enumerate(pages, 1):
                if "-P-E" not in text or "主板" not in text:
                    continue
                for part_number, body in COMPONENT_ROW_PATTERN.findall(text):
                    if part_number.startswith("1-") and "主板" in body[:20] and "本次增加" in body:
                        board_candidates.append((page_number, part_number, body))
            if len(board_candidates) != 1:
                warnings.append("新增主板方案未能在电磁兼容关键件清单中唯一定位")
                continue
            page_number, part_number, body = board_candidates[0]
            model_match = re.search(r"主板\s*/\s*(.*?)\s*/(?=\s|$)", body, re.S)
            if not model_match:
                warnings.append("新增主板方案的型号未能解析")
                continue
            scheme_match = re.search(r"方案\s*(\d+)", body)
            name = f"主板方案 {scheme_match.group(1)}" if scheme_match else "主板方案"
            model = re.sub(r"-\s+(?=[A-Za-z0-9])", "-", normalize_text(model_match.group(1)))
            change["components"].append({
                "part_number": part_number, "name": name, "model": model,
                "certificate": None, "page": page_number, "evidence": normalize_text(body),
            })
        elif "PCB" in change["item"].upper():
            certificates = re.findall(r"CQC\d{8,16}", change["after"], re.I)
            if not certificates:
                warnings.append("变更确认表没有给出 PCB 证书号，无法可靠关联具体型号")
                continue
            for certificate in dict.fromkeys(certificates):
                candidates = [
                    row for row in rows
                    if row["part_number"].startswith("2-")
                    and certificate.upper() in re.sub(r"\s+", "", row["body"]).upper()
                ]
                if len(candidates) != 1:
                    warnings.append(f"PCB 证书号 {certificate} 未能在安全关键件清单中唯一定位")
                    continue
                row = candidates[0]
                model_match = re.search(r"(.*?)\s+V-0\s*级", row["body"], re.S)
                if not model_match:
                    warnings.append(f"PCB 证书号 {certificate} 对应的型号未能解析")
                    continue
                change["components"].append({
                    "part_number": row["part_number"],
                    "name": "印制板基材（PCB）",
                    "model": normalize_text(model_match.group(1)),
                    "certificate": certificate,
                    "page": row["page"],
                    "evidence": normalize_text(row["body"]),
                })
    return warnings


def count_page_photo_images(page, reader: PdfReader) -> int:
    resources = page.get("/Resources")
    contents = page.get_contents()
    if resources is None or contents is None:
        return 0

    def count_drawn_images(stream, current_resources, depth):
        if depth > 5:
            return 0
        resource_dict = current_resources.get_object()
        references = resource_dict.get("/XObject")
        objects = references.get_object() if references else {}
        count = 0
        for operands, operator in ContentStream(stream, reader).operations:
            if operator == b"Do":
                reference = objects.get(operands[0])
                if reference is None:
                    continue
                image = reference.get_object()
                if image.get("/Subtype") == "/Image":
                    if int(image.get("/Width", 0)) >= 200 and int(image.get("/Height", 0)) >= 150:
                        count += 1
                elif image.get("/Subtype") == "/Form":
                    count += count_drawn_images(image, image.get("/Resources") or current_resources, depth + 1)
            elif operator == b"INLINE IMAGE" and isinstance(operands, dict):
                settings = operands.get("settings", {})
                width = settings.get("/W", settings.get("/Width", 0))
                height = settings.get("/H", settings.get("/Height", 0))
                if int(width) >= 200 and int(height) >= 150:
                    count += 1
        return count

    return count_drawn_images(contents, resources, 0)


def check_photo_pages(reader: PdfReader, pages: list[str]) -> dict:
    checked = []
    issues = []
    reference_pages = []
    for index, text in enumerate(pages):
        section = PHOTO_SECTION_PATTERN.search(text)
        if not section:
            continue
        page_number = index + 1
        description = text[section.end():]
        if re.search(r"(?:详见|参见)\s*.*(?:照片|图片).*页", description):
            reference_pages.append(page_number)
            continue
        captions = [
            normalize_text(line) for line in description.splitlines()
            if ("图" in line or "照片" in line) and not PHOTO_PLACEHOLDER_PATTERN.search(line)
        ]
        try:
            image_count = count_page_photo_images(reader.pages[index], reader)
        except Exception:
            image_count = None
        reasons = []
        if image_count is None:
            reasons.append("无法读取该页的嵌入图像，请核对原 PDF")
        elif captions and image_count < len(captions):
            reasons.append(f"发现 {len(captions)} 处照片标题，仅检测到 {image_count} 张嵌入图片")
        elif not captions:
            reasons.append("照片页未识别到图示标题，无法确认图片是否齐全")
        if PHOTO_PLACEHOLDER_PATTERN.search(description):
            reasons.append("页面文字包含待贴图或缺图标记")
        row = {"page": page_number, "captions": captions, "image_count": image_count}
        checked.append(row)
        if reasons:
            issues.append({**row, "reason": "；".join(reasons)})

    return {
        "status": "未发现疑似漏贴" if checked and not issues else "疑似漏贴" if issues else "未找到照片页",
        "pages_checked": len(checked),
        "reference_pages": reference_pages,
        "expected_images": sum(len(row["captions"]) for row in checked),
        "embedded_images": sum(row["image_count"] or 0 for row in checked),
        "issues": issues,
    }


def analyze_cqc_pdf(contents: bytes) -> dict:
    try:
        reader = PdfReader(io.BytesIO(contents))
        if reader.is_encrypted:
            raise ValueError("暂不支持加密的 PDF 报告")
        if len(reader.pages) > 200:
            raise ValueError("报告页数超过 200 页，请拆分或人工处理")
        pages = []
        total_chars = 0
        for page in reader.pages:
            text = page.extract_text() or ""
            total_chars += len(text)
            if len(text) > 200000 or total_chars > 2000000:
                raise ValueError("PDF 文本量过大，请拆分报告后重试")
            pages.append(text)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("无法读取 PDF 报告，请检查文件是否损坏") from exc

    if sum(len(page.strip()) for page in pages) < 50:
        raise ValueError("PDF 缺少可提取文字；扫描版报告暂需先进行 OCR")

    fields = extract_fields(pages)
    changes, change_page = extract_changes(pages)
    safety_components = extract_safety_components(pages)
    photo_check = check_photo_pages(reader, pages)
    warnings = [f"未提取到{field}，请人工核对" for field, data in fields.items() if not data["value"]]
    blank_pages = [str(i + 1) for i, text in enumerate(pages) if not text.strip()]
    if blank_pages:
        warnings.append("以下页未提取到文字，可能是扫描页或空白页，需人工核对：" + "、".join(blank_pages))
    if not safety_components:
        warnings.append("未识别安全关键件清单，不能据此认定没有关键件或变更")
    if not photo_check["pages_checked"]:
        warnings.append("未识别可检查的照片页，照片完整性检查未完成")
    warnings.extend(enrich_changes(pages, changes, safety_components))
    if change_page is None:
        warnings.append("未识别到变更确认表，无法可靠判定增加或减少的内容")
    elif not changes:
        warnings.append("变更确认表未识别到编号变更项，请人工核对")
    if not any(change["category"] == "减少" for change in changes):
        warnings.append("报告的变更确认表未明确列出减少项；这不等于确认没有减少")
    if any("激光模块" in change["item"] and "粗体" in change["after"] for change in changes):
        warnings.append("激光模块依据关键件清单中的非原报告确认项定位，请对照 PDF 粗体标记复核")

    dates = {}
    for field in ("收样日期", "完成日期"):
        value = fields[field]["value"]
        if not value:
            continue
        try:
            dates[field] = datetime.strptime(value, "%Y年%m月%d日")
        except ValueError:
            warnings.append(f"{field}格式或日期不合法，请人工核对：{value}")
    if len(dates) == 2 and dates["完成日期"] < dates["收样日期"]:
        warnings.append("完成日期早于收样日期，请人工核对")

    return {
        "page_count": len(pages),
        "fields": fields,
        "changes": changes,
        "safety_components": safety_components,
        "photo_check": photo_check,
        "change_page": change_page,
        "warnings": warnings,
        "summary": {
            "added": sum(change["category"] == "新增" for change in changes),
            "removed": sum(change["category"] == "减少" for change in changes),
            "other": sum(change["category"] == "其他变更" for change in changes),
        },
    }
