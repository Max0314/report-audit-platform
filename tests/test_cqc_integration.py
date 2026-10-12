import asyncio
import io
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.auth import hardware_auth_middleware
from app.routers import cqc
from app.services.cqc_ai import analyze_with_ai, build_evidence, validate_answer
from app.services.cqc_report import analyze_cqc_pdf, extract_changes
from app.services.cqc_compare import compare_safety_components
from app.services.cqc_image_compare import compare_photo_images
from tests.cqc_fixtures import sample_pdf
from tests.test_cqc_compare import component
from pypdf import PdfWriter
from app.services.analysis_service_v2 import BoxPlotChart


class RulesRegressionTests(unittest.TestCase):
    def test_original_box_chart_retains_one_trace_per_item(self):
        result = BoxPlotChart().generate({"电压": [5, 5.1, 5.2], "电流": [1, 1.1]}, {})
        self.assertEqual([trace["name"] for trace in result["data"]], ["电压", "电流"])

    def test_encrypted_and_scanned_reports_are_rejected(self):
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        output = io.BytesIO()
        writer.write(output)
        with self.assertRaisesRegex(ValueError, "OCR"):
            analyze_cqc_pdf(output.getvalue())
        writer.encrypt("synthetic-only")
        output = io.BytesIO()
        writer.write(output)
        with self.assertRaisesRegex(ValueError, "加密"):
            analyze_cqc_pdf(output.getvalue())

    def test_shared_certificate_does_not_swap_removed_model(self):
        old = [component("1-1", "适配器", "A", "SHARED"), component("1-2", "适配器", "B", "SHARED")]
        new = [component("1-1", "适配器", "B", "SHARED")]
        result = compare_safety_components(old, new)
        self.assertEqual([row["model"] for row in result["removed"]], ["A"])
        self.assertEqual(result["modified"], [])
        self.assertEqual(len(result["unchanged"]), 1)

    def test_cross_page_change_table(self):
        changes, page = extract_changes(["变更确认表\n（1）增加电源适配器 详见原报告\n", "TEST-A\n变更结论：同意"])
        self.assertEqual(page, 1)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["pages"], [1, 2])
        self.assertIn("TEST-A", changes[0]["after"])

    def test_missing_photo_pages_is_incomplete(self):
        result = compare_photo_images(None, None, ["普通文字"], ["普通文字"])
        self.assertEqual(result["coverage"], "未完成")
        self.assertTrue(result["warnings"])

    def test_repeated_heading_keeps_previous_page_changes(self):
        changes, page = extract_changes(["目录 变更确认表", "变更确认表\n（1）增加适配器 详见原报告 A\n",
                                         "变更确认表（续）\n（2）减少PCB 详见原报告 B\n变更结论：同意"])
        self.assertEqual(page, 2)
        self.assertEqual([row["number"] for row in changes], ["1", "2"])

    def test_actual_pdf_and_export_do_not_execute_formulas(self):
        result = analyze_cqc_pdf(sample_pdf())
        self.assertEqual(result["fields"]["样品型号"]["value"], "SYNTHETIC-TEST-01")
        self.assertEqual(result["page_count"], 2)
        self.assertEqual(len(result["changes"]), 1)
        content = cqc.export_workbook({"value": '=HYPERLINK("http://example.invalid")'})
        workbook = load_workbook(io.BytesIO(content))
        self.assertEqual(workbook.active["B2"].data_type, "s")


class AiTests(unittest.IsolatedAsyncioTestCase):
    def test_fabricated_evidence_is_rejected(self):
        answer = {"findings": [{"severity": "中", "issue": "需要核对", "suggestion": "请核实", "citations": [{"id": "E1", "quote": "虚构"}]}]}
        with self.assertRaises(ValueError):
            validate_answer(json.dumps(answer), [{"id": "E1", "source": "第一页", "text": "真实文字"}])
        answer["findings"][0]["citations"][0]["quote"] = "真实文字"
        validated = validate_answer(json.dumps(answer), [{"id": "E1", "source": "第一页", "text": "真实文字"}])
        self.assertEqual(validated["findings"][0]["citations"][0]["source"], "第一页")

    def test_evidence_budget_reports_partial(self):
        result = analyze_cqc_pdf(sample_pdf())
        result["safety_components"] = [{"raw": "x" * 70000}]
        evidence, partial = build_evidence(result, "review")
        self.assertTrue(partial)
        self.assertEqual(evidence[0]["id"], "W1")

    async def test_neoflow_call_and_failure_preserve_rule_facts(self):
        result = analyze_cqc_pdf(sample_pdf())
        cfg = SimpleNamespace(api_key="fake-test-only", provider="neoflow", model="deepseek/deepseek-v4-pro",
                              base_url="https://example.invalid/v1", timeout=30, temperature=0.1,
                              max_tokens=4000, enable_thinking=False)
        client = AsyncMock()
        client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content='{"findings": []}'))])
        manager = AsyncMock()
        manager.__aenter__.return_value = client
        with patch("app.services.cqc_ai.get_settings", return_value=SimpleNamespace(ai_service=cfg)), patch("app.services.cqc_ai.AsyncOpenAI", return_value=manager):
            answer = await analyze_with_ai(result, "review")
            self.assertEqual(answer["status"], "completed")
            call = client.chat.completions.create.call_args.kwargs
            self.assertEqual(call["extra_body"], {"enable_thinking": False})
            self.assertEqual(call["model"], cfg.model)
            self.assertGreaterEqual(call["max_tokens"], 12000)
            client.chat.completions.create.return_value.choices[0].finish_reason = "length"
            truncated = await analyze_with_ai(result, "review")
            self.assertEqual(truncated["status"], "failed")
            client.chat.completions.create.side_effect = TimeoutError("secret must not leak")
            answer = await analyze_with_ai(result, "review")
        self.assertEqual(answer["status"], "failed")
        self.assertNotIn("secret", json.dumps(answer))
        self.assertEqual(result["page_count"], 2)


