"""
MinerU Structured Adapter - Python
"""

import asyncio
import base64
import os
import re
import urllib.parse
import uuid
from datetime import datetime
from pathlib import Path

import aiofiles
import aiohttp
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

# ── 配置 ──────────────────────────────────────────────────────────────
MINERU_API_URL = os.getenv("MINERU_API_URL",  "http://localhost:8080/parse")
PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL", "http://localhost:3333/mineru_images")
MINERU_BACKEND = os.getenv("MINERU_BACKEND",  "vlm-vllm-async-engine")
BASE_IMAGE_DIR = Path(__file__).parent / "temp_images"
PORT = 3333

# 并发写入限制，防止瞬间开启大量文件句柄耗尽系统 Open Files Limit
_WRITE_SEM = asyncio.Semaphore(10)

BASE_IMAGE_DIR.mkdir(parents=True, exist_ok=True)

# ── App ───────────────────────────────────────────────────────────────
app = FastAPI(title="MinerU Structured Adapter")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount(
    "/mineru_images",
    StaticFiles(directory=str(BASE_IMAGE_DIR)),
    name="mineru_images",
)


# ── 工具函数 ──────────────────────────────────────────────────────────

def sanitize_filename(filename: str) -> str:
    """将文件名中路径/特殊字符替换为下划线"""
    safe = re.sub(r'[.\/\\?%*:|"<>]', "_", filename)
    return safe or "document"


def encode_uri_component(text: str) -> str:
    """URL 编码（等价于 JS 的 encodeURIComponent）"""
    return urllib.parse.quote(text, safe="")


async def save_base64_image(img_data: str, filepath: Path) -> None:
    """受 Semaphore 保护的异步 base64 图片写入"""
    async with _WRITE_SEM:
        if "," in img_data:
            img_data = img_data.split(",", 1)[1]
        image_bytes = base64.b64decode(img_data)
        async with aiofiles.open(filepath, "wb") as f:
            await f.write(image_bytes)


async def call_mineru_api(file_bytes: bytes, filename: str) -> dict:
    """调用 MinerU API，非 2xx 状态码直接抛异常"""
    form_data = aiohttp.FormData()
    form_data.add_field("files",         file_bytes, filename=filename)
    form_data.add_field("backend",       MINERU_BACKEND)
    form_data.add_field("return_images", "true")

    timeout = aiohttp.ClientTimeout(total=600)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(MINERU_API_URL, data=form_data) as resp:
            resp.raise_for_status()  # 非 2xx 立即抛 ClientResponseError
            return await resp.json()


# ── 核心处理 ──────────────────────────────────────────────────────────

async def process_single_file(
    task_id: str,
    original_filename: str,
    content: dict,
) -> tuple[str, int]:
    md_content: str = content.get("md_content", "")
    images: dict = content.get("images") or {}   # 防御 None
    pages:  int = content.get("pages", 1)

    safe_folder = sanitize_filename(Path(original_filename).stem)
    target_folder = BASE_IMAGE_DIR / task_id / safe_folder

    # mkdir 是同步调用，用 run_in_executor 避免阻塞事件循环
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None, lambda: target_folder.mkdir(parents=True, exist_ok=True)
    )

    if not images:
        return md_content, pages

    # ── 并行保存所有图片（受 Semaphore 限流）────────────────────────
    save_tasks: list = []
    # (re.escape(img_name), target_url)
    replacements: list[tuple[str, str]] = []

    for img_name, b64_data in images.items():
        img_path = target_folder / img_name
        target_url = (
            f"{PUBLIC_BASE_URL}/{task_id}"
            f"/{encode_uri_component(safe_folder)}"
            f"/{encode_uri_component(img_name)}"
        )
        save_tasks.append(save_base64_image(b64_data, img_path))
        replacements.append((re.escape(img_name), target_url))

    await asyncio.gather(*save_tasks)

    # ── 替换 Markdown 图片链接 ────────────────────────────────────────
    # 兼容三种路径格式：
    #   images/xxx.png
    #   ./images/xxx.png
    #   /images/xxx.png
    for escaped_name, target_url in replacements:
        md_content = re.sub(
            rf"!\[.*?\]\(\.?/?images/{escaped_name}\)",
            f"![image]({target_url})",
            md_content,
        )

    return md_content, pages


# ── 路由 ──────────────────────────────────────────────────────────────

@app.post("/mineru_parse")
async def parse_document(file: UploadFile = File(...)):
    """处理文档解析请求"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file uploaded")

    task_id = str(uuid.uuid4())
    print(f"[{task_id}] Start processing: {file.filename}")

    try:
        file_bytes = await file.read()

        results = await call_mineru_api(file_bytes, file.filename)
        del file_bytes  # 尽早释放，防止大文件 OOM

        results_data: dict = results.get("results") or {}
        if not results_data:
            raise ValueError("MinerU API returned no results")

        # 并行处理多文件结果
        processed = await asyncio.gather(*[
            process_single_file(task_id, fname, content)
            for fname, content in results_data.items()
        ])

        final_markdown = "\n\n".join(md for md, _ in processed).strip()
        total_pages = sum(p for _, p in processed)

        print(f"[{task_id}] Success. pages={total_pages}")
        return JSONResponse({
            "success":  True,
            "markdown": final_markdown,
            "pages":    total_pages,
        })

    except aiohttp.ClientResponseError as e:
        # MinerU 返回非 2xx
        print(f"[{task_id}] MinerU HTTP Error: {e.status} {e.message}")
        return JSONResponse(
            {"success": False, "markdown": "", "pages": 0,
             "error": f"MinerU API error {e.status}: {e.message}"},
            status_code=502,
        )
    except aiohttp.ClientError as e:
        # 网络连接错误
        print(f"[{task_id}] MinerU Connection Error: {e}")
        return JSONResponse(
            {"success": False, "markdown": "", "pages": 0,
             "error": f"MinerU connection error: {e}"},
            status_code=502,
        )
    except Exception as e:
        print(f"[{task_id}] Adapter Error: {e}")
        return JSONResponse(
            {"success": False, "markdown": "", "pages": 0, "error": str(e)},
            status_code=500,
        )


@app.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


# ── 入口 ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=PORT,
        loop="uvloop",
        workers=4,
        timeout_keep_alive=650,
    )
