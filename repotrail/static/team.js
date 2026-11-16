'use strict';

const $ = (id) => document.getElementById(id);
const state = { user: null, csrf: '', projects: [], project: null, course: null, chapter: 0,
  progress: null, conversations: [], conversation: null, view: 'courses', epoch: 0, streams: new Map() };
const statusNames = { queued: '等待处理', running: '处理中', complete: '已完成', failed: '失败',
  cancelled: '已停止', draft: '草稿', published: '已发布', learning: '学习中', submitted: '待审核',
  approved: '培训已完成', changes_requested: '待补充' };
const jobNames = { index: '源码索引', document: '文档索引', entries: '入口推荐', course: '课程生成',
  trail: '阅读路线', chat: '学习追问', reembed: '向量更新' };

function node(tag, className = '', text) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text !== undefined) result.textContent = text;
  return result;
}

function feedback(text = '', error = false) {
  $('feedback').textContent = text;
  $('feedback').hidden = !text;
  $('feedback').classList.toggle('error', error);
  if (!error && (text === '已完成' || text.endsWith('已保存'))) setTimeout(() => {
    if ($('feedback').textContent === text) $('feedback').hidden = true;
  }, 5000);
}

async function api(path, body, method = 'POST') {
  const authVersion = state.authVersion || 0;
  const options = { headers: {} };
  if (body !== undefined) {
    options.method = method;
    options.headers['X-CSRF-Token'] = state.csrf;
    if (body instanceof FormData) options.body = body;
    else {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body);
    }
  }
  const response = await fetch(path, options);
  const data = await response.json();
  if (path !== '/api/login' && authVersion !== (state.authVersion || 0)) throw new Error('登录会话已切换，请重新操作');
  if (response.status === 401 && path !== '/api/login') showLogin();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请求未完成，请重试');
  return data;
}

function button(text, action, className = '') {
  const result = node('button', className, text);
  result.type = 'button';
  result.addEventListener('click', async () => {
    result.disabled = true;
    try { await action(); }
    catch (error) { feedback(error.message, true); }
    finally { result.disabled = false; }
  });
  return result;
}

function field(label, name, value = '', type = 'text') {
  const wrapper = node('label', '', label);
  const input = node(type === 'textarea' ? 'textarea' : 'input');
  if (type !== 'textarea') input.type = type;
  input.name = name;
  input.value = value;
  input.required = true;
  if (type === 'password') input.autocomplete = 'new-password';
  wrapper.append(input);
  return wrapper;
}

function submitForm(form, handler) {
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const submit = form.querySelector('[type=submit]');
    if (submit) submit.disabled = true;
    try { await handler(Object.fromEntries(new FormData(form))); }
    catch (error) { feedback(error.message, true); }
    finally { if (submit) submit.disabled = false; }
  });
}

function submitButton(text) {
  const result = node('button', 'primary', text);
  result.type = 'submit';
  return result;
}

function header(title, description = '', actions = []) {
  const result = node('div', 'page-header');
  const copy = node('div');
  copy.append(node('h1', '', title));
  if (description) copy.append(node('p', '', description));
  const controls = node('div', 'actions');
  controls.append(...actions);
  result.append(copy, controls);
  return result;
}

function empty(title, description, action) {
  const result = node('div', 'empty');
  result.append(node('h2', '', title), node('p', '', description));
  if (action) result.append(action);
  return result;
}

function row(title, description = '', actions = [], meta = '') {
  const result = node('div', 'row');
  const text = node('div');
  text.append(node('h3', '', title));
  if (description) text.append(node('p', '', description));
  if (meta) text.append(node('div', 'meta mono', meta));
  const controls = node('div', 'actions');
  controls.append(...actions);
  result.append(text, controls);
  return result;
}

function showDialog(title, body) {
  $('dialog-title').textContent = title;
  $('dialog-body').replaceChildren(body);
  if (!$('dialog').open) $('dialog').showModal();
}

