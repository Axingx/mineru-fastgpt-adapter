# MinerU 4.x Adapter

FastGPT 与 MinerU 4.x（4.0.12）之间的适配器：接收文件，调用 MinerU 解析，把结果中的图片保存到本地并改写 Markdown 图片链接为可访问地址。MinerU 3.x 请使用 `main` 分支。

## 接口

```bash
curl -X POST http://localhost:3333/mineru_parse -F "file=@document.pdf"
```

```json
{"success": true, "markdown": "# 正文\n\n![](http://.../mineru_images/...)", "pages": 2}
```

失败时 `success` 为 `false`，`error` 为原因。健康检查：`GET /health`。

## 部署适配器

```bash
cp .env.example .env   # 修改 MINERU_API_URL、PUBLIC_BASE_URL
mkdir -p deploy/mineru_temp_images && sudo chown 1000:1000 deploy/mineru_temp_images
docker compose -f deploy/docker-compose.yml up -d --build
```

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MINERU_API_URL` | `http://localhost:8002/v1/file_parse` | MinerU 解析接口地址 |
| `PUBLIC_BASE_URL` | `http://localhost:3333/mineru_images` | FastGPT 可访问的图片地址前缀 |
| `MINERU_TIER` | `standard` | PDF/图片解析档位：flash/basic/standard/advanced；Office 文件固定 flash |
| `MINERU_TIMEOUT` | `570` | 单次解析总超时（秒），调用方超时需大于此值 |
| `MINERU_ENABLE_PAGE_HEADER` | `false` | 是否将首个页眉加到正文开头 |
| `PORT` | `3333` | 监听端口 |

## 部署 MinerU（GPU 服务器）

`deploy/mineru/` 构建 MinerU 4.0.12 镜像（含模型），以 router 方式运行在 8002 端口。

```bash
docker compose -f deploy/mineru/compose.yml build
docker compose -f deploy/mineru/compose.yml up -d
curl http://localhost:8002/v1/health
```

替换旧版本时需先删除同名旧容器：`docker rm -f mineru-router`。

## 测试

需要 Python 3.12（`uvloop` 暂不支持 3.13）。

```bash
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```
