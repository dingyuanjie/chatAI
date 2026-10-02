import { useCallback, useEffect, useRef, useState } from "react";
import { Button, ConfigProvider, Drawer, Form, Input, InputNumber, Modal, Popconfirm, Select, Spin, Switch, Upload, message as toast, theme as antdTheme } from "antd";
import {
  ArrowDownOutlined, ArrowRightOutlined, BulbOutlined, CheckOutlined, DeleteOutlined, ExperimentOutlined, FileMarkdownOutlined, HistoryOutlined,
  LogoutOutlined, MenuOutlined, MessageOutlined, MoonOutlined, PaperClipOutlined, PlusOutlined, SunOutlined,
  SendOutlined, SettingOutlined, UploadOutlined, UserOutlined,
} from "@ant-design/icons";
import axios from "axios";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import "highlight.js/styles/github-dark.css";
import "./style.css";

axios.defaults.withCredentials = true;

type User = { id: string; email: string; name: string };
type ChatItem = { role: "human" | "ai" | "assistant" | "system"; content: string };
type ChatSession = { session_id: string; title: string; updated_at: string };
type KnowledgeFile = { id: string; filename: string; created_at: string; chunks: number; version?: number };
type PreviewFile = { id: string; filename: string; content: string };
type KnowledgeVersion = { version: number; content_hash: string; characters: number; created_at: string };
type Locale = "zh" | "en";
type Theme = "light" | "dark";
type ModelSettings = { provider: "local" | "remote"; ollama_url: string; remote_base_url: string; local_chat_model: string; local_research_model: string; remote_chat_model: string; remote_research_model: string; embedding_model: string; api_key_configured: boolean };
type ResearchAgent = { id: string; name: string; name_en: string; focus: string; focus_en: string; layer: string };
type ResearchWorkflow = { id: string; name: string; name_en: string; budgets: Record<string, { max_active_agents: number; max_parallel_agents: number; max_debate_rounds: number; max_context_tokens: number }> };
type ResearchOutput = { id: string; run_id: string; round_no: number; agent_id: string; agent_name: string; status: string; content: string; sources: { provider: string; title: string; url: string; snippet?: string }[] };
type ResearchTask = { id: string; round_no: number; stage: string; agent_id: string; status: string; attempt: number; error: string };
type ResearchEvidence = { id: string; round_no: number; provider: string; source_type: string; title: string; url: string; snippet: string; cited_by: string[] };
type ResearchClaimEvidence = { id: string; citation_label: string; source_type: string; title: string; url: string; relation: string };
type ResearchClaim = { id: string; round_no: number; agent_id: string; claim_text: string; claim_type: string; epistemic_status: string; uncertainty: string; evidence: ResearchClaimEvidence[] };
type ResearchMemory = { id: string; title: string; question: string; round_no: number; summary: string; relevance?: number; open_questions: { text: string; type: string }[] };
type ResearchGraph = { run_id: string; round_start: number; round_end: number; nodes: { id: string; type: string; label: string; relation?: string; status?: string; round_no?: number; stage?: string; preview?: string; source_type?: string; url?: string; external_run?: boolean }[]; edges: { source: string; target: string; relation: string; relevance?: string }[] };
type ResearchRun = { id: string; title: string; question: string; agents: string[]; workflow_mode: string; research_depth: string; route_reason: string; current_stage?: string; tasks?: ResearchTask[]; evidence?: ResearchEvidence[]; claims?: ResearchClaim[]; related_memory?: ResearchMemory[]; memory?: ResearchMemory[]; max_rounds: number | null; continuous: boolean; current_round: number; status: string; summary: string; error: string; updated_at: string; outputs?: ResearchOutput[] };

const newId = () => crypto.randomUUID();
const apiErrorInEnglish: Record<string, string> = {
  "请输入有效的邮箱地址": "Enter a valid email address.",
  "密码长度需要在 8 到 256 个字符之间": "Password must be between 8 and 256 characters.",
  "该邮箱已注册，请直接登录": "This email is already registered. Please sign in.",
  "邮箱或密码不正确": "The email or password is incorrect.",
  "请先登录": "Please sign in first.",
  "登录已过期，请重新登录": "Your session expired. Please sign in again.",
  "仅支持上传 .md Markdown 文件": "Only Markdown .md files are supported.",
  "Markdown 文件不能超过 2 MB": "Markdown files must be 2 MB or smaller.",
  "文件必须使用 UTF-8 编码": "The file must use UTF-8 encoding.",
  "Markdown 文件内容为空": "The Markdown file is empty.",
  "文件不存在": "File not found.",
  "本地模型回复失败，请检查 Ollama 服务后重试。": "The local model could not reply. Check Ollama and try again.",
};
const apiErrorText = (error: any, locale: Locale, fallback: string) => {
  const detail = error?.response?.data?.detail || error?.data;
  if (!detail) return fallback;
  return locale === "en" ? apiErrorInEnglish[detail] || detail : detail;
};

function AuthScreen({ onAuth, locale, setLocale, theme, setTheme }: {
  onAuth: (user: User) => void;
  locale: Locale;
  setLocale: (locale: Locale) => void;
  theme: Theme;
  setTheme: (theme: Theme) => void;
}) {
  const t = (zh: string, en: string) => locale === "zh" ? zh : en;
  const [registering, setRegistering] = useState(false);
  const [busy, setBusy] = useState(false);
  const [form] = Form.useForm();
  const submit = async (values: { email: string; password: string; name?: string }) => {
    setBusy(true);
    try {
      const { data } = await axios.post<User>(`/api/auth/${registering ? "register" : "login"}`, values);
      onAuth(data);
      toast.success(registering ? t("账号已创建，欢迎使用", "Account created. Welcome!") : t("登录成功，欢迎回来", "Welcome back!") );
    } catch (e: any) {
      toast.error(apiErrorText(e, locale, t("请求失败，请检查后端服务", "Request failed. Check that the server is running.")));
    } finally { setBusy(false); }
  };
  return <main className="auth-page">
    <div className="appearance-controls auth-appearance">
      <button onClick={() => setLocale(locale === "zh" ? "en" : "zh")} aria-label={t("切换为英文", "Switch to Chinese")}>{locale === "zh" ? "EN" : "中"}</button>
      <button onClick={() => setTheme(theme === "light" ? "dark" : "light")} aria-label={t("切换主题", "Toggle theme")}>{theme === "light" ? <MoonOutlined /> : <SunOutlined />}</button>
    </div>
    <section className="auth-story">
      <div className="brand"><span className="brand-mark"><BulbOutlined /></span><span>{t("结构生力理论", "Structural Vital Force Theory")} <small>RESEARCH SPACE</small></span></div>
      <div className="story-copy">
        <div className="eyebrow"><span className="live-dot" /> {t("结构生力理论 · 研究空间", "STRUCTURAL VITAL FORCE THEORY · RESEARCH")}</div>
        <h1>{locale === "zh" ? <>结构生力理论，<br /><span>持续研究与交流。</span></> : <>Structural Vital Force<br /><span>Theory Research.</span></>}</h1>
        <p>{t("在同一空间整理理论资料、提出问题，并延续你的研究对话。", "Organize theory materials, ask questions, and continue your research in one place.")}</p>
        <div className="story-note"><div className="note-orb"><BulbOutlined /></div><div><b>{t("由本地 AI 驱动", "Powered by local AI")}</b><span>{t("对话与理论资料保存在本机", "Chats and theory materials stay on this device")}</span></div><CheckOutlined /></div>
      </div>
      <div className="story-footer">{t("结构生力理论研究空间", "Structural Vital Force Theory Research Space")} <span>© 2026</span></div>
      <div className="glow glow-one" /><div className="glow glow-two" />
    </section>
    <section className="auth-panel">
      <div className="auth-card">
        <div className="auth-mobile-brand"><span className="brand-mark"><BulbOutlined /></span> {t("结构生力理论", "Structural Vital Force Theory")}</div>
        <div className="auth-kicker">{registering ? t("建立理论研究空间", "CREATE A RESEARCH SPACE") : t("结构生力理论研究空间", "STRUCTURAL VITAL FORCE THEORY")}</div>
        <h2>{registering ? t("创建研究账号", "Create your account") : t("继续理论研究", "Continue your research")}</h2>
        <p className="auth-subtitle">{registering ? t("创建账号以保存理论资料、研究对话与个人会话。", "Create an account to save theory materials, research chats, and sessions.") : t("登录后继续查看理论资料、对话与知识库。", "Sign in to continue with your theory materials, chats, and knowledge base.")}</p>
        <Form form={form} layout="vertical" requiredMark={false} onFinish={submit} className="auth-form">
          {registering && <Form.Item name="name" label={t("怎么称呼你", "Your name")} rules={[{ required: true, message: t("请输入你的名字", "Please enter your name") }]}><Input size="large" prefix={<UserOutlined />} placeholder={t("你的名字", "Name")} autoComplete="name" /></Form.Item>}
          <Form.Item name="email" label={t("邮箱地址", "Email address")} rules={[{ required: true, type: "email", message: t("请输入有效邮箱", "Enter a valid email address") }]}><Input size="large" prefix={<span className="field-at">@</span>} placeholder="you@example.com" autoComplete="email" /></Form.Item>
          <Form.Item name="password" label={t("密码", "Password")} rules={[{ required: true, min: 8, message: t("密码至少需要 8 个字符", "Password must be at least 8 characters") }]}><Input.Password size="large" placeholder={t("至少 8 个字符", "At least 8 characters")} autoComplete={registering ? "new-password" : "current-password"} /></Form.Item>
          <Button type="primary" htmlType="submit" loading={busy} size="large" block className="auth-submit">{registering ? t("创建账号", "Create account") : t("登录", "Sign in")}<ArrowRightOutlined /></Button>
        </Form>
        <div className="auth-switch">{registering ? t("已经有账号了？", "Already have an account?") : t("首次使用？", "New here?")}<button onClick={() => { setRegistering(!registering); form.resetFields(); }}>{registering ? t("直接登录", "Sign in instead") : t("创建研究账号", "Create a research account")}</button></div>
        <div className="privacy-line"><span><CheckOutlined /></span> {t("账号资料与知识库保存在本机", "Account data and knowledge base stay on this device")}</div>
      </div>
    </section>
  </main>;
}

