"""CQC 双报告比较的同型号校验和差异归并测试。"""

import unittest
from unittest.mock import patch

from PIL import Image

from app.services.cqc_compare import (
    compare_cqc_pdfs,
    compare_safety_components,
    normalized_model,
)
from app.services.cqc_image_compare import preview, visual_difference


def component(number, name, model, certificate=None, remark=None, **details):
    return {"part_number": number, "name": name, "model": model,
            "certificate": certificate, "remark": remark, "page": 9,
            "raw": "原始关键件条目", **details}


class CqcCompareTests(unittest.TestCase):
    def test_model_compares_without_whitespace_or_case(self):
        self.assertEqual(normalized_model("CMCC FXM6000"), normalized_model("cmccfxm6000"))
        self.assertNotEqual(normalized_model("DCMG150"), normalized_model("DCMG151"))

    def test_reordered_components_are_not_reported_as_changed(self):
        original = [component("1-1", "适配器", "A-100", "CERT001"),
                    component("1-2", "适配器", "B-200", "CERT002")]
        updated = [component("1-2", "适配器", "B-200", "CERT002"),
                   component("1-3", "适配器", "A-100", "CERT001")]
        result = compare_safety_components(original, updated)
        self.assertEqual({key: value for key, value in result.items() if key != "unchanged"},
                         {"added": [], "removed": [], "modified": [], "ambiguous": {"old": [], "new": []}})
        self.assertEqual(len(result["unchanged"]), 2)

    def test_component_add_remove_and_certificate_or_model_changes(self):
        original = [component("1-1", "适配器", "A-100", "CERT001"),
                    component("1-2", "适配器", "B-200", "CERT002"),
                    component("1-3", "适配器", "C-300", "CERT003")]
        updated = [component("1-1", "适配器", "A-101", "CERT001"),
                   component("1-2", "适配器", "B-200", "CERT004"),
                   component("1-4", "适配器", "D-400", "CERT005")]
        result = compare_safety_components(original, updated)
        self.assertEqual([part["model"] for part in result["added"]], ["D-400"])
        self.assertEqual([part["model"] for part in result["removed"]], ["C-300"])
        self.assertEqual([part["changed_fields"] for part in result["modified"]],
                         [["model"], ["certificate"]])

    def test_changed_factory_and_unparsed_detail_are_not_missed(self):
        original = [component("1-1", "电源适配器", "A-100", "CERT001", factory="深圳工厂"),
                    component("1-2", "电源适配器", "B-200", "CERT002")]
        updated = [component("1-1", "电源适配器", "A-100", "CERT001", factory="广东工厂"),
                   component("1-2", "电源适配器", "B-200", "CERT002")]
        updated[1]["raw"] = "原始关键件条目，新加备注"
        result = compare_safety_components(original, updated)
        self.assertEqual([item["changed_fields"] for item in result["modified"]],
                         [["factory"], ["raw"]])

    def test_pcb_model_variants_are_listed_separately(self):
        old = [component("2-1", "印制板基材（PCB）", "KB-6160、KB-6164", "CERT001")]
        new = [component("2-1", "印制板基材（PCB）", "KB-6164，NY2140", "CERT001")]
        change = compare_safety_components(old, new)["modified"][0]
        self.assertEqual(change["model_added"], ["NY2140"])
        self.assertEqual(change["model_removed"], ["KB-6160"])

    def test_visual_change_and_thumbnail_generation(self):
        old_image = Image.new("RGB", (220, 180), "red")
        changed_image = Image.new("RGB", (220, 180), "blue")
        resized_image = Image.new("RGB", (221, 180), "red")
        self.assertGreater(visual_difference(old_image, changed_image), 0.02)
        self.assertEqual(visual_difference(old_image, resized_image), 0)
        self.assertTrue(preview(changed_image).startswith("data:image/jpeg;base64,"))

    def test_mismatched_sample_models_are_rejected(self):
        def report(model):
            return {"fields": {"样品型号": {"value": model}}}

        with patch("app.services.cqc_compare.analyze_cqc_pdf", side_effect=[report("DCMG150"), report("DCMG151")]):
            with self.assertRaisesRegex(ValueError, "样品型号不一致"):
                compare_cqc_pdfs(b"old", b"new", "old.pdf", "new.pdf")


if __name__ == "__main__":
    unittest.main()