function inline(container, text, refs) {
  for (const part of text.split(/(\*\*[^*]+\*\*|`[^`]+`|\[[a-f0-9]{32}\]|\[[^\]]+\]\(https?:\/\/[^\s)]+\))/g)) {
    if (part.startsWith('**') && part.endsWith('**')) container.append(node('strong', '', part.slice(2, -2)));
    else if (part.startsWith('`') && part.endsWith('`')) container.append(node('code', '', part.slice(1, -1)));
    else if (/^\[[a-f0-9]{32}\]$/.test(part)) {
      const id = part.slice(1, -1);
      const reference = refs.find((item) => item.id === id);
      if (reference) container.append(button(`[${refs.indexOf(reference) + 1}]`, () => showSource(id), 'citation'));
      else container.append(document.createTextNode(part));
    } else {
      const link = /^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/.exec(part);
      if (link) {
        const anchor = node('a', '', link[1]); anchor.href = link[2]; anchor.target = '_blank'; anchor.rel = 'noopener noreferrer';
        container.append(anchor);
      } else container.append(document.createTextNode(part));
    }
  }
}

function markdown(text, refs = [], title = '') {
  const result = node('div', 'prose');
  let code = null;
  let paragraph = [];
  let list = null;
  const flush = () => {
    if (paragraph.length) {
      const element = node('p'); inline(element, paragraph.join('\n'), refs); result.append(element); paragraph = [];
    }
  };
  const lines = text.split('\n');
  const first = lines.findIndex((line) => line.trim());
  if (title && first >= 0 && lines[first].replace(/^#{1,6}\s+/, '').trim() === title.trim()) lines.splice(first, 1);
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index];
    if (line.startsWith('```')) {
      flush(); list = null;
      if (code) { result.append(code); code = null; }
      else code = node('pre');
      continue;
    }
    if (code) { code.append(document.createTextNode(line + '\n')); continue; }
    if (!line.trim()) { flush(); list = null; continue; }
    if (line.includes('|') && /^\s*\|?\s*:?-{3,}/.test(lines[index + 1] || '')) {
      flush(); list = null;
      const wrapper = node('div', 'table-scroll');
      const table = node('table');
      const cells = (value) => value.trim().replace(/^\|/, '').replace(/\|$/, '').split(/(?<!\\)\|/).map((cell) => cell.trim());
      const heading = node('tr');
      cells(line).forEach((value) => { const cell = node('th'); inline(cell, value, refs); heading.append(cell); });
      table.append(heading); index++;
      while (index + 1 < lines.length && lines[index + 1].includes('|') && lines[index + 1].trim()) {
        const row = node('tr');
        cells(lines[++index]).forEach((value) => { const cell = node('td'); inline(cell, value, refs); row.append(cell); });
        table.append(row);
      }
      wrapper.append(table); result.append(wrapper); continue;
    }
    const heading = /^(#{1,6})\s+(.+)/.exec(line);
    if (heading) {
      flush(); list = null;
      const element = node('h' + Math.min(heading[1].length + 1, 4));
      inline(element, heading[2], refs); result.append(element); continue;
    }
    const bullet = /^\s*([-*+]|\d+\.)\s+(.+)/.exec(line);
    if (bullet) {
      flush();
      const type = /^\d/.test(bullet[1]) ? 'OL' : 'UL';
      if (!list || list.tagName !== type) {
        list = node(type.toLowerCase());
        if (type === 'OL') list.start = Number.parseInt(bullet[1], 10);
        result.append(list);
      }
      const item = node('li'); inline(item, bullet[2], refs); list.append(item); continue;
    }
    list = null;
    paragraph.push(line);
  }
  flush();
  if (code) result.append(code);
  return result;
}

function referenceButtons(refs = []) {
  const result = node('div', 'references');
  refs.forEach((ref, index) => {
    const location = ref.location;
    const suffix = location.symbol_id ? `L${location.start}–${location.end}` : `P${location.page || 1} · L${location.start}`;
    result.append(button(`[${index + 1}] ${location.file} · ${suffix}`, () => showSource(ref.id)));
  });
  return result;
}

async function showSource(id) {
  const epoch = state.epoch;
  const source = await api(`/api/chunks/${id}`);
  if (epoch !== state.epoch) return;
  $('source-title').textContent = source.title;
  $('source-body').textContent = source.body;
  $('source-dialog').showModal();
}

function showLogin() {
  for (const stream of state.streams.values()) stream.close();
  state.streams.clear();
  $('workspace').hidden = true;
  $('login-screen').hidden = false;
  state.user = null;
  state.conversation = null;
  state.project = null;
  state.course = null;
  state.progress = null;
  state.csrf = '';
  state.projects = [];
  state.conversations = [];
  state.authVersion = (state.authVersion || 0) + 1;
  state.projectRequest = (state.projectRequest || 0) + 1;
  state.courseRequest = (state.courseRequest || 0) + 1;
  state.epoch++;
  for (const id of ['main', 'messages', 'conversation-list', 'project-picker', 'dialog-body', 'source-body']) $(id).replaceChildren();
  for (const dialog of document.querySelectorAll('dialog[open]')) dialog.close();
  feedback();
}

async function authenticated(data) {
  state.authVersion = (state.authVersion || 0) + 1;
  state.view = 'courses';
  state.user = data.user;
  state.csrf = data.csrf;
  $('login-screen').hidden = true;
  $('workspace').hidden = false;
  $('main').replaceChildren(node('p', 'meta', '正在打开工作区'));
  $('account-name').textContent = data.user.name;
  document.querySelectorAll('.admin-only').forEach((item) => { item.hidden = !data.user.admin; });
  state.projects = await api('/api/projects');
  $('project-picker').replaceChildren(...state.projects.map((project) => {
    const option = node('option', '', project.name);
    option.value = project.id;
    return option;
  }));
  const preferred = localStorage.getItem('repotrail-project-' + state.user.id);
  const project = state.projects.find((item) => item.id === preferred) || state.projects[0];
  if (project) await loadProject(project.id);
  else {
    state.project = null;
    $('chat-panel').hidden = true;
    $('conversation-list').replaceChildren();
    $('main').replaceChildren(empty('建立第一个项目', state.user.admin ? '接入源码与团队资料，生成项目培训课程。' :
      '管理员分配项目后，你可以在这里开始学习。', state.user.admin ? button('新建项目', createProject, 'primary') : null));
  }
}

async function refreshConversations() {
  if (!state.project) return;
  const key = state.project.id;
  const values = await api(`/api/projects/${key}/conversations`);
  if (state.project?.id !== key) return;
  state.conversations = values;
  $('conversation-list').replaceChildren(...values.map((conversation) => button(conversation.title,
    () => openConversation(conversation.id), conversation.id === state.conversation?.id ? 'active' : '')));
}

async function loadProject(key) {
  const request = (state.projectRequest || 0) + 1;
  state.projectRequest = request;
  state.epoch++;
  const project = await api(`/api/projects/${key}`);
  if (request !== state.projectRequest) return;
  state.project = project;
  state.course = null;
  state.conversation = null;
  state.progress = null;
  $('chat-panel').hidden = true;
  $('project-picker').value = key;
  $('breadcrumb').textContent = state.project.name;
  localStorage.setItem('repotrail-project-' + state.user.id, key);
  $('project-version').textContent = state.project.overview?.revision?.slice(0, 8) ||
    state.project.overview?.digest?.slice(0, 8) || '';
  await refreshConversations();
  if (request !== state.projectRequest) return;
  if (state.view !== 'courses') { await navigate(state.view); return; }
  const previous = localStorage.getItem(`repotrail-conversation-${state.user.id}-${key}`);
  const savedCourse = localStorage.getItem(`repotrail-course-${state.user.id}-${key}`);
  if (state.conversations.some((item) => item.id === previous && (!savedCourse || item.course_id === savedCourse))) {
    await openConversation(previous);
  } else {
    const course = savedCourse || state.project.published_course_id;
    if (course) await loadCourse(course);
    await navigate('courses');
  }
}

async function navigate(view) {
  if (!state.project && view !== 'settings') return;
  state.view = view;
  state.epoch++;
  const epoch = state.epoch;
  document.querySelectorAll('[data-view]').forEach((item) => item.classList.toggle('active', item.dataset.view === view));
  const renderers = { courses: renderCourses, entries: renderEntries, knowledge: renderKnowledge,
    shared: renderShared, jobs: renderJobs, members: renderMembers, settings: renderSettings, acceptance: renderAcceptance };
  const content = await renderers[view]();
  if (epoch === state.epoch) $('main').replaceChildren(content);
}

async function watchJob(job, onUpdate) {
  const project = state.project?.id;
  return new Promise((resolve, reject) => {
    const stream = new EventSource(`/api/jobs/${job.id}/events`);
    state.streams.set(job.id, stream);
    const close = () => { stream.close(); state.streams.delete(job.id); };
    stream.onmessage = (event) => {
      const current = JSON.parse(event.data);
      if (state.project?.id === project) {
        if (onUpdate) onUpdate(current);
        else feedback(current.stage);
      }
      if (['complete', 'failed', 'cancelled'].includes(current.status)) {
        close();
        if (current.status === 'failed') reject(new Error(current.error));
        else resolve(current);
      }
    };
    stream.onerror = async () => {
      close();
      try {
        const current = await api(`/api/jobs/${job.id}`);
        if (current.status === 'complete') resolve(current);
        else reject(new Error(current.error || '任务连接已中断，可在任务记录中继续查看'));
      } catch (error) { reject(error); }
    };
  });
}

async function projectJob(kind, body = {}) {
  const key = state.project.id;
  const result = await watchJob(await api(`/api/projects/${key}/${kind}`, body));
  if (state.project?.id === key) {
    state.project = await api(`/api/projects/${key}`);
    if (result.status === 'complete') feedback('已完成');
  }
  return result;
}

function createProject() {
  const form = node('form');
  const token = field('仓库访问令牌', 'token', '', 'password');
  token.querySelector('input').required = false;
  const description = field('项目介绍', 'description', '', 'textarea');
  description.querySelector('textarea').required = false;
  form.append(field('项目名称', 'name'), field('源码位置 · HTTPS 地址或服务器目录', 'source'), token, description, submitButton('创建项目'));
  submitForm(form, async (values) => {
    const project = await api('/api/projects', values);
    form.reset();
    $('dialog').close();
    state.projects = await api('/api/projects');
    const option = node('option', '', values.name);
    option.value = project.id;
    $('project-picker').append(option);
    await loadProject(project.id);
    await navigate('knowledge');
  });
  showDialog('新建项目', form);
}

async function loadCourse(key) {
  const request = (state.courseRequest || 0) + 1;
  state.courseRequest = request;
  const course = await api(`/api/courses/${key}`);
  const progress = course.status === 'published' ? await api(`/api/courses/${key}/progress`) : null;
  if (request !== state.courseRequest || course.project_id !== state.project?.id) return;
  state.course = course;
  localStorage.setItem(`repotrail-course-${state.user.id}-${state.project.id}`, course.id);
  if (state.conversation && state.conversation.course_id !== course.id) {
    state.conversation = null;
    $('chat-panel').hidden = true;
    await refreshConversations();
  }
  state.chapter = 0;
  state.progress = progress;
  if (state.progress) state.chapter = state.progress.chapter;
}

async function selectChapter(index) {
  state.chapter = index;
  if (state.conversation?.course_id === state.course.id) {
    await api(`/api/conversations/${state.conversation.id}/position`, { chapter: index }, 'PUT');
    state.conversation.chapter = index;
  }
  if (state.progress) {
    await api(`/api/courses/${state.course.id}/progress`, { chapters: state.progress.chapters, chapter: index }, 'PUT');
    state.progress.chapter = index;
  }
  await navigate('courses');
  $('main').scrollIntoView({ block: 'start' });
}

async function renderCourses() {
  const result = node('div');
  if (state.course) {
    const course = state.course;
    if (course.status === 'published') {
      const progress = await api(`/api/courses/${course.id}/progress`);
      if (state.course?.id !== course.id) return result;
      state.progress = progress;
    }
    const controls = [button('全部课程', async () => { state.course = null; await navigate('courses'); })];
    if (state.user.admin && course.status === 'draft') {
      controls.push(button('编辑本章', editChapter));
      controls.push(button('审核并发布', async () => {
        await api(`/api/courses/${course.id}/publish`, {});
        await loadCourse(course.id);
        state.project = await api(`/api/projects/${state.project.id}`);
        await navigate('courses');
      }, 'primary'));
    } else if (course.status === 'published') controls.push(button('开始追问', newConversation));
    result.append(header(course.title, `${statusNames[course.status]} · ${course.chapters.length} 章 · ${new Date(course.created * 1000).toLocaleDateString()}`, controls));
    if (course.newer_materials) result.append(node('p', 'meta', '项目资料已有更新，本课程使用发布时绑定的资料版本。'));
    const layout = node('div', 'course-layout');
    const list = node('div', 'chapter-list');
    course.chapters.forEach((chapter, index) => list.append(button(
      `${state.progress?.chapters.includes(index) ? '✓ ' : ''}${chapter.title}`, async () => {
        await selectChapter(index);
      }, index === state.chapter ? 'active' : '')));
    if (state.progress) list.append(node('div', 'meta', `${state.progress.chapters.length}/${course.chapters.length} 章已读 · ${statusNames[state.progress.status]}`));
    const chapter = course.chapters[state.chapter];
    const article = node('article', 'chapter');
    article.append(node('h2', '', chapter.title));
    const objectives = node('div', 'objectives');
    objectives.append(node('span', '', '本章学习目标'));
    const goals = node('ul');
    chapter.objectives.forEach((goal) => goals.append(node('li', '', goal)));
    objectives.append(goals);
    article.append(objectives, markdown(chapter.content, chapter.citations, chapter.title), referenceButtons(chapter.citations));
    const footer = node('div', 'chapter-footer');
    const previous = button('上一章', () => selectChapter(state.chapter - 1));
    previous.disabled = state.chapter === 0;
    const next = button('下一章', () => selectChapter(state.chapter + 1));
    next.disabled = state.chapter === course.chapters.length - 1;
    footer.append(previous);
    if (state.progress) footer.append(button(state.progress.chapters.includes(state.chapter) ? '取消已读' : '标记本章已读', async () => {
      const chapters = new Set(state.progress.chapters);
      if (chapters.has(state.chapter)) chapters.delete(state.chapter);
      else chapters.add(state.chapter);
      await api(`/api/courses/${course.id}/progress`, { chapters: [...chapters] }, 'PUT');
      state.progress = await api(`/api/courses/${course.id}/progress`);
      await navigate('courses');
    }));
    footer.append(next);
    article.append(footer);
    if (state.progress) {
      const summary = node('form', 'summary');
      summary.append(node('h2', '', '学习总结'), node('p', '', '围绕业务背景、系统架构、核心流程和开发规范，整理你的理解与待解决的问题。'));
      const input = node('textarea');
      input.name = 'summary'; input.required = true; input.maxLength = 20000;
      input.value = state.progress.summary;
      input.setAttribute('aria-label', '学习总结');
      summary.append(input, submitButton('提交管理员验收'));
      if (state.progress.feedback) summary.append(node('p', '', '审核反馈 · ' + state.progress.feedback));
      submitForm(summary, async (values) => {
        await api(`/api/courses/${course.id}/summary`, values);
        state.progress = await api(`/api/courses/${course.id}/progress`);
        feedback('总结已提交，等待管理员审核');
        await navigate('courses');
      });
      article.append(summary);
    }
    layout.append(list, article);
    result.append(layout);
    return result;
  }
  const controls = [];
  if (state.user.admin) controls.push(button('生成课程草稿', async () => {
    const project = state.project.id;
    const current = await projectJob('courses');
    if (current.status === 'complete' && state.project?.id === project && state.view === 'courses' && !state.course) {
      await loadCourse(current.result.course_id); await navigate('courses');
    }
  }, 'primary'));
  result.append(header('培训课程', state.project.description, controls));
  const courses = await api(`/api/projects/${state.project.id}/courses`);
  if (!courses.length) result.append(empty('从一份课程开始', state.user.admin ?
    '建立源码索引，添加团队文档，再生成并审核培训课程。' : '项目负责人发布课程后，你可以在这里开始学习。'));
  const rows = node('div', 'rows');
  courses.forEach((course) => rows.append(row(course.title, `${statusNames[course.status]} · ${course.chapter_count} 章`,
    [button('打开课程', async () => { await loadCourse(course.id); await navigate('courses'); })],
    new Date(course.created * 1000).toLocaleString())));
  result.append(rows);
  return result;
}

function editChapter() {
  const course = state.course;
  const index = state.chapter;
  const chapter = course.chapters[index];
  const form = node('form');
  const content = field('章节内容 · Markdown', 'content', chapter.content, 'textarea');
  content.querySelector('textarea').className = 'editor';
  form.append(field('课程标题', 'course_title', course.title), field('章节标题', 'title', chapter.title),
    field('学习目标 · 每行一项', 'objectives', chapter.objectives.join('\n'), 'textarea'), content, submitButton('保存草稿'));
  submitForm(form, async (values) => {
    const chapters = course.chapters.map((item, number) => ({ title: number === index ? values.title : item.title,
      objectives: number === index ? values.objectives.split('\n').map((value) => value.trim()).filter(Boolean) : item.objectives,
      content: number === index ? values.content : item.content,
      source_ids: item.citations.map((ref) => ref.id) }));
    await api(`/api/courses/${course.id}`, { title: values.course_title, chapters }, 'PUT');
    const selected = state.chapter;
    await loadCourse(course.id);
    state.chapter = selected;
    $('dialog').close();
    await navigate('courses');
  });
  showDialog('编辑课程草稿', form);
}

async function renderEntries() {
  const result = node('div');
  result.append(header('源码阅读', '选择项目起点，沿核心流程阅读实现。', [button('推荐阅读起点', async () => {
    const project = state.project.id;
    await projectJob('entries');
    if (state.project?.id === project && state.view === 'entries') await navigate('entries');
  }, 'primary')]));
  if (!state.project.overview) {
    result.append(empty('建立源码索引', '索引完成后可以浏览模块、选择符号并生成阅读路线。',
      state.user.admin ? button('建立索引', async () => { await projectJob('index'); await navigate('entries'); }) : null));
    return result;
  }
  const search = node('form', 'field-row');
  search.append(field('查找自定义起点', 'query'), submitButton('查找符号'));
  const matches = node('div', 'rows');
  submitForm(search, async (values) => {
    const symbols = await api(`/api/projects/${state.project.id}/symbols?q=${encodeURIComponent(values.query)}`);
    matches.replaceChildren(...symbols.map((symbol) => entryRow({ entry: symbol.id, title: symbol.name, reason: symbol.signature })));
    if (!symbols.length) matches.append(node('p', 'meta', '没有匹配的符号'));
  });
  result.append(search);
  const rows = node('div', 'rows section');
  const entries = state.project.overview.flows;
  entries.slice(0, 8).forEach((entry) => rows.append(entryRow(entry)));
  if (entries.length > 8) {
    const more = node('details');
    more.append(node('summary', '', `展开其余 ${entries.length - 8} 个阅读起点`));
    entries.slice(8).forEach((entry) => more.append(entryRow(entry)));
    rows.append(more);
  }
  result.append(matches, rows);
  const modules = node('details', 'section');
  modules.append(node('summary', '', '核心模块'));
  for (const module of state.project.overview.modules) modules.append(row(module.file, `${module.symbols} 个符号`,
    [button('查看源码', async () => {
      const source = await api(`/api/snapshots/${state.project.snapshot_id}/source?file=${encodeURIComponent(module.file)}`);
      $('source-title').textContent = module.file;
      $('source-body').textContent = source.source;
      $('source-dialog').showModal();
    })]));
  result.append(modules);
  return result;
}

function entryRow(entry) {
  return row(entry.title, entry.reason, [button('生成阅读路线', async () => {
    const key = state.project.id;
    const snapshot = state.project.snapshot_id;
    const job = await projectJob('trails', { entry: entry.entry, title: entry.title });
    if (state.project?.id === key && job.status === 'complete') renderTrail(job.result, job.id, snapshot);
  })], entry.entry);
}

function renderTrail(trail, jobId, snapshot) {
  const result = node('div');
  const download = node('a', '', '导出 Markdown');
  download.href = `/api/jobs/${jobId}/export`;
  result.append(header(trail.title, trail.summary, [download]));
  for (const step of trail.steps) {
    const section = node('section', 'section');
    section.append(node('h2', '', step.title), markdown(step.explanation));
    section.append(button(`${step.file} · L${step.start}–${step.end}`, async () => {
      const source = await api(`/api/snapshots/${snapshot}/source?file=${encodeURIComponent(step.file)}`);
      $('source-title').textContent = `${step.file} · L${step.start}–${step.end}`;
      $('source-body').textContent = source.source.split('\n').map((line, index) => `${index + 1}  ${line}`).join('\n');
      $('source-dialog').showModal();
    }));
    result.append(section);
  }
  $('main').replaceChildren(result);
}

async function renderKnowledge() {
  const result = node('div');
  const controls = state.user.admin ? [button('项目设置', editProject), button('更新源码索引', async () => {
    const project = state.project.id;
    await projectJob('index');
    if (state.project?.id === project && state.view === 'knowledge') await navigate('knowledge');
  })] : [];
  result.append(header('项目知识库', '源码与团队资料共同构成课程和问答的依据。', controls));
  if (state.user.admin) {
    const form = node('form', 'field-row');
    const file = field('上传团队文档', 'file', '', 'file');
    const input = file.querySelector('input'); input.accept = '.md,.pdf';
    form.append(file, submitButton('上传并建立索引'));
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const submit = form.querySelector('[type=submit]'); submit.disabled = true;
      const project = state.project.id;
      try {
        await watchJob(await api(`/api/projects/${project}/documents`, new FormData(form)));
        if (state.project?.id === project && state.view === 'knowledge') { form.reset(); await navigate('knowledge'); }
      } catch (error) { feedback(error.message, true); }
      finally { submit.disabled = false; }
    });
    result.append(form);
  }
  const documents = await api(`/api/projects/${state.project.id}/documents`);
  const rows = node('div', 'rows section');
  for (const document of documents) {
    const controls = [button('查看', async () => {
      const current = await api(`/api/documents/${document.id}`);
      $('source-title').textContent = current.name;
      $('source-body').textContent = current.body;
      $('source-dialog').showModal();
    })];
    if (state.user.admin) controls.push(button('删除', async () => {
      if (!confirm(`删除文档 ${document.name}？`)) return;
      await api(`/api/projects/${state.project.id}/documents/${document.id}`, {}, 'DELETE');
      await navigate('knowledge');
    }, 'quiet danger'));
    rows.append(row(document.name, document.origin === 'repository' ? '仓库文档' : '上传文档', controls,
      `${document.digest.slice(0, 8)} · ${new Date(document.created * 1000).toLocaleDateString()}`));
  }
  if (!documents.length) rows.append(empty('添加项目资料', '上传 Markdown 或文本型 PDF，补充业务背景、架构决策和开发规范。'));
  result.append(rows);
  return result;
}

