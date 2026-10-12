/* CQC 公共交互。报告和 AI 文本只通过 textContent 渲染。 */
window.Cqc = (() => {
    const url = path => window.ReportAudit.url(path);
    const text = (parent, tag, value) => {
        const element = document.createElement(tag);
        element.textContent = value;
        parent.appendChild(element);
        return element;
    };
    function panel(job) {
        document.getElementById('aiPanel').hidden = false;
        const ai = job.ai || {message: '正在解析报告…', findings: []};
        const labels = {completed: '完成', partial: '部分完成', failed: '未完成', running: '进行中'};
        document.getElementById('aiStatus').textContent =
            `${labels[ai.status] || '进行中'} · ${ai.message || ''}${ai.model ? ' 模型：' + ai.model : ''}`;
        const warnings = document.getElementById('ruleWarnings');
        warnings.replaceChildren();
        for (const warning of job.result?.warnings || []) text(warnings, 'p', '需复核：' + warning);
        const findings = document.getElementById('aiFindings');
        findings.replaceChildren();
        for (const row of ai.findings || []) {
            const card = document.createElement('article');
            card.className = 'cqc-ai-finding';
            text(card, 'h3', `[${row.severity}] ${row.issue}`);
            text(card, 'p', row.suggestion);
            const details = document.createElement('details');
            text(details, 'summary', '查看引用依据与 PDF 页码');
            for (const source of row.citations) {
                text(details, 'p', `${source.id} · ${source.source} · 引用：${source.quote}`);
                text(details, 'pre', source.text);
            }
            card.appendChild(details);
            findings.appendChild(card);
        }
        if (ai.status === 'completed' && !ai.findings?.length) {
            text(findings, 'p', 'AI 未提出额外发现；不代表报告通过认证或不存在未提取的问题。');
        }
        const download = document.getElementById('cqcExport');
        download.hidden = job.status !== 'completed';
        download.href = url(`/api/v1/cqc/tasks/${job.task_id}/export`);
    }
    async function read(response) {
        let data;
        try { data = await response.json(); } catch { throw new Error('服务暂时不可用，请稍后重试'); }
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请求失败');
        return data;
    }
    async function poll(taskId, render, status, mode) {
        let rendered = false;
        const deadline = Date.now() + 12 * 60 * 1000;
        while (Date.now() < deadline) {
            const job = await read(await fetch(url(`/api/v1/cqc/tasks/${taskId}`), {cache: 'no-store'}));
            if (job.mode !== mode) throw new Error('任务类型与当前页面不一致，请打开对应功能入口');
            if (job.status === 'failed') throw new Error(job.message || '处理失败');
            if (job.result && !rendered) { render(job.result); rendered = true; }
            panel(job);
            if (job.status === 'completed') {
                status.textContent = job.ai.status === 'completed' ? '规则与 AI 分析完成，请人工复核后使用。' : '规则检查完成；AI 分析未全部完成，请查看说明。';
                return;
            }
            status.textContent = job.status === 'analyzing' ? '规则结果已显示，AI 辅助分析进行中…' : '报告解析中…';
            await new Promise(resolve => setTimeout(resolve, 2000));
        }
        throw new Error('等待超时，可保留当前结果链接并稍后刷新查看');
    }
    async function run(mode, body, render, status) {
        document.getElementById('aiPanel').hidden = true;
        const files = [...body.values()].filter(value => value instanceof File);
        if (files.some(file => file.size > 50 * 1024 * 1024)) throw new Error('单份 PDF 不能超过 50 MB');
        const job = await read(await fetch(url(`/api/v1/cqc-${mode}/analyze`), {
            method: 'POST', body, headers: {'X-CSRF-Token': window.ReportAudit.csrfToken()},
        }));
        history.replaceState(null, '', `${location.pathname}?task=${job.task_id}`);
        await poll(job.task_id, render, status, mode);
    }
    function resume(mode, render, status, button) {
        const taskId = new URLSearchParams(location.search).get('task');
        if (!taskId) return;
        if (!/^[a-f0-9]{32}$/.test(taskId)) { status.textContent = '无效的结果链接'; return; }
        button.disabled = true;
        poll(taskId, render, status, mode).catch(error => { status.textContent = error.message; })
            .finally(() => { button.disabled = false; });
    }
    return {run, resume};
})();
