# MinerU 4.x Adapter

本分支对接 MinerU 4.x，已按 4.0.12 协议实现。使用 MinerU 3.x 的客户继续使用 `main` 分支及对应镜像。

适配器部署在客户侧：接收文件，调用 MinerU，保存结果中的图片，并将 Markdown 中的图片链接改成客户侧可访问的地址，供 FastGPT 使用。适配器本身不需要 GPU，也不安装 MinerU。

## 对外接口

```bash
curl -X POST http://localhost:3333/mineru_parse -F "file=@document.pdf"
curl http://localhost:3333/health
```

成功响应保持原格式：

```json
{"success": true, "markdown": "# 正文\n\n![image](https://customer.example/mineru_images/...)", "pages": 2}
```

图片通过 `/mineru_images/` 提供。图片目录使用日期、任务 ID、文件名分组，已有目录和链接不需要改名。`/health` 只检查适配器存活，不能证明上游或图片公网访问正常。

## 客户侧部署

Python 3.12+，或使用 Docker：

```bash
cp .env.example .env
# 修改 MINERU_API_URL 和 PUBLIC_BASE_URL 为实际地址
# 绑定目录必须允许容器用户 1000:1000 写入
mkdir -p deploy/mineru_temp_images
sudo chown 1000:1000 deploy/mineru_temp_images
docker compose -f deploy/docker-compose.yml up -d --build
```

已有客户图片目录应继续使用原挂载，不能用空目录替换。直接运行 `python src/server.py` 时环境变量需要由 shell 注入；该入口不会自动加载 `.env`。

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MINERU_API_URL` | `http://localhost:8002/v1/file_parse` | MinerU 4.x 同步解析入口，含路径 |
| `MINERU_TIER` | `standard` | PDF/图片档位：flash/basic/standard/advanced |
| `MINERU_TIMEOUT` | `570` | 上传读取、上游等待、轮询及图片写入的总预算，秒 |
| `PUBLIC_BASE_URL` | `http://localhost:3333/mineru_images` | FastGPT 能访问的客户侧图片地址 |
| `MINERU_ENABLE_PAGE_HEADER` | `false` | 将新版结构中的首个页眉追加到正文前 |
| `PORT` | `3333` | 适配器监听端口 |

DOCX/PPTX/XLSX 使用原生 `flash` 解析。单文件上限为 200 MiB，结果 ZIP 解包内容上限为 512 MiB。请求过大返回 413，上游故障返回 502，总预算耗尽返回 504。调用方和代理的超时应大于 `MINERU_TIMEOUT`。

## MinerU Linux 部署

`deploy/mineru/` 是独立的 NVIDIA 服务配置，与客户侧适配器分开部署。Dockerfile 固定安装 MinerU 4.0.12，使用 vLLM 0.21.0/CUDA 13 基础镜像，构建时下载并检查 Torch 小模型和 VLM 权重。

```bash
docker compose -f deploy/mineru/compose.yml build
# 模型预加载占用 GPU；切换前停止旧模型服务，释放 8002 端口
docker compose -f deploy/mineru/compose.yml up -d
curl http://localhost:8002/v1/health
```

FRP 仍将公网 8002 转发到 Linux 8002，腾讯云 Nginx 原来的根路径代理可以继续使用。原生 MinerU 接口为 `/v1/file_parse`，不再提供旧 `/file_parse` 协议；新版适配器对外仍是 `/mineru_parse`。

## 新版结果处理

上传只携带 `files`，解析参数通过查询串传入。请求 ZIP 后读取 `markdown.md`、`middle_json.json` 和 `images/`；同步等待返回 202 时继续查询同源任务结果。Markdown 图片和 HTML `<img src>` 都会改写，页数使用实际结构中的页面数量。Office 页数是解析结果的逻辑页数，不保证与打印排版页数一致。

上游文件和任务默认有保留期限；适配器在当前请求内下载结果并保存图片。服务重启不保证旧上游任务 ID 可恢复。

## 验证

```bash
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

本地测试使用真实 HTTP mock 和 ZIP fixture，覆盖异步等待、失败、超时、Office 档位、页眉及图片访问。真实验收还需覆盖原生 PDF、扫描 PDF、含表格图片 PDF 和 DOCX，并在 FastGPT 中确认图片可见。
