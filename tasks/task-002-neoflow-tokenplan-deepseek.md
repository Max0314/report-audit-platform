# Task 002：测试报告审查切换 NeoFlow Token Plan DeepSeek

## 状态

- 交付分支：`fix/task-002-neoflow-tokenplan-deepseek`
- Worktree：`D:\code_CPL\.codex-worktrees\report-audit-neoflow-tokenplan`
- 基线：`origin/main` `53ac242`
- 当前状态：未提交

## 目标

- 默认通过 NeoFlow OpenAI 兼容接口调用 Token Plan 的 `deepseek/deepseek-v4-pro`。
- 未启用思考模式时显式传递 `enable_thinking=false`，避免 DeepSeek 默认返回推理内容。
- 使用本服务独立 `AI_API_KEY`，不复用硬件门户主服务或其他应用密钥。

## 验收条件

- 默认 provider 为通用 `neoflow`，base URL 和 model 指向 NeoFlow 模型服务；不固定上游供应商。
- NeoFlow Token Plan 模式始终显式传递思考开关。
- NeoFlow 请求不发送 `provider`，由平台按 Token Plan 优先、多 Key 自动切换和 OpenRouter 回退策略路由。
- 既有 DashScope/DeepSeek 配置仍可通过环境变量覆盖。
- 单元测试和 Python 编译检查通过。

## 正式切换前置条件

NeoFlow 平台需先创建测试报告审查独立应用并生成专用 API Key；未满足时仅交付兼容代码，不修改正式环境。
