# MinerU Adapter

一个将文档上传至 [MinerU](https://github.com/opendatalab/MinerU) 解析服务并返回结构化 Markdown 的 API 适配层。

## 功能特性

- **FastAPI 构建** — 高性能异步 API，支持大文件上传
- **图片 URL 重写** — 将解析后的图片路径替换为可访问的 CDN/代理地址
- **并发限流** — 避免同时写入过多文件导致系统文件句柄耗尽
- **页眉提取** — 可选提取文档页眉内容并追加至 Markdown 开头
- **跨域支持** — 开箱即用的 CORS 配置

## 快速开始

### 环境要求

- Python 3.12+
- Docker (可选)

### 安装

```bash
pip install -r requirements.txt
```

### 运行

```bash
python src/server.py
```

服务将在 `http://localhost:3333` 启动。

### 使用 Docker

```bash
# 构建镜像
docker build -t mineru-adapter:v1.0 .

# 运行容器
docker run -d -p 3333:3333 \
  -e MINERU_API_URL=http://your-mineru-api/parse \
  -e MINERU_BACKEND=vlm-auto-engine \
  -e PUBLIC_BASE_URL=http://localhost:3333/mineru_images \
  mineru-adapter:v1.0
```

### 使用 Docker Compose

```bash
cd deploy
docker-compose up -d
```

## API 文档

### 解析文档

```bash
curl -X POST http://localhost:3333/mineru_parse \
  -F "file=@/path/to/document.pdf"
```

**响应示例:**

```json
{
  "success": true,
  "markdown": "# 文档标题\n\n正文内容...",
  "pages": 12
}
```

### 健康检查

```bash
curl http://localhost:3333/health
```

## 配置项

| 环境变量                    | 默认值                                | 说明                |
| --------------------------- | ------------------------------------- | ------------------- |
| `MINERU_API_URL`            | `http://localhost:8080/parse`         | MinerU 解析服务地址 |
| `MINERU_BACKEND`            | `vlm-vllm-async-engine`               | 后端引擎类型        |
| `MINERU_ENABLE_PAGE_HEADER` | `false`                               | 是否提取页眉        |
| `PUBLIC_BASE_URL`           | `http://localhost:3333/mineru_images` | 图片基础 URL        |
| `PORT`                      | `3333`                                | 服务端口            |

## 目录结构

```
.
├── src/
│   └── server.py          # FastAPI 服务主文件
├── deploy/
│   └── docker-compose.yml # Docker Compose 部署配置
├── requirements.txt       # Python 依赖
├── Dockerfile             # Docker 镜像构建文件
└── README.md
```

## 图片目录映射 (Nginx)

如需通过 Nginx 反向代理访问图片，需在 Nginx 配置中添加:

```nginx
location /mineru_images/ {
    proxy_pass http://127.0.0.1:3333/mineru_images/;
}
```

## License

MIT
