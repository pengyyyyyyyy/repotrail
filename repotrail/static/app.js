const $ = (id) => document.getElementById(id);
const state = { repository: null, trail: null, flow: null, step: 0, job: null, busy: false, modelReady: false, epoch: 0, sourceRequest: 0 };

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请求未能完成，请检查输入');
  return data;
}

function message(id, text) {
  $(id).textContent = text;
  $(id).hidden = !text;
}

function refreshButton() {
  $('generate').disabled = state.busy || !state.flow || !(state.modelReady || state.repository?.demo);
  $('generate').textContent = state.busy ? '正在读取与核对源码' : '生成所选导览 →';
  $('open-repository').disabled = state.busy;
  $('load-demo').disabled = state.busy;
}

function renderRepository(repository) {
  state.repository = repository;
  state.flow = repository.flows[0]?.id || null;
  $('repo-name').textContent = repository.demo ? 'Paperplane' : repository.name;
  $('breadcrumb-repo').textContent = repository.demo ? 'Paperplane' : repository.name;
  $('repo-revision').textContent = repository.revision ? `Git ${repository.revision.slice(0, 8)}` : `快照 ${repository.digest.slice(0, 8)}`;
  $('flow-count').textContent = repository.flows.length;
  $('file-count').textContent = repository.file_count;
  $('flows').replaceChildren();
  for (const flow of repository.flows) {
    const button = element('button', `flow${flow.id === state.flow ? ' active' : ''}`);
    button.type = 'button';
    button.setAttribute('aria-pressed', String(flow.id === state.flow));
    button.title = flow.reason;
    button.append(element('span', 'flow-marker'));
    const label = element('span');
    label.append(element('span', 'flow-name', flow.title));
    label.append(element('span', 'flow-entry', flow.entry.split('::').pop()));
    button.append(label);
    button.addEventListener('click', () => {
      if (state.busy) return;
      state.flow = flow.id;
      for (const item of $('flows').children) {
        item.classList.toggle('active', item === button);
        item.setAttribute('aria-pressed', String(item === button));
      }
      refreshButton();
    });
    $('flows').append(button);
  }
  $('modules').replaceChildren();
  for (const module of repository.modules) {
    const button = element('button', 'module');
    button.type = 'button';
    button.append(element('span', 'module-path', module.file), element('span', 'module-count', module.symbols));
    button.title = `${module.symbols} 个符号，${module.references} 个静态引用候选`;
    button.addEventListener('click', () => showFile(module.file));
    $('modules').append(button);
  }
  const overview = $('overview');
  overview.replaceChildren();
  for (const [count, label] of [[repository.file_count, 'Python 文件'], [repository.symbol_count, '可定位符号'], [repository.edge_count, '静态调用候选']]) {
    const item = element('span', 'overview-item');
    item.append(element('strong', '', count), document.createTextNode(label));
    overview.append(item);
  }
  overview.append(element('span', 'overview-snapshot', `snapshot ${repository.digest.slice(0, 12)}`));
  $('snapshot-hash').textContent = repository.digest.slice(0, 8);
  message('notice', repository.demo ? '' : state.modelReady ? '生成时会将所选源码上下文发送到已配置的模型服务' : '已建立源码快照。配置 REPOTRAIL_API_KEY 后重新启动服务，即可生成模型导览');
  if (repository.warnings.length) message('notice', repository.warnings.join('；'));
  refreshButton();
}

function clearTrail() {
  state.trail = null;
  state.job = null;
  $('export-button').disabled = true;
  $('export-menu').hidden = true;
  $('export-button').setAttribute('aria-expanded', 'false');
  for (const id of ['steps', 'step-details', 'validation', 'gaps', 'source']) $(id).replaceChildren();
  $('step-count').textContent = '';
  $('source-file').textContent = '源码';
  $('source-range').textContent = '';
  $('source-status').textContent = '选择候选流程后生成导览';
  $('run-metrics').textContent = '';
  $('previous').disabled = true;
  $('next').disabled = true;
}