function editProject() {
  const project = state.project;
  const form = node('form');
  const token = field('访问令牌 · 更换源码位置时重新填写', 'token', '', 'password');
  token.querySelector('input').required = false;
  const description = field('项目介绍', 'description', project.description, 'textarea');
  description.querySelector('textarea').required = false;
  form.append(field('项目名称', 'name', project.name), field('源码位置', 'source', project.source), token,
    description, submitButton('保存项目设置'));
  submitForm(form, async (values) => {
    await api(`/api/projects/${project.id}`, values, 'PUT');
    form.reset(); $('dialog').close();
    $('project-picker').querySelector(`option[value="${project.id}"]`).textContent = values.name;
    await loadProject(project.id); await navigate('knowledge');
  });
  showDialog('项目设置', form);
}

async function newConversation() {
  if (!state.course || state.course.status !== 'published') {
    feedback('请先打开一门已发布课程'); return;
  }
  const value = await api(`/api/projects/${state.project.id}/conversations`, { course_id: state.course.id });
  await openConversation(value.id);
}

async function openConversation(key) {
  const conversation = await api(`/api/conversations/${key}`);
  if (conversation.project_id !== state.project.id) return;
  state.conversation = conversation;
  await loadCourse(conversation.course_id);
  if (state.conversation?.id !== key || state.course?.id !== conversation.course_id) return;
  state.chapter = conversation.chapter;
  localStorage.setItem(`repotrail-conversation-${state.user.id}-${state.project.id}`, key);
  $('chat-panel').hidden = false;
  $('chat-context').textContent = state.course.title;
  renderMessages();
  await refreshConversations();
  await navigate('courses');
  if (conversation.active_job_id && !state.streams.has(conversation.active_job_id)) {
    monitorAnswer(conversation.active_job_id, key).catch((error) => feedback(error.message, true));
  }
}

