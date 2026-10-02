import { useCallback, useEffect, useRef, useState } from "react";
import { Button, ConfigProvider, Drawer, Form, Input, Modal, Popconfirm, Spin, Upload, message as toast, theme as antdTheme } from "antd";
import {
  ArrowDownOutlined, ArrowRightOutlined, BulbOutlined, CheckOutlined, DeleteOutlined, FileMarkdownOutlined,
  LogoutOutlined, MenuOutlined, MessageOutlined, MoonOutlined, PaperClipOutlined, PlusOutlined, SunOutlined,
  SendOutlined, UploadOutlined, UserOutlined,
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
type KnowledgeFile = { id: string; filename: string; created_at: string; chunks: number };
type PreviewFile = { id: string; filename: string; content: string };
type Locale = "zh" | "en";
type Theme = "light" | "dark";

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
      <div className="brand"><span className="brand-mark"><BulbOutlined /></span><span>知伴 <small>LOCAL AI</small></span></div>
      <div className="story-copy">
        <div className="eyebrow"><span className="live-dot" /> {t("私有 · 本地 · 专属", "PRIVATE · LOCAL · YOURS")}</div>
        <h1>{locale === "zh" ? <>你的知识，<br /><span>值得被记住。</span></> : <>Your knowledge,<br /><span>remembered.</span></>}</h1>
        <p>{t("把想法与资料交给知伴。在一场自然的对话里，找到答案，也找到新的灵感。", "Bring your thoughts and notes to Zhiban. Find answers and fresh ideas in a natural conversation.")}</p>
        <div className="story-note"><div className="note-orb"><BulbOutlined /></div><div><b>{t("由本地 AI 驱动", "Powered by local AI")}</b><span>{t("对话与知识库都留在你的设备上", "Your chats and knowledge stay on this device")}</span></div><CheckOutlined /></div>
      </div>
      <div className="story-footer">{t("为思考留一处安静的空间", "A quiet space for thought")} <span>© 2026 {t("知伴", "Zhiban")}</span></div>
      <div className="glow glow-one" /><div className="glow glow-two" />
    </section>
    <section className="auth-panel">
      <div className="auth-card">
        <div className="auth-mobile-brand"><span className="brand-mark"><BulbOutlined /></span> {t("知伴", "Zhiban")}</div>
        <div className="auth-kicker">{registering ? t("开始一段新的旅程", "A NEW JOURNEY") : t("欢迎回到你的空间", "WELCOME BACK")}</div>
        <h2>{registering ? t("创建你的账号", "Create your account") : t("继续与你的 AI 对话", "Continue your conversations")}</h2>
        <p className="auth-subtitle">{registering ? t("只需一分钟，打造你的私人知识空间。", "Set up your private knowledge space in a minute.") : t("登录后继续探索你保存的想法与对话。", "Sign in to pick up where your ideas and chats left off.")}</p>
        <Form form={form} layout="vertical" requiredMark={false} onFinish={submit} className="auth-form">
          {registering && <Form.Item name="name" label={t("怎么称呼你", "Your name")} rules={[{ required: true, message: t("请输入你的名字", "Please enter your name") }]}><Input size="large" prefix={<UserOutlined />} placeholder={t("你的名字", "Name")} autoComplete="name" /></Form.Item>}
          <Form.Item name="email" label={t("邮箱地址", "Email address")} rules={[{ required: true, type: "email", message: t("请输入有效邮箱", "Enter a valid email address") }]}><Input size="large" prefix={<span className="field-at">@</span>} placeholder="you@example.com" autoComplete="email" /></Form.Item>
          <Form.Item name="password" label={t("密码", "Password")} rules={[{ required: true, min: 8, message: t("密码至少需要 8 个字符", "Password must be at least 8 characters") }]}><Input.Password size="large" placeholder={t("至少 8 个字符", "At least 8 characters")} autoComplete={registering ? "new-password" : "current-password"} /></Form.Item>
          <Button type="primary" htmlType="submit" loading={busy} size="large" block className="auth-submit">{registering ? t("创建账号", "Create account") : t("登录", "Sign in")}<ArrowRightOutlined /></Button>
        </Form>
        <div className="auth-switch">{registering ? t("已经有账号了？", "Already have an account?") : t("第一次来知伴？", "New to Zhiban?")}<button onClick={() => { setRegistering(!registering); form.resetFields(); }}>{registering ? t("直接登录", "Sign in instead") : t("创建新账号", "Create an account")}</button></div>
        <div className="privacy-line"><span><CheckOutlined /></span> {t("你的内容保存在本机，只有你可以访问", "Your content stays on this device, private to you")}</div>
      </div>
    </section>
  </main>;
}