function renderTrail(trail, job) {
  state.trail = trail;
  state.job = job;
  state.step = 0;
  $('mode-label').textContent = trail.mode === 'offline_demo' ? '离线示例 · 未调用模型' : '模型生成 · 源码依据已检查';
  $('trail-title').textContent = trail.title;
  $('trail-summary').textContent = trail.summary;
  $('step-count').textContent = `${trail.steps.length} 个步骤`;
  $('steps').replaceChildren();
  trail.steps.forEach((step, index) => {
    const button = element('button', 'step');
    button.type = 'button';
    const content = element('span');
    content.append(element('span', 'step-name', step.title), element('span', 'step-file', `${step.file} · L${step.start}`));
    button.append(element('span', 'step-dot', index + 1), content);
    button.addEventListener('click', () => selectStep(index));
    $('steps').append(button);
  });
  const check = trail.validation;
  $('validation').replaceChildren();
  for (const [count, label] of [[`${check.located_steps}/${check.requested_steps}`, '符号已定位'], [check.static_connections, '静态连接可匹配'], [check.unresolved_connections, '连接待核实']]) {
    const item = element('span');
    item.append(element('strong', '', count), document.createTextNode(label));
    $('validation').append(item);
  }
  $('gaps').replaceChildren(...trail.gaps.map((gap) => element('li', '', gap)));
  const metrics = trail.metrics;
  const tokens = metrics.prompt_tokens + metrics.completion_tokens;
  const cost = metrics.estimated_cost == null ? '费用未配置或用量缺失' : `约 ${Number(metrics.estimated_cost).toFixed(4)} ${metrics.currency}`;
  $('run-metrics').textContent = `${metrics.model} · ${metrics.calls} 次调用 · ${metrics.usage_complete ? tokens.toLocaleString() : '用量缺失'} tokens · ${metrics.elapsed_seconds}s · ${cost}`;
  $('export-button').disabled = !job;
  for (const format of ['markdown', 'codetour', 'json']) $('export-' + format).href = `/api/export/${job}?format=${format}`;
  if (trail.steps.length) selectStep(0);
  else message('error', '本次结果没有可定位步骤，请查看待核实部分');
}