function renderMessages() {
  const conversation = state.conversation;
  if (!conversation) return;
  const container = $('messages');
  const nearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 100;
  const rows = conversation.messages.map((message) => {
    const result = node('div', 'message ' + message.role);
    result.append(node('div', 'message-label', message.role === 'user' ? '你' : 'RepoTrail'));
    result.append(markdown(message.content || (message.status === 'queued' ? '等待回答' : '正在检索与阅读'), message.citations));
    if (message.role === 'assistant') {
      result.append(referenceButtons(message.citations));
      const actions = node('div', 'actions');
      if (message.status === 'complete') actions.append(button(message.shared ? '撤回分享' : '分享这组问答', async () => {
        await api(`/api/messages/${message.id}/share`, { shared: !message.shared });
        message.shared = !message.shared; renderMessages();
      }, 'quiet'));
      if (['failed', 'cancelled'].includes(message.status)) {
        actions.append(node('span', 'meta', statusNames[message.status]));
        if (message.job_id) actions.append(button('重试', async () => {
          const job = await api(`/api/jobs/${message.job_id}/retry`, {});
          conversation.active_job_id = job.id;
          await monitorAnswer(job.id, conversation.id);
        }));
      }
      result.append(actions);
    }
    return result;
  });
  if (conversation.has_more && conversation.messages.length) rows.unshift(button('查看更早的消息', async () => {
    const first = conversation.messages[0].ordinal;
    const older = await api(`/api/conversations/${conversation.id}?before=${first}`);
    if (state.conversation?.id !== conversation.id) return;
    const height = container.scrollHeight;
    conversation.messages = [...older.messages, ...conversation.messages];
    conversation.has_more = older.has_more;
    renderMessages();
    container.scrollTop += container.scrollHeight - height;
  }, 'quiet'));
  if (!rows.length) rows.push(node('p', 'meta', '从当前章节出发，追问业务概念、模块关系或实现细节。'));
  container.replaceChildren(...rows);
  if (nearBottom) container.scrollTop = container.scrollHeight;
  $('send-question').disabled = Boolean(conversation.active_job_id);
  $('stop-answer').hidden = !conversation.active_job_id;
}

