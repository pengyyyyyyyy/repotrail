const $ = (id) => document.getElementById(id);
const state = { repository: null, trail: null, flow: null, step: 0, job: null,
  busy: false, modelReady: false, provider: null, providerBusy: false, epoch: 0, sourceRequest: 0 };

const providerPresets = {
  custom: { name: '兼容 API', base_url: '' },
  deepseek: { name: 'DeepSeek', base_url: 'https://api.deepseek.com/v1' },
  openrouter: { name: 'OpenRouter', base_url: 'https://openrouter.ai/api/v1' },
};

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
  $('generate').disabled = state.busy || !state.flow;
  $('generate').textContent = state.busy ? '正在生成导览' : '生成导览 →';
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
    button.title = `${module.symbols} 个符号`;
    button.addEventListener('click', () => showFile(module.file));
    $('modules').append(button);
  }
  const overview = $('overview');
  overview.replaceChildren();
  for (const [count, label] of [[repository.file_count, 'Python 文件'], [repository.symbol_count, '个符号']]) {
    const item = element('span', 'overview-item');
    item.append(element('strong', '', count), document.createTextNode(label));
    overview.append(item);
  }
  overview.append(element('span', 'overview-snapshot', repository.revision ? `Git ${repository.revision.slice(0, 8)}` : 'Python'));
  $('snapshot-hash').textContent = 'UTF-8';
  message('notice', '');
  if (repository.warnings.length) message('notice', repository.warnings.join('；'));
  refreshButton();
}

function clearTrail() {
  state.trail = null;
  state.job = null;
  $('export-button').disabled = true;
  $('export-menu').hidden = true;
  $('export-button').setAttribute('aria-expanded', 'false');
  for (const id of ['steps', 'step-details', 'source']) $(id).replaceChildren();
  $('step-count').textContent = '';
  $('source-file').textContent = '源码';
  $('source-range').textContent = '';
  $('source-status').textContent = 'Python';
  $('run-metrics').textContent = '';
  $('previous').disabled = true;
  $('next').disabled = true;
}

function renderTrail(trail, job) {
  state.trail = trail;
  state.job = job;
  state.step = 0;
  $('mode-label').textContent = trail.mode === 'offline_demo' ? '示例仓库' : '阅读导览';
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
  const metrics = trail.metrics;
  const tokens = metrics.prompt_tokens + metrics.completion_tokens;
  $('run-metrics').textContent = trail.mode === 'offline_demo' ? 'Paperplane / Python' :
    `${metrics.model} · ${metrics.elapsed_seconds}s${metrics.usage_complete ? ` · ${tokens.toLocaleString()} tokens` : ''}`;
  $('export-button').disabled = !job;
  for (const format of ['markdown', 'codetour', 'json']) $('export-' + format).href = `/api/export/${job}?format=${format}`;
  if (trail.steps.length) selectStep(0);
  else message('error', '本次生成没有返回阅读步骤，请重试或选择其他入口');
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
  if (connection?.status === 'static_match') {
    details.append(element('div', 'connection-note', `来自步骤 ${parent} · ${connection.file} L${connection.line}`));
  }
  $('previous').disabled = index === 0;
  $('next').disabled = index === trail.steps.length - 1;
  $('source-status').textContent = 'Python';
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
    if (state.sourceRequest === sourceRequest && state.epoch === epoch) $('source-status').textContent = 'Python · 源码片段';
  }
}

async function showFile(file) {
  const epoch = state.epoch;
  const sourceRequest = ++state.sourceRequest;
  try {
    const data = await api(`/api/source/${state.repository.repository_id}?file=${encodeURIComponent(file)}`);
    if (epoch !== state.epoch || state.sourceRequest !== sourceRequest) return;
    renderSource(file, data.source);
    $('source-status').textContent = 'Python';
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
  if (!state.repository.demo && !state.modelReady) {
    await openProvider();
    return;
  }
  state.busy = true;
  refreshButton();
  message('error', '');
  message('notice', '正在阅读源码并生成导览');
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
    $('mode-label').textContent = '仓库';
    $('trail-title').textContent = repository.name;
    $('trail-summary').textContent = '选择一个阅读起点，沿着调用关系了解这个项目。';
    $('repository-dialog').close();
    message('error', '');
  } catch (error) { message('dialog-error', error.message); }
  finally { $('analyze-button').disabled = false; $('analyze-button').textContent = '打开仓库 →'; }
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
    updateProvider(await api('/api/provider'));
    await loadDemo();
  } catch (error) { message('error', '本地服务连接失败，请确认服务正在运行'); }
}
initialize();