function ScienceWorkspace({ locale, t }: { locale: Locale; t: (zh: string, en: string) => string }) {
  const [agents, setAgents] = useState<ResearchAgent[]>([]);
  const [workflows, setWorkflows] = useState<ResearchWorkflow[]>([]);
  const [runs, setRuns] = useState<ResearchRun[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<ResearchRun | null>(null);
  const [selectedOutput, setSelectedOutput] = useState<ResearchOutput | null>(null);
  const [graph, setGraph] = useState<ResearchGraph | null>(null);
  const [graphOpen, setGraphOpen] = useState(false);
  const [graphBusy, setGraphBusy] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [question, setQuestion] = useState("");
  const [selectedAgents, setSelectedAgents] = useState(["physics", "math", "complexity", "critic"]);
  const [agentSearch, setAgentSearch] = useState("");
  const [autoRoute, setAutoRoute] = useState(true);
  const [workflowMode, setWorkflowMode] = useState("multidisciplinary");
  const [researchDepth, setResearchDepth] = useState("normal");
  const [maxRounds, setMaxRounds] = useState<number | null>(5);
  const [continuous, setContinuous] = useState(false);
  const [creating, setCreating] = useState(false);
  const [actionBusy, setActionBusy] = useState(false);
  const [filter, setFilter] = useState("all");
  const maxSelectableAgents = Math.min(researchDepth === "fast" ? 3 : researchDepth === "deep" ? 15 : 7, agents.length);
  const agentName = (agentId: string, fallback?: string) => {
    const agent = agents.find((item) => item.id === agentId);
    if (!agent) return fallback || agentId;
    return locale === "zh" ? agent.name : agent.name_en;
  };
  const workflowName = (workflowId: string) => {
    const workflow = workflows.find((item) => item.id === workflowId);
    return workflow ? (locale === "zh" ? workflow.name : workflow.name_en) : workflowId;
  };
  const depthName = (depth: string) => ({
    fast: t("快速", "Fast"), normal: t("标准", "Standard"), deep: t("深度", "Deep"),
  }[depth] || depth);
  const statusLabel = (status: string) => ({
    queued: t("排队中", "Queued"), running: t("探索中", "Running"), pause_requested: t("正在暂停…", "Pausing…"),
    paused: t("已暂停", "Paused"), cancel_requested: t("正在停止…", "Stopping…"), cancelled: t("已停止", "Stopped"),
    completed: t("已完成", "Completed"), failed: t("遇到错误", "Needs attention"),
  }[status] || status);
  const refreshRuns = useCallback(async () => {
    const { data } = await axios.get<ResearchRun[]>("/api/research/runs");
    setRuns(data);
    return data;
  }, []);
  const refreshDetail = useCallback(async (id: string) => {
    const { data } = await axios.get<ResearchRun>(`/api/research/runs/${id}`);
    setDetail(data);
    return data;
  }, []);

  useEffect(() => {
    Promise.all([axios.get<ResearchAgent[]>("/api/research/agents"), axios.get<ResearchWorkflow[]>("/api/research/workflows"), refreshRuns()]).then(([agentResponse, workflowResponse, currentRuns]) => {
      setAgents(agentResponse.data);
      setWorkflows(workflowResponse.data);
      if (currentRuns.length) setSelectedId((existing) => existing || currentRuns[0].id);
    }).catch((error) => toast.error(apiErrorText(error, locale, t("无法加载科学探索工作区", "Could not load the science workspace"))));
  }, [refreshRuns, locale, t]);

  useEffect(() => {
    if (!selectedId) { setDetail(null); return; }
    let active = true;
    const load = async () => {
      try {
        const updated = await refreshDetail(selectedId);
        if (!active) return;
        await refreshRuns();
        return updated;
      } catch (error) {
        if (active) toast.error(apiErrorText(error, locale, t("无法读取探索任务", "Could not load this exploration")));
      }
    };
    void load();
    const timer = window.setInterval(() => { void load(); }, 4000);
    return () => { active = false; window.clearInterval(timer); };
  }, [selectedId, refreshDetail, refreshRuns, locale, t]);

  const createRun = async () => {
    const cleanQuestion = question.trim();
    if (cleanQuestion.length < 8 || (!autoRoute && (selectedAgents.length < 2 || selectedAgents.length > maxSelectableAgents))) {
      toast.error(t("请填写研究问题，并确保手动选择的智能体数量符合当前研究档位", "Enter a question and choose a valid number of agents for this depth"));
      return;
    }
    setCreating(true);
    try {
      const payload = { title: title.trim() || cleanQuestion.split("\n")[0].slice(0, 80), question: cleanQuestion, agents: autoRoute ? [] : selectedAgents, workflow_mode: workflowMode, research_depth: researchDepth, max_rounds: continuous ? 0 : (maxRounds || 5) };
      const { data } = await axios.post<ResearchRun>("/api/research/runs", payload);
      setCreateOpen(false); setQuestion(""); setTitle("");
      await refreshRuns(); setSelectedId(data.id);
      toast.success(t("探索任务已启动，智能体正在并行工作", "Exploration started. Agents are working in parallel."));
    } catch (error) { toast.error(apiErrorText(error, locale, t("无法启动探索任务", "Could not start the exploration"))); }
    finally { setCreating(false); }
  };

  const controlRun = async (action: "pause" | "resume" | "stop") => {
    if (!detail) return;
    setActionBusy(true);
    try {
      await axios.post(`/api/research/runs/${detail.id}/${action}`);
      await refreshDetail(detail.id); await refreshRuns();
    } catch (error) { toast.error(apiErrorText(error, locale, t("任务操作失败", "Could not update the task"))); }
    finally { setActionBusy(false); }
  };

  const toggleResearchGraph = async () => {
    if (graphOpen) { setGraphOpen(false); return; }
    setGraphOpen(true);
    setGraphBusy(true);
    try {
      const { data } = await axios.get<ResearchGraph>(`/api/research/runs/${selectedId}/graph`);
      setGraph(data);
    } catch (error) {
      setGraphOpen(false);
      toast.error(apiErrorText(error, locale, t("无法加载研究图谱", "Could not load the research graph")));
    } finally { setGraphBusy(false); }
  };

  const filteredRuns = runs.filter((run) => filter === "all" || run.status === filter);
  const outputs = detail?.outputs || [];
  const tasks = detail?.tasks || [];
  const evidence = detail?.evidence || [];
  const claims = detail?.claims || [];
  const relatedMemory = detail?.related_memory || [];
  const activeTaskRound = Math.max(0, ...tasks.map((task) => task.round_no));
  const activeTasks = tasks.filter((task) => task.round_no === activeTaskRound);
  const graphNodeMap = new Map((graph?.nodes || []).map((node) => [node.id, node]));
  const graphEdges = graph && graph.run_id === detail?.id ? graph.edges.slice(0, 80) : [];
  const rounds = [...new Set(outputs.map((item) => item.round_no))].sort((a, b) => b - a);
  const progress = detail?.continuous ? null : detail?.max_rounds ? Math.min(100, Math.round((detail.current_round / detail.max_rounds) * 100)) : 0;
  const statusClass = (status?: string) => status === "running" || status === "queued" ? "is-running" : status === "completed" ? "is-done" : status === "failed" ? "is-failed" : "is-paused";

  return <div className="science-workspace">
    <div className="science-heading">
      <div><div className="science-kicker"><ExperimentOutlined /> {t("多智能体 · 跨学科研究", "MULTI-AGENT · INTERDISCIPLINARY RESEARCH")}</div><h1>{t("科学探索", "Science exploration")}</h1><p>{t("让不同学科的研究智能体围绕同一个问题并行分析、交叉质疑，并在每一轮留下可追溯的研究记录。", "Bring multiple research agents together to analyze one question, challenge each other, and preserve a traceable record of every round.")}</p></div>
      <Button type="primary" size="large" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>{t("新建探索", "New exploration")}</Button>
    </div>
    <div className="research-layout">
      <aside className="research-rail">
        <div className="research-rail-head"><b>{t("探索任务", "EXPLORATIONS")}</b><span>{runs.length}</span></div>
        <div className="run-filters">{[["all", t("全部", "All")], ["running", t("运行中", "Running")], ["paused", t("已暂停", "Paused")], ["completed", t("已完成", "Done")]].map(([id, label]) => <button key={id} className={filter === id ? "selected" : ""} onClick={() => setFilter(id)}>{label}</button>)}</div>
        <div className="run-list">{filteredRuns.length === 0 ? <div className="runs-empty"><ExperimentOutlined /><span>{t("还没有探索任务", "No explorations yet")}</span><button onClick={() => setCreateOpen(true)}>{t("发起第一次探索", "Start your first one")}</button></div> : filteredRuns.map((run) => <button key={run.id} className={`run-card ${selectedId === run.id ? "active" : ""}`} onClick={() => setSelectedId(run.id)}><div className="run-card-top"><span className={`status-dot ${statusClass(run.status)}`} />{statusLabel(run.status)}<time>{new Date(run.updated_at).toLocaleDateString(locale === "zh" ? "zh-CN" : "en-US", { month: "short", day: "numeric" })}</time></div><b>{run.title}</b><span className="run-card-bottom">{t(`第 ${run.current_round} 轮`, `Round ${run.current_round}`)} · {run.continuous ? t("持续运行", "Continuous") : t(`共 ${run.max_rounds} 轮`, `${run.max_rounds} rounds`)}</span></button>)}</div>
        <div className="research-rail-foot"><span className="live-dot" />{t("本地模型 · 学术网络 · 个人资料库", "Local model · scholarly web · your library")}</div>
      </aside>
      <section className="research-detail">
        {!detail ? <div className="research-welcome"><span className="research-hero-icon"><ExperimentOutlined /></span><h2>{t("从一个问题开始", "Start with a question")}</h2><p>{t("选择多种学科视角，让理论、证据和反例在持续迭代中相遇。", "Bring theories, evidence, and counterarguments together through iterative, interdisciplinary work.")}</p><Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>{t("创建科学探索", "Create an exploration")}</Button></div> : <>
          <div className="detail-topline"><span className={`status-chip ${statusClass(detail.status)}`}><i />{statusLabel(detail.status)}</span><span className="current-stage-label">{t("阶段", "Stage")}: {({ retrieval: t("资料检索", "Retrieval"), expert_analysis: t("专家分析", "Expert analysis"), debate: t("交叉质疑", "Adversarial review"), synthesis: t("综合判断", "Synthesis"), queued: t("排队中", "Queued") }[detail.current_stage || "queued"] || detail.current_stage)}</span><span>{detail.continuous ? t(`已完成 ${detail.current_round} 轮 · 持续运行`, `${detail.current_round} rounds · continuous`) : t(`第 ${detail.current_round} / ${detail.max_rounds} 轮`, `Round ${detail.current_round} / ${detail.max_rounds}`)}</span><div className="detail-actions">
            {(detail.status === "running" || detail.status === "queued" || detail.status === "pause_requested") && <Button disabled={detail.status === "pause_requested" || actionBusy} onClick={() => void controlRun("pause")}>{t("暂停并保存", "Pause & checkpoint")}</Button>}
            {(detail.status === "paused" || detail.status === "failed" || detail.status === "cancelled") && <Button type="primary" loading={actionBusy} onClick={() => void controlRun("resume")}>{t("从检查点继续", "Resume from checkpoint")}</Button>}
            {(detail.status === "running" || detail.status === "queued" || detail.status === "pause_requested") && <Popconfirm title={t("停止这个探索？", "Stop this exploration?")} description={t("已有结果和检查点会保留，之后仍可继续。", "Current outputs and checkpoints will be kept; you can resume later.")} onConfirm={() => void controlRun("stop")}><Button danger loading={actionBusy}>{t("停止", "Stop")}</Button></Popconfirm>}
          </div></div>
          {progress !== null && <div className="research-progress"><div style={{ width: `${progress}%` }} /></div>}
          <div className="detail-scroll"><div className="research-question"><div className="research-kicker">{t("核心研究问题", "RESEARCH QUESTION")}</div><h2>{detail.title}</h2><p>{detail.question}</p><div className="agent-chips">{detail.agents.map((id) => <span key={id}><i />{agentName(id)}</span>)}</div><div className="route-summary"><b>{workflowName(detail.workflow_mode)} · {depthName(detail.research_depth)}</b><span>{detail.route_reason}</span></div></div>
            <div className="graph-toggle-row"><span>{t("查看任务、主张、来源与记忆之间的关系", "Explore task, claim, source, and memory links")}</span><Button size="small" loading={graphBusy} onClick={() => void toggleResearchGraph()}>{graphOpen ? t("收起研究图谱", "Hide research graph") : t("打开研究图谱", "Open research graph")}</Button></div>
            {graphOpen && graph?.run_id === detail.id && <section className="research-graph-board"><div className="graph-board-heading"><div><b>{t("研究关系图", "RESEARCH GRAPH")}</b><small>{t(`显示第 ${graph.round_start}–${graph.round_end} 轮 · 保留最多 50 轮`, `Rounds ${graph.round_start}–${graph.round_end} · up to 50 rounds`)}</small></div><span>{graph.nodes.length} {t("节点", "nodes")} · {graph.edges.length} {t("关系", "links")}</span></div><div className="graph-node-counts">{["task", "output", "claim", "evidence", "memory"].map((type) => <div key={type}><b>{graph.nodes.filter((node) => node.type === type).length}</b><span>{({ task: t("任务", "Tasks"), output: t("输出", "Outputs"), claim: t("主张", "Claims"), evidence: t("来源", "Sources"), memory: t("记忆", "Memories") }[type])}</span></div>)}</div><div className="graph-edge-list">{graphEdges.length === 0 ? <p>{t("当前还没有可连接的研究节点", "No linked research nodes yet")}</p> : graphEdges.map((edge, index) => { const source = graphNodeMap.get(edge.source); const target = graphNodeMap.get(edge.target); return <div className="graph-edge" key={`${edge.source}-${edge.target}-${edge.relation}-${index}`}><span className={`graph-node-type ${source?.type || ""}`}>{source?.type || "node"}</span><b title={source?.label}>{source?.label || edge.source}</b><i>→ {({ has_task: t("包含任务", "has task"), has_output: t("包含输出", "has output"), produces: t("产生", "produces"), asserts: t("提出", "asserts"), supports: t("支持", "supports"), contradicts: t("反驳", "contradicts"), context: t("参考", "references"), records: t("记录", "records"), learns_from: t("关联记忆", "learns from") }[edge.relation] || edge.relation)} →</i><span className={`graph-node-type ${target?.type || ""}`}>{target?.type || "node"}</span><b title={target?.label}>{target?.label || edge.target}</b></div>; })}</div></section>}
            {activeTasks.length > 0 && <section className="research-stage-board"><div className="stage-board-heading"><b>{t(`第 ${activeTaskRound} 轮 · 研究阶段`, `Round ${activeTaskRound} · Research stages`)}</b><span>{activeTasks.filter((task) => task.status === "completed").length}/{activeTasks.length} {t("项完成", "complete")}</span></div><div className="stage-task-grid">{activeTasks.map((task) => <div className={`stage-task ${task.status}`} key={task.id}><span className="stage-task-dot" /><div><b>{task.agent_id === "retrieval" ? t("资料检索", "Source retrieval") : task.stage === "debate" ? t("交叉质疑", "Adversarial review") : agentName(task.agent_id)}</b><small>{({ queued: t("排队中", "Queued"), running: t("进行中", "Working"), completed: t("已完成", "Complete"), failed: t("失败", "Failed") }[task.status] || task.status)}{task.attempt > 1 ? ` · ${t(`尝试 ${task.attempt}`, `Attempt ${task.attempt}`)}` : ""}</small></div></div>)}</div></section>}
            {relatedMemory.length > 0 && <section className="research-memory-board"><div className="memory-board-heading"><div><b>{t("关联历史研究", "RELATED RESEARCH MEMORY")}</b><small>{t("历史模型结论仅作研究线索，引用来源需重新核查", "Past model conclusions are leads; re-check their sources")}</small></div><span>{relatedMemory.length}</span></div><div className="memory-items">{relatedMemory.map((memory) => <article className="memory-item" key={`${memory.id}-${memory.round_no}`}><div><b>{memory.title}</b><span>{Math.round((memory.relevance || 0) * 100)}% {t("主题相关", "topic overlap")}</span></div><small>{memory.question}</small><p>{memory.summary}</p>{memory.open_questions.length > 0 && <div className="memory-open-questions"><b>{t("待追问题", "Open questions")}</b>{memory.open_questions.slice(0, 3).map((item, index) => <span key={index}>· {item.text}</span>)}</div>}</article>)}</div></section>}
            {detail.summary && <div className="round-summary"><div><ExperimentOutlined /> {t("当前综合摘要", "CURRENT SYNTHESIS")} · {t(`第 ${detail.current_round} 轮`, `ROUND ${detail.current_round}`)}</div><ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>{detail.summary}</ReactMarkdown></div>}
            {detail.error && <div className="research-error">{detail.error}</div>}
            {claims.length > 0 && <section className="research-claim-board"><div className="claim-board-heading"><div><b>{t("结构化主张", "STRUCTURED CLAIMS")}</b><small>{t("只收录智能体按明确标签标注的条目", "Only statements explicitly tagged by agents are indexed")}</small></div><span>{claims.length}</span></div><div className="claim-items">{claims.map((claim) => <article className="claim-item" key={claim.id}><div className="claim-item-heading"><span className={`claim-kind ${claim.claim_type}`}>{({ finding: t("研究发现", "Finding"), hypothesis: t("假设", "Hypothesis"), prediction: t("预测", "Prediction"), counterevidence: t("反例 / 反证", "Counterevidence"), limitation: t("限制", "Limitation") }[claim.claim_type] || claim.claim_type)}</span><span className={`claim-epistemic ${claim.epistemic_status}`}>{({ external_source: t("外部来源", "External source"), theory_internal: t("内部理论", "Internal theory"), hypothesis: t("未验证假设", "Untested hypothesis"), model_inference: t("模型推断", "Model inference") }[claim.epistemic_status] || claim.epistemic_status)}</span><small>{agentName(claim.agent_id)} · {t(`第 ${claim.round_no} 轮`, `Round ${claim.round_no}`)}</small></div><p>{claim.claim_text}</p>{claim.evidence.length > 0 && <div className="claim-evidence-links">{claim.evidence.map((ref) => <span key={`${claim.id}-${ref.id}-${ref.relation}`} className={ref.relation}>{ref.citation_label} · {ref.relation === "contradicts" ? t("反驳", "contradicts") : t("支持", "supports")} · {ref.title}</span>)}</div>}</article>)}</div></section>}
            {evidence.length > 0 && <section className="research-evidence-board"><div className="evidence-board-heading"><div><b>{t("可追溯来源", "TRACEABLE SOURCES")}</b><small>{t("来源已关联到引用它的智能体输出；数据库记录不代表同行评审", "Sources link to agent outputs; bibliographic records are not proof of peer review")}</small></div><span>{evidence.length}</span></div><div className="evidence-items">{evidence.map((item) => <article className="evidence-item" key={item.id}><div className="evidence-item-heading"><span className={`evidence-kind ${item.source_type}`}>{({ theory_internal: t("理论内部资料", "Internal theory"), bibliographic_record: t("文献数据库记录", "Bibliographic record"), peer_reviewed: t("已核实同行评审", "Verified peer reviewed"), preprint: t("预印本", "Preprint"), web_reference: t("网页资料", "Web source"), hypothesis: t("假设", "Hypothesis"), model_inference: t("模型推断", "Model inference"), experiment_result: t("实验结果", "Experiment") }[item.source_type] || item.source_type)}</span><b>{item.title}</b><small>{item.provider} · {t(`第 ${item.round_no} 轮`, `Round ${item.round_no}`)}</small></div>{item.snippet && <p>{item.snippet}</p>}<div className="evidence-item-foot"><span>{t("引用角色", "Used by")}: {item.cited_by.map((agentId) => agentName(agentId)).join("、") || t("尚未关联输出", "Not linked to an output")}</span>{item.url.startsWith("http") && <a href={item.url} target="_blank" rel="noreferrer">{t("打开来源", "Open source")} <ArrowRightOutlined /></a>}</div></article>)}</div></section>}
            {rounds.length === 0 ? <div className="agent-wait"><Spin /> <span>{t("正在检索资料并启动研究智能体…", "Searching sources and starting the research agents…")}</span></div> : rounds.map((round) => <div className="output-round" key={round}><div className="round-heading"><span>{t(`第 ${round} 轮`, `ROUND ${round}`)}</span><i /><small>{outputs.filter((item) => item.round_no === round).length} {t("项输出", "outputs")}</small></div><div className="output-grid">{outputs.filter((item) => item.round_no === round).map((output) => <button className={`output-card ${output.agent_id === "synthesis" ? "synthesis-card" : ""}`} key={output.id} onClick={() => setSelectedOutput(output)}><div className="output-card-top"><span className={`status-dot ${output.status === "completed" ? "is-done" : "is-failed"}`} />{agentName(output.agent_id, output.agent_name)}<ArrowRightOutlined /></div><p>{output.content.slice(0, 205)}{output.content.length > 205 ? "…" : ""}</p><div className="output-card-meta"><span>{output.status === "completed" ? t("预览完整输出", "Preview full output") : t("运行失败 · 查看详情", "Failed · view details")}</span><span>{output.sources.length} {t("个来源", "sources")}</span></div></button>)}</div></div>)}
          </div>
        </>}
      </section>
    </div>

    <Modal className="research-create-modal" title={<div className="research-modal-title"><ExperimentOutlined />{t("发起一轮科学探索", "Start a science exploration")}</div>} open={createOpen} onCancel={() => setCreateOpen(false)} onOk={() => void createRun()} okText={t("启动多智能体探索", "Start multi-agent exploration")} cancelText={t("取消", "Cancel")} confirmLoading={creating} width={740} destroyOnClose>
      <div className="research-form"><label>{t("探索标题", "Exploration title")}</label><Input value={title} onChange={(event) => setTitle(event.target.value)} placeholder={t("例如：从简单底层原理到宇宙结构的涌现路径", "e.g. Emergence from simple foundations to cosmic structure")} maxLength={160} />
        <label>{t("核心研究问题", "Core research question")}</label><Input.TextArea value={question} onChange={(event) => setQuestion(event.target.value)} autoSize={{ minRows: 4, maxRows: 8 }} placeholder={t("例如：宇宙是否可能源于简单底层原理，并通过层层涌现形成已知现象？请比较现有理论、寻找共同结构和反证。", "Could the universe emerge from simple foundations through successive layers? Compare existing theories, look for shared structures, and seek counterevidence.")} />
        <div className="run-settings"><div className="round-setting"><label>{t("协作模式", "Workflow")}</label><Select value={workflowMode} onChange={setWorkflowMode} options={workflows.map((workflow) => ({ value: workflow.id, label: locale === "zh" ? workflow.name : workflow.name_en }))} /></div><div className="round-setting"><label>{t("研究深度", "Research depth")}</label><Select value={researchDepth} onChange={(value) => { setResearchDepth(value); if (value === "fast") setSelectedAgents((current) => current.slice(0, 3)); }} options={[{ value: "fast", label: t("快速 · 最多 3 位", "Fast · up to 3") }, { value: "normal", label: t("标准 · 最多 7 位", "Normal · up to 7") }, { value: "deep", label: t("深度 · 最多 15 位", "Deep · up to 15") }]} /></div></div>
        <div className="continuous-setting route-toggle"><div><b>{t("自动选择相关专家", "Auto-select relevant experts")}</b><small>{t("按问题领域、协作模式和计算预算路由", "Route by topic, workflow, and compute budget")}</small></div><Switch checked={autoRoute} onChange={setAutoRoute} /></div>
        {!autoRoute && <><div className="agent-selector-head"><div><label>{t("手动指定专家", "Expert overrides")}</label><small>{t("从 40 个学科角色中搜索；各角色共享本地模型，按预算限制运行数量", "Search 40 specialties; roles share the local model and are bounded by your compute budget")}</small></div><span>{selectedAgents.length}/{maxSelectableAgents}</span></div>
        <Input allowClear value={agentSearch} onChange={(event) => setAgentSearch(event.target.value)} placeholder={t("搜索领域、角色或关键词", "Search disciplines, roles, or keywords")} />
        <div className="agent-picker">{agents.filter((agent) => { const query = agentSearch.trim().toLocaleLowerCase(); return !query || `${agent.name} ${agent.name_en} ${agent.focus} ${agent.focus_en}`.toLocaleLowerCase().includes(query); }).map((agent) => { const checked = selectedAgents.includes(agent.id); return <button type="button" key={agent.id} className={`agent-option ${checked ? "selected" : ""}`} onClick={() => setSelectedAgents((current) => checked ? current.filter((id) => id !== agent.id) : current.length < maxSelectableAgents ? [...current, agent.id] : current)}><span className="agent-check">{checked ? "✓" : "+"}</span><b>{locale === "zh" ? agent.name : agent.name_en}</b><small>{locale === "zh" ? agent.focus : agent.focus_en}</small></button>; })}</div></>}
        <div className="run-settings"><div className="round-setting"><label>{t("迭代轮数", "Research rounds")}</label><InputNumber min={1} max={100} value={maxRounds} disabled={continuous} onChange={(value) => setMaxRounds(value)} /><span>{t("每轮都会保存检查点", "Checkpointed every round")}</span></div><div className="continuous-setting"><div><b>{t("持续运行", "Run continuously")}</b><small>{t("直到你手动暂停或停止", "Until you pause or stop it")}</small></div><Switch checked={continuous} onChange={setContinuous} /></div></div>
        <div className="research-disclaimer">{t("联网检索来源会附在每条输出中；本地知识库资料仅对当前登录账号开放。研究结果用于探索与讨论，不代表已证实的科学结论。", "Web sources are attached to each output; local library access is limited to this account. Results are exploratory and are not established scientific conclusions.")}</div>
      </div>
    </Modal>
    <Modal className="output-preview-modal" title={<div className="preview-heading"><ExperimentOutlined />{selectedOutput ? <>{agentName(selectedOutput.agent_id)} <span>· {t(`第 ${selectedOutput.round_no} 轮`, `Round ${selectedOutput.round_no}`)}</span></> : t("智能体输出", "Agent output")}</div>} open={Boolean(selectedOutput)} onCancel={() => setSelectedOutput(null)} footer={null} width={900} destroyOnClose>
      {selectedOutput && <div className="output-preview"><div className="output-preview-content"><ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>{selectedOutput.content}</ReactMarkdown></div><div className="source-list"><h3>{t("参考来源", "Sources")} <span>{selectedOutput.sources.length}</span></h3>{selectedOutput.sources.length === 0 ? <p>{t("本轮未检索到来源", "No sources were found in this round")}</p> : selectedOutput.sources.map((source, index) => <a key={`${source.url}-${index}`} href={source.url.startsWith("http") ? source.url : undefined} target="_blank" rel="noreferrer" className="source-row"><span className="source-index">{index + 1}</span><span><b>{source.title}</b><small>{source.provider}{source.snippet ? ` · ${source.snippet.slice(0, 260)}` : ""}</small></span>{source.url.startsWith("http") && <ArrowRightOutlined />}</a>)}</div></div>}
    </Modal>
  </div>;
}

export default function App() {
  const [locale, setLocale] = useState<Locale>(() => localStorage.getItem("chatai_locale") === "en" ? "en" : "zh");
  const [theme, setTheme] = useState<Theme>(() => localStorage.getItem("chatai_theme") === "dark" ? "dark" : "light");
  const [activeView, setActiveView] = useState<"chat" | "research">("chat");
  const [user, setUser] = useState<User | null>(null);
  const [authChecking, setAuthChecking] = useState(true);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [sessionId, setSessionId] = useState("");
  const [messages, setMessages] = useState<ChatItem[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [knowledgeOpen, setKnowledgeOpen] = useState(false);
  const [modelSettingsOpen, setModelSettingsOpen] = useState(false);
  const [modelSettings, setModelSettings] = useState<ModelSettings | null>(null);
  const [modelOptions, setModelOptions] = useState<{ local: string[]; remote: string[] }>({ local: [], remote: [] });
  const [modelApiKey, setModelApiKey] = useState("");
  const [clearModelApiKey, setClearModelApiKey] = useState(false);
  const [modelBusy, setModelBusy] = useState(false);
  const [knowledgeFiles, setKnowledgeFiles] = useState<KnowledgeFile[]>([]);
  const [previewFile, setPreviewFile] = useState<PreviewFile | null>(null);
  const [versionFile, setVersionFile] = useState<KnowledgeFile | null>(null);
  const [versions, setVersions] = useState<KnowledgeVersion[]>([]);
  const [versionDiff, setVersionDiff] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [bottomVisible, setBottomVisible] = useState(false);
  const [kbText, setKbText] = useState("");
  const [kbLoading, setKbLoading] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<any>(null);
  const t = useCallback((zh: string, en: string) => locale === "zh" ? zh : en, [locale]);

  useEffect(() => {
    document.documentElement.lang = locale === "zh" ? "zh-CN" : "en";
    document.documentElement.dataset.theme = theme;
    document.title = locale === "zh" ? "结构生力理论 · 研究空间" : "Structural Vital Force Theory · Research Space";
    const description = document.querySelector<HTMLMetaElement>('meta[name="description"]');
    if (description) description.content = locale === "zh" ? "结构生力理论研究空间 - 整理理论资料、研究对话与本地知识库" : "Structural Vital Force Theory research space for materials, conversations, and a local knowledge base";
    const themeColor = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
    if (themeColor) themeColor.content = theme === "dark" ? "#171915" : "#f7f7f3";
    localStorage.setItem("chatai_locale", locale);
    localStorage.setItem("chatai_theme", theme);
  }, [locale, theme]);

  useEffect(() => { axios.get<User>("/api/auth/me").then(({ data }) => setUser(data)).catch(() => setUser(null)).finally(() => setAuthChecking(false)); }, []);

  const refreshSessions = useCallback(async () => {
    const { data } = await axios.get<ChatSession[]>("/api/chat/sessions");
    setSessions(data);
    return data as ChatSession[];
  }, []);
  const refreshFiles = useCallback(async () => {
    const { data } = await axios.get<KnowledgeFile[]>("/api/rag/files");
    setKnowledgeFiles(data);
  }, []);
  const loadModelSettings = useCallback(async () => {
    const [{ data: settings }, { data: available }] = await Promise.all([axios.get<ModelSettings>("/api/settings/models"), axios.get("/api/settings/models/available")]);
    setModelSettings(settings); setModelOptions({ local: available.local || [], remote: available.remote || [] });
  }, []);
  const openModelSettings = async () => {
    setModelSettingsOpen(true); setModelBusy(true);
    try { await loadModelSettings(); } catch (e: any) { toast.error(apiErrorText(e, locale, t("读取模型设置失败", "Could not load model settings"))); }
    finally { setModelBusy(false); }
  };
  const saveModelSettings = async () => {
    if (!modelSettings || modelBusy) return;
    setModelBusy(true);
    try {
      const payload: Record<string, unknown> = { ...modelSettings, remote_api_key: modelApiKey || undefined, clear_api_key: clearModelApiKey };
      const { data } = await axios.put("/api/settings/models", payload);
      setModelSettings(data); setModelApiKey(""); setClearModelApiKey(false);
      toast.success(data.reindexed_chunks ? t(`全局设置已保存，并重建 ${data.reindexed_chunks} 个向量片段`, `Global settings saved; re-indexed ${data.reindexed_chunks} chunks`) : t("全局模型设置已保存", "Global model settings saved"));
      const { data: available } = await axios.get("/api/settings/models/available"); setModelOptions({ local: available.local || [], remote: available.remote || [] });
    } catch (e: any) { toast.error(apiErrorText(e, locale, t("保存模型设置失败", "Could not save model settings"))); }
    finally { setModelBusy(false); }
  };
  const testModelSettings = async () => {
    if (!modelSettings) return;
    setModelBusy(true);
    try { await axios.post("/api/settings/models/test", { ...modelSettings, remote_api_key: modelApiKey || undefined, clear_api_key: clearModelApiKey }); toast.success(t("模型服务连接正常", "Model service connection is healthy")); }
    catch (e: any) { toast.error(apiErrorText(e, locale, t("模型连接测试失败", "Model connection test failed"))); }
    finally { setModelBusy(false); }
  };
  useEffect(() => { if (user) void loadModelSettings().catch(() => {}); }, [user, loadModelSettings]);

  const createSession = useCallback(async (select = true) => {
    const id = newId();
    const title = "新对话";
    await axios.post("/api/chat/sessions", { session_id: id, title });
    setMessages([]);
    if (select) setSessionId(id);
    const all = await refreshSessions();
    if (select && !all.some((s) => s.session_id === id)) setSessions([{ session_id: id, title, updated_at: new Date().toISOString() }, ...all]);
    return id;
  }, [refreshSessions]);

  useEffect(() => {
    if (!user) return;
    let active = true;
    (async () => {
      try {
        const all = await refreshSessions();
        if (!active) return;
        if (all.length) setSessionId(all[0].session_id);
        else {
          const previousSession = localStorage.getItem("session_id");
          if (previousSession) {
            try {
              await axios.post("/api/chat/sessions", { session_id: previousSession, title: t("历史对话", "Previous chat") });
              localStorage.removeItem("session_id");
              setSessionId(previousSession);
              await refreshSessions();
            } catch {
              localStorage.removeItem("session_id");
              await createSession();
            }
          } else await createSession();
        }
        await refreshFiles();
      } catch { toast.error(t("加载数据失败，请刷新页面重试", "Could not load your data. Please refresh and try again.")); }
    })();
    return () => { active = false; };
  }, [user, refreshSessions, createSession, refreshFiles]);

  useEffect(() => {
    if (!user || !sessionId) return;
    let active = true;
    axios.get<ChatItem[]>(`/api/history/${sessionId}`).then(({ data }) => { if (active) setMessages(data); }).catch(() => { if (active) setMessages([]); });
    return () => { active = false; };
  }, [user, sessionId]);

  useEffect(() => { if (!bottomVisible) endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [messages, loading, bottomVisible]);
  useEffect(() => { if (knowledgeOpen) void refreshFiles(); }, [knowledgeOpen, refreshFiles]);

  const selectSession = (id: string) => { setSessionId(id); setSidebarOpen(false); };
  const onSend = async (text = input) => {
    const clean = text.trim();
    if (!clean || loading || !sessionId) return;
    setInput(""); setLoading(true); setBottomVisible(false);
    setMessages((m) => [...m, { role: "human", content: clean }, { role: "assistant", content: "" }]);
    const params = new URLSearchParams({ session_id: sessionId, message: clean });
    const es = new EventSource(`/api/chat/stream?${params.toString()}`);
    es.onmessage = (ev) => setMessages((m) => { const last = m[m.length - 1]; return last?.role === "assistant" ? [...m.slice(0, -1), { ...last, content: last.content + (ev.data || "") }] : m; });
    es.addEventListener("done", () => { setLoading(false); es.close(); void refreshSessions(); });
    es.addEventListener("error", (ev: any) => {
      setLoading(false); es.close();
      const detail = apiErrorText(ev, locale, t("连接中断，请确认本地模型和后端服务正常后重试。", "Connection interrupted. Check the local model and server, then try again."));
      toast.error(detail);
      setMessages((m) => { const last = m[m.length - 1]; return last?.role === "assistant" ? [...m.slice(0, -1), { ...last, content: last.content ? `${last.content}\n\n${detail}` : detail }] : m; });
    });
  };

  const logout = async () => { try { await axios.post("/api/auth/logout"); } catch {} setUser(null); setMessages([]); setSessions([]); setSessionId(""); };
  const clearCurrent = async () => {
    if (!sessionId) return;
    try { await axios.delete(`/api/chat/sessions/${sessionId}`); const all = await refreshSessions(); setMessages([]); if (all.length) setSessionId(all[0].session_id); else await createSession(); toast.success(t("对话已删除", "Chat deleted")); }
    catch { toast.error(t("删除对话失败", "Could not delete the chat")); }
  };
  const deleteKnowledgeFile = async (file: KnowledgeFile) => {
    try { await axios.delete(`/api/rag/files/${file.id}`); setKnowledgeFiles((current) => current.filter((item) => item.id !== file.id)); toast.success(t("文件已从知识库移除", "File removed from your knowledge base")); }
    catch (e: any) { toast.error(apiErrorText(e, locale, t("文件删除失败", "Could not delete the file"))); }
  };
  const previewKnowledgeFile = async (fileId: string) => {
    try { const { data } = await axios.get<PreviewFile>(`/api/rag/files/${fileId}`); setPreviewFile(data); }
    catch (e: any) { toast.error(apiErrorText(e, locale, t("Markdown 预览加载失败", "Could not load the Markdown preview"))); }
  };
  const showVersions = async (file: KnowledgeFile) => {
    try {
      const { data } = await axios.get<KnowledgeVersion[]>(`/api/rag/files/${file.id}/versions`);
      setVersionFile(file); setVersions(data); setVersionDiff("");
      if (data.length > 1) {
        const result = await axios.get(`/api/rag/files/${file.id}/compare`, { params: { from_version: data[1].version, to_version: data[0].version } });
        setVersionDiff(result.data.diff || t("两个版本没有文本差异", "No text differences between these versions"));
      }
    } catch (e: any) { toast.error(apiErrorText(e, locale, t("加载版本记录失败", "Could not load version history"))); }
  };
  const ingestText = async () => {
    const content = kbText.trim(); if (!content || kbLoading) return;
    setKbLoading(true);
    try { await axios.post("/api/rag/ingest", { content, metadata: { filename: `manual-note-${Date.now()}.md` } }); setKbText(""); await refreshFiles(); toast.success(t("内容已加入知识库", "Added to your knowledge base")); }
    catch (e: any) { toast.error(apiErrorText(e, locale, t("加入知识库失败", "Could not add this to your knowledge base"))); }
    finally { setKbLoading(false); }
  };

  if (authChecking) return <div className="auth-loading"><Spin size="large" /></div>;
  const themeConfig = {
    algorithm: theme === "dark" ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
    token: { colorPrimary: theme === "dark" ? "#98a782" : "#788564", borderRadius: 9 },
  };
  if (!user) return <ConfigProvider theme={themeConfig}><AuthScreen onAuth={setUser} locale={locale} setLocale={setLocale} theme={theme} setTheme={setTheme} /></ConfigProvider>;

  return <ConfigProvider theme={themeConfig}><div className="app-shell">
    {sidebarOpen && <button className="mobile-scrim" onClick={() => setSidebarOpen(false)} aria-label={t("关闭导航", "Close navigation")} />}
    <aside className={`sidebar ${sidebarOpen ? "sidebar-open" : ""}`}>
      <div className="side-brand"><span className="brand-mark"><BulbOutlined /></span><span>{t("结构生力理论", "Structural Vital Force Theory")} <small>RESEARCH SPACE</small></span><button className="mobile-close" onClick={() => setSidebarOpen(false)} aria-label={t("关闭导航", "Close navigation")}>×</button></div>
      <button className="new-chat-btn" onClick={() => void createSession()}><PlusOutlined /> {t("新建对话", "New chat")} <span>⌘ K</span></button>
      <div className="side-label">{t("工作空间", "WORKSPACE")}</div>
      <button className={`nav-item ${activeView === "chat" ? "selected" : ""}`} onClick={() => { setActiveView("chat"); setSidebarOpen(false); }}><MessageOutlined /><span>{t("普通对话", "Chat")}</span></button>
      <button className={`nav-item ${activeView === "research" ? "selected" : ""}`} onClick={() => { setActiveView("research"); setSidebarOpen(false); }}><ExperimentOutlined /><span>{t("科学探索", "Science exploration")}</span></button>
      <button className="nav-item" onClick={() => setKnowledgeOpen(true)}><FileMarkdownOutlined /><span>{t("理论资料库", "Theory library")}</span><b>{knowledgeFiles.length}</b></button>
      <button className="nav-item" onClick={() => void openModelSettings()}><SettingOutlined /><span>{t("全局模型设置", "Global model settings")}</span></button>
      <div className="history-head"><span>{t("最近对话", "RECENT CHATS")}</span><button title={t("新建对话", "New chat")} onClick={() => void createSession()}><PlusOutlined /></button></div>
      <div className="session-list">
        {sessions.length === 0 && <div className="empty-history">{t("还没有对话记录", "No conversations yet")}</div>}
        {sessions.map((s) => <button key={s.session_id} className={`session-item ${s.session_id === sessionId ? "active" : ""}`} onClick={() => selectSession(s.session_id)}><MessageOutlined /><span>{locale === "en" && s.title === "新对话" ? "New chat" : locale === "en" && s.title === "历史对话" ? "Previous chat" : s.title || t("新对话", "New chat")}</span></button>)}
      </div>
      <div className="sidebar-bottom">
        <div className="local-status"><span className="live-dot" /><span>{modelSettings?.provider === "remote" ? t("远端模型", "Remote model") : t("本地模型", "Local model")}</span><span className="local-tag">{modelSettings?.provider === "remote" ? "REMOTE" : "LOCAL"}</span></div>
        <div className="user-menu"><div className="avatar">{(user.name || "知").slice(0, 1).toUpperCase()}</div><div className="user-label"><b>{user.name}</b><span>{user.email}</span></div><button title={t("退出登录", "Sign out")} onClick={() => void logout()}><LogoutOutlined /></button></div>
      </div>
    </aside>
    <main className="main-panel">
      <header className="topbar">
        <button className="mobile-menu" onClick={() => setSidebarOpen(true)}><MenuOutlined /></button>
        <div className="breadcrumb"><span>{t("结构生力理论", "SVF Theory")}</span><span className="crumb-sep">/</span><b>{activeView === "research" ? t("科学探索", "Science exploration") : sessions.find((s) => s.session_id === sessionId)?.title || t("新对话", "New chat")}</b></div>
      <div className="top-actions">{activeView === "chat" ? <><span className="model-pill"><span className="live-dot" /> {modelSettings?.provider === "remote" ? `${modelSettings.remote_chat_model || "Remote"} · ${t("远端", "Remote")}` : `${modelSettings?.local_chat_model || "Ollama"} · ${t("本地", "Local")}`}</span><button className="top-icon" title={t("全局模型设置", "Global model settings")} onClick={() => void openModelSettings()}><SettingOutlined /></button><button className="top-icon" title={t("理论资料库", "Theory library")} onClick={() => setKnowledgeOpen(true)}><FileMarkdownOutlined /></button>{messages.length > 0 && <Popconfirm title={t("删除当前对话？", "Delete this chat?")} description={t("这条对话的历史记录会被清空。", "This chat history will be deleted.")} onConfirm={() => void clearCurrent()}><button className="top-icon" title={t("删除当前对话", "Delete chat")}><DeleteOutlined /></button></Popconfirm>}</> : <span className="research-model-pill"><ExperimentOutlined /> {t("多智能体任务独立运行", "Agents run independently")}</span>}<button className="appearance-button locale-button" onClick={() => setLocale(locale === "zh" ? "en" : "zh")} aria-label={t("切换为英文", "Switch to Chinese")}>{locale === "zh" ? "EN" : "中"}</button><button className="appearance-button" onClick={() => setTheme(theme === "light" ? "dark" : "light")} aria-label={t("切换主题", "Toggle theme")}>{theme === "light" ? <MoonOutlined /> : <SunOutlined />}</button><button className="top-icon logout-top" title={t("退出登录", "Sign out")} onClick={() => void logout()}><LogoutOutlined /></button></div>
      </header>
      {activeView === "research" ? <ScienceWorkspace locale={locale} t={t} /> : <>
      <section className="chat-scroll" onScroll={(e) => { const el = e.currentTarget; setBottomVisible(el.scrollHeight - el.scrollTop - el.clientHeight > 160); }}>
        <div className={`conversation ${messages.length ? "has-messages" : "welcome"}`}>
          {messages.length === 0 ? <div className="welcome-content">
            <div className="welcome-icon"><BulbOutlined /></div>
            <div className="welcome-eyebrow">{t(`${user.name || "你好"}，欢迎回来`, `Welcome back, ${user.name || "researcher"}`)}</div>
            <h1>{locale === "zh" ? <>一起研究<br /><span>结构生力理论。</span></> : <>Explore Structural<br /><span>Vital Force Theory.</span></>}</h1>
            <p>{t("围绕理论概念提出问题，整理研究思路，或从已上传的资料中查找依据。", "Ask about the theory, organize your research, or find supporting material in your library.")}</p>
            <div className="suggestions">
              {[t("梳理一个理论问题", "Help me frame a theory question"), t("总结已上传的理论资料", "Summarize my theory materials"), t("组织研究思路", "Help organize my research")].map((idea, i) => <button key={idea} onClick={() => void onSend(idea)}><span className={`suggestion-icon s${i}`}><>{[<BulbOutlined />, <FileMarkdownOutlined />, <ArrowRightOutlined />][i]}</></span><span>{idea}</span><ArrowRightOutlined /></button>)}
            </div>
            <div className="welcome-foot"><span><span className="live-dot" /> {t("本地 AI 随时为你服务", "Local AI is ready when you are")}</span><span>{t("试试", "Press")} <kbd>Ctrl</kbd> + <kbd>Enter</kbd> {t("发送", "to send")}</span></div>
          </div> : <div className="message-list">
            {messages.map((item, idx) => {
              const human = item.role === "human";
              const streaming = loading && idx === messages.length - 1 && !human;
              return <article className={`message-row ${human ? "user-row" : "assistant-row"}`} key={`${idx}-${human ? "u" : "a"}`}>
                {!human && <div className="message-avatar"><BulbOutlined /></div>}
                <div className="message-content-wrap">
                  {!human && <div className="message-author">{t("结构生力理论", "SVF Theory")} <span>{t("研究助手", "Research assistant")}</span></div>}
                  <div className={`message-bubble ${human ? "user-bubble" : "assistant-bubble"}`}>
                    {streaming && !item.content ? <span className="thinking"><i /><i /><i /> {t("正在思考", "Thinking")}</span> : <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>{item.content}</ReactMarkdown>}
                  </div>
                </div>
                {human && <div className="user-avatar">{user.name.slice(0, 1).toUpperCase()}</div>}
              </article>;
            })}
            <div ref={endRef} />
          </div>}
        </div>
      </section>
      {bottomVisible && <button className="scroll-bottom" onClick={() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); setBottomVisible(false); }}><ArrowDownOutlined /></button>}
      <footer className="composer-area">
        <div className="composer">
          <Input.TextArea ref={inputRef} value={input} onChange={(e) => setInput(e.target.value)} autoSize={{ minRows: 1, maxRows: 6 }} placeholder={t("输入关于结构生力理论的问题…", "Ask about Structural Vital Force Theory…")} onPressEnter={(e) => { if ((e.metaKey || e.ctrlKey) && !loading) { e.preventDefault(); void onSend(); } }} />
          <div className="composer-tools"><div><button title={t("管理知识库", "Manage knowledge base")} onClick={() => setKnowledgeOpen(true)}><PaperClipOutlined /> <span>{t("知识库", "Knowledge")}</span></button><span className="composer-hint">{t("AI 可能会犯错，请核实重要信息", "AI can make mistakes. Verify important information.")}</span></div><Button className="send-button" type="primary" icon={<SendOutlined />} disabled={!input.trim() || loading} loading={loading} onClick={() => void onSend()}>{t("发送", "Send")}</Button></div>
        </div>
      </footer>
      </>}
    </main>

    <Drawer title={<div className="drawer-title"><span className="drawer-icon"><SettingOutlined /></span><span>{t("全局模型设置", "Global model settings")}<small>{t("聊天、科研与知识库共用", "Shared by chat, research, and knowledge search")}</small></span></div>} placement="right" width={520} open={modelSettingsOpen} onClose={() => setModelSettingsOpen(false)} className="knowledge-drawer model-settings-drawer">
      {!modelSettings ? <div style={{ padding: 30, textAlign: "center" }}><Spin /></div> : <>
        <div className="knowledge-intro">{t("这些设置对整个网站和所有账号生效。向量模型更改后，会自动重建所有用户的知识库索引。", "These settings apply to the whole site and every account. Changing the embedding model rebuilds every user's knowledge index.")}</div>
        <Form layout="vertical" requiredMark={false}>
          <Form.Item label={t("聊天与科研模型来源", "Chat and research provider")}><Select value={modelSettings.provider} onChange={(provider: "local" | "remote") => setModelSettings({ ...modelSettings, provider })} options={[{ value: "local", label: t("本地 Ollama 模型", "Local Ollama models") }, { value: "remote", label: t("远端 OpenAI 兼容 API", "Remote OpenAI-compatible API") }]} /></Form.Item>
          {modelSettings.provider === "local" ? <>
            <Form.Item label={t("Ollama 地址", "Ollama URL")}><Input value={modelSettings.ollama_url} placeholder="http://127.0.0.1:11434" onChange={(e) => setModelSettings({ ...modelSettings, ollama_url: e.target.value })} /></Form.Item>
            <Form.Item label={t("聊天模型", "Chat model")}><Select showSearch value={modelSettings.local_chat_model} onChange={(local_chat_model) => setModelSettings({ ...modelSettings, local_chat_model })} options={[...new Set([...modelOptions.local, modelSettings.local_chat_model])].filter(Boolean).map((value) => ({ value, label: value }))} /></Form.Item>
            <Form.Item label={t("科研模型", "Research model")}><Select showSearch value={modelSettings.local_research_model} onChange={(local_research_model) => setModelSettings({ ...modelSettings, local_research_model })} options={[...new Set([...modelOptions.local, modelSettings.local_research_model])].filter(Boolean).map((value) => ({ value, label: value }))} /></Form.Item>
          </> : <>
            <Form.Item label={t("远端 API 地址", "Remote API base URL")}><Input value={modelSettings.remote_base_url} placeholder="https://api.example.com/v1" onChange={(e) => setModelSettings({ ...modelSettings, remote_base_url: e.target.value })} /></Form.Item>
            <Form.Item label={t("API 密钥", "API key")}><Input.Password value={modelApiKey} placeholder={modelSettings.api_key_configured ? t("已配置；留空以保留当前密钥", "Configured; leave blank to keep current key") : t("输入 API 密钥（可选）", "Enter API key (optional)")} onChange={(e) => { setModelApiKey(e.target.value); setClearModelApiKey(false); }} />{modelSettings.api_key_configured && <Button type="link" danger onClick={() => { setClearModelApiKey(true); setModelApiKey(""); }}>{clearModelApiKey ? t("保存时将清除密钥", "Key will be cleared on save") : t("清除已保存密钥", "Clear saved key")}</Button>}</Form.Item>
            <Form.Item label={t("聊天模型名称", "Chat model ID")}><Input value={modelSettings.remote_chat_model} placeholder={t("例如 gpt-4o-mini", "e.g. gpt-4o-mini")} onChange={(e) => setModelSettings({ ...modelSettings, remote_chat_model: e.target.value })} /></Form.Item>
            <Form.Item label={t("科研模型名称（可留空以共用聊天模型）", "Research model ID (optional; defaults to chat model)")}><Input value={modelSettings.remote_research_model} onChange={(e) => setModelSettings({ ...modelSettings, remote_research_model: e.target.value })} /></Form.Item>
            {modelOptions.remote.length > 0 && <div className="knowledge-intro">{t("服务端模型列表", "Models exposed by this service")}: {modelOptions.remote.join(", ")}</div>}
          </>}
          <Form.Item label={t("本地向量模型（Ollama）", "Local embedding model (Ollama)")}><Select showSearch value={modelSettings.embedding_model} onChange={(embedding_model) => setModelSettings({ ...modelSettings, embedding_model })} options={[...new Set([...modelOptions.local, modelSettings.embedding_model])].filter(Boolean).map((value) => ({ value, label: value }))} /></Form.Item>
          <div className="model-settings-note">{t("科研联网检索不变；选择远端后，聊天与科研智能体会使用远端服务。知识库向量始终由此处指定的本地 Ollama 模型生成。", "Web search remains unchanged. Remote mode routes chat and research agents to the remote service. Knowledge-base embeddings always use the selected local Ollama model.")}</div>
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 16 }}><Button loading={modelBusy} onClick={() => void testModelSettings()}>{t("测试连接", "Test connection")}</Button><Button type="primary" loading={modelBusy} onClick={() => void saveModelSettings()}>{t("保存全局设置", "Save global settings")}</Button></div>
        </Form>
      </>}
    </Drawer>
    <Drawer title={<div className="drawer-title"><span className="drawer-icon"><FileMarkdownOutlined /></span><span>{t("我的知识库", "My knowledge base")}<small>{t("你的本地资料，随时可检索", "Your local notes, ready to search")}</small></span></div>} placement="right" width={490} open={knowledgeOpen} onClose={() => setKnowledgeOpen(false)} className="knowledge-drawer">
      <div className="knowledge-intro">{t("上传结构生力理论相关的 Markdown 文档，整理为可检索的理论资料。文件与向量保存在本机。", "Upload Markdown source materials about Structural Vital Force Theory. Files and embeddings stay on this device.")}</div>
      <Upload.Dragger className="knowledge-uploader" name="file" accept=".md,text/markdown" multiple action="/api/rag/files" showUploadList beforeUpload={(file) => {
        if (!file.name.toLowerCase().endsWith(".md")) { toast.error(t("仅支持 .md 文件", "Only .md files are supported")); return Upload.LIST_IGNORE; }
        if (file.size > 2 * 1024 * 1024) { toast.error(t("每个文件不能超过 2 MB", "Each file must be 2 MB or smaller")); return Upload.LIST_IGNORE; }
        return true;
      }} onChange={({ file }) => { if (file.status === "done") { toast.success(t("已加入本地知识库", "Added to your local knowledge base")); void refreshFiles(); } else if (file.status === "error") toast.error(apiErrorText(file, locale, t("上传失败，请检查本地向量模型", "Upload failed. Check that the local embedding model is running."))); }}>
        <div className="upload-cloud"><UploadOutlined /></div><div className="upload-main">{t("拖放文件到这里，或", "Drop files here, or")} <b>{t("浏览文件", "browse files")}</b></div><div className="upload-sub">{t("仅支持 UTF-8 Markdown · 单个文件最大 2 MB", "UTF-8 Markdown only · Max 2 MB per file")}</div>
      </Upload.Dragger>
      <div className="manual-entry"><div><b>{t("添加理论资料", "Add theory material")}</b><span>{t("粘贴结构生力理论相关内容", "Paste content related to the theory")}</span></div><Input.TextArea value={kbText} onChange={(e) => setKbText(e.target.value)} placeholder={t("粘贴要整理或检索的理论内容…", "Paste theory content to organize or search…")} autoSize={{ minRows: 2, maxRows: 4 }} /><Button type="primary" loading={kbLoading} disabled={!kbText.trim()} onClick={() => void ingestText()}>{t("加入资料库", "Add to library")} <ArrowRightOutlined /></Button></div>
      <div className="files-heading"><span>{t("已收录的资料", "Your files")}</span><span>{t(`${knowledgeFiles.length} 个文件`, `${knowledgeFiles.length} files`)}</span></div>
      <div className="file-list">{knowledgeFiles.length === 0 ? <div className="empty-files"><FileMarkdownOutlined /><b>{t("资料库还是空的", "No materials yet")}</b><span>{t("上传理论相关 Markdown，开始建立资料库", "Upload theory-related Markdown to build your library")}</span></div> : knowledgeFiles.map((file) => <div className="file-card" key={file.id}><div className="file-type"><FileMarkdownOutlined /></div><div className="file-info"><b>{file.filename}</b><span>{t(`${file.chunks} 个检索片段`, `${file.chunks} chunks`)} · v{file.version || 1} · {new Date(file.created_at).toLocaleDateString(locale === "zh" ? "zh-CN" : "en-US")}</span></div><button title={t("版本历史", "Version history")} onClick={() => void showVersions(file)}><HistoryOutlined /></button><button title={t("预览", "Preview")} onClick={() => void previewKnowledgeFile(file.id)}><ArrowRightOutlined /></button><Popconfirm title={t(`删除 ${file.filename}？`, `Delete ${file.filename}?`)} description={t("相关向量片段也会被移除。", "Its searchable chunks will also be removed.")} onConfirm={() => void deleteKnowledgeFile(file)}><button className="file-delete" title={t("删除", "Delete")}><DeleteOutlined /></button></Popconfirm></div>)}</div>
      <div className="embedding-note"><span className="live-dot" /> {t(`由 ${modelSettings?.embedding_model || "本地向量模型"} 提供检索`, `Search powered by ${modelSettings?.embedding_model || "local embeddings"}`)}</div>
    </Drawer>
    <Modal title={<div className="preview-heading"><FileMarkdownOutlined /> {previewFile?.filename || t("Markdown 预览", "Markdown preview")}</div>} open={Boolean(previewFile)} onCancel={() => setPreviewFile(null)} footer={null} width={780} destroyOnClose>
      <div className="markdown-preview"><ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>{previewFile?.content || ""}</ReactMarkdown></div>
    </Modal>
    <Modal title={<div className="preview-heading"><HistoryOutlined /> {versionFile?.filename} · {t("版本历史", "Version history")}</div>} open={Boolean(versionFile)} onCancel={() => setVersionFile(null)} footer={null} width={780} destroyOnClose>
      <div style={{ display: "grid", gap: 10, marginBottom: 16 }}>{versions.map((v) => <div key={v.version} className="file-card"><div className="file-info"><b>v{v.version} {v.version === (versionFile?.version || 1) ? `· ${t("当前版本", "Current")}` : ""}</b><span>{new Date(v.created_at).toLocaleString(locale === "zh" ? "zh-CN" : "en-US")} · {v.characters} {t("字符", "characters")} · SHA-256 {v.content_hash.slice(0, 12)}</span></div><button title={t("查看此版本", "Preview this version")} onClick={async () => { try { const { data } = await axios.get(`/api/rag/files/${versionFile!.id}/versions/${v.version}`); setPreviewFile({ id: versionFile!.id, filename: `${versionFile!.filename} · v${v.version}`, content: data.content }); } catch { toast.error(t("版本读取失败", "Could not load this version")); } }}><ArrowRightOutlined /></button></div>)}</div>
      {versions.length > 1 && <><b>{t("最近版本差异", "Latest version changes")}</b><pre style={{ whiteSpace: "pre-wrap", overflow: "auto", maxHeight: 360, padding: 14, borderRadius: 8, background: "var(--surface-muted, #f5f5f2)" }}>{versionDiff}</pre></>}
    </Modal>
  </div></ConfigProvider>;
}