export default function App() {
  const [locale, setLocale] = useState<Locale>(() => localStorage.getItem("chatai_locale") === "en" ? "en" : "zh");
  const [theme, setTheme] = useState<Theme>(() => localStorage.getItem("chatai_theme") === "dark" ? "dark" : "light");
  const [user, setUser] = useState<User | null>(null);
  const [authChecking, setAuthChecking] = useState(true);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [sessionId, setSessionId] = useState("");
  const [messages, setMessages] = useState<ChatItem[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [knowledgeOpen, setKnowledgeOpen] = useState(false);
  const [knowledgeFiles, setKnowledgeFiles] = useState<KnowledgeFile[]>([]);
  const [previewFile, setPreviewFile] = useState<PreviewFile | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [bottomVisible, setBottomVisible] = useState(false);
  const [kbText, setKbText] = useState("");
  const [kbLoading, setKbLoading] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<any>(null);
  const t = (zh: string, en: string) => locale === "zh" ? zh : en;

  useEffect(() => {
    document.documentElement.lang = locale === "zh" ? "zh-CN" : "en";
    document.documentElement.dataset.theme = theme;
    document.title = locale === "zh" ? "知伴 · 私人 AI 工作空间" : "Zhiban · Private AI Workspace";
    const description = document.querySelector<HTMLMetaElement>('meta[name="description"]');
    if (description) description.content = locale === "zh" ? "知伴 - 由本地 AI 驱动的私人对话与知识库" : "Zhiban - A private chat and knowledge base powered by local AI";
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
      <div className="side-brand"><span className="brand-mark"><BulbOutlined /></span><span>{t("知伴", "Zhiban")} <small>LOCAL AI</small></span><button className="mobile-close" onClick={() => setSidebarOpen(false)} aria-label={t("关闭导航", "Close navigation")}>×</button></div>
      <button className="new-chat-btn" onClick={() => void createSession()}><PlusOutlined /> {t("新建对话", "New chat")} <span>⌘ K</span></button>
      <div className="side-label">{t("工作空间", "WORKSPACE")}</div>
      <button className="nav-item" onClick={() => setKnowledgeOpen(true)}><FileMarkdownOutlined /><span>{t("我的知识库", "My knowledge")}</span><b>{knowledgeFiles.length}</b></button>
      <div className="history-head"><span>{t("最近对话", "RECENT CHATS")}</span><button title={t("新建对话", "New chat")} onClick={() => void createSession()}><PlusOutlined /></button></div>
      <div className="session-list">
        {sessions.length === 0 && <div className="empty-history">{t("还没有对话记录", "No conversations yet")}</div>}
        {sessions.map((s) => <button key={s.session_id} className={`session-item ${s.session_id === sessionId ? "active" : ""}`} onClick={() => selectSession(s.session_id)}><MessageOutlined /><span>{locale === "en" && s.title === "新对话" ? "New chat" : locale === "en" && s.title === "历史对话" ? "Previous chat" : s.title || t("新对话", "New chat")}</span></button>)}
      </div>
      <div className="sidebar-bottom">
        <div className="local-status"><span className="live-dot" /><span>{t("本地模型已连接", "Local model connected")}</span><span className="local-tag">LOCAL</span></div>
        <div className="user-menu"><div className="avatar">{(user.name || "知").slice(0, 1).toUpperCase()}</div><div className="user-label"><b>{user.name}</b><span>{user.email}</span></div><button title={t("退出登录", "Sign out")} onClick={() => void logout()}><LogoutOutlined /></button></div>
      </div>
    </aside>
    <main className="main-panel">
      <header className="topbar">
        <button className="mobile-menu" onClick={() => setSidebarOpen(true)}><MenuOutlined /></button>
        <div className="breadcrumb"><span>{t("工作空间", "Workspace")}</span><span className="crumb-sep">/</span><b>{sessions.find((s) => s.session_id === sessionId)?.title || t("新对话", "New chat")}</b></div>
      <div className="top-actions"><span className="model-pill"><span className="live-dot" /> Qwen3 · {t("本地运行", "Local")}</span><button className="top-icon" title={t("知识库", "Knowledge base")} onClick={() => setKnowledgeOpen(true)}><FileMarkdownOutlined /></button>{messages.length > 0 && <Popconfirm title={t("删除当前对话？", "Delete this chat?")} description={t("这条对话的历史记录会被清空。", "This chat history will be deleted.")} onConfirm={() => void clearCurrent()}><button className="top-icon" title={t("删除当前对话", "Delete chat")}><DeleteOutlined /></button></Popconfirm>}<button className="appearance-button locale-button" onClick={() => setLocale(locale === "zh" ? "en" : "zh")} aria-label={t("切换为英文", "Switch to Chinese")}>{locale === "zh" ? "EN" : "中"}</button><button className="appearance-button" onClick={() => setTheme(theme === "light" ? "dark" : "light")} aria-label={t("切换主题", "Toggle theme")}>{theme === "light" ? <MoonOutlined /> : <SunOutlined />}</button><button className="top-icon logout-top" title={t("退出登录", "Sign out")} onClick={() => void logout()}><LogoutOutlined /></button></div>
      </header>
      <section className="chat-scroll" onScroll={(e) => { const el = e.currentTarget; setBottomVisible(el.scrollHeight - el.scrollTop - el.clientHeight > 160); }}>
        <div className={`conversation ${messages.length ? "has-messages" : "welcome"}`}>
          {messages.length === 0 ? <div className="welcome-content">
            <div className="welcome-icon"><BulbOutlined /></div>
            <div className="welcome-eyebrow">{t(`你好，${user.name || "朋友"}`, `Hello, ${user.name || "there"}`)}</div>
            <h1>{locale === "zh" ? <>今天想一起<br /><span>探索些什么？</span></> : <>What shall we<br /><span>explore today?</span></>}</h1>
            <p>{t("问一个问题，整理一个想法，或从你的知识库中寻找答案。", "Ask a question, shape an idea, or find something in your knowledge base.")}</p>
            <div className="suggestions">
              {[t("帮我梳理一个新想法", "Help me shape a new idea"), t("总结我上传的知识", "Summarize my uploaded notes"), t("给我一些灵感和建议", "Give me some inspiration")].map((idea, i) => <button key={idea} onClick={() => void onSend(idea)}><span className={`suggestion-icon s${i}`}><>{[<BulbOutlined />, <FileMarkdownOutlined />, <ArrowRightOutlined />][i]}</></span><span>{idea}</span><ArrowRightOutlined /></button>)}
            </div>
            <div className="welcome-foot"><span><span className="live-dot" /> {t("本地 AI 随时为你服务", "Local AI is ready when you are")}</span><span>{t("试试", "Press")} <kbd>Ctrl</kbd> + <kbd>Enter</kbd> {t("发送", "to send")}</span></div>
          </div> : <div className="message-list">
            {messages.map((item, idx) => {
              const human = item.role === "human";
              const streaming = loading && idx === messages.length - 1 && !human;
              return <article className={`message-row ${human ? "user-row" : "assistant-row"}`} key={`${idx}-${human ? "u" : "a"}`}>
                {!human && <div className="message-avatar"><BulbOutlined /></div>}
                <div className="message-content-wrap">
                  {!human && <div className="message-author">知伴 <span>{t("AI 助手", "AI assistant")}</span></div>}
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
          <Input.TextArea ref={inputRef} value={input} onChange={(e) => setInput(e.target.value)} autoSize={{ minRows: 1, maxRows: 6 }} placeholder={t("给知伴发送消息…", "Message your AI…")} onPressEnter={(e) => { if ((e.metaKey || e.ctrlKey) && !loading) { e.preventDefault(); void onSend(); } }} />
          <div className="composer-tools"><div><button title={t("管理知识库", "Manage knowledge base")} onClick={() => setKnowledgeOpen(true)}><PaperClipOutlined /> <span>{t("知识库", "Knowledge")}</span></button><span className="composer-hint">{t("AI 可能会犯错，请核实重要信息", "AI can make mistakes. Verify important information.")}</span></div><Button className="send-button" type="primary" icon={<SendOutlined />} disabled={!input.trim() || loading} loading={loading} onClick={() => void onSend()}>{t("发送", "Send")}</Button></div>
        </div>
      </footer>
    </main>

    <Drawer title={<div className="drawer-title"><span className="drawer-icon"><FileMarkdownOutlined /></span><span>{t("我的知识库", "My knowledge base")}<small>{t("你的本地资料，随时可检索", "Your local notes, ready to search")}</small></span></div>} placement="right" width={490} open={knowledgeOpen} onClose={() => setKnowledgeOpen(false)} className="knowledge-drawer">
      <div className="knowledge-intro">{t("上传 Markdown 文档，知伴会将内容整理成可检索的知识片段。文件与向量保存在本机。", "Upload Markdown files to make them searchable. Files and embeddings stay on this device.")}</div>
      <Upload.Dragger className="knowledge-uploader" name="file" accept=".md,text/markdown" multiple action="/api/rag/files" showUploadList beforeUpload={(file) => {
        if (!file.name.toLowerCase().endsWith(".md")) { toast.error(t("仅支持 .md 文件", "Only .md files are supported")); return Upload.LIST_IGNORE; }
        if (file.size > 2 * 1024 * 1024) { toast.error(t("每个文件不能超过 2 MB", "Each file must be 2 MB or smaller")); return Upload.LIST_IGNORE; }
        return true;
      }} onChange={({ file }) => { if (file.status === "done") { toast.success(t("已加入本地知识库", "Added to your local knowledge base")); void refreshFiles(); } else if (file.status === "error") toast.error(apiErrorText(file, locale, t("上传失败，请检查本地向量模型", "Upload failed. Check that the local embedding model is running."))); }}>
        <div className="upload-cloud"><UploadOutlined /></div><div className="upload-main">{t("拖放文件到这里，或", "Drop files here, or")} <b>{t("浏览文件", "browse files")}</b></div><div className="upload-sub">{t("仅支持 UTF-8 Markdown · 单个文件最大 2 MB", "UTF-8 Markdown only · Max 2 MB per file")}</div>
      </Upload.Dragger>
      <div className="manual-entry"><div><b>{t("快速添加笔记", "Quick note")}</b><span>{t("直接粘贴文字到知识库", "Paste text into your knowledge base")}</span></div><Input.TextArea value={kbText} onChange={(e) => setKbText(e.target.value)} placeholder={t("输入一段想保存的内容…", "Write something you want to remember…")} autoSize={{ minRows: 2, maxRows: 4 }} /><Button type="primary" loading={kbLoading} disabled={!kbText.trim()} onClick={() => void ingestText()}>{t("加入知识库", "Add to knowledge base")} <ArrowRightOutlined /></Button></div>
      <div className="files-heading"><span>{t("已收录的资料", "Your files")}</span><span>{t(`${knowledgeFiles.length} 个文件`, `${knowledgeFiles.length} files`)}</span></div>
      <div className="file-list">{knowledgeFiles.length === 0 ? <div className="empty-files"><FileMarkdownOutlined /><b>{t("这里还没有资料", "No files yet")}</b><span>{t("上传一份 Markdown，开始构建你的知识库", "Upload a Markdown file to start building your knowledge base")}</span></div> : knowledgeFiles.map((file) => <div className="file-card" key={file.id}><div className="file-type"><FileMarkdownOutlined /></div><div className="file-info"><b>{file.filename}</b><span>{t(`${file.chunks} 个检索片段`, `${file.chunks} chunks`)} · {new Date(file.created_at).toLocaleDateString(locale === "zh" ? "zh-CN" : "en-US")}</span></div><button title={t("预览", "Preview")} onClick={() => void previewKnowledgeFile(file.id)}><ArrowRightOutlined /></button><Popconfirm title={t(`删除 ${file.filename}？`, `Delete ${file.filename}?`)} description={t("相关向量片段也会被移除。", "Its searchable chunks will also be removed.")} onConfirm={() => void deleteKnowledgeFile(file)}><button className="file-delete" title={t("删除", "Delete")}><DeleteOutlined /></button></Popconfirm></div>)}</div>
      <div className="embedding-note"><span className="live-dot" /> {t("由 Qwen3 Embedding 本地向量模型提供检索", "Search powered by local Qwen3 Embedding")}</div>
    </Drawer>
    <Modal title={<div className="preview-heading"><FileMarkdownOutlined /> {previewFile?.filename || t("Markdown 预览", "Markdown preview")}</div>} open={Boolean(previewFile)} onCancel={() => setPreviewFile(null)} footer={null} width={780} destroyOnClose>
      <div className="markdown-preview"><ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>{previewFile?.content || ""}</ReactMarkdown></div>
    </Modal>
  </div></ConfigProvider>;
}

