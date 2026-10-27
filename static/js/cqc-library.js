(() => {
    const url = path => window.ReportAudit.url('/api/v1/cqc-library' + path);
    const status = document.getElementById('libraryStatus');
    const errors = document.getElementById('libraryErrors');
    let revision = null;
    let busy = false;
    const line = (parent, value) => { const p = document.createElement('p'); p.textContent = value; parent.appendChild(p); };
    const date = value => new Date(value).toLocaleString('zh-CN');
    async function read(response) {
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请求失败，请刷新重试');
        return data;
    }
    function controls() {
        document.getElementById('librarySubmit').disabled = busy || revision === null;
        document.getElementById('libraryRefresh').disabled = busy;
        for (const button of document.querySelectorAll('#libraryVersions button')) button.disabled = busy;
    }
    async function refresh() {
        revision = null;
        controls();
        const state = await read(await fetch(url(''), {cache: 'no-store'}));
        revision = state.revision;
        const active = state.versions.find(v => v.version === state.active);
        document.getElementById('libraryCurrent').textContent = active ?
            `当前库：${active.record_count} 条 PCB · 版本 ${active.version.slice(0, 12)} · 最近导入：${active.filename}` : '尚未配置认证库，请上传首个版本。';
        const versions = document.getElementById('libraryVersions'); versions.replaceChildren();
        for (const v of [...state.versions].reverse()) {
            const card = document.createElement('article'); card.className = 'library-version';
            line(card, `${v.version === state.active ? '当前生效 · ' : ''}${v.filename} · ${v.record_count} 条 · ${v.version.slice(0, 12)}`);
            line(card, `上传时间：${date(v.uploaded_at)} · 操作人：${v.uploaded_by}`);
            if (v.version !== state.active) {
                const button = document.createElement('button'); button.className = 'btn'; button.textContent = '回滚到此完整库';
                button.addEventListener('click', () => {
                    if (!window.confirm('回滚将恢复该版本的完整认证库，之后新增或修改的记录不会保留。是否继续？')) return;
                    update(async () => {
                    await read(await fetch(url('/activate'), {method: 'POST', headers: {
                        'Content-Type': 'application/json', 'X-CSRF-Token': window.ReportAudit.csrfToken(),
                    }, body: JSON.stringify({version: v.version, expected_revision: revision})}));
                    status.textContent = `已启用版本 ${v.version.slice(0, 12)}，后续审核立即生效。`;
                    });
                });
                card.appendChild(button);
            }
            versions.appendChild(card);
        }
        const events = document.getElementById('libraryEvents'); events.replaceChildren();
        for (const event of state.events.slice(-20).reverse()) line(events,
            `${date(event.at)} · ${event.actor} · ${event.action === 'upload' ? '上传启用' : '版本切换'} · ${event.version.slice(0, 12)}`);
        controls();
    }
    async function update(action) {
        if (busy || revision === null) return;
        busy = true; controls(); errors.textContent = ''; status.textContent = '正在校验并保存，请稍候…';
        try { await action(); await refresh(); }
        catch (error) { status.textContent = error.message; revision = null; }
        finally { busy = false; controls(); }
    }
    document.getElementById('libraryForm').addEventListener('submit', event => {
        event.preventDefault();
        const file = document.getElementById('libraryFile').files[0];
        if (!file) return;
        if (file.size > 5 * 1024 * 1024) { status.textContent = 'Excel 不能超过 5 MB'; return; }
        update(async () => {
            const body = new FormData(); body.append('file', file); body.append('expected_revision', revision);
            const result = await read(await fetch(url('/upload'), {method: 'POST', body,
                headers: {'X-CSRF-Token': window.ReportAudit.csrfToken()}}));
            if (result.errors) {
                status.textContent = `校验失败（${result.error_count} 项），未更新认证库。`;
                errors.textContent = result.errors.join('\n');
            } else if (result.unchanged) status.textContent = '本次导入记录与当前库一致，无需更新；未提及的记录保持不变。';
            else {
                const d = result.changes;
                status.textContent = `增量更新成功：新增 ${d.added} 条、修改 ${d.modified} 条、保留不变 ${d.unchanged} 条；未删除任何记录。`;
            }
        });
    });
    document.getElementById('libraryRefresh').addEventListener('click', () => refresh().catch(error => { status.textContent = error.message; }));
    refresh().catch(error => { status.textContent = error.message; });
})();
