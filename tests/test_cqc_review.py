"""CQC 单报告字段和变更项提取测试。"""

import io
import unittest
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject


spec = spec_from_file_location("cqc_report", Path(__file__).parents[1] / "app/services/cqc_report.py")
cqc_report = module_from_spec(spec)
spec.loader.exec_module(cqc_report)


class CqcReviewTests(unittest.TestCase):
    def test_extract_fields_preserves_multiline_sample_name(self):
        pages = [
            "报告编号：A-001\n",
            "样品名称：10Gbit/s无源光\n网络用户端设备（XG-PON\nONU）\n"
            "样品型号：DCMG150\n样品数量：1台\n"
            "收样日期：2026年03月26日\n完成日期：2026年03月27日\n委托人：测试公司\n",
        ]
        fields = cqc_report.extract_fields(pages)
        self.assertEqual(fields["样品名称"]["value"], "10Gbit/s无源光网络用户端设备（XG-PON ONU）")
        self.assertEqual(fields["样品名称"]["page"], 2)
        self.assertEqual(fields["样品型号"]["value"], "DCMG150")
        self.assertEqual(fields["完成日期"]["value"], "2026年03月27日")

    def test_extract_changes_only_from_confirmation_table(self):
        pages = [
            "报告组成\n变更确认表\n",
            "变更确认表\n序号和名称\n（1）增加一款激\n光模块 详见原报告\n"
            "本次增加：见关键件清单\n（2）减少一款 PCB 详见原报告\n"
            "本次删除：证书号 A\n安全试验 /\n变更结论：准予变更\n",
            "本次申请仅增加一款激光模块\n",
        ]
        changes, page = cqc_report.extract_changes(pages)
        self.assertEqual(page, 2)
        self.assertEqual(len(changes), 2)
        self.assertEqual(changes[0]["category"], "新增")
        self.assertEqual(changes[0]["item"], "增加一款激光模块")
        self.assertEqual(changes[1]["category"], "减少")
        self.assertIn("证书号 A", changes[1]["after"])

    def test_missing_confirmation_table_does_not_infer_changes(self):
        changes, page = cqc_report.extract_changes(["本次申请增加两款 PCB，详见原报告。"])
        self.assertEqual(changes, [])
        self.assertIsNone(page)

    def test_resolve_added_laser_and_pcb_by_confirmation_and_parts_list(self):
        pages = [
            "变更确认表\n（1）增加一款激光模块 详见原报告\n"
            "本次增加：见关键件清单中粗体部分\n"
            "（2）增加两款 PCB 详见原报告\n"
            "本次增加：证书号：CQC21001318856\n"
            "证书号：CQC24001452089\n安全试验 /\n变更结论：准予变更\n",
            "报告编号：A-P-S\n2-9 RJX-01 V-0级 制造商 GB 4943.1-2022\n"
            "证书号：\nCQC210013188\n56\n"
            "2-10 KB-6164，NY2140 V-0级 制造商 GB 4943.1-2022\n"
            "证书号：\nCQC240014520\n89\n"
            "3 / / 外壳\n4-5 激光模块（下行） OLD-01\n波长：1490nm 原报告确认\n",
            "报告编号：A-P-S\n4-6 激光模块（上行）\n"
            "YSB962D3C-P-S784-\nC2694\n波长：1270/1310nm\n"
            "经核查，无需试验\n",
        ]
        changes, _ = cqc_report.extract_changes(pages)
        warnings = cqc_report.enrich_changes(pages, changes)
        self.assertEqual(warnings, [])
        self.assertEqual([(part["part_number"], part["model"], part["page"])
                          for part in changes[0]["components"]],
                         [("4-6", "YSB962D3C-P-S784-C2694", 3)])
        self.assertEqual([(part["part_number"], part["model"], part["certificate"])
                          for part in changes[1]["components"]],
                         [("2-9", "RJX-01", "CQC21001318856"),
                          ("2-10", "KB-6164，NY2140", "CQC24001452089")])

    def test_does_not_guess_laser_when_multiple_unconfirmed_candidates(self):
        changes = [{"category": "新增", "item": "增加一款激光模块", "after": "见清单"}]
        pages = ["报告编号：A-P-S\n"
                 "4-1 激光模块（上行） OLD-01 波长：1270nm 原报告确认\n"
                 "4-2 激光模块（上行） NEW-01 波长：1270nm 经核查\n"
                 "4-3 激光模块（上行） NEW-02 波长：1270nm 经核查\n"]
        warnings = cqc_report.enrich_changes(pages, changes)
        self.assertEqual(changes[0]["components"], [])
        self.assertTrue(any("唯一定位" in warning for warning in warnings))

    def test_extract_complete_safety_components_with_inherited_names(self):
        pages = [
            "安全关键件清单:\n报告编号：A-P-S\n"
            "1-1 / / 电源适配器 TEKA-TD120200CH 输入：100V GB 4943.1-2022\n"
            "证书号：\n2020010907351\n198\n（主用）\n"
            "2-1 / / 印制板基材（PCB）NY1140、S1141 V-0级 制造商\n"
            "证书号：CQC1000105466\n9\n",
            "报告编号：A-P-S\n"
            "2-9 RJX-01 V-0级 制造商 GB 4943.1-2022\n"
            "证书号：\nCQC210013188\n56\n"
            "3 / / 外壳 HI-121H ABS 原报告确认\n"
            "4-6 激光模块（上行）\nYSB962D3C-P-S784-\nC2694\n"
            "波长：1270nm 经核查，无需试验\n",
            "报告编号：A-P-S\n产品电气原理图\n1-2 不是清单中的条目\n",
        ]
        components = cqc_report.extract_safety_components(pages)
        self.assertEqual([part["part_number"] for part in components], ["1-1", "2-1", "2-9", "3", "4-6"])
        self.assertEqual(components[0]["certificate"], "2020010907351198")
        self.assertEqual(components[2]["certificate"], "CQC21001318856")
        self.assertEqual(components[2]["name"], "印制板基材（PCB）")
        self.assertEqual(components[3]["model"], "HI-121H")
        self.assertEqual(components[4]["model"], "YSB962D3C-P-S784-C2694")
        self.assertEqual(components[4]["page"], 2)

    def test_safety_components_support_v0_and_inherited_part_names(self):
        pages = [
            "安全关键件清单\n报告编号：A-P-S\n"
            "1-4 开关电源适\n配器 SCGQ24-120200-CC 输入：100V 证书号：202501090776\n3116\n"
            "2-1 / / 印制板基材（PCB）KB-6165F，KB-6167F V-0 制造商 证书号：CQC1800118558\n7\n"
            "2-2 KB-6160，KB-6164F V-0 制造商 证书号：CQC1500112181\n6\n",
            "报告编号：A-P-S\n"
            "3-1 / / 外壳 HI-121H ABS 原报告确认\n"
            "3-2 / / HI-121H ABS 原报告确认\n"
            "4-1 / / 激光模块 YSB762D4C-P-S268-\nC2192(CH)-1 波长：1270nm 原报告确认\n"
            "4-2 / / OB491-34E-33-17UX-\n210UPC 波长：1270nm 原报告确认\n",
        ]
        components = cqc_report.extract_safety_components(pages)
        self.assertEqual(len(components), 7)
        self.assertFalse(any(part["model"] == "未识别" for part in components))
        self.assertEqual([part["model"] for part in components], [
            "SCGQ24-120200-CC", "KB-6165F，KB-6167F", "KB-6160，KB-6164F",
            "HI-121H", "HI-121H", "YSB762D4C-P-S268-C2192(CH)-1",
            "OB491-34E-33-17UX-210UPC",
        ])
        self.assertEqual(components[3]["name"], "外壳")
        self.assertEqual(components[5]["name"], "激光模块")

    def test_change_table_and_board_variant_are_not_folded_into_title(self):
        pages = [
            "变更确认表\n（1）增加一款电源适配器 详见原报告 本次增加：证书号：2025010907763116\n"
            "（2）增加一款主板方案 详见原报告 本次增加：见关键件清单中粗体部分\n"
            "（3）更新认证实施规则 CQC12-045800-2022《原规则》 CQC12-045800-2026《新规则》\n"
            "（4）删除一款 PCB 详见原报告 本次删除：证书号：CQC23001382250\n"
            "（5）变更RJ45端口传输速率 10/100/1000Mbps 10/100/1000/2500Mbps\n"
            "（6）增加产品名称 10Gbit/s 旧名称 10Gbit/s 新名称\n"
            "安全试验 /\n变更结论：准予变更\n",
            "安全关键件清单\n报告编号：A-P-S\n"
            "1-4 电源适配器 SCGQ24-120200-CC 输入：100V 证书号：202501090776\n3116\n",
            "电磁兼容关键件清单\n报告编号：A-P-E\n"
            "1-1 主板 / HC6-\nFMIProPCB-XGPON_P0 / 厂家 原报告确认 方案 1\n"
            "1-3 主板 / HC6-\nFMIProPCB-XGPON_P0 / 厂家 本次增加，方案3\n",
        ]
        changes, page = cqc_report.extract_changes(pages)
        safety_components = cqc_report.extract_safety_components(pages)
        warnings = cqc_report.enrich_changes(pages, changes, safety_components)
        self.assertEqual(page, 1)
        self.assertEqual(warnings, [])
        self.assertEqual(changes[0]["components"][0]["model"], "SCGQ24-120200-CC")
        self.assertEqual(changes[1]["components"][0]["model"], "HC6-FMIProPCB-XGPON_P0")
        self.assertEqual(changes[1]["components"][0]["part_number"], "1-3")
        self.assertEqual(changes[2]["item"], "更新认证实施规则")
        self.assertEqual(changes[2]["before"], "CQC12-045800-2022《原规则》")
        self.assertEqual(changes[2]["after"], "CQC12-045800-2026《新规则》")
        self.assertEqual(changes[3]["category"], "减少")
        self.assertEqual(changes[4]["before"], "10/100/1000Mbps")
        self.assertEqual(changes[4]["after"], "10/100/1000/2500Mbps")
        self.assertEqual(changes[5]["item"], "增加产品名称")
        self.assertEqual(changes[5]["before"], "10Gbit/s 旧名称")
        self.assertEqual(changes[5]["after"], "10Gbit/s 新名称")

    def test_photo_check_finds_missing_images_but_skips_reference_page(self):
        writer = PdfWriter()
        for sizes in ([(220, 180)], [(220, 180), (300, 200)], [], [(119, 83)], [(220, 180)]):
            page = writer.add_blank_page(width=600, height=800)
            objects = DictionaryObject()
            drawing = []
            for index, (width, height) in enumerate(sizes):
                image = DecodedStreamObject()
                image.set_data(b"\xff" * (width * height))
                image.update({
                    NameObject("/Type"): NameObject("/XObject"),
                    NameObject("/Subtype"): NameObject("/Image"),
                    NameObject("/Width"): NumberObject(width),
                    NameObject("/Height"): NumberObject(height),
                    NameObject("/ColorSpace"): NameObject("/DeviceGray"),
                    NameObject("/BitsPerComponent"): NumberObject(8),
                })
                name = NameObject(f"/IM{index}")
                objects[name] = writer._add_object(image)
                drawing.append(f"q {name} Do Q")
            content = DecodedStreamObject()
            content.set_data("\n".join(drawing).encode("ascii"))
            page[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"): objects})
            page[NameObject("/Contents")] = writer._add_object(content)

        stream = io.BytesIO()
        writer.write(stream)
        stream.seek(0)
        reader = PdfReader(stream)
        texts = [
            "样 品 照 片（安全）\n外观图\n铭牌图",
            "样 品 照 片（安全）\n外观图\n铭牌图",
            "样 品 照 片（EMC）\n详见安全描述报告样品照片页",
            "样 品 照 片（安全）\n外观图",
            "样 品 照 片（安全）\n外观图\n图片待补",
        ]
        result = cqc_report.check_photo_pages(reader, texts)
        self.assertEqual(result["pages_checked"], 4)
        self.assertEqual(result["reference_pages"], [3])
        self.assertEqual(result["expected_images"], 6)
        self.assertEqual(result["embedded_images"], 4)
        self.assertEqual([issue["page"] for issue in result["issues"]], [1, 4, 5])
        self.assertEqual(result["status"], "疑似漏贴")


if __name__ == "__main__":
    unittest.main()
