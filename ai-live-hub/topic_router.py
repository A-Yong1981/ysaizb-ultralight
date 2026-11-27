# -*- coding: utf-8 -*-
"""
topic_router.py —— 知识库「按主题索引」+ 观众弹幕点播识别（中控进程，0 显存）

白话说明：
  1. 把 kb_data 下所有讲稿，按「分类文件夹 → 一级章节(#) → 二级小节(##)」整理成一本“主题目录”，
     每个主题记住：属于哪份讲稿、标题、观众常用叫法(关键词)、板书要点、对应的口播段落。
  2. 观众在弹幕里说“先讲讲防骗”“想听三国”“说说 AI 换脸”时：
       - 先用关键词规则秒判（不花钱、0 延迟）；
       - 规则拿不准时，再交给已配置的大模型（DeepSeek / 硅基流动 / 魔搭 容灾池）判断
         “是不是在点播？点的是目录里哪个主题？”——大模型只能从目录里选，不能编造主题。
  3. 命中知识库已有主题 → 生成“插播计划”，把该主题的讲稿段落按顺序推给数字人口播，
     讲完自动回到原来的章节续讲；知识库里没有的主题 → 交回原问答流程简短回应，绝不硬编。

讲稿可选增强写法（不写也能用，写了识别更准）：
  > [主题词] 防骗|反诈|骗子|电信诈骗      给当前章/小节补充观众的口语叫法
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from kb_engine import is_placeholder_key, parse_markdown, tokenize_mixed

CJK = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_CJK_RE = re.compile(f"[{CJK}]")
FAQ_HEADER_WORDS = ("常见高频问答", "精准直出", "FAQ")

# 观众“点播”说法（命中任一即视为想让主播讲某个主题）
REQUEST_PATTERNS: Tuple[str, ...] = (
    "先讲讲", "先说说", "先聊聊", "先来讲讲", "先给我们讲讲", "先讲", "先说", "先聊", "讲讲", "讲一讲", "讲一下", "讲下", "讲个", "讲点", "讲讲看", "来讲",
    "说说", "说一说", "说一下", "说下", "聊聊", "聊一聊", "聊一下", "聊下", "介绍一下", "介绍下", "介绍介绍",
    "科普一下", "科普下", "科普科普", "讲解一下", "讲解下", "展开讲", "详细讲", "细讲", "多讲", "重点讲",
    "想听", "我要听", "要听", "爱听", "能讲", "可以讲", "能不能讲", "能说说", "能聊聊", "安排一下", "安排上",
    "来一段", "来段", "开讲", "下一个讲", "接下来讲", "换个话题讲", "talk about", "tell us about",
    "explain", "can you cover", "i want to hear",
)
# 疑问说法（只是问一个具体小问题，走原来的问答流程而不是整段点播）
QUESTION_HINTS: Tuple[str, ...] = (
    "什么", "为什么", "为啥", "怎么", "如何", "吗", "么", "？", "?", "是不是", "哪个", "哪些", "多少", "几",
)
# 只在首尾剥离的口头语
_EDGE_FILLERS = (
    "主播", "老师", "小姐姐", "姐姐", "哥哥", "大佬", "您", "你", "咱们", "我们", "大家", "给我们", "给大家",
    "给我", "帮我", "帮忙", "麻烦", "请", "可以", "能不能", "能否", "可不可以", "好不好", "行不行", "一下",
    "一段", "一些", "吧", "呗", "呀", "啊", "哈", "嘛", "呢", "么", "吗", "了", "啦", "的", "那个", "这个",
    "内容", "话题", "知识", "部分", "专题", "主题", "下",
)
_TITLE_PREFIX_RE = re.compile(
    r"^(?:第\s*[0-9一二三四五六七八九十百零〇两]+\s*[章节讲课回篇部集期]|[0-9]+(?:\.[0-9]+)*[.、)]?\s+|[0-9]+[.、)]"
    r"|[（(][0-9一二三四五六七八九十]+[)）]|[一二三四五六七八九十]+[、.])\s*")
_SUBPHRASE_RE = re.compile(r"与|和|及|跟|以及|还有|怎么|如何|为什么|为啥|什么是|什么|到底|有什么|的")
_KW_SPLIT_RE = re.compile(r"[：:，,、|｜/（）()\[\]【】—\-·\s~～!！?？。;；“”\"']+")

DEFAULT_ROUTER_CFG: Dict[str, Any] = {
    "enabled": True,
    "detect_mode": "auto",          # auto=规则秒判+AI复核（推荐）| ai=每条都让AI判断 | rules=只用关键词规则
    "strong_score": 0.55,           # 规则直接点播的把握度门槛
    "min_score": 0.30,              # 没有 AI 时，规则点播的最低把握度
    "ai_min_confidence": 0.55,      # AI 判断“是点播”的最低自信度
    "ai_timeout": 6,
    "max_beats": 6,                 # 一次点播最多连讲几段（每段约 15 秒）
    "cooldown_sec": 300,            # 同一主题多久之内不重复整段点播（改走简短问答）
    "answer_during_topic": True,    # 点播讲解期间，有人提问先回答再接着讲
    "answer_every_n_beats": 2,      # 点播期间每讲几段穿插回答一条提问
    "max_pending": 3,               # 最多排队几个点播主题
    "prefer_active_doc": True,      # 当前讲稿里的主题优先
    "intro_template": "好嘞，{user}想听「{title}」，咱们这就展开讲讲。",
    "queued_template": "收到{user}的点播，「{title}」排在下一个讲。",
    "outro_template": "「{title}」就先讲到这儿，还想听哪个主题，直接在公屏打“讲讲”加主题名。",
    "resume_template": "咱们接着说「{title}」。",
    "recap_template": "「{title}」刚刚讲过，没赶上的朋友我挑重点再说一句：",
}


def fill_template(tpl: Any, **kw: Any) -> str:
    """安全填充 {user}/{title} 占位符（不用 str.format，防止配置里的模板做属性注入）。"""
    out = str(tpl or "")
    for k, v in kw.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def router_cfg(kb_cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out = dict(DEFAULT_ROUTER_CFG)
    out.update(((kb_cfg or {}).get("topic_router") or {}))
    return out


def _norm(text: str) -> str:
    return re.sub(r"[\s，,。！？!?、~～：:；;“”\"'（）()【】\[\]]+", "", str(text or "").lower())


def clean_title(title: str) -> str:
    t = re.sub(r"[\U0001F000-\U0001FFFF\u2600-\u27BF]", "", str(title or "")).strip()
    t = _TITLE_PREFIX_RE.sub("", t).strip(" ：:—-·")
    return t or str(title or "").strip()


def has_request_intent(text: str) -> bool:
    t = str(text or "").lower()
    return any(p in t for p in REQUEST_PATTERNS)


def is_question_like(text: str) -> bool:
    t = str(text or "").lower()
    return any(h in t for h in QUESTION_HINTS)


def extract_topic_phrase(text: str) -> str:
    """把“主播能不能先讲讲防骗的内容呀”剥成“防骗”。"""
    t = str(text or "").strip().lower()
    for _ in range(3):
        before = t
        for p in sorted(REQUEST_PATTERNS, key=len, reverse=True):
            t = t.replace(p, " ")
        if t == before:
            break
    t = re.sub(r"关于|有关|一下", " ", t)
    t = re.sub(r"[\s，,。！？!?、~～：:；;“”\"'（）()]+", " ", t).strip()
    parts = [p for p in t.split(" ") if p]
    t = max(parts, key=len) if parts else ""
    changed = True
    while changed and t:
        changed = False
        for f in sorted(_EDGE_FILLERS, key=len, reverse=True):
            if t.startswith(f) and len(t) > len(f):
                t, changed = t[len(f):], True
            if t.endswith(f) and len(t) > len(f):
                t, changed = t[: -len(f)], True
    return t.strip()


def _split_keywords(text: str) -> List[str]:
    out: List[str] = []
    for k in (x.strip() for x in _KW_SPLIT_RE.split(str(text or ""))):
        if len(k) < 2:
            continue
        for cand in [k] + [y.strip() for y in _SUBPHRASE_RE.split(k)]:
            if len(cand) >= 2 and cand not in out:
                out.append(cand)
    return out


class TopicIndex:
    """跨全部讲稿的主题目录（纯 CPU 词法 + 可选 AI 复核）。"""

    def __init__(self, kb_dir: Path, kb_cfg: Optional[Dict[str, Any]] = None) -> None:
        self.kb_dir = Path(kb_dir)
        self.index_dir = self.kb_dir / ".indexes"
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.index_dir / "topics.json"
        self.kb_cfg: Dict[str, Any] = dict(kb_cfg or {})
        self.topics: List[Dict[str, Any]] = []
        self.by_id: Dict[str, Dict[str, Any]] = {}
        self.signature: str = ""
        self.built_at: str = ""
        self.idf: Dict[str, float] = {}
        self.avg_idf: float = 1.0
        self._last_check: float = 0.0
        self.last_error: str = ""

    # ------------------------------------------------------------ 配置
    def update_config(self, kb_cfg: Dict[str, Any]) -> None:
        self.kb_cfg = dict(kb_cfg or {})

    @property
    def cfg(self) -> Dict[str, Any]:
        return router_cfg(self.kb_cfg)

    # ------------------------------------------------------------ 建目录
    def list_docs(self) -> List[str]:
        out: List[str] = []
        for p in sorted(self.kb_dir.rglob("*.md")):
            rel = p.relative_to(self.kb_dir).as_posix()
            if rel.startswith(".") or "/." in rel:
                continue
            out.append(rel)
        return out

    def _scan_signature(self) -> str:
        h = hashlib.md5()
        h.update(str(self.kb_cfg.get("chunk_max_chars", 100)).encode())
        for rel in self.list_docs():
            try:
                st = (self.kb_dir / rel).stat()
                h.update(f"{rel}|{st.st_size}|{int(st.st_mtime)}".encode("utf-8"))
            except OSError:
                continue
        return h.hexdigest()

    def ensure_fresh(self, min_interval: float = 8.0) -> bool:
        """讲稿有增删改时自动重建（最多每 8 秒检查一次文件时间戳，几乎不耗 CPU）。"""
        now = time.time()
        if self.topics and now - self._last_check < min_interval:
            return False
        self._last_check = now
        sig = self._scan_signature()
        if sig == self.signature and self.topics:
            return False
        if not self.topics and self._load_cache(sig):
            return True
        self.build(signature=sig)
        return True

    def _load_cache(self, sig: str) -> bool:
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception:
            return False
        if data.get("signature") != sig or not isinstance(data.get("topics"), list):
            return False
        self._install(data["topics"], sig, data.get("built_at", ""))
        return True

    def build(self, signature: Optional[str] = None) -> Dict[str, Any]:
        sig = signature or self._scan_signature()
        max_len = int(self.kb_cfg.get("chunk_max_chars", 100))
        topics: List[Dict[str, Any]] = []
        for rel in self.list_docs():
            try:
                md = (self.kb_dir / rel).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            topics.extend(self._topics_for_doc(rel, md, max_len))
        built_at = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            tmp = self.index_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"signature": sig, "built_at": built_at, "topics": topics},
                                      ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.index_path)
        except Exception as exc:
            self.last_error = f"主题目录缓存写入失败: {exc}"
        self._install(topics, sig, built_at)
        return {"topics": len(topics), "docs": len(self.list_docs()), "built_at": built_at}

    @staticmethod
    def _topics_for_doc(rel: str, md: str, max_len: int) -> List[Dict[str, Any]]:
        chunks, _faqs, chapters = parse_markdown(md, max_len)
        parts = rel.split("/")
        category = parts[0] if len(parts) > 1 else ""
        doc_title = clean_title(Path(rel).stem.lstrip("0123456789_- "))
        out: List[Dict[str, Any]] = []
        by_ch: Dict[int, List[Dict[str, Any]]] = {}
        for c in chunks:
            by_ch.setdefault(int(c.get("chapter_idx", 0)), []).append(c)
        for ci, ch_chunks in sorted(by_ch.items()):
            if not ch_chunks:
                continue
            ch_title = ch_chunks[0].get("chapter", "")
            if any(w in ch_title for w in FAQ_HEADER_WORDS):
                continue

            def _mk(tid: str, title: str, level: str, items: List[Dict[str, Any]], section: str = "") -> Dict[str, Any]:
                kws: List[str] = []
                for k in _split_keywords(clean_title(title)) + [clean_title(title)]:
                    if k not in kws:
                        kws.append(k)
                for it in items:
                    for k in it.get("topic_keywords", []) or []:
                        k = str(k).strip()
                        if len(k) >= 2 and k not in kws:
                            kws.append(k)
                points: List[str] = []
                for it in items:
                    for p in it.get("board_points", []) or []:
                        if p not in points:
                            points.append(p)
                return {
                    "tid": tid,
                    "doc": rel,
                    "doc_title": doc_title,
                    "category": category,
                    "level": level,
                    "chapter": ch_title,
                    "section": section,
                    "title": clean_title(title),
                    "keywords": kws[:24],
                    "points": points[:8],
                    "start_id": int(items[0].get("id", 0)),
                    "summary": str(items[0].get("text", ""))[:60],
                    "chunks": [{
                        "id": int(it.get("id", 0)),
                        "text": it.get("text", ""),
                        "chapter": it.get("chapter", ""),
                        "section": it.get("section", ""),
                        "points": it.get("board_points", []) or [],
                        "bg_video": it.get("bg_video", "") or "",
                    } for it in items],
                }

            out.append(_mk(f"{rel}#c{ci}", ch_title, "chapter", ch_chunks))
            sections: List[str] = []
            for c in ch_chunks:
                s = c.get("section", "")
                if s and s not in sections:
                    sections.append(s)
            if len(sections) >= 2:
                for si, s in enumerate(sections):
                    items = [c for c in ch_chunks if c.get("section", "") == s]
                    if items:
                        out.append(_mk(f"{rel}#c{ci}s{si}", s, "section", items, section=s))
        return out

    def _install(self, topics: List[Dict[str, Any]], sig: str, built_at: str) -> None:
        self.topics = topics
        self.by_id = {t["tid"]: t for t in topics}
        self.signature = sig
        self.built_at = built_at
        n = max(1, len(topics))
        df: Dict[str, int] = {}
        for t in topics:
            t["_title_tok"] = set(tokenize_mixed(" ".join([t["title"]] + t.get("keywords", []))))
            body = " ".join(t.get("points", []) + [c["text"] for c in t.get("chunks", [])])
            t["_body_tok"] = set(tokenize_mixed(body))
            t["_kw_norm"] = [_norm(k) for k in t.get("keywords", []) if len(_norm(k)) >= 2]
            for tok in t["_title_tok"] | t["_body_tok"]:
                df[tok] = df.get(tok, 0) + 1
        self.idf = {tok: math.log((n + 1.0) / (c + 0.5)) + 1.0 for tok, c in df.items()}
        self.avg_idf = (sum(self.idf.values()) / len(self.idf)) if self.idf else 1.0

    # ------------------------------------------------------------ 目录输出
    def catalog(self) -> List[Dict[str, Any]]:
        return [{
            "tid": t["tid"], "title": t["title"], "doc": t["doc"], "doc_title": t["doc_title"],
            "category": t["category"], "level": t["level"], "chapter": t["chapter"], "section": t["section"],
            "keywords": t["keywords"][:8], "beats": len(t["chunks"]), "summary": t["summary"],
        } for t in self.topics]

    # ------------------------------------------------------------ 打分
    def score(self, text: str, active_doc: str = "", top_n: int = 8) -> Tuple[str, List[Dict[str, Any]]]:
        """返回 (主题短语, 候选列表)；候选含 score / kw_hit / why。"""
        phrase = extract_topic_phrase(text)
        basis = phrase if len(phrase) >= 2 else str(text or "")
        qn = _norm(basis)
        q_tok = set(tokenize_mixed(basis))
        if not self.topics or (not qn and not q_tok):
            return phrase, []
        qw = sum(self.idf.get(t, self.avg_idf) for t in q_tok) or 1.0
        prefer = bool(self.cfg.get("prefer_active_doc", True))
        res: List[Dict[str, Any]] = []
        for t in self.topics:
            kw_hit = ""
            q_ok = len(qn) >= (2 if _CJK_RE.search(qn) else 3)
            for k in t["_kw_norm"]:
                hit = ""
                if len(k) >= (2 if _CJK_RE.search(k) else 3) and k in qn:
                    hit = k
                elif q_ok and qn in k:
                    hit = qn
                if len(hit) > len(kw_hit):
                    kw_hit = hit
            title_ov = sum(self.idf.get(x, self.avg_idf) for x in q_tok if x in t["_title_tok"]) / qw
            body_ov = sum(self.idf.get(x, self.avg_idf) for x in q_tok if x in t["_body_tok"]) / qw
            s = 0.5 * min(1.0, title_ov) + 0.3 * min(1.0, body_ov)
            if kw_hit:
                s += 0.35 + min(0.15, 0.03 * len(kw_hit))
            if prefer and active_doc and t["doc"] == active_doc:
                s += 0.04
            if t["level"] == "section" and kw_hit and _norm(t["section"]).find(kw_hit) >= 0:
                s += 0.03
            if s <= 0.05:
                continue
            why = f"关键词「{kw_hit}」" if kw_hit else f"标题重合 {title_ov:.2f} / 内容重合 {body_ov:.2f}"
            res.append({"tid": t["tid"], "title": t["title"], "doc": t["doc"], "level": t["level"],
                        "score": round(min(1.0, s), 3), "kw_hit": kw_hit, "why": why})
        res.sort(key=lambda x: (x["score"], x["level"] == "section"), reverse=True)
        return phrase, res[:max(1, int(top_n))]

    def quick_check(self, text: str, active_doc: str = "") -> Optional[Dict[str, Any]]:
        """不调 AI 的秒判：明显的点播 + 高把握主题 → 返回候选，用于立即柔和打断当前讲稿。"""
        cfg = self.cfg
        if not cfg.get("enabled", True) or not has_request_intent(text):
            return None
        _phrase, cands = self.score(text, active_doc, top_n=1)
        if cands and cands[0]["score"] >= float(cfg.get("strong_score", 0.55)):
            return cands[0]
        return None

    # ------------------------------------------------------------ AI 复核
    async def ai_classify(
        self,
        client: httpx.AsyncClient,
        providers: List[Dict[str, Any]],
        text: str,
        cands: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """让已配置的大模型判断是否点播及主题编号；全部节点失败返回 None（调用方降级规则）。"""
        usable = [p for p in providers if p.get("enabled") and not is_placeholder_key(p.get("api_key"))]
        if not usable or client is None:
            return None
        menu = list(cands[:8]) if cands else []
        seen = {c["tid"] for c in menu}
        # 目录较小时整本给 AI；较大时给「候选 + 全部一级章节」，保证词法没命中时 AI 也能从章节里挑
        pool = self.topics if len(self.topics) <= 30 else [t for t in self.topics if t["level"] == "chapter"]
        menu = menu + [{"tid": t["tid"], "title": t["title"]} for t in pool if t["tid"] not in seen]
        menu = menu[:36]
        if not menu:
            return None
        lines = []
        for i, c in enumerate(menu, 1):
            t = self.by_id.get(c["tid"], {})
            kws = "、".join((t.get("keywords") or [])[:5])
            lines.append(f"{i}. {c['title']}" + (f" | {kws}" if kws else ""))
        system = ("你是直播间弹幕意图分析助手。判断观众弹幕是不是在“点播/要求主播讲某个主题”。"
                  "只能从给定主题目录里选编号，不能编造；只输出一行 JSON，不要解释。")
        user = (f"主题目录：\n" + "\n".join(lines) + f"\n\n观众弹幕：「{text}」\n\n"
                '输出：{"intent":"topic_request|question|chat","topic_no":编号或0,"confidence":0到1,"reason":"15字内"}\n'
                "判断规则：要求先讲/讲讲/想听/介绍某个主题 → topic_request；只问一个具体小问题 → question；"
                "打招呼闲聊 → chat。目录里没有对应主题时 topic_no 填 0。")
        timeout = float(self.cfg.get("ai_timeout", 6))
        for prov in usable:
            payload: Dict[str, Any] = {
                "model": prov["model"],
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "temperature": 0.1,
                "max_tokens": 80,
            }
            if isinstance(prov.get("extra_body"), dict):
                payload.update(prov["extra_body"])
            try:
                resp = await client.post(
                    prov["base_url"].rstrip("/") + "/chat/completions",
                    headers={"Authorization": f"Bearer {prov['api_key']}", "Content-Type": "application/json"},
                    json=payload, timeout=min(timeout, float(prov.get("timeout", timeout))),
                )
                if resp.status_code != 200:
                    self.last_error = f"{prov.get('name')} HTTP {resp.status_code}"
                    continue
                content = resp.json()["choices"][0]["message"].get("content") or ""
                m = re.search(r"\{.*\}", content, re.S)
                if not m:
                    continue
                data = json.loads(m.group(0))
                no = int(data.get("topic_no") or 0)
                tid = menu[no - 1]["tid"] if 1 <= no <= len(menu) else ""
                return {
                    "intent": str(data.get("intent") or "chat"),
                    "tid": tid,
                    "confidence": float(data.get("confidence") or 0.0),
                    "reason": str(data.get("reason") or "")[:30],
                    "provider": prov.get("name", ""),
                }
            except Exception as exc:
                self.last_error = f"{prov.get('name')} 异常: {exc}"
                continue
        return None

    # ------------------------------------------------------------ 总判定
    async def route(
        self,
        client: Optional[httpx.AsyncClient],
        text: str,
        providers: Optional[List[Dict[str, Any]]] = None,
        active_doc: str = "",
        allow_ai: bool = True,
    ) -> Dict[str, Any]:
        """判定一条弹幕是否点播知识库主题。返回 dict（is_request / topic / method / reason ...）。"""
        cfg = self.cfg
        out: Dict[str, Any] = {"is_request": False, "topic": None, "tid": "", "method": "none", "score": 0.0,
                               "phrase": "", "candidates": [], "reason": "", "ai": None}
        text = str(text or "").strip()
        if not cfg.get("enabled", True) or len(text) < 2:
            out["reason"] = "主题点播未开启" if not cfg.get("enabled", True) else "弹幕太短"
            return out
        self.ensure_fresh()
        phrase, cands = self.score(text, active_doc)
        out.update(phrase=phrase, candidates=cands)
        best = cands[0] if cands else None
        rule_req = has_request_intent(text)
        question = is_question_like(text)
        mode = str(cfg.get("detect_mode", "auto")).lower()
        strong = float(cfg.get("strong_score", 0.55))
        min_rules = float(cfg.get("min_score", 0.30))
        ai_conf = float(cfg.get("ai_min_confidence", 0.55))
        providers = providers or []
        ai_ready = allow_ai and mode in ("auto", "ai") and any(
            p.get("enabled") and not is_placeholder_key(p.get("api_key")) for p in providers)

        def _accept(tid: str, method: str, score: float, reason: str) -> Dict[str, Any]:
            t = self.by_id.get(tid)
            if t:
                out.update(is_request=True, topic=t, tid=tid, method=method, score=round(score, 3), reason=reason)
            return out

        if not best and not (rule_req and ai_ready):
            out["reason"] = "知识库里没有相近主题"
            return out
        best = best or {"tid": "", "title": "", "score": 0.0, "kw_hit": "", "why": ""}
        # 1) 规则秒判：明确点播 + 高把握主题
        if rule_req and best["tid"] and best["score"] >= strong and mode != "ai":
            return _accept(best["tid"], "rules", best["score"], f"点播说法 + {best['why']}")
        # 2) 需要 AI 复核的情况
        need_ai = False
        if mode == "ai":
            need_ai = best["score"] >= 0.12 or rule_req
        elif mode == "auto":
            need_ai = rule_req or (bool(best["kw_hit"]) and not question and len(text) <= 12)
        if need_ai and ai_ready:
            ai = await self.ai_classify(client, providers, text, cands)
            out["ai"] = ai
            if ai is not None:
                if ai["intent"] == "topic_request" and ai["tid"] and ai["confidence"] >= ai_conf:
                    sc = next((c["score"] for c in cands if c["tid"] == ai["tid"]), best["score"])
                    return _accept(ai["tid"], "ai", max(sc, ai["confidence"]),
                                   f"AI 判断点播（{ai.get('provider', '')}：{ai.get('reason', '')}）")
                out["reason"] = f"AI 判断不是点播（{ai.get('intent')}：{ai.get('reason', '')}）"
                return out
        # 3) 没有 AI 或 AI 全部失败 → 规则兜底
        if not best["tid"]:
            out["reason"] = "知识库里没有相近主题"
            return out
        if rule_req and best["score"] >= min_rules:
            return _accept(best["tid"], "rules_fallback", best["score"], f"点播说法 + {best['why']}（AI 不可用，规则兜底）")
        if best["kw_hit"] and not question and len(text) <= 8 and best["score"] >= strong:
            return _accept(best["tid"], "rules_short", best["score"], f"短弹幕直呼主题「{best['kw_hit']}」")
        out["reason"] = "不像点播" if not rule_req else f"点播说法但主题把握度不足（{best['score']}）"
        return out

    # ------------------------------------------------------------ 插播计划
    def recap_text(self, tid: str, user: str = "观众", max_chars: int = 120) -> str:
        """冷却期内重复点播：不重讲整段，只用一句话回顾重点（板书要点优先）。"""
        t = self.by_id.get(tid)
        if not t:
            return ""
        key = "；".join(t.get("points", [])[:2]) or (t["chunks"][0]["text"] if t.get("chunks") else "")
        head = fill_template(self.cfg.get("recap_template", ""), user=user, title=t["title"])
        return (head + key)[:max_chars]

    def build_plan(self, tid: str, user: str = "观众", max_beats: Optional[int] = None) -> Optional[Dict[str, Any]]:
        t = self.by_id.get(tid)
        if not t:
            return None
        cfg = self.cfg
        n = max(1, int(max_beats or cfg.get("max_beats", 6)))
        items = t["chunks"][:n]
        total = len(items)
        beats: List[Dict[str, Any]] = []
        for i, c in enumerate(items):
            beats.append({
                "text": c["text"],
                "board": {
                    "chapter": c.get("chapter") or t["chapter"],
                    "section": c.get("section") or t["section"],
                    "points": c.get("points") or t.get("points", []),
                    "bg_video": c.get("bg_video", ""),
                    "progress": f"点播 {i + 1}/{total}",
                    "beat": f"{i + 1}/{total}",
                },
            })
        if beats:
            intro = fill_template(cfg.get("intro_template", ""), user=user, title=t["title"])
            beats[0]["text"] = (intro + beats[0]["text"]) if intro else beats[0]["text"]
            outro = fill_template(cfg.get("outro_template", ""), user=user, title=t["title"])
            if outro:
                beats.append({"text": outro, "board": dict(beats[-1]["board"]), "outro": True})
        return {"tid": tid, "title": t["title"], "doc": t["doc"], "user": user, "beats": beats, "idx": 0,
                "start_id": t["start_id"], "end_id": items[-1]["id"] if items else t["start_id"],
                "created": time.time(), "resume_needed": False}
