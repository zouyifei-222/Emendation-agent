import test from 'node:test';
import assert from 'node:assert/strict';
import {escapeHTML, governanceHealth, renderGovernancePage} from '../governance-view.mjs';

test('escapeHTML masks attribute-breaking characters', () => {
  assert.equal(escapeHTML('<a href="x">&\'</a>'), '&lt;a href=&quot;x&quot;&gt;&amp;&#39;&lt;/a&gt;');
  assert.equal(escapeHTML(undefined), '');
  assert.equal(escapeHTML(0), '0');
});

test('health labels distinguish archived, stale and healthy assets', () => {
  assert.deepEqual(governanceHealth({pruned: true}), {label: '已归档', tone: 'muted'});
  assert.deepEqual(governanceHealth({pruned: false, staleSuggested: true}), {label: '建议归档', tone: 'amber'});
  assert.deepEqual(governanceHealth({pruned: false, staleSuggested: false}), {label: '健康', tone: 'ok'});
});

test('list view shows usage, champion, health and guarded actions without injecting markup', () => {
  const html = renderGovernancePage({loaded: true, view: 'list', errors: [], staleThreshold: {minRetrieved: 12, maxUsed: 0},
    assets: [{name: '<img src=x>', description: '论文格式', source: 'project', pruned: false,
      staleSuggested: true, champion: {scope: 'regression'}, usage: {retrieved: 12, relevant: 0, used: 0}}]});
  assert.match(html, /资产治理/);
  assert.match(html, /12 \/ 0 \/ 0/);
  assert.match(html, /建议归档/);
  assert.match(html, /有优胜版本/);
  assert.match(html, /data-gov="archive"/);
  assert.match(html, /data-name="&lt;img src=x&gt;"/);
  assert.doesNotMatch(html, /<img src=x>/);
  assert.match(html, /回归表现/);
});

test('empty and loading states stay readable', () => {
  assert.match(renderGovernancePage({loaded: false, view: 'list'}), /正在读取/);
  assert.match(renderGovernancePage({loaded: true, view: 'list', assets: [], errors: []}), /还没有可治理的规则/);
});

test('detail view renders body, audit pointers and reversible actions', () => {
  const html = renderGovernancePage({view: 'detail', detail: {name: 'demo', version: '0.1.2',
    description: 'demo', instructions: '# Goal\n\nBe concise.', file: '/tmp/SKILL.md', pruned: false,
    usage: {retrieved: 47, relevant: 39, used: 31},
    history: [{event: 'snapshot', time: '2026-11-06', version: '0.1.1', actor: 'web-governance', lesson: 'edit'}],
    audit: {history: '/tmp/history/demo.jsonl', usage: '/tmp/usage.jsonl'}}});
  assert.match(html, /Be concise\./);
  assert.match(html, /47 \/ 39 \/ 31/);
  assert.match(html, /\/tmp\/history\/demo.jsonl/);
  assert.match(html, /data-gov="rollback"/);
  assert.match(html, /返回资产列表/);
});

test('detail view shows frozen recommendation, gates, lint and decision ledger', () => {
  const html = renderGovernancePage({view: 'detail', detail: {name: '<script>bad</script>', version: '0.2',
    description: 'd', instructions: '# Goal\n\nBe concise.', pruned: false,
    usage: {retrieved: 3, relevant: 2, used: 1},
    lastReport: {recommendation: {label: '建议采纳', reason: '门槛通过', automerge: false},
      frozen: {errors: ['数据集尚未冻结'], gates: {promoted: true, gates: {probe_gate: true, pr_val_gate: true}}}},
    lint: [{rule_id: 'SKL507', message: '缺少迁移路径'}],
    decisions: [{event: 'edit', time: '2026-11-08', actor: 'web-governance'}],
    audit: {history: '/tmp/h.jsonl', usage: '/tmp/u.jsonl', decisions: '/tmp/d.jsonl', holdout: '/tmp/holdout'}}});
  assert.match(html, /建议采纳/);
  assert.match(html, /probe true/);
  assert.match(html, /需人工发布/);
  assert.match(html, /数据集尚未冻结/);
  assert.match(html, /SKL507/);
  assert.match(html, /决策记录/);
  assert.match(html, /\/tmp\/holdout/);
  assert.match(html, /data-gov="evaluate"/);
  assert.doesNotMatch(html, /<script>bad<\/script>/);
});

test('archived assets cannot trigger a paid evaluation', () => {
  const html = renderGovernancePage({view: 'detail', detail: {name: 'demo', pruned: true,
    usage: {}, instructions: 'x', audit: {}, decisions: [], lint: []}});
  assert.doesNotMatch(html, /data-gov="evaluate"/);
  assert.match(html, /data-gov="restore"/);
});

test('edit form keeps the edited body verbatim and never renames an existing asset', () => {
  const html = renderGovernancePage({view: 'edit', edit: {mode: 'edit', detail: {name: 'demo', description: 'd',
    instructions: '# Skill Instructions\n\nKeep this.'}}});
  assert.match(html, /id="governance-form"/);
  assert.match(html, /name="mode" value="edit"/);
  assert.match(html, /name="name"[^>]*readonly/);
  assert.match(html, /# Skill Instructions/);
  assert.doesNotMatch(html, /name="target"/);
  const fresh = renderGovernancePage({view: 'edit', edit: {mode: 'new'}});
  assert.match(fresh, /name="target"/);
  assert.match(fresh, /新建规则/);
});

test('list view surfaces cross-asset conflicts and the tool SOP scope note', () => {
  const html = renderGovernancePage({loaded: true, assets: [
    {name: 'A', description: 'd', usage: {retrieved: 1}, status: 'active', owner: 'alice', source: 'project'}],
    conflicts: [{type: 'mutually_exclusive', message: '<script>bad</script> 互斥', assets: ['A', 'B']}],
    toolScopeNote: '工具型 SOP 需单独运行'});
  assert.match(html, /跨规则冲突（1）/);
  assert.match(html, /工具型 SOP 需单独运行/);
  assert.match(html, /alice/);
  assert.match(html, /<th>状态<\/th>/);
  assert.doesNotMatch(html, /<script>bad<\/script>/);
});

test('detail view shows per-rule pass rates, rule confirmation and lifecycle actions', () => {
  const html = renderGovernancePage({view: 'detail', detail: {name: 'demo', pruned: false,
    usage: {}, instructions: '# Goal', audit: {}, decisions: [], lint: [],
    registry: {status: 'review', owner: 'alice', team: 'docs', scope: 'project'},
    rules: {active: [{rule_id: 'req:1', label: '先给结论', confirmed_by: 'alice'}],
            pending: [{rule_id: 'check:2', label: '<img src=x>'}]},
    conflicts: [{type: 'duplicate_scope', message: '重复适用场景'}],
    lastReport: {ruleMetrics: [{rule_id: 'req:1', label: '先给结论', pass_rate: 0.25, passed: 1, total: 4}],
                 historicScan: {scanned: 2, compliant: 1, semantic_skipped: 2}}}});
  assert.match(html, /逐规则通过率/);
  assert.match(html, /25% \(1\/4\)/);
  assert.match(html, /历史回复体检/);
  assert.match(html, /data-gov="confirm-rule"/);
  assert.match(html, /id="rule-stage-form"/);
  assert.match(html, /data-gov="status" data-status="active"/);
  assert.match(html, /重复适用场景/);
  assert.match(html, /规则通过率不等于任务成功率/);
  assert.doesNotMatch(html, /<img src=x>/);
});