async function monitorAnswer(jobId, conversationId) {
  try {
    await watchJob({ id: jobId }, (current) => {
      if (state.conversation?.id !== conversationId) return;
      const message = current.message;
      if (message) {
        const index = state.conversation.messages.findIndex((item) => item.id === message.id);
        if (index >= 0) state.conversation.messages[index] = message;
        else state.conversation.messages.push(message);
      }
      renderMessages();
    });
  } finally {
    if (state.conversation?.id === conversationId) {
      const previous = state.conversation;
      const current = await api(`/api/conversations/${conversationId}`);
      const messages = new Map(previous.messages.map((message) => [message.id, message]));
      current.messages.forEach((message) => messages.set(message.id, message));
      current.messages = [...messages.values()].sort((left, right) => left.ordinal - right.ordinal);
      if (previous.messages.length > 50) current.has_more = previous.has_more;
      state.conversation = current;
      renderMessages();
      await refreshConversations();
    }
  }
}

async function renderShared() {
  const result = node('div');
  result.append(header('共享问答', '项目成员主动分享的学习记录。'));
  const values = await api(`/api/projects/${state.project.id}/shared`);
  if (!values.length) result.append(empty('还没有共享问答', '可以在学习追问中分享一组已完成的问答。'));
  for (const value of values) {
    const section = node('section', 'section');
    section.append(node('h2', '', value.question), markdown(value.content, value.citations), referenceButtons(value.citations));
    result.append(section);
  }
  return result;
}

