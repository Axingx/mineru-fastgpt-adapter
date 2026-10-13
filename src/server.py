"""
MinerU 4.x Structured Adapter - Python
"""

import asyncio
import io
import json
import logging
import os
import re
import urllib.parse
import uuid
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

import aiofiles
import aiohttp
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger("mineru-adapter")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(_handler)

MINERU_API_URL = os.getenv("MINERU_API_URL", "http://localhost:8002/v1/file_parse")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:3333/mineru_images").rstrip("/")
MINERU_TIER = os.getenv("MINERU_TIER", "standard")
MINERU_ENABLE_PAGE_HEADER = os.getenv("MINERU_ENABLE_PAGE_HEADER", "false").lower() == "true"
MINERU_TIMEOUT = float(os.getenv("MINERU_TIMEOUT", "570"))
BASE_IMAGE_DIR = Path(__file__).parent / "temp_images"
PORT = int(os.getenv("PORT", "3333"))
MAX_FILE_BYTES = 200 * 1024 * 1024
MAX_RESULT_BYTES = 512 * 1024 * 1024
_WRITE_SEM = asyncio.Semaphore(10)

BASE_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
app = FastAPI(title="MinerU 4.x Structured Adapter")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/mineru_images", StaticFiles(directory=str(BASE_IMAGE_DIR)), name="mineru_images")


class MinerUResponseError(ValueError):
    """上游任务或结果不符合 MinerU 4.x 协议。"""


def sanitize_filename(filename: str) -> str:
    safe = re.sub(r'[.\/\\?%*:|"<>]', "_", filename)
    return safe or "document"


def read_result_zip(data: bytes) -> dict:
    """读取单文件结果，不直接解压不受信任的 ZIP 路径。"""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if sum(info.file_size for info in infos) > MAX_RESULT_BYTES:
                raise MinerUResponseError("MinerU result exceeds 512 MiB")
            for info in infos:
                path = PurePosixPath(info.filename)
                if path.is_absolute() or ".." in path.parts or "\\" in info.filename:
                    raise MinerUResponseError("Invalid path in MinerU result ZIP")
            middle_json = json.loads(archive.read("middle_json.json"))
            if middle_json.get("schema") != "docvortex.middle" or not isinstance(middle_json.get("pages"), list):
                raise MinerUResponseError("Invalid MinerU middle JSON")
            images = {
                info.filename[len("images/"):]: archive.read(info)
                for info in infos
                if info.filename.startswith("images/") and not info.is_dir()
            }
            return {
                "md_content": archive.read("markdown.md").decode("utf-8"),
                "images": images,
                "pages": len(middle_json["pages"]),
                "middle_json": middle_json,
            }
    except (zipfile.BadZipFile, KeyError, UnicodeError, json.JSONDecodeError, AttributeError) as e:
        raise MinerUResponseError("Invalid MinerU result ZIP") from e


def task_result_url(url: str) -> str:
    result = urllib.parse.urljoin(MINERU_API_URL, url)
    source = urllib.parse.urlsplit(MINERU_API_URL)
    target = urllib.parse.urlsplit(result)
    if (source.scheme, source.netloc) != (target.scheme, target.netloc):
        raise MinerUResponseError("MinerU returned a result URL on another origin")
    return result


async def task_error(resp: aiohttp.ClientResponse) -> str:
    """提取上游失败原因：任务失败时为 409 + 任务 JSON，其余错误为 detail。"""
    try:
        body = await resp.json(content_type=None)
    except (aiohttp.ContentTypeError, json.JSONDecodeError, UnicodeError):
        body = {}
    if isinstance(body, dict):
        for item in body.get("files") or []:
            if isinstance(item, dict) and isinstance(item.get("error"), dict) and item["error"].get("message"):
                return f"MinerU parse failed: {item['error']['message']}"
        if isinstance(body.get("error"), dict) and body["error"].get("message"):
            return f"MinerU API error {resp.status}: {body['error']['message']}"
        if body.get("detail"):
            return f"MinerU API error {resp.status}: {body['detail']}"
        if body.get("status"):
            return f"MinerU task {body['status']}"
    return f"MinerU API error {resp.status}: {resp.reason}"


async def read_task_result(resp: aiohttp.ClientResponse) -> dict | None:
    """200 返回解析结果；202 返回 None 表示仍在处理；其余视为失败。"""
    if resp.status == 202:
        return None
    if resp.status == 200 and resp.headers.get("X-MinerU-Task-Status", "completed") == "completed":
        return await asyncio.to_thread(read_result_zip, await resp.read())
    raise MinerUResponseError(await task_error(resp))