class CqcApiTests(unittest.TestCase):
    def setUp(self):
        cqc._jobs.clear()
        cqc._active_owners.clear()
        self.user = {"id": "owner-a"}
        self.verify = patch("app.auth._verify", new=AsyncMock(side_effect=lambda _: (self.user, True)))
        self.verify.start()
        self.objects = {}
        store = SimpleNamespace(put_bytes=lambda key, value, *args: self.objects.__setitem__(key, value),
                                get_bytes=lambda key: self.objects.get(key), delete=lambda key: self.objects.pop(key, None))
        self.store = patch("app.routers.cqc.get_store", return_value=store)
        self.store.start()
        self.storage = patch("app.storage.get_store", return_value=store)
        self.storage.start()
        self.ai = patch("app.routers.cqc.analyze_with_ai", new=AsyncMock(return_value={"status": "completed", "findings": [], "message": "test"}))
        self.ai.start()
        app = FastAPI()
        app.middleware("http")(hardware_auth_middleware)
        app.include_router(cqc.router)
        self.client = TestClient(app).__enter__()
        self.client.cookies.set("csrf_token", "test-csrf")
        self.headers = {"X-CSRF-Token": "test-csrf"}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.ai.stop()
        self.storage.stop()
        self.store.stop()
        self.verify.stop()

    def submit_and_wait(self, mode="review", new_model="SYNTHETIC-TEST-01"):
        files = {"file": ("synthetic.pdf", sample_pdf(), "application/pdf")} if mode == "review" else {
            "old_file": ("old.pdf", sample_pdf(), "application/pdf"),
            "new_file": ("new.pdf", sample_pdf("new", new_model), "application/pdf")}
        response = self.client.post(f"/api/v1/cqc-{mode}/analyze", files=files, headers=self.headers)
        self.assertEqual(response.status_code, 202, response.text)
        task_id = response.json()["task_id"]
        for _ in range(100):
            job = self.client.get(f"/api/v1/cqc/tasks/{task_id}").json()
            if job["status"] in ("completed", "failed"):
                return task_id, job
            time.sleep(0.02)
        self.fail("job did not finish")

    def test_review_compare_export_and_owner_scoped_oss_recovery(self):
        for mode in ("review", "compare"):
            task_id, job = self.submit_and_wait(mode)
            self.assertEqual(job["status"], "completed", job)
            self.assertEqual(job["ai"]["status"], "completed")
            route = f"/api/v1/cqc/tasks/{task_id}"
            self.assertEqual(self.client.get(route + "/export").status_code, 200)
            self.user = {"id": "owner-b"}
            self.assertEqual(self.client.get(route).status_code, 404)
            self.assertEqual(self.client.get(route + "/export").status_code, 404)
            cqc._jobs.clear()
            self.assertEqual(self.client.get(route).status_code, 404)
            self.user = {"id": "owner-a"}
            recovered = self.client.get(route).json()
            self.assertEqual(recovered["result"], job["result"])
        self.assertEqual(len(self.objects), 5)  # 三份 PDF、两份结果归档

    def test_model_mismatch_rolls_back_sources(self):
        _, job = self.submit_and_wait("compare", "DIFFERENT-MODEL")
        self.assertEqual(job["status"], "failed")
        self.assertIn("型号不一致", job["message"])
        self.assertEqual(self.objects, {})

    def test_auth_csrf_invalid_file_and_capacity(self):
        files = {"file": ("test.pdf", sample_pdf(), "application/pdf")}
        self.user = None
        self.assertEqual(self.client.post('/api/v1/cqc-review/analyze', files=files).status_code, 401)
        self.user = {"id": "owner-a"}
        self.assertEqual(self.client.post('/api/v1/cqc-review/analyze', files=files).status_code, 403)
        cqc._active_owners.add("owner-a")
        self.assertEqual(self.client.post('/api/v1/cqc-review/analyze', files=files, headers=self.headers).status_code, 429)
        cqc._active_owners.clear()
        files = {"file": ("bad.pdf", b"not pdf", "application/pdf")}
        self.assertEqual(self.client.post('/api/v1/cqc-review/analyze', files=files, headers=self.headers).status_code, 400)

    def test_ai_failure_still_exports_rules(self):
        with patch("app.routers.cqc.analyze_with_ai", new=AsyncMock(return_value={"status": "failed", "findings": [], "message": "timeout"})):
            task_id, job = self.submit_and_wait()
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["ai"]["status"], "failed")
        self.assertEqual(self.client.get(f"/api/v1/cqc/tasks/{task_id}/export").status_code, 200)

    def test_storage_failure_stops_ai(self):
        with patch("app.routers.cqc.persist_upload", new=AsyncMock(side_effect=OSError("failure"))):
            _, job = self.submit_and_wait()
        self.assertEqual(job["status"], "failed")
        self.assertEqual(self.objects, {})


if __name__ == "__main__":
    unittest.main()