function highlight(text) {
  const fragment = document.createDocumentFragment();
  const tokens = text.split(/(#.*$|"[^"\n]*"|'[^'\n]*'|\b(?:def|class|return|import|from|as|if|else|elif|for|while|with|try|except|raise|async|await|yield|None|True|False|and|or|not|in|is|pass)\b)/g);
  for (const token of tokens) {
    let className = '';
    if (token.startsWith('#')) className = 'code-comment';
    else if (/^["']/.test(token)) className = 'code-string';
    else if (/^(def|class|return|import|from|as|if|else|elif|for|while|with|try|except|raise|async|await|yield|None|True|False|and|or|not|in|is|pass)$/.test(token)) className = 'code-keyword';
    fragment.append(className ? element('span', className, token) : document.createTextNode(token));
  }
  return fragment;
}

function renderSource(file, source, start = 0, end = 0, offset = 1) {
  $('source-file').textContent = file;
  $('source-range').textContent = start ? `L${start}–L${end}` : '文件快照';
  const rows = source.split('\n').map((line, index) => {
    const number = index + offset;
    const row = element('div', `code-line${number >= start && number <= end ? ' highlight' : ''}`);
    const code = element('code', 'code-text');
    code.append(highlight(line));
    row.append(element('span', 'line-number', number), code);
    return row;
  });
  $('source').replaceChildren(...rows);
  $('source').scrollTop = 0;
}

async function selectStep(index) {
  const trail = state.trail;
  if (!trail || !trail.steps[index]) return;
  const sourceRequest = ++state.sourceRequest;
  state.step = index;
  const step = trail.steps[index];
  [...$('steps').children].forEach((button, number) => {
    button.classList.toggle('active', number === index);
    button.setAttribute('aria-current', number === index ? 'step' : 'false');
  });
  const details = $('step-details');
  details.replaceChildren(element('h3', '', '这一步看什么'), element('p', '', step.explanation));
  const connection = step.connection;
  const parent = connection ? trail.steps.findIndex((item) => item.symbol_id === connection.source) + 1 : 0;
  const label = !connection ? '从已识别的入口开始' : connection.status === 'static_match' ? `由第 ${parent} 步调用 · ${connection.file} L${connection.line}` : '已读步骤到此处的连接需要核实';
  details.append(element('div', `connection-note${connection?.status === 'unresolved' ? ' unresolved' : ''}`, label));
  $('previous').disabled = index === 0;
  $('next').disabled = index === trail.steps.length - 1;
  $('source-status').textContent = '源码与当前快照一致 · 解释语义待人工核对';
  renderSource(step.file, step.source, step.start, step.end, step.start);
  const epoch = state.epoch;
  try {
    const data = await api(`/api/source/${state.repository.repository_id}?file=${encodeURIComponent(step.file)}`);
    if (state.epoch !== epoch || state.sourceRequest !== sourceRequest || state.trail !== trail) return;
    const lines = data.source.split('\n');
    const first = Math.max(1, step.start - 5);
    const last = Math.min(lines.length, step.end + 4);
    renderSource(step.file, lines.slice(first - 1, last).join('\n'), step.start, step.end, first);
  } catch (error) {
    if (state.sourceRequest === sourceRequest && state.epoch === epoch) $('source-status').textContent = '显示导览保存的源码片段 · 完整快照已不可用';
  }
}

async function showFile(file) {
  const epoch = state.epoch;
  const sourceRequest = ++state.sourceRequest;
  try {
    const data = await api(`/api/source/${state.repository.repository_id}?file=${encodeURIComponent(file)}`);
    if (epoch !== state.epoch || state.sourceRequest !== sourceRequest) return;
    renderSource(file, data.source);
    $('source-status').textContent = '模块文件快照 · 选择阅读步骤可返回对应代码';
    for (const item of $('modules').children) item.classList.toggle('active', item.firstChild.textContent === file);
  } catch (error) { message('error', error.message); }
}

async function loadDemo() {
  if (state.busy) return;
  state.epoch++;
  const epoch = state.epoch;
  message('error', '');
  try {
    const data = await api('/api/demo');
    if (state.epoch !== epoch) return;
    clearTrail();
    renderRepository(data.repository);
    renderTrail(data.trail, data.job_id);
  } catch (error) { message('error', error.message); }
}

$('generate').addEventListener('click', async () => {
  if (state.busy) return;
  state.busy = true;
  refreshButton();
  message('error', '');
  message('notice', '正在读取源码并校验导览依据，模型服务较慢时可能需要数分钟');
  try {
    const job = await api('/api/generate', { repository_id: state.repository.repository_id, flow_id: state.flow });
    const started = Date.now();
    while (Date.now() - started < 300000) {
      const result = await api(`/api/jobs/${job.job_id}`);
      if (result.status === 'failed') throw new Error(result.error);
      if (result.status === 'complete') {
        renderTrail(result.result, job.job_id);
        message('notice', '');
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
    throw new Error('等待任务超时，请检查本地服务状态');
  } catch (error) { message('error', error.message); message('notice', ''); }
  finally { state.busy = false; refreshButton(); }
});

$('repository-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  $('analyze-button').disabled = true;
  $('analyze-button').textContent = '正在建立源码快照';
  message('dialog-error', '');
  try {
    const repository = await api('/api/analyze', { source: $('repository-source').value.trim() });
    state.epoch++;
    clearTrail();
    renderRepository(repository);
    $('mode-label').textContent = '仓库快照 · 选择候选流程';
    $('trail-title').textContent = repository.name;
    $('trail-summary').textContent = '从左侧选择一条候选流程，生成带源码依据的阅读路线。模块地图支持直接查看文件。';
    $('repository-dialog').close();
    message('error', '');
  } catch (error) { message('dialog-error', error.message); }
  finally { $('analyze-button').disabled = false; $('analyze-button').textContent = '分析仓库 →'; }
});

$('open-repository').addEventListener('click', () => $('repository-dialog').showModal());
for (const id of ['close-dialog', 'cancel-dialog']) $(id).addEventListener('click', () => $('repository-dialog').close());
$('load-demo').addEventListener('click', loadDemo);
$('previous').addEventListener('click', () => selectStep(state.step - 1));
$('next').addEventListener('click', () => selectStep(state.step + 1));
$('export-button').addEventListener('click', () => {
  $('export-menu').hidden = !$('export-menu').hidden;
  $('export-button').setAttribute('aria-expanded', String(!$('export-menu').hidden));
});
document.addEventListener('click', (event) => {
  if (!event.target.closest('.export-control')) {
    $('export-menu').hidden = true;
    $('export-button').setAttribute('aria-expanded', 'false');
  }
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    $('export-menu').hidden = true;
    $('export-button').setAttribute('aria-expanded', 'false');
  }
});

async function initialize() {
  try {
    const health = await api('/api/health');
    state.modelReady = health.model_ready;
    $('model-status').textContent = health.model_ready ? health.model : '模型 API 待配置';
    await loadDemo();
  } catch (error) { message('error', '本地服务连接失败，请确认服务正在运行'); }
}
initialize();

