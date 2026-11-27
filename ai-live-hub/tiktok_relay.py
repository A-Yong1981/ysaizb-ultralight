# -*- coding: utf-8 -*-
"""TikTok LIVE -> AI Live Hub 弹幕转发器（可选，需 pip install TikTokLive）
用法：python tiktok_relay.py --user 主播用户名 --proxy http://127.0.0.1:7890
代理只作用于本进程（由中控按平台预设注入），中控 / LLM / TTS 保持直连。
"""
import argparse
import asyncio

import httpx

PLATFORM = "tiktok"


def build_client(user: str, proxy: str):
    from TikTokLive import TikTokLiveClient

    unique_id = user if user.startswith("@") else f"@{user}"
    if proxy:
        try:
            return TikTokLiveClient(unique_id=unique_id, web_proxy=httpx.Proxy(proxy), ws_proxy=httpx.Proxy(proxy))
        except TypeError:
            pass  # 旧版本不支持该参数时，依赖环境变量 HTTP(S)_PROXY
    return TikTokLiveClient(unique_id=unique_id)


async def main(user: str, hub: str, proxy: str) -> None:
    try:
        from TikTokLive.events import CommentEvent, ConnectEvent, GiftEvent
    except ImportError:
        print("[TikTokRelay] 未安装 TikTokLive：请在 ultralight 环境执行 pip install TikTokLive")
        return
    api_url = f"{hub.rstrip('/')}/api/danmaku"
    http = httpx.AsyncClient(timeout=5.0, trust_env=False)  # 发往本机中控不走代理

    async def push(payload):
        payload["platform"] = PLATFORM
        try:
            await http.post(api_url, json=payload)
        except Exception as exc:
            print(f"[TikTokRelay] 推送中控失败: {exc}")

    while True:
        client = build_client(user, proxy)

        @client.on(ConnectEvent)
        async def on_connect(_):
            print(f"[TikTokRelay] 已连接 @{user.lstrip('@')} 直播间")

        @client.on(CommentEvent)
        async def on_comment(event):
            nick = getattr(event.user, "nickname", None) or getattr(event.user, "unique_id", "viewer")
            text = (getattr(event, "comment", "") or "").strip()
            if text:
                await push({"user": nick, "text": text, "kind": "chat"})

        @client.on(GiftEvent)
        async def on_gift(event):
            gift = getattr(event, "gift", None)
            # 连击礼物只在连击结束时感谢一次
            streakable = bool(getattr(gift, "streakable", False))
            streaking = bool(getattr(event, "streaking", False))
            if streakable and streaking:
                return
            nick = getattr(event.user, "nickname", None) or "viewer"
            await push({
                "user": nick,
                "kind": "gift",
                "gift_item": getattr(gift, "name", "gift") or "gift",
                "gift_num": int(getattr(event, "repeat_count", 1) or 1),
            })

        try:
            await client.start()
        except Exception as exc:
            print(f"[TikTokRelay] 连接断开或未开播 ({exc})，15 秒后重试...")
        await asyncio.sleep(15)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="TikTok LIVE -> AI Live Hub")
    ap.add_argument("--user", required=True, help="TikTok 主播用户名（不带 @ 也可以）")
    ap.add_argument("--hub", default="http://127.0.0.1:28910")
    ap.add_argument("--proxy", default="", help="仅本进程使用的代理，如 http://127.0.0.1:7890")
    a = ap.parse_args()
    asyncio.run(main(a.user, a.hub, a.proxy))
