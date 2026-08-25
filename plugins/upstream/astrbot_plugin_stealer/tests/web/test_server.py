#!/usr/bin/env python3
"""WebUI测试服务器 - 模拟API响应用于预览界面"""

import asyncio
import json
from pathlib import Path
from aiohttp import web


class MockWebServer:
    def __init__(self, host="127.0.0.1", port=8080):
        self.host = host
        self.port = port
        self.web_dir = Path(__file__).parent.parent.parent.resolve() / "web"
        self.app = web.Application()
        self._setup_routes()

    def _setup_routes(self):
        self.app.router.add_get("/", self.handle_index)
        self.app.router.add_get("/login.html", self.handle_login)
        self.app.router.add_get("/index.html", self.handle_index)
        self.app.router.add_get("/app.css", self.handle_root_css)
        self.app.router.add_get("/app.js", self.handle_root_js)
        self.app.router.add_get("/web/{path:.*}", self.handle_static)
        self.app.router.add_get("/images/{path:.*}", self.handle_dummy_image)
        self.app.router.add_get("/auth/info", self.handle_auth_info)
        self.app.router.add_post("/auth/login", self.handle_login_post)
        self.app.router.add_get("/api/health", self.handle_health)
        self.app.router.add_get("/api/stats", self.handle_stats)
        self.app.router.add_get("/api/images", self.handle_images)
        self.app.router.add_get("/api/emotions", self.handle_emotions)
        self.app.router.add_get("/api/categories", self.handle_categories)

    async def handle_index(self, request):
        return await self._serve_file("index.html")

    async def handle_login(self, request):
        return await self._serve_file("login.html")

    async def _serve_file(self, filename):
        file_path = self.web_dir / filename
        if not file_path.exists():
            return web.Response(text=f"Not found: {filename}", status=404)
        content = file_path.read_text(encoding="utf-8")
        return web.Response(text=content, content_type="text/html")

    async def handle_root_css(self, request):
        file_path = self.web_dir / "app.css"
        if not file_path.exists():
            return web.Response(text="Not found", status=404)
        content = file_path.read_text(encoding="utf-8")
        return web.Response(text=content, content_type="text/css")

    async def handle_root_js(self, request):
        file_path = self.web_dir / "app.js"
        if not file_path.exists():
            return web.Response(text="Not found", status=404)
        content = file_path.read_text(encoding="utf-8")
        return web.Response(text=content, content_type="application/javascript")

    async def handle_static(self, request):
        path = request.match_info.get("path", "")
        file_path = self.web_dir / path
        if not file_path.exists():
            return web.Response(text="Not found", status=404)
        if path.endswith(".css"):
            content_type = "text/css"
            content = file_path.read_text(encoding="utf-8")
        else:
            content_type = "application/javascript"
            content = file_path.read_text(encoding="utf-8")
        return web.Response(text=content, content_type=content_type)

    async def handle_dummy_image(self, request):
        path = request.match_info.get("path", "")
        file_path = self.web_dir / "web" / path
        if file_path.exists():
            content = file_path.read_bytes()
            return web.Response(body=content, content_type="image/png")
        placeholder = self.web_dir / "web" / "logo.png"
        if placeholder.exists():
            return web.Response(body=placeholder.read_bytes(), content_type="image/png")
        return web.Response(text="No image", status=404)

    async def handle_auth_info(self, request):
        return web.json_response({"requires_auth": False, "session_timeout": 3600})

    async def handle_login_post(self, request):
        data = await request.json()
        password = data.get("password", "")
        if password == "admin":
            response = web.json_response({"success": True})
            response.set_cookie("stealer_webui_session", "mock_session_id")
            return response
        return web.json_response({"success": False, "error": "密码错误"})

    async def handle_health(self, request):
        return web.Response(status=200)

    async def handle_stats(self, request):
        return web.json_response({
            "success": True,
            "stats": {
                "total": 42,
                "categories": 8,
                "today": 3
            }
        })

    async def handle_images(self, request):
        mock_images = [
            {
                "hash": f"abc{i}123",
                "url": f"/images/sample_{i}.png",
                "category": ["happy", "sad", "angry", "surprised", "troll", "cry", "confused", "embarrassed", "love", "disgust", "fear", "excitement", "tired", "sigh", "thank", "dumb"][i % 16],
                "tags": ["表情", "搞笑", "可爱"][i % 3],
                "desc": f"这是一个示例表情包 {i+1}",
                "scenes": ["聊天", "群聊"],
                "scope_mode": "public",
                "origin_target": "",
                "created_at": 1700000000 + i * 3600
            }
            for i in range(16)
        ]
        return web.json_response({
            "success": True,
            "images": mock_images,
            "total": 16,
            "categories": ["happy", "sad", "angry", "shy", "surprised", "troll", "cry", "confused", "embarrassed", "love", "disgust", "fear", "excitement", "tired", "sigh", "thank", "dumb"]
        })

    async def handle_emotions(self, request):
        return web.json_response({
            "success": True,
            "emotions": [
                {"key": "happy", "name": "开心", "desc": "快乐、愉悦、满足、好心情"},
                {"key": "sad", "name": "难过", "desc": "悲伤、沮丧、失落、emo"},
                {"key": "angry", "name": "生气", "desc": "愤怒、恼火、不满、暴躁"},
                {"key": "shy", "name": "害羞", "desc": "羞涩、不好意思、腼腆"},
                {"key": "surprised", "name": "惊讶", "desc": "意外、震惊、惊奇、啊？"},
                {"key": "troll", "name": "整活", "desc": "调皮、搞怪、发癫、抽象"},
                {"key": "cry", "name": "哭哭", "desc": "哭泣、流泪、委屈、破防"},
                {"key": "confused", "name": "困惑", "desc": "迷茫、不解、疑惑、问号脸"},
                {"key": "embarrassed", "name": "尴尬", "desc": "社死、窘迫、为难、脚趾抠地"},
                {"key": "love", "name": "喜欢", "desc": "喜爱、爱慕、宠溺、心动"},
                {"key": "disgust", "name": "嫌弃", "desc": "厌恶、反感、讨厌、yue"},
                {"key": "fear", "name": "害怕", "desc": "恐惧、担心、紧张、怂"},
                {"key": "excitement", "name": "兴奋", "desc": "激动、亢奋、嗨、上头"},
                {"key": "tired", "name": "困倦", "desc": "疲惫、困、无力、想躺"},
                {"key": "sigh", "name": "无奈", "desc": "叹气、摆烂、算了、心累"},
                {"key": "thank", "name": "感谢", "desc": "道谢、感恩、收到、爱了"},
                {"key": "dumb", "name": "无语", "desc": "呆住、傻眼、离谱、沉默"},
            ]
        })

    async def handle_categories(self, request):
        return web.json_response({
            "success": True,
            "categories": {
                "happy": 15,
                "sad": 8,
                "angry": 5,
                "shy": 3,
                "surprised": 7,
                "troll": 12,
                "cry": 6,
                "confused": 4,
                "embarrassed": 2,
                "love": 9,
                "disgust": 3,
                "fear": 2,
                "excitement": 5,
                "tired": 4,
                "sigh": 3,
                "thank": 6,
                "dumb": 8,
            }
        })

    def run(self):
        print(f"\n" + "="*50)
        print(f"  WebUI 测试服务器已启动")
        print(f"  访问地址: http://{self.host}:{self.port}")
        print(f"  登录页:   http://{self.host}:{self.port}/login.html")
        print(f"  主页面:   http://{self.host}:{self.port}/index.html")
        print(f"  登录密码: admin")
        print("="*50 + "\n")
        web.run_app(self.app, host=self.host, port=self.port, print=None)


if __name__ == "__main__":
    server = MockWebServer(host="127.0.0.1", port=8080)
    server.run()
