import os
import unittest
from unittest.mock import patch

from app.core.config import AIServiceSettings
from app.services.ai_checker import build_thinking_extra_body


class NeoFlowTokenPlanSettingsTests(unittest.TestCase):
    def test_defaults_use_neoflow_deepseek_v4_pro(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = AIServiceSettings()
        self.assertEqual(settings.provider, "neoflow")
        self.assertEqual(settings.base_url, "https://neoflow.neo-net.com/api/v1")
        self.assertEqual(settings.model, "deepseek/deepseek-v4-pro")

    def test_neoflow_explicitly_disables_thinking(self):
        self.assertEqual(
            build_thinking_extra_body("neoflow_token_plan", False),
            {"enable_thinking": False},
        )

    def test_neoflow_can_enable_thinking(self):
        self.assertEqual(
            build_thinking_extra_body("neoflow_token_plan", True),
            {"enable_thinking": True},
        )

    def test_legacy_provider_keeps_previous_behavior(self):
        self.assertEqual(build_thinking_extra_body("dashscope", False), {})

    def test_neoflow_does_not_pin_upstream_provider(self):
        self.assertNotIn(
            "provider", build_thinking_extra_body("neoflow_token_plan", False)
        )


if __name__ == "__main__":
    unittest.main()