async def call_mineru_api(file_bytes: bytes, filename: str) -> dict:
    form_data = aiohttp.FormData()
    form_data.add_field("files", file_bytes, filename=filename)
    tier = "flash" if Path(filename).suffix.lower() in {".docx", ".pptx", ".xlsx"} else MINERU_TIER
    # 不传 page_range：服务端默认整份解析，且 Office 文件传入该参数会直接失败
    params = {"tier": tier, "ocr_mode": "auto", "response_format": "zip", "wait_timeout": "300"}
    timeout = aiohttp.ClientTimeout(total=MINERU_TIMEOUT, connect=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(MINERU_API_URL, data=form_data, params=params) as resp:
            result = await read_task_result(resp)
            if result is not None:
                return result
            pending = await resp.json(content_type=None)
        result_url = pending.get("result_url")
        if not result_url:
            raise MinerUResponseError("MinerU task response has no result_url")
        result_url = task_result_url(result_url)
        while True:
            await asyncio.sleep(1)
            async with session.get(result_url, params={"response_format": "zip"}) as resp:
                result = await read_task_result(resp)
                if result is not None:
                    return result


def extract_headers(content: dict) -> str:
    def text(value):
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "".join(text(item) for item in value)
        if isinstance(value, dict):
            return text(value.get("content", ""))
        return ""

    for page in content["middle_json"]["pages"]:
        if not isinstance(page, dict):
            continue
        for block in page.get("blocks") or []:
            if isinstance(block, dict) and block.get("type") == "header":
                header = text(block.get("content", "")).strip()
                if header:
                    return header
    return ""


async def save_image(image_bytes: bytes, filepath: Path) -> None:
    async with _WRITE_SEM:
        await asyncio.to_thread(filepath.parent.mkdir, parents=True, exist_ok=True)
        async with aiofiles.open(filepath, "wb") as f:
            await f.write(image_bytes)


async def process_single_file(task_id: str, original_filename: str, content: dict) -> tuple[str, int]:
    md_content = content["md_content"]
    safe_folder = sanitize_filename(Path(original_filename).stem)
    date_str = datetime.now().strftime("%Y-%m-%d")
    target_folder = BASE_IMAGE_DIR / date_str / task_id / safe_folder
    save_tasks = []
    for img_name, image_bytes in content["images"].items():
        target_url = (
            f"{PUBLIC_BASE_URL}/{date_str}/{task_id}/"
            f"{urllib.parse.quote(safe_folder, safe='')}/{urllib.parse.quote(img_name, safe='/')}"
        )
        save_tasks.append(save_image(image_bytes, target_folder / img_name))
        image_path = rf"\.?/?images/{re.escape(img_name)}"
        md_content = re.sub(
            rf"(!\[[^\]]*\]\()({image_path})(\))",
            lambda match: match[1] + target_url + match[3], md_content,
        )
        md_content = re.sub(
            rf"(<img\b[^>]*?\bsrc\s*=\s*)([\"'])({image_path})\2",
            lambda match: match[1] + match[2] + target_url + match[2], md_content, flags=re.IGNORECASE,
        )
    await asyncio.gather(*save_tasks)
    return md_content, content["pages"]


@app.post("/mineru_parse")
async def parse_document(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file uploaded")
    task_id = str(uuid.uuid4())
    logger.info(f"[{task_id}] Start processing: {file.filename}")
    try:
        async with asyncio.timeout(MINERU_TIMEOUT):
            if file.size is not None and file.size > MAX_FILE_BYTES:
                raise HTTPException(status_code=413, detail="File exceeds MinerU's 200 MiB limit")
            file_bytes = await file.read(MAX_FILE_BYTES + 1)
            if not file_bytes:
                raise HTTPException(status_code=400, detail="Empty file uploaded")
            if len(file_bytes) > MAX_FILE_BYTES:
                raise HTTPException(status_code=413, detail="File exceeds MinerU's 200 MiB limit")
            content = await call_mineru_api(file_bytes, file.filename)
            del file_bytes
            markdown, pages = await process_single_file(task_id, file.filename, content)
            first_header = extract_headers(content) if MINERU_ENABLE_PAGE_HEADER else ""
            final_markdown = ((first_header + "\n\n" if first_header else "") + markdown.strip()).strip()
        logger.info(f"[{task_id}] Success. pages={pages}")
        return JSONResponse({"success": True, "markdown": final_markdown, "pages": pages})
    except HTTPException:
        raise
    except TimeoutError:
        status_code, error = 504, "MinerU parsing timed out"
    except aiohttp.ClientResponseError as e:
        status_code, error = 502, f"MinerU API error {e.status}: {e.message}"
    except (aiohttp.ClientError, MinerUResponseError) as e:
        status_code, error = 502, str(e)
    except Exception as e:
        status_code, error = 500, str(e)
    logger.error(f"[{task_id}] {error}")
    return JSONResponse({"success": False, "markdown": "", "pages": 0, "error": error}, status_code=status_code)


@app.get("/health")
async def health_check():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=PORT, loop="uvloop", workers=4, timeout_keep_alive=650, access_log=False)