async function renderJobs() {
  const result = node('div');
  result.append(header('任务记录', '查看生成进度，恢复中断的任务。', [button('刷新', () => navigate('jobs'))]));
  const jobs = await api(`/api/projects/${state.project.id}/jobs`);
  const rows = node('div', 'rows');
  for (const job of jobs) {
    const controls = [];
    if (['failed', 'cancelled'].includes(job.status)) controls.push(button('重试', async () => {
      try { await watchJob(await api(`/api/jobs/${job.id}/retry`, {})); }
      finally { if (state.view === 'jobs') await navigate('jobs'); }
    }));
    if (['running', 'queued'].includes(job.status)) controls.push(button('停止', async () => {
      await api(`/api/jobs/${job.id}/cancel`, {}); await navigate('jobs');
    }));
    if (job.status === 'complete' && job.kind === 'course') controls.push(button('打开课程', async () => {
      await loadCourse(job.result.course_id); await navigate('courses');
    }));
    if (job.status === 'complete' && job.kind === 'trail') controls.push(button('打开路线', () =>
      renderTrail(job.result, job.id, job.snapshot_id)));
    rows.append(row(`${jobNames[job.kind]} · ${statusNames[job.status]}`, job.error || job.stage, controls,
      new Date(job.created * 1000).toLocaleString()));
  }
  result.append(rows);
  return result;
}

