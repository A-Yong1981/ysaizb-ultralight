# -*- coding: utf-8 -*-
"""DouyinLive(:1089) -> AI Live Hub 弹幕转发器
用法：python danmaku_relay.py --room 你的直播间号
若使用 cyber-xiaoya-danmaku，直接把其推送目标改为 http://127.0.0.1:28910/api/danmaku 即可，无需本脚本。
"""
import argparse
import asyncio
import json

import httpx
import websockets


async def relay_douyin(room_id: str, ws_base: str, hub_url: str) -> None:
    ws_url = f"{ws_base.rstrip('/')}/ws/{room_id}"
    api_url = f"{hub_url.rstrip('/')}/api/danmaku"
    async with httpx.AsyncClient(timeout=5.0) as client:
        while True:
            try:
                print(f"[Relay] 正在连接弹幕源: {ws_url}")
                async with websockets.connect(ws_url, ping_interval=20) as ws:
                    print("[Relay] 连接成功，开始实时转发至中控网关...")
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        method = msg.get("method", "")
                        user_obj = msg.get("user") or {}
                        nick = user_obj.get("nickname") or msg.get("nick") or "观众"
                        # 聊天弹幕
                        if "ChatMessage" in method or msg.get("content"):
                            text = str(msg.get("content", "")).strip()
                            if text:
                                await client.post(
                                    api_url, json={"user": nick, "text": text, "kind": "chat", "platform": "douyin"}
                                )
                        # 礼物消息
                        elif "GiftMessage" in method or msg.get("gift"):
                            gift = msg.get("gift") or {}
                            await client.post(
                                api_url,
                                json={
                                    "user": nick,
                                    "kind": "gift",
                                    "platform": "douyin",
                                    "gift_item": gift.get("name", "礼物"),
                                    "gift_num": msg.get("repeatCount", 1),
                                },
                            )
            except Exception as exc:
                print(f"[Relay] 连接断开 ({exc})，3 秒后自动重连...")
                await asyncio.sleep(3)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DouyinLive -> AI Live Hub 弹幕转发器")
    parser.add_argument("--room", required=True, help="抖音直播间房间号")
    parser.add_argument("--ws-base", default="ws://127.0.0.1:1089", help="douyinLive 服务地址")
    parser.add_argument("--hub", default="http://127.0.0.1:28910", help="中控网关地址")
    args = parser.parse_args()
    asyncio.run(relay_douyin(args.room, args.ws_base, args.hub))
