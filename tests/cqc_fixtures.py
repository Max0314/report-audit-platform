"""完全合成的 PDF 夹具，不包含客户报告。"""
import io
from pathlib import Path
from openpyxl import Workbook

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen.canvas import Canvas


def sample_pdf(version="old", model="SYNTHETIC-TEST-01") -> bytes:
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    output = io.BytesIO()
    canvas = Canvas(output)
    pages = [
        ["合成验收报告（仅测试，不作认证使用）", "样品名称：测试用电源", f"样品型号：{model}",
         "收样日期：2026年09月01日", f"完成日期：2026年09月{'02' if version == 'old' else '03'}日"],
        ["变更确认表", "序号 变更项目 变更前 变更后", "（1）增加电源适配器 详见原报告 TEST-ADAPTER-02", "变更结论：同意变更"],
    ]
    for lines in pages:
        canvas.setFont("STSong-Light", 12)
        for i, line in enumerate(lines):
            canvas.drawString(45, 790 - 30 * i, line)
        canvas.showPage()
    canvas.save()
    return output.getvalue()


def write_fixtures(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for version in ("old", "new"):
        (directory / f"{version}.pdf").write_bytes(sample_pdf(version))
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "合成验收数据"
    sheet.append(["测试项目", "测试值", "测试结果", "备注"])
    for value in (5.0, 5.1, 5.2):
        sheet.append(["输出电压", value, "合格", "合成数据，仅用于服务验收"])
    workbook.save(directory / "synthetic.xlsx")
