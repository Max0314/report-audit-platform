---
AIGC:
    ContentProducer: Minimax Agent AI
    ContentPropagator: Minimax Agent AI
    Label: AIGC
    ProduceID: "00000000000000000000000000000000"
    PropagateID: "00000000000000000000000000000000"
    ReservedCode1: 3045022100adf7ae9377a187d2322a72cfd398aaf7074ace3ba33c475b948d6dfe5586165402207ceec326144fca4dd16c28f230895d8bbebe924f906376d032a5aff19b4418f7
    ReservedCode2: 304402203dcbc6e932c4fdadb6ff26382b453ace4f5623c573fb7d5e615b2869fae0f7ab022005cefd584d51ec1be6c77238d33019228b465171e4479a0638f47c557859791a
---

# DeepSeek API 配置教程

## 一、DeepSeek API 简介

DeepSeek 是一个国产的大语言模型服务提供商，提供高性能的 AI 能力。本平台集成了 DeepSeek API 实现智能错别字检测和语义分析功能。

---

## 二、获取 DeepSeek API Key

### 2.1 注册账号

1. 访问 DeepSeek 官网：https://www.deepseek.com
2. 点击「注册」按钮
3. 使用邮箱或手机号完成注册
4. 完成实名认证（可选，但推荐）

### 2.2 创建 API Key

1. 登录后进入控制台
2. 找到「API Keys」或「开发者」选项
3. 点击「创建新的 API Key」
4. 复制生成的 API Key（注意：只会显示一次，请妥善保存）

---

## 三、配置 API Key

### 3.1 方式一：环境变量（推荐）

编辑项目根目录下的 `.env` 文件：

```bash
# 填入您的 API Key
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### 3.2 方式二：配置文件

编辑 `config/settings.yaml`：

```yaml
ai_service:
  api_key: "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

> ⚠️ **注意**：建议使用环境变量方式，避免 API Key 泄露

---

## 四、验证配置

### 4.1 启动服务

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 4.2 检查 AI 服务状态

1. 访问 http://localhost:8000/review
2. 点击页面右上角的「检查 AI 服务状态」按钮
3. 如果显示「AI 服务可用」，则配置成功

---

## 五、使用说明

### 5.1 错别字检测

在审核系统中上传测试报告后，系统会自动调用 DeepSeek API 检测：

- 错别字
- 专业术语错误
- 标点符号错误
- 语义不一致

### 5.2 一致性检查

AI 还会协助检查：

- 不同 Sheet 之间的关键信息一致性
- 逻辑矛盾检测

---

## 六、费用说明

- DeepSeek API 采用按量计费模式
- 具体费用请参考官方定价：https://www.deepseek.com/pricing
- 建议设置使用限额，避免意外扣费

---

## 七、常见问题

### Q1: API Key 格式是什么？

A: DeepSeek API Key 格式为 `sk-` 开头的一串字符。

### Q2: 调用失败怎么办？

A: 请检查：
1. API Key 是否正确配置
2. 网络是否正常
3. API 配额是否用完

### Q3: 可以不使用 AI 功能吗？

A: 可以。平台的基础校验功能（日期、一致性、逻辑、完整性）不需要 AI 即可使用。

---

## 八、备份与安全

- **不要**将 API Key 提交到代码仓库
- 定期更换 API Key
- 使用环境变量管理敏感信息

---

*文档版本：1.0.0*