async function renderMembers() {
  const result = node('div');
  result.append(header('成员与权限', '为项目分配成员，管理团队账号。', [button('创建成员账号', () => {
    const form = node('form');
    form.append(field('用户名', 'username'), field('姓名', 'name'), field('初始密码 · 至少 12 位', 'password', '', 'password'), submitButton('创建成员'));
    submitForm(form, async (values) => { await api('/api/users', values); form.reset(); $('dialog').close(); await navigate('members'); });
    showDialog('创建成员账号', form);
  }, 'primary')]));
  const users = await api('/api/users');
  const members = await api(`/api/projects/${state.project.id}/members`);
  const rows = node('div', 'rows');
  for (const user of users) {
    const included = members.some((item) => item.id === user.id);
    const controls = [];
    if (!user.admin && user.active) {
      controls.push(button(included ? '移出项目' : '加入项目', async () => {
        const path = `/api/projects/${state.project.id}/members`;
        if (included) await api(`${path}/${user.id}`, {}, 'DELETE');
        else await api(path, { user_id: user.id });
        await navigate('members');
      }));
      controls.push(button('停用账号', async () => {
        if (!confirm(`停用 ${user.name} 的账号？`)) return;
        await api(`/api/users/${user.id}/disable`, {}); await navigate('members');
      }, 'quiet danger'));
    }
    rows.append(row(user.name, user.username, controls, user.admin ? '管理员' : !user.active ? '已停用' : included ? '项目成员' : '未加入项目'));
  }
  result.append(rows);
  return result;
}

async function renderAcceptance() {
  const result = node('div');
  result.append(header('学习验收', '按业务背景、系统架构、核心流程和开发规范复核学习总结。'));
  const learning = await api(`/api/projects/${state.project.id}/learning`);
  for (const item of learning) result.append(row(item.user.name, item.course_title, [],
    `${item.read_chapters}/${item.total_chapters} 章已读 · ${statusNames[item.status]}`));
  const courses = (await api(`/api/projects/${state.project.id}/courses`)).filter((course) => course.status === 'published');
  for (const course of courses) {
    const section = node('section', 'section');
    section.append(node('h2', '', course.title));
    const values = await api(`/api/courses/${course.id}/submissions`);
    if (!values.length) section.append(node('p', 'meta', '暂未收到学习总结'));
    for (const value of values) {
      const review = button('查看总结', () => {
        const form = node('form');
        form.append(markdown(value.summary));
        if (value.status === 'submitted') {
          const feedbackField = field('审核意见', 'feedback', '', 'textarea');
          feedbackField.querySelector('textarea').required = false;
          const actions = node('div', 'actions');
          const finish = async (approved) => {
            await api(`/api/submissions/${value.id}/review`, { approved, feedback: new FormData(form).get('feedback') });
            feedback(approved ? '已确认培训完成' : '已提交补充意见');
            $('dialog').close(); await navigate('acceptance');
          };
          actions.append(button('需要补充', () => finish(false)), button('确认培训完成', () => finish(true), 'primary'));
          form.append(feedbackField, actions);
        } else if (value.feedback) form.append(node('p', '', value.feedback));
        showDialog(value.user.name + ' · 学习总结', form);
      });
      section.append(row(value.user.name, statusNames[value.status], [review], new Date(value.created * 1000).toLocaleString()));
    }
    result.append(section);
  }
  return result;
}

