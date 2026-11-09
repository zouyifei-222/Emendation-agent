/* Team Prompt / SOP asset governance board. Pure rendering, no DOM access. */
export const escapeHTML = (value = '') => String(value ?? '').replace(/[&<>"']/g, c => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[c]));

export function governanceHealth(asset) {
  if (asset.pruned) return {label: '已归档', tone: 'muted'};
  if (asset.staleSuggested) return {label: '建议归档', tone: 'amber'};
  return {label: '健康', tone: 'ok'};
}

const number = (value) => Number(value || 0).toLocaleString('zh-CN');
const nameAttr = (value) => escapeHTML(value);

function heading(state) {
  const back = state.view === 'list' ? '' : '<button class="button secondary" data-gov="back">返回资产列表</button>';
  const actions = state.view === 'list'
    ? '<button class="button secondary" data-gov="refresh">刷新</button><button class="button primary" data-gov="new">新建规则</button>'
    : back;
  return `<div class="page-heading"><div><div class="eyebrow">GOVERNANCE</div><h1>资产治理</h1><p>把团队规则当成可评测、可发布、可归档的工程资产。</p></div><div class="gov-toolbar">${actions}</div></div>`;
}

function listView(state) {
  const errors = state.errors?.length
    ? `<div class="info-note">部分数据无法读取：<br>${state.errors.map(escapeHTML).join('<br>')}</div>` : '';
  if (!state.loaded) return `<div class="page-content"><p class="muted-empty">正在读取当前项目的资产数据…</p></div>`;
  const threshold = state.staleThreshold || {minRetrieved: 12, maxUsed: 0};
  const note = `<div class="info-note">建议归档只按「检索 ≥ ${number(threshold.minRetrieved)} 次且使用 ≤ ${number(threshold.maxUsed)} 次」给出，需人工确认，归档后可恢复。<br>相关与使用判定来自模型，不是客观埋点；历史回放分数只代表回归表现，不等于真实任务效果。<br>${escapeHTML(state.toolScopeNote || '')}</div>`;
  const conflictRows = (state.conflicts || []).map(row => `<li><span class="table-tag">${escapeHTML(row.type)}</span>
    ${escapeHTML(row.message)} <span class="table-description">${escapeHTML((row.assets || []).join(' / '))}</span></li>`).join('');
  const conflicts = conflictRows ? `<section class="setting-group"><h2>跨规则冲突（${(state.conflicts || []).length}）</h2><ul class="gov-history">${conflictRows}</ul></section>` : '';
  if (!state.assets.length) {
    return `<div class="page-content">${errors}<div class="library-empty"><div class="empty-symbol">+</div><h2>还没有可治理的规则</h2><p>新建一条团队规则，或从在线学习产生的候选中发布一条。</p></div>${note}</div>`;
  }
  const rows = state.assets.map(asset => {
    const health = governanceHealth(asset);
    const usage = asset.usage || {};
    const champion = asset.champion ? `<span class="table-tag amber">有优胜版本</span>` : '<span class="table-description">尚无</span>';
    return `<tr>
      <td><button class="table-name" data-gov="detail" data-name="${nameAttr(asset.name)}">${escapeHTML(asset.name)}</button>
        <p class="table-description"><span class="table-tag">${escapeHTML(asset.source === 'user' ? '用户级' : '项目级')}</span>
        ${asset.pruned ? '<span class="table-tag">已归档</span>' : ''}</p></td>
      <td class="table-description">${escapeHTML(asset.description || '暂无描述')}</td>
      <td>${number(usage.retrieved)} / ${number(usage.relevant)} / ${number(usage.used)}</td>
      <td>${champion}</td>
      <td><span class="gov-badge ${health.tone}">${health.label}</span></td>
      <td><span class="table-tag">${escapeHTML(asset.status || 'draft')}</span>
        <p class="table-description">${escapeHTML(asset.owner || '未指定负责人')}</p></td>
      <td class="gov-row-actions"><button class="text-button" data-gov="edit" data-name="${nameAttr(asset.name)}">编辑</button>
        ${asset.pruned
          ? `<button class="text-button" data-gov="restore" data-name="${nameAttr(asset.name)}">恢复</button>`
          : `<button class="text-button" data-gov="rollback" data-name="${nameAttr(asset.name)}">回退</button><button class="text-button" data-gov="archive" data-name="${nameAttr(asset.name)}">归档</button>`}</td>
    </tr>`;
  }).join('');
  return `<div class="page-content">${errors}<div class="gov-summary"><div><small>资产总数</small><strong>${state.assets.length}</strong></div>
    <div><small>建议归档</small><strong>${state.assets.filter(a => a.staleSuggested).length}</strong></div>
    <div><small>已归档</small><strong>${state.assets.filter(a => a.pruned).length}</strong></div>
    <div><small>有优胜版本</small><strong>${state.assets.filter(a => a.champion).length}</strong></div></div>
    <div><small>已登记资产</small><strong>${number(state.assetsSummary?.registered)}</strong></div></div>
    <table class="data-table gov-table"><thead><tr><th>名称</th><th>描述</th><th>检索 / 相关 / 使用</th><th>评测</th><th>健康</th><th>状态</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table>${conflicts}${note}</div>`;
}

function detailView(state) {
  const item = state.detail || {};
  const usage = item.usage || {};
  const history = (item.history || []).map(row => `<li><span class="table-tag">${escapeHTML(row.event || 'snapshot')}</span>
    <span>${escapeHTML(row.time || '')}</span><span class="table-description">${escapeHTML(row.version || '')} · ${escapeHTML(row.actor || '')}</span>
    <p class="table-description">${escapeHTML(row.lesson || row.rationale || '')}</p></li>`).join('');
  const last = item.lastReport;
  const rec = last?.recommendation;
  const frozen = last?.frozen;
  const recommendation = rec
    ? `<div class="info-note"><strong>${escapeHTML(rec.label)}</strong> · ${escapeHTML(rec.reason)}${rec.automerge === false ? ' · 需人工发布' : ''}</div>` : '';
  const gates = frozen?.gates
    ? `<p class="table-description">冻结题门槛：probe ${escapeHTML(frozen.gates.gates?.probe_gate)} / pr_val ${escapeHTML(frozen.gates.gates?.pr_val_gate)} / 晋级 ${escapeHTML(frozen.gates.promoted)}；回归与冻结分数分列，默认不自动合并或发布。</p>` : '';
  const frozenErrors = (frozen?.errors || []).map(error => `<div class="info-note">冻结集不可用：${escapeHTML(error)}</div>`).join('');
  const report = last ? `${recommendation}${gates}${frozenErrors}<pre class="gov-pre">${escapeHTML(JSON.stringify(last, null, 2))}</pre>` : '<p class="muted-empty">尚无该规则的评测报告。</p>';
  const registry = item.registry || {};
  const rules = item.rules || {};
  const activeRules = (rules.active || []).map(row => `<li><span class="table-tag">${escapeHTML(row.rule_id)}</span> ${escapeHTML(row.label)}
    <span class="table-description">${escapeHTML(row.confirmed_by || '')}</span></li>`).join('');
  const pendingRules = (rules.pending || []).map(row => `<li><span class="table-tag">${escapeHTML(row.rule_id)}</span> ${escapeHTML(row.label)}
    <button class="text-button" data-gov="confirm-rule" data-rule="${escapeHTML(row.rule_id)}" data-name="${nameAttr(item.name)}">确认生效</button></li>`).join('');
  const detailConflicts = (item.conflicts || []).map(row => `<li><span class="table-tag">${escapeHTML(row.type)}</span> ${escapeHTML(row.message)}</li>`).join('');
  const metrics = (last?.ruleMetrics || []).map(row => `<li><span class="table-tag">${escapeHTML(row.rule_id)}</span> ${escapeHTML(row.label)}
    <span class="table-description">${Math.round(Number(row.pass_rate || 0) * 100)}% (${number(row.passed)}/${number(row.total)})</span></li>`).join('');
  const scan = last?.historicScan;
  const scanNote = scan ? `<p class="table-description">历史回复体检：扫描 ${number(scan.scanned)} 条，合规 ${number(scan.compliant)} 条，语义判定跳过 ${number(scan.semantic_skipped)} 条；${escapeHTML(scan.scope || '')}</p>` : '';
  const statusButtons = ['draft', 'review', 'active', 'deprecated', 'archived'].map(status =>
    `<button class="text-button" data-gov="status" data-status="${status}" data-name="${nameAttr(item.name)}">${status}</button>`).join(' ');
  const lint = (item.lint || []).map(row => `<li><span class="table-tag">${escapeHTML(row.rule_id)}</span> ${escapeHTML(row.message)}</li>`).join('');
  const decisions = (item.decisions || []).map(row => `<li><span class="table-tag">${escapeHTML(row.event)}</span>
    <span>${escapeHTML(row.time || '')}</span><span class="table-description">${escapeHTML(row.actor || '')}</span></li>`).join('');
  return `<div class="page-content"><div class="gov-summary">
      <div><small>版本</small><strong>${escapeHTML(item.version || '—')}</strong></div>
      <div><small>检索 / 相关 / 使用</small><strong>${number(usage.retrieved)} / ${number(usage.relevant)} / ${number(usage.used)}</strong></div>
      <div><small>优胜版本</small><strong>${item.champion ? escapeHTML(item.champion.scope || '已登记') : '尚无'}</strong></div>
      <div><small>状态</small><strong>${item.pruned ? '已归档' : item.staleSuggested ? '建议归档' : '健康'}</strong></div>
    </div>
    <section class="setting-group"><h2>规则正文</h2><p class="table-description">${escapeHTML(item.description || '')}</p>
      <pre class="gov-pre">${escapeHTML(item.instructions || '')}</pre></section>
    <section class="setting-group"><h2>最近评测</h2>${report}
      ${metrics ? `<h3>逐规则通过率</h3><ul class="gov-history">${metrics}</ul><p class="table-description">规则通过率不等于任务成功率。</p>` : ''}${scanNote}</section>
    <section class="setting-group"><h2>生命周期与负责人</h2>
      <p class="table-description">状态：${escapeHTML(registry.status || 'draft')} · 负责人：${escapeHTML(registry.owner || '未指定')} · 团队：${escapeHTML(registry.team || '未指定')} · 范围：${escapeHTML(registry.scope || 'project')}</p>
      <div class="gov-toolbar">${statusButtons}</div>
      <p class="table-description">仅允许合法状态转换；废弃或归档需填写迁移路径，并写入评审记录与决策台账。</p></section>
    <section class="setting-group"><h2>已确认规则</h2><ul class="gov-history">${activeRules || '<li class="muted-empty">尚无人工确认的规则</li>'}</ul></section>
    <section class="setting-group"><h2>待确认候选规则</h2><ul class="gov-history">${pendingRules || '<li class="muted-empty">没有待确认候选</li>'}</ul>
      <form id="rule-stage-form" class="gov-form"><input type="hidden" name="name" value="${nameAttr(item.name)}">
        <label>粘贴模型抽取的候选规则 JSON（不做关键词自动推导）<textarea name="raw" rows="4" placeholder='{"checks":[{"mode":"contains","text":"结论"}],"requirements":["先给结论"]}'></textarea></label>
        <button type="submit" class="button secondary">提交待确认</button></form></section>
    ${detailConflicts ? `<section class="setting-group"><h2>该规则涉及的冲突</h2><ul class="gov-history">${detailConflicts}</ul></section>` : ''}
    <section class="setting-group"><h2>静态校验</h2><ul class="gov-history">${lint || '<li class="muted-empty">未发现问题</li>'}</ul></section>
    <section class="setting-group"><h2>决策记录</h2><ul class="gov-history">${decisions || '<li class="muted-empty">尚无决策记录</li>'}</ul></section>
    <section class="setting-group"><h2>版本与审计</h2><p class="table-description">文件：${escapeHTML(item.file || '')}</p>
      <p class="table-description">历史：${escapeHTML(item.audit?.history || '')}</p>
      <p class="table-description">使用记录：${escapeHTML(item.audit?.usage || '')}</p>
      <p class="table-description">决策：${escapeHTML(item.audit?.decisions || '')}</p>
      <p class="table-description">冻结题：${escapeHTML(item.audit?.holdout || '')}</p>
      <ul class="gov-history">${history || '<li class="muted-empty">尚无版本快照</li>'}</ul></section>
    <div class="gov-toolbar gov-bottom">
      <button class="button primary" data-gov="edit" data-name="${nameAttr(item.name)}">编辑</button>
      ${item.pruned
        ? `<button class="button secondary" data-gov="restore" data-name="${nameAttr(item.name)}">恢复</button>`
        : `<button class="button secondary" data-gov="evaluate" data-name="${nameAttr(item.name)}">按此规则评测</button>
           <button class="button secondary" data-gov="rollback" data-name="${nameAttr(item.name)}">回退上一版</button><button class="button danger" data-gov="archive" data-name="${nameAttr(item.name)}">归档</button>`}
    </div></div>`;
}

function editView(state) {
  const edit = state.edit || {};
  const item = edit.detail || {};
  const isEdit = edit.mode === 'edit';
  return `<div class="page-content"><form id="governance-form" class="gov-form">
    <input type="hidden" name="mode" value="${isEdit ? 'edit' : 'new'}">
    <div class="setting-group"><h2>${isEdit ? '编辑规则' : '新建规则'}</h2>
      <label>名称<input name="name" maxlength="120" value="${escapeHTML(isEdit ? item.name || '' : edit.name || '')}" ${isEdit ? 'readonly' : 'required'}></label>
      <label>描述<input name="description" maxlength="500" value="${escapeHTML(item.description || '')}" required></label>
      <label>适用场景（可选）<input name="whenToUse" maxlength="500" value="${escapeHTML(item.whenToUse || '')}"></label>
      ${isEdit ? '' : '<label>保存位置<select name="target"><option value="project">项目级</option><option value="user">用户级</option></select></label>'}
      <label>规则正文<textarea name="instructions" rows="16" required>${escapeHTML(item.instructions || '')}</textarea></label>
      <label>修改说明（可选，写入版本记录）<input name="rationale" maxlength="500" value=""></label>
      <p class="table-description">保存会生成版本快照，可随时回退；编辑不会自动发布为生效版本的评测冠军。</p>
    </div><div class="gov-toolbar"><button type="submit" class="button primary">保存</button><button type="button" class="button secondary" data-gov="back">取消</button></div>
  </form></div>`;
}

export function renderGovernancePage(state = {}) {
  const view = state.view || 'list';
  const body = view === 'detail' ? detailView(state) : view === 'edit' ? editView(state) : listView(state);
  return heading(state) + body;
}
