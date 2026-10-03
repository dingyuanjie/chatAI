// React 浏览器端启动入口：挂载唯一的 App 根组件，并预加载 Ant Design 基础样式重置。
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "antd/dist/reset.css";

// StrictMode 在开发时帮助发现副作用问题；生产构建会按 React 正常行为运行。
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