async function renderSettings() {
  const result = node('div');
  result.append(header('模型与用量', '统一管理团队的生成模型和检索配置。'));
  const provider = await api('/api/provider');
  const form = node('form', 'field-stack');
  const key = field('API Key', 'api_key', '', 'password');
  key.querySelector('input').required = false;
  key.querySelector('input').placeholder = provider.has_key ? '已保存，留空保留' : '输入 API Key';
  const modelField = field('模型 ID', 'model', provider.model);
  modelField.querySelector('input').setAttribute('list', 'model-options');
  const options = node('datalist'); options.id = 'model-options';
  const actions = node('div', 'actions');
  const values = () => {
    const body = Object.fromEntries(new FormData(form));
    body.input_price = body.input_price === '' ? null : Number(body.input_price);
    body.output_price = body.output_price === '' ? null : Number(body.output_price);
    return body;
  };
  const providerRequest = async (endpoint) => {
    const body = values();
    const controls = [...form.querySelectorAll('input,button')].map((input) => [input, input.disabled]);
    controls.forEach(([input]) => { input.disabled = true; });
    try { return await api(endpoint, body); }
    finally { controls.forEach(([input, disabled]) => { input.disabled = disabled; }); }
  };
  actions.append(button('获取模型', async () => {
    const data = await providerRequest('/api/provider/models');
    options.replaceChildren(...data.models.map((model) => { const option = node('option'); option.value = model; return option; }));
    feedback(`已获取 ${data.models.length} 个模型`);
  }), button('测试连接', async () => {
    const data = await providerRequest('/api/provider/test'); feedback(data.message);
  }), submitButton('保存模型配置'));
  const pricing = node('div', 'field-row');
  for (const [name, label] of [['input_price', '每百万输入 Token 单价'], ['output_price', '每百万输出 Token 单价']]) {
    const price = field(label, name, provider[name] ?? '', 'number');
    const input = price.querySelector('input'); input.required = false; input.min = '0'; input.step = 'any';
    pricing.append(price);
  }
  form.append(field('连接名称', 'name', provider.name), field('API Base URL', 'base_url', provider.base_url), key,
    modelField, options, pricing, field('计价币种', 'currency', provider.currency || 'CNY'), actions);
  submitForm(form, async () => { await providerRequest('/api/provider'); key.querySelector('input').value = ''; feedback('模型配置已保存'); });
  result.append(form);
  const embedding = await api('/api/embeddings');
  const embeddingForm = node('form', 'field-stack section');
  embeddingForm.append(node('h2', '', '知识库检索'));
  const modeLabel = node('label', '', '嵌入方式');
  const mode = node('select'); mode.name = 'mode';
  for (const [value, title] of [['local', '本地多语言模型'], ['api', '兼容 Embeddings API'], ['off', '关键词检索']]) {
    const option = node('option', '', title); option.value = value; mode.append(option);
  }
  mode.value = embedding.mode;
  modeLabel.append(mode);
  const url = field('嵌入 API Base URL', 'base_url', embedding.base_url);
  url.querySelector('input').required = false;
  const secret = field('嵌入 API Key', 'api_key', '', 'password');
  secret.querySelector('input').required = false;
  secret.querySelector('input').placeholder = embedding.has_key ? '已保存，留空保留' : '输入嵌入 API Key';
  embeddingForm.append(modeLabel, field('嵌入模型', 'model', embedding.model), url, secret, submitButton('保存检索配置'));
  submitForm(embeddingForm, async (body) => {
    await api('/api/embeddings', body); secret.querySelector('input').value = '';
    feedback('检索配置已保存。更新已有资料的向量时，请重建项目检索索引。');
  });
  result.append(embeddingForm);
  if (state.project) result.append(button('重建当前项目检索索引', async () => { await projectJob('reembed'); feedback('项目检索索引已更新'); }));
  const usage = await api('/api/usage');
  const section = node('section', 'section');
  section.append(node('h2', '', '模型调用记录'));
  const table = node('table', 'usage-table');
  const head = node('tr');
  ['项目', '成员', '模型', '输入 / 输出 Token', '费用估算'].forEach((title) => head.append(node('th', '', title)));
  table.append(head);
  const users = await api('/api/users');
  for (const item of usage.slice(0, 100)) {
    const tr = node('tr');
    const metrics = item.metrics;
    const project = state.projects.find((value) => value.id === item.project_id);
    const user = users.find((value) => value.id === item.user_id);
    [project?.name || item.project_id.slice(0, 8), user?.name || '', metrics.model,
      metrics.usage_complete ? `${metrics.prompt_tokens} / ${metrics.completion_tokens}` : '服务未返回完整用量',
      metrics.estimated_cost === null ? (metrics.usage_complete ? '单价未记录' : '用量未完整返回') : `${metrics.estimated_cost.toFixed(4)} ${metrics.currency}`]
      .forEach((text) => tr.append(node('td', '', text)));
    table.append(tr);
  }
  section.append(table);
  result.append(section);
  return result;
}

$('login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const submit = form.querySelector('button'); submit.disabled = true;
  $('login-error').textContent = '';
  try {
    const data = await api('/api/login', Object.fromEntries(new FormData(form)));
    form.reset(); await authenticated(data);
  } catch (error) { $('login-error').textContent = error.message; }
  finally { submit.disabled = false; }
});

$('logout').addEventListener('click', async () => {
  try { await api('/api/logout', {}); showLogin(); }
  catch (error) { feedback(error.message, true); }
});
$('project-picker').addEventListener('change', (event) => loadProject(event.target.value).catch((error) => feedback(error.message, true)));
$('create-project').addEventListener('click', createProject);
document.querySelectorAll('[data-view]').forEach((item) => item.addEventListener('click', () => navigate(item.dataset.view).catch((error) => feedback(error.message, true))));
$('close-dialog').addEventListener('click', () => $('dialog').close());
$('close-source').addEventListener('click', () => $('source-dialog').close());
$('new-chat').addEventListener('click', () => newConversation().catch((error) => feedback(error.message, true)));
$('delete-chat').addEventListener('click', async () => {
  try {
    if (!confirm('删除这段学习会话及其分享记录？')) return;
    await api(`/api/conversations/${state.conversation.id}`, {}, 'DELETE');
    localStorage.removeItem(`repotrail-conversation-${state.user.id}-${state.project.id}`);
    state.conversation = null; $('chat-panel').hidden = true; await refreshConversations();
  } catch (error) { feedback(error.message, true); }
});
$('stop-answer').addEventListener('click', async () => {
  try { await api(`/api/jobs/${state.conversation.active_job_id}/cancel`, {}); }
  catch (error) { feedback(error.message, true); }
});
$('chat-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!state.conversation || state.conversation.active_job_id) return;
  const content = $('question').value.trim();
  if (!content) return;
  $('send-question').disabled = true;
  const key = state.conversation.id;
  try {
    const requestId = globalThis.crypto?.randomUUID?.() || Date.now().toString(36) + Math.random().toString(36);
    const job = await api(`/api/conversations/${key}/messages`, { content, request_id: requestId });
    $('question').value = '';
    state.conversation = await api(`/api/conversations/${key}`);
    renderMessages();
    await monitorAnswer(job.id, key);
  } catch (error) { feedback(error.message, true); }
  finally { if (state.conversation?.id === key) renderMessages(); }
});
$('change-password').addEventListener('click', () => {
  const form = node('form');
  const current = field('当前密码', 'current', '', 'password');
  current.querySelector('input').autocomplete = 'current-password';
  form.append(current, field('新密码 · 至少 12 位', 'password', '', 'password'), submitButton('更新密码并重新登录'));
  submitForm(form, async (values) => { await api('/api/password', values); form.reset(); $('dialog').close(); showLogin(); });
  showDialog('修改密码', form);
});

(async () => {
  try { await authenticated(await api('/api/me')); }
  catch (error) { if (!state.user) showLogin(); }
})();
