"""Authenticated, read-only Cognitive Observatory endpoints."""

from __future__ import annotations

from quart import jsonify, request

from iris_memory.cognitive.iris_adapter import get_cognitive_runtime
from iris_memory.web.services import P1ObservatoryService

PLUGIN_NAME = "astrbot_plugin_iris_memory"


def get_observatory_service() -> P1ObservatoryService:
    runtime = get_cognitive_runtime()
    observer = runtime.episode_observer
    return P1ObservatoryService(
        getattr(observer, "store", None),
        execution_observatory=getattr(runtime, "execution_observatory", None),
    )


async def observatory_summary():
    return jsonify({"success": True, "summary": get_observatory_service().summary()})


async def observatory_episodes():
    try:
        limit = min(max(int(request.args.get("limit", 50)), 1), 200)
        offset = max(int(request.args.get("offset", 0)), 0)
        payload = get_observatory_service().list_episodes(state=request.args.get("state"), query=request.args.get("query"), limit=limit, offset=offset)
        return jsonify({"success": True, **payload})
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


async def observatory_episode_detail(episode_id: str):
    try:
        return jsonify({"success": True, **get_observatory_service().episode_detail(episode_id)})
    except KeyError:
        return jsonify({"success": False, "error": "episode not found"}), 404
    except RuntimeError as exc:
        return jsonify({"success": False, "error": str(exc)}), 503


async def observatory_preview(episode_id: str):
    try:
        return jsonify({"success": True, **get_observatory_service().preview_review(episode_id)})
    except KeyError:
        return jsonify({"success": False, "error": "episode not found"}), 404
    except RuntimeError as exc:
        return jsonify({"success": False, "error": str(exc)}), 503


async def observatory_demo_cases():
    return jsonify({"success": True, "demo": True, "cases": get_observatory_service().demo_cases()})


async def observatory_demo_case(case_id: str):
    try:
        return jsonify({"success": True, **get_observatory_service().demo_case(case_id)})
    except KeyError:
        return jsonify({"success": False, "error": "demo case not found"}), 404


def register_observatory_routes(context) -> None:
    prefix = f"/{PLUGIN_NAME}/cognitive-observatory"
    for route, handler, methods, description in [
        (f"{prefix}/summary", observatory_summary, ["GET"], "获取认知观测台摘要"),
        (f"{prefix}/episodes", observatory_episodes, ["GET"], "获取 Episode 列表"),
        (f"{prefix}/episodes/<episode_id>", observatory_episode_detail, ["GET"], "获取 Episode 详情"),
        (f"{prefix}/episodes/<episode_id>/preview", observatory_preview, ["POST"], "预览 Review（不持久化）"),
        (f"{prefix}/demo-cases", observatory_demo_cases, ["GET"], "获取 P1 演示案例"),
        (f"{prefix}/demo-cases/<case_id>", observatory_demo_case, ["GET"], "获取 P1 演示案例详情"),
    ]:
        context.register_web_api(route, handler, methods, description)
