import { useEffect, useMemo, useState } from "react";
import { Layout, Typography, Input, Button, List, Space, Segmented, message as antdMessage } from "antd";
import { DeleteOutlined, SendOutlined } from "@ant-design/icons";
import axios from "axios";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";

type ChatItem = {
  role: "human" | "ai" | "assistant" | "system";
  content: string;
};

function genSessionId() {
  const saved = localStorage.getItem("session_id");
  if (saved) return saved;
  const id = crypto.randomUUID();
  localStorage.setItem("session_id", id);
  return id;
}

export default function App() {
  const [sessionId, setSessionId] = useState<string>(() => genSessionId());
  const [messages, setMessages] = useState<ChatItem[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [kbText, setKbText] = useState("");
  const [kbLoading, setKbLoading] = useState(false);

  const normalizeMdPreview = (text: string, inStreaming: boolean) => {
    if (!inStreaming) return text;
    let t = text || "";
    if (!t.endsWith("\n")) t += "\n";
    const fenceCount = (t.match(/```/g) || []).length;
    if (fenceCount % 2 === 1) t += "```\n";
    return t;
  };

  useEffect(() => {
    (async () => {
      try {
        const { data } = await axios.get<ChatItem[]>(`/api/history/${sessionId}`);
        setMessages(data);
      } catch {
        setMessages([]);
      }
    })();
  }, [sessionId]);

  const onSend = async () => {
    if (!input.trim()) return;
    const userMsg: ChatItem = { role: "human", content: input };
    setMessages((m) => [...m, userMsg]);
    setInput("");
    setLoading(true);
    const assistantPlaceholder: ChatItem = { role: "assistant", content: "" };
    setMessages((m) => [...m, assistantPlaceholder]);
    const params = new URLSearchParams({ session_id: sessionId, message: userMsg.content });
    const es = new EventSource(`/api/chat/stream?${params.toString()}`);
    es.onmessage = (ev) => {
      const chunk = ev.data || "";
      setMessages((m) => {
        const last = m[m.length - 1];
        if (!last || last.role !== "assistant") return m;
        const updated = { ...last, content: last.content + chunk };
        return [...m.slice(0, -1), updated];
      });
    };
    es.addEventListener("done", () => {
      setLoading(false);
      es.close();
    });
    es.addEventListener("error", (ev: any) => {
      setLoading(false);
      es.close();
      antdMessage.error("流式连接错误");
    });
  };

  const clearMemory = async () => {
    try {
      await axios.delete(`/api/history/${sessionId}`);
      setMessages([]);
      antdMessage.success("已清空记忆");
    } catch {
      antdMessage.error("清空失败");
    }
  };

  const newSession = () => {
    const id = crypto.randomUUID();
    localStorage.setItem("session_id", id);
    setSessionId(id);
  };

  return (
    <Layout style={{ minHeight: "100vh", background: "#fff" }}>
      <Layout.Header style={{ background: "#fff", padding: "0 24px", borderBottom: "1px solid #f0f0f0" }}>
        <Space style={{ width: "100%", justifyContent: "space-between" }}>
          <Typography.Title level={4} style={{ margin: 0 }}>
            带记忆的聊天机器人
          </Typography.Title>
          <Space>
            <Button icon={<DeleteOutlined />} onClick={clearMemory} danger>
              清空记忆
            </Button>
            <Button onClick={newSession}>新会话</Button>
          </Space>
        </Space>
      </Layout.Header>
      <Layout.Content style={{ padding: 24 }}>
        <Space style={{ marginBottom: 16 }}>
          <Input.TextArea
            value={kbText}
            onChange={(e) => setKbText(e.target.value)}
            autoSize={{ minRows: 2, maxRows: 4 }}
            placeholder="输入知识库文本（提交后用于检索增强）"
            style={{ width: 600 }}
          />
          <Button
            loading={kbLoading}
            onClick={async () => {
              const text = kbText.trim();
              if (!text) return;
              setKbLoading(true);
              try {
                await axios.post("/api/rag/ingest", { content: text, metadata: { source: "manual" } });
                setKbText("");
                antdMessage.success("已加入知识库");
              } catch {
                antdMessage.error("加入知识库失败");
              } finally {
                setKbLoading(false);
              }
            }}
          >
            加入知识库
          </Button>
        </Space>
        <List
          bordered
          dataSource={messages}
          renderItem={(item, idx) => {
            const isStreamingAssistant = loading && idx === messages.length - 1 && item.role === "assistant";
            const mdText = normalizeMdPreview(item.content, isStreamingAssistant);
            return (
            <List.Item>
              <Space direction="vertical" style={{ width: "100%" }}>
                <Typography.Text strong>
                  {item.role === "human" ? "我" : item.role === "assistant" ? "助手" : item.role}
                </Typography.Text>
                <div style={{ marginBottom: 0 }}>
                  <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
                    {mdText}
                  </ReactMarkdown>
                </div>
              </Space>
            </List.Item>
            );
          }}
          style={{ marginBottom: 16 }}
        />
        <Space.Compact style={{ width: "100%" }}>
          <Input.TextArea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            autoSize={{ minRows: 2, maxRows: 6 }}
            placeholder="请输入消息..."
            onPressEnter={(e) => {
              if (!e.shiftKey) {
                e.preventDefault();
                onSend();
              }
            }}
          />
          <Button type="primary" icon={<SendOutlined />} loading={loading} onClick={onSend}>
            发送
          </Button>
        </Space.Compact>
      </Layout.Content>
    </Layout>
  );
}
