import asyncio
import base64
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from aiohttp import web
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import server

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aXioAAAAASUVORK5CYII=")


def result_zip(extra=None):
    import zipfile
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("markdown.md", '# Test\n![alt](images/a.png)\n<img alt="table" src="./images/nested/b.png">')
        archive.writestr("middle_json.json", json.dumps({
            "schema": "docvortex.middle", "schema_version": "2.0",
            "pages": [
                {"page_idx": 0, "blocks": [
                    {"type": "header", "index": 0, "content": [{"type": "text", "content": "Page header"}]},
                    {"type": "page_number", "index": 1, "content": [{"type": "text", "content": "1"}]},
                ]},
                {"page_idx": 1, "blocks": []},
            ],
        }))
        # 真实 4.0.12 的 model_output 每页是块数组，而不是 {"blocks": [...]}
        archive.writestr("model_output.json", json.dumps({"schema": "docvortex.model", "pages": [[
            {"type": "header", "content": [{"type": "text", "content": "Model header"}]}
        ], []]}))
        archive.writestr("images/a.png", PNG)
        archive.writestr("images/nested/b.png", PNG)
        for name, value in (extra or {}).items():
            archive.writestr(name, value)
    return data.getvalue()


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(server, "BASE_IMAGE_DIR", Path(self.tmp.name)),
                        patch.object(server, "PUBLIC_BASE_URL", "http://adapter/mineru_images")]
        for p in self.patches:
            p.start()
        self.mount = next(route for route in server.app.routes if route.path == "/mineru_images")
        self.old_static = self.mount.app
        self.mount.app = StaticFiles(directory=self.tmp.name)
        self.runner = None
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://adapter")

    async def asyncTearDown(self):
        await self.client.aclose()
        if self.runner:
            await self.runner.cleanup()
        self.mount.app = self.old_static
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    async def upstream(self, post, get=None):
        app = web.Application()
        app.router.add_post("/v1/file_parse", post)
        if get:
            app.router.add_get("/v1/tasks/test/result", get)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        p = patch.object(server, "MINERU_API_URL", f"http://127.0.0.1:{port}/v1/file_parse")
        p.start()
        self.patches.append(p)

    async def upload(self, filename="sample.pdf", data=b"sample"):
        return await self.client.post("/mineru_parse", files={"file": (filename, data)})

    async def test_sync_zip_and_accessible_markdown_and_html_images(self):
        async def post(request):
            self.assertEqual(request.query["tier"], "standard")
            self.assertNotIn("page_range", request.query)
            form = await request.post()
            self.assertEqual(set(form), {"files"})
            self.assertEqual(form["files"].file.read(), b"sample")
            form["files"].file.close()
            return web.Response(body=result_zip(), content_type="application/zip")
        await self.upstream(post)
        response = await self.upload()
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(set(result), {"success", "markdown", "pages"})
        self.assertEqual(result["pages"], 2)
        self.assertNotIn("Page header", result["markdown"])
        self.assertIn("![alt](http://adapter/mineru_images/", result["markdown"])
        import re
        urls = re.findall(r"http://adapter/mineru_images/[^\s\)\"']+", result["markdown"])
        self.assertEqual(len(urls), 2)
        for url in urls:
            image = await self.client.get(url)
            self.assertEqual(image.status_code, 200)
            self.assertEqual(image.content, PNG)

    async def test_pending_task_is_polled_until_zip_is_available(self):
        calls = []
        async def post(request):
            return web.json_response({"result_url": "/v1/tasks/test/result"}, status=202)
        async def get(request):
            calls.append(request.query["response_format"])
            if len(calls) == 1:
                return web.json_response({"status": "parsing"}, status=202)
            return web.Response(body=result_zip(), content_type="application/zip")
        await self.upstream(post, get)
        response = await self.upload()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls, ["zip", "zip"])

    async def test_failed_task_returns_upstream_error(self):
        async def post(request):
            return web.json_response({"result_url": "/v1/tasks/test/result"}, status=202)
        async def get(request):
            return web.json_response({"status": "failed", "files": [{"status": "failed", "error": {
                "code": "page_range_invalid", "message": "Page range is only supported for PDF files",
            }}]}, status=409)
        await self.upstream(post, get)
        response = await self.upload()
        self.assertEqual(response.status_code, 502)
        self.assertFalse(response.json()["success"])
        self.assertIn("Page range is only supported for PDF files", response.json()["error"])

    async def test_timeout_applies_to_the_entire_request(self):
        async def post(request):
            await asyncio.sleep(.2)
            return web.Response(body=result_zip())
        await self.upstream(post)
        with patch.object(server, "MINERU_TIMEOUT", .05):
            response = await self.upload()
        self.assertEqual(response.status_code, 504)

    async def test_invalid_zip_returns_upstream_error(self):
        async def post(request):
            return web.json_response({"results": {}})
        await self.upstream(post)
        response = await self.upload()
        self.assertEqual(response.status_code, 502)

    async def test_unsafe_zip_member_is_rejected_before_image_writes(self):
        async def post(request):
            return web.Response(body=result_zip({"images/../../outside.png": PNG}))
        await self.upstream(post)
        response = await self.upload()
        self.assertEqual(response.status_code, 502)
        self.assertEqual(list(Path(self.tmp.name).rglob("*.png")), [])

    async def test_office_uses_flash_and_optional_header_is_prepended(self):
        async def post(request):
            self.assertEqual(request.query["tier"], "flash")
            self.assertNotIn("page_range", request.query)
            return web.Response(body=result_zip())
        await self.upstream(post)
        with patch.object(server, "MINERU_ENABLE_PAGE_HEADER", True):
            response = await self.upload("sample.docx")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["markdown"].startswith("Page header\n\n"))

    async def test_empty_and_oversized_uploads_are_rejected(self):
        self.assertEqual((await self.upload(data=b"")).status_code, 400)
        with patch.object(server, "MAX_FILE_BYTES", 4):
            self.assertEqual((await self.upload(data=b"12345")).status_code, 413)

    async def test_foreign_task_url_is_rejected(self):
        async def post(request):
            return web.json_response({"result_url": "https://other.invalid/task"}, status=202)
        await self.upstream(post)
        response = await self.upload()
        self.assertEqual(response.status_code, 502)


if __name__ == "__main__":
    unittest.main()