function updateProvider(provider) {
  state.provider = provider;
  state.modelReady = provider.configured;
  $('model-status').textContent = provider.configured ? provider.model : 'API 接入';
  $('model-dot').classList.toggle('connected', provider.configured);
  refreshButton();
}

function providerFeedback(text, error = false) {
  message('provider-feedback', text);
  $('provider-feedback').classList.toggle('error', error);
}

function normalizeBase(value) {
  return value.trim().replace(/\/+$/, '').replace(/\/(chat\/completions|models)$/, '');
}

function providerPayload() {
  return {
    name: $('provider-name').value.trim(),
    base_url: $('provider-url').value.trim(),
    api_key: $('provider-key').value,
    model: $('provider-model').value.trim(),
  };
}

function updateEndpoint() {
  const base = normalizeBase($('provider-url').value);
  $('endpoint-preview').textContent = base ? `${base}/chat/completions` : '/chat/completions';
  $('provider-key').placeholder = state.provider?.has_key && base === state.provider.base_url ? '已保存，留空保留' : '输入 API Key';
}

function selectProviderPreset(id) {
  for (const button of document.querySelectorAll('[data-provider]')) {
    const active = button.dataset.provider === id;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  }
}

async function openProvider() {
  providerFeedback('');
  try {
    const provider = await api('/api/provider');
    updateProvider(provider);
    $('provider-name').value = provider.name;
    $('provider-url').value = provider.base_url;
    $('provider-model').value = provider.model;
    $('provider-key').value = '';
    $('provider-key').type = 'password';
    $('toggle-key').textContent = '显示';
    $('provider-models').replaceChildren();
    message('model-list-status', '');
    const preset = Object.entries(providerPresets).find(([, value]) => value.base_url === provider.base_url);
    selectProviderPreset(preset ? preset[0] : 'custom');
    updateEndpoint();
    $('provider-dialog').showModal();
  } catch (error) { message('error', error.message); }
}

function providerBusy(busy) {
  state.providerBusy = busy;
  for (const control of $('provider-dialog').querySelectorAll('button, input')) control.disabled = busy;
}

for (const id of ['provider-settings', 'sidebar-settings']) $(id).addEventListener('click', openProvider);
$('close-provider').addEventListener('click', () => $('provider-dialog').close());
$('provider-dialog').addEventListener('cancel', (event) => {
  if (state.providerBusy) event.preventDefault();
});
$('provider-url').addEventListener('input', () => {
  updateEndpoint();
  $('provider-models').replaceChildren();
  message('model-list-status', '');
});
$('toggle-key').addEventListener('click', () => {
  const reveal = $('provider-key').type === 'password';
  $('provider-key').type = reveal ? 'text' : 'password';
  $('toggle-key').textContent = reveal ? '隐藏' : '显示';
});
for (const button of document.querySelectorAll('[data-provider]')) {
  button.addEventListener('click', () => {
    const preset = providerPresets[button.dataset.provider];
    selectProviderPreset(button.dataset.provider);
    $('provider-name').value = preset.name;
    $('provider-url').value = preset.base_url;
    $('provider-model').value = '';
    $('provider-key').value = '';
    $('provider-models').replaceChildren();
    message('model-list-status', '');
    providerFeedback('');
    updateEndpoint();
  });
}
$('fetch-models').addEventListener('click', async () => {
  if (!$('provider-url').reportValidity()) return;
  providerBusy(true);
  providerFeedback('正在获取模型列表');
  try {
    const data = await api('/api/provider/models', providerPayload());
    $('provider-models').replaceChildren(...data.models.map((id) => {
      const option = element('option');
      option.value = id;
      return option;
    }));
    if (!$('provider-model').value && data.models.length) $('provider-model').value = data.models[0];
    message('model-list-status', `${data.models.length} 个可用模型，可输入模型 ID 筛选`);
    providerFeedback('');
  } catch (error) { providerFeedback(error.message, true); }
  finally { providerBusy(false); }
});
$('test-provider').addEventListener('click', async () => {
  if (!$('provider-form').reportValidity()) return;
  providerBusy(true);
  providerFeedback('正在测试模型连接与工具调用');
  try {
    const data = await api('/api/provider/test', providerPayload());
    providerFeedback(data.message);
  } catch (error) { providerFeedback(error.message, true); }
  finally { providerBusy(false); }
});
$('provider-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  providerBusy(true);
  providerFeedback('正在保存');
  try {
    const provider = await api('/api/provider', providerPayload());
    updateProvider(provider);
    $('provider-key').value = '';
    $('provider-key').type = 'password';
    $('provider-dialog').close();
    message('notice', provider.storage === 'session' ? '模型配置已应用到当前会话' : '模型配置已保存');
  } catch (error) { providerFeedback(error.message, true); }
  finally { providerBusy(false); }
});

