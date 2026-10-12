"""对报告中的样品照片进行视觉比较并生成变化预览。"""

import base64
import hashlib
import io
import re
from difflib import SequenceMatcher
from itertools import islice

from PIL import Image, ImageChops, ImageOps, ImageStat

from app.services.cqc_report import PHOTO_SECTION_PATTERN, normalize_text


def photo_pages(reader, texts: list[str]) -> list[dict]:
    photos = []
    for index, text in enumerate(texts):
        heading = PHOTO_SECTION_PATTERN.search(text)
        if not heading:
            continue
        description = text[heading.end():]
        if re.search(r"(?:详见|参见)\s*.*(?:照片|图片).*页", description):
            continue
        captions = [normalize_text(line) for line in description.splitlines()
                    if "图" in line or "照片" in line]
        photos.append({"page": index + 1, "captions": captions, "reader_page": reader.pages[index]})
    return photos


def visual_difference(old_image: Image.Image, new_image: Image.Image) -> float:
    old_small = ImageOps.exif_transpose(old_image).convert("RGB").resize((96, 96), Image.Resampling.LANCZOS)
    new_small = ImageOps.exif_transpose(new_image).convert("RGB").resize((96, 96), Image.Resampling.LANCZOS)
    return sum(ImageStat.Stat(ImageChops.difference(old_small, new_small)).mean) / (3 * 255)


def preview(image: Image.Image) -> str:
    resized = ImageOps.exif_transpose(image).convert("RGB")
    resized.thumbnail((480, 360), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    resized.save(output, format="JPEG", quality=75, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def compare_photo_images(old_reader, new_reader, old_texts: list[str], new_texts: list[str]) -> dict:
    old_pages = photo_pages(old_reader, old_texts)
    new_pages = photo_pages(new_reader, new_texts)
    old_captions = [tuple(page["captions"]) for page in old_pages]
    new_captions = [tuple(page["captions"]) for page in new_pages]
    matcher = SequenceMatcher(None, old_captions, new_captions, autojunk=False)
    paired = []
    for _, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        shared = min(old_end - old_start, new_end - new_start)
        paired.extend((old_pages[old_start + offset], new_pages[new_start + offset])
                      for offset in range(shared))
        paired.extend((old_pages[index], None) for index in range(old_start + shared, old_end))
        paired.extend((None, new_pages[index]) for index in range(new_start + shared, new_end))

    changes = []
    format_only = []
    warnings = []
    if not old_pages or not new_pages:
        warnings.append("至少一份报告未识别到可比较的样品照片页，不能据此判断照片无变化")
    compared = 0
    processed = 0
    for old_page, new_page in paired:
        if processed >= 100:
            warnings.append("照片处理达到 100 项上限，其余照片需人工核对")
            break
        try:
            old_images = list(islice(old_page["reader_page"].images, 21)) if old_page else []
            new_images = list(islice(new_page["reader_page"].images, 21)) if new_page else []
            if max(len(old_images), len(new_images)) > 20:
                warnings.append("部分照片页图像超过 20 张，仅处理前 20 张，其余需人工核对")
            old_images, new_images = old_images[:20], new_images[:20]
        except Exception:
            page_number = (old_page or new_page)["page"]
            warnings.append(f"PDF 第 {page_number} 页图片解码失败，请对照原文件人工核对")
            continue

        old_images = [image for image in old_images if min(image.image.size) >= 150]
        new_images = [image for image in new_images if min(image.image.size) >= 150]
        for index in range(max(len(old_images), len(new_images))):
            if processed >= 100:
                warnings.append("照片处理达到 100 项上限，其余照片需人工核对")
                break
            processed += 1
            old_image = old_images[index] if index < len(old_images) else None
            new_image = new_images[index] if index < len(new_images) else None
            old_caption = old_page["captions"][index] if old_page and index < len(old_page["captions"]) else None
            new_caption = new_page["captions"][index] if new_page and index < len(new_page["captions"]) else None
            description = new_caption or old_caption or f"第 {index + 1} 张图片"
            if old_image and new_image:
                compared += 1
                if hashlib.sha256(old_image.data).digest() == hashlib.sha256(new_image.data).digest():
                    continue
                score = visual_difference(old_image.image, new_image.image)
                status = "图片内容变化" if score >= 0.02 else "仅编码或轻微画面变化"
            else:
                score = None
                status = "新增图片" if new_image else "减少图片"
            result = {
                "status": status,
                "caption": description,
                "old_page": old_page["page"] if old_page else None,
                "new_page": new_page["page"] if new_page else None,
                "old_size": list(old_image.image.size) if old_image else None,
                "new_size": list(new_image.image.size) if new_image else None,
                "visual_difference_percent": round(score * 100, 2) if score is not None else None,
                "old_preview": preview(old_image.image) if old_image else None,
                "new_preview": preview(new_image.image) if new_image else None,
            }
            if status == "仅编码或轻微画面变化":
                format_only.append(result)
            else:
                changes.append(result)

    if not compared:
        warnings.append("没有成功比较的新旧照片对，照片一致性检查未完成")
    coverage = "未完成" if not compared else "部分完成" if warnings else "已完成"
    return {
        "coverage": coverage,
        "old_photo_pages": len(old_pages),
        "new_photo_pages": len(new_pages),
        "images_compared": compared,
        "changes": changes,
        "format_only": format_only,
        "warnings": warnings,
    }
