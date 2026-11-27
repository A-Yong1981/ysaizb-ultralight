# -*- coding: utf-8 -*-
"""
kb_engine.py —— 双模式知识库引擎（0 显存）
  模式 1「章节主动解说」：Markdown 讲稿 → 口播段落；空闲时按顺序连播，联动 OBS 板书，答疑后断点续讲
  模式 2「弹幕答疑 RAG」：L1 精准 FAQ 直出 → L2 硅基流动 bge-m3 向量 + 词法混合 → L3 纯 CPU 词法兜底

讲稿格式（kb_data/*.md）：
  # 第1章 标题          一级章节（OBS 板书标题）
  > [板书] 要点         板书要点（属于所在章节，建议 12~22 字）
  ## 小节标题
  普通段落             口播正文（自动按句切成 ≤ chunk_max_chars 字的播讲段）
  Q: 触发词1|触发词2    精准问答（触发词建议 3 字以上的口语问法）
  A: 标准回答
  <!-- 注释 -->        忽略
"""
import hashlib
import json
import math
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

CJK = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_CJK_RE = re.compile(f"[{CJK}]")
FAQ_HEADER_WORDS = ("常见高频问答", "精准直出", "FAQ")


def is_placeholder_key(key: Any) -> bool:
    k = str(key or "").strip()
    return (not k) or k.startswith("填入")


def tokenize_mixed(text: str) -> List[str]:
    """英文/数字按词，中文按二元组切分（纯 CPU，无需分词库）。"""
    text = str(text or "").lower()
    tokens: List[str] = re.findall(r"[a-z0-9][a-z0-9\-\.]*", text)
    for seg in re.findall(f"[{CJK}]+", text):
        if len(seg) == 1:
            tokens.append(seg)
        else:
            tokens.extend(seg[i:i + 2] for i in range(len(seg) - 1))
    return tokens


def cosine_sim(v1: List[float], v2: List[float]) -> float:
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    n1 = math.sqrt(sum(a * a for a in v1))
    n2 = math.sqrt(sum(b * b for b in v2))
    if n1 == 0.0 or n2 == 0.0:
        return 0.0
    return dot / (n1 * n2)


def _norm_q(text: str) -> str:
    return re.sub(r"[\s，,。！？!?、~～]+", "", str(text or "").lower())


def split_spoken_chunks(paragraph: str, max_len: int = 100) -> List[str]:
    """按句号/问号/分号切句，再拼成不超过 max_len 的口播段；超长句优先在逗号处断开。"""
    clean = re.sub(r"\s+", " ", paragraph).strip()
    if not clean:
        return []
    if len(clean) <= max_len:
        return [clean]
    sentences = [s.strip() for s in re.split(r"(?<=[。！？!?；;])\s*|(?<=\.)\s+", clean) if s and s.strip()]
    pieces: List[str] = []
    for s in sentences:
        if len(s) <= max_len:
            pieces.append(s)
            continue
        buf = ""
        for part in re.split(r"(?<=[，,、：:])", s):
            if len(buf) + len(part) <= max_len:
                buf += part
            else:
                if buf:
                    pieces.append(buf)
                while len(part) > max_len:
                    pieces.append(part[:max_len])
                    part = part[max_len:]
                buf = part
        if buf:
            pieces.append(buf)
    chunks: List[str] = []
    buf = ""
    for p in pieces:
        sep = " " if (buf and not _CJK_RE.search(buf[-1:]) and not _CJK_RE.search(p[:1])) else ""
        if len(buf) + len(sep) + len(p) <= max_len:
            buf = f"{buf}{sep}{p}" if buf else p
        else:
            if buf:
                chunks.append(buf)
            buf = p
    if buf:
        chunks.append(buf)
    return chunks


def _clean_line(line: str) -> str:
    line = re.sub(r"^\s*(?:[-*+•]|\d+[.、)])\s+", "", line)   # 列表符号
    line = re.sub(r"\*\*|__|`", "", line)                     # 加粗 / 代码
    return line.strip()


def parse_markdown(md_text: str, max_len: int = 100) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    chunks: List[Dict[str, Any]] = []
    faqs: List[Dict[str, Any]] = []
    chapters: List[Dict[str, Any]] = []
    cur_chapter = "开场导览"
    cur_section = ""
    cur_bg = ''
    cur_points: List[str] = []
    para_buf: List[str] = []
    pending_q: Optional[List[str]] = None
    in_comment = False
    in_fence = False

    def flush_para() -> None:
        nonlocal para_buf
        if not para_buf:
            return
        joined = ""
        for seg in para_buf:
            if joined and not _CJK_RE.search(joined[-1:]) and not _CJK_RE.search(seg[:1]):
                joined += " "
            joined += seg
        para_buf = []
        for piece in split_spoken_chunks(joined.strip(), max_len):
            chunks.append({
                "id": len(chunks),
                "chapter": cur_chapter,
                "chapter_idx": max(0, len(chapters) - 1),
                "section": cur_section,
                "bg_video": cur_bg,
                "board_points": list(cur_points),
                "text": piece,
                "embedding": [],
            })

    for raw in md_text.splitlines():
        line = raw.strip()
        if in_comment:
            if "-->" in line:
                in_comment = False
            continue
        if line.startswith("<!--"):
            if "-->" not in line:
                in_comment = True
            continue
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not line or re.fullmatch(r"[-=*_]{3,}", line):
            flush_para()
            continue
        m_q = re.match(r"^(?:Q|q|问)\s*\d*\s*[:：]\s*(.*)$", line)
        if m_q:
            flush_para()
            pending_q = [k.strip() for k in re.split(r"[|｜/、,，]", m_q.group(1)) if k.strip()]
            continue
        m_a = re.match(r"^(?:A|a|答)\s*\d*\s*[:：]\s*(.*)$", line)
        if m_a and pending_q:
            ans = _clean_line(m_a.group(1))
            if ans:
                faqs.append({"keywords": pending_q, "answer": ans})
            pending_q = None
            continue
        if re.match(r"^#\s+", line):
            flush_para()
            title = re.sub(r"^#\s+", "", line).strip()
            cur_section = ""
            cur_points = []
            cur_bg = ''
            if any(w in title for w in FAQ_HEADER_WORDS):
                cur_chapter = title
                continue
            cur_chapter = title
            chapters.append({"title": cur_chapter, "start_idx": len(chunks)})
            continue
        if re.match(r"^#{2,6}\s+", line):
            flush_para()
            cur_section = re.sub(r"^#{2,6}\s+", "", line).strip()
            continue
        if line.startswith(">"):
            flush_para()
            bg = re.match(r"^>\s*(?:\[背景\]|【背景】)\s*(.+)$", line)
            if bg:
                cur_bg = bg[1].strip()
                continue
            pt = re.sub(r"^>\s*(\[板书\]|【板书】|板书\s*[:：]|\[board\])?\s*", "", re.sub(r"\*\*|__", "", line), flags=re.I).strip()
            if pt:
                cur_points.append(_clean_line(pt))
            continue
        cl = _clean_line(line)
        if cl:
            para_buf.append(cl)
    flush_para()
    valid = [c for c in chapters if c["start_idx"] < len(chunks)]
    for i, c in enumerate(valid):
        c["idx"] = i
    # chapter_idx 与过滤后的章节对齐
    for ch in chunks:
        ch["chapter_idx"] = max([c["idx"] for c in valid if c["start_idx"] <= ch["id"]] or [0])
    return chunks, faqs, valid


def match_faq_list(faqs: List[Dict[str, Any]], query: str) -> Optional[Dict[str, Any]]:
    """L1 精准直出：任一触发词出现在问题里即命中，取最长的触发词。
    过短的触发词（中文 <2 字 / 英文 <3 字，如“AI”）必须整句相等才算，防止误命中。"""
    q = _norm_q(query)
    if not q:
        return None
    best = None
    for item in faqs or []:
        for kw in item.get("keywords", []):
            k = _norm_q(kw)
            if not k:
                continue
            min_len = 2 if _CJK_RE.search(k) else 3
            if (len(k) >= min_len and k in q) or q == k:
                if best is None or len(k) > len(best[0]):
                    best = (k, item)
    if best:
        return {"keyword": best[0], "answer": best[1]["answer"]}
    return None


class KnowledgeBaseEngine:
    def __init__(self, kb_dir: Path, cfg: Dict[str, Any]) -> None:
        self.kb_dir = kb_dir
        self.kb_dir.mkdir(parents=True, exist_ok=True)
        self.index_dir = self.kb_dir / ".indexes"
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.cursor_path = self.index_dir / "cursors.json"
        self.cfg: Dict[str, Any] = dict(cfg or {})
        self.active_doc: str = ""
        self.chunks: List[Dict[str, Any]] = []
        self.faqs: List[Dict[str, Any]] = []
        self.chapters: List[Dict[str, Any]] = []
        self.doc_hash: str = ""
        self.idf_map: Dict[str, float] = {}
        self.chunk_tokens: List[set] = []
        self.max_idf: float = 1.0
        self.vectorized: bool = False
        self.doc_cursors: Dict[str, int] = self._load_cursors()
        self.cursor: int = 0
        self.paused: bool = False
        self.interrupted_by_qa: bool = False
        self.building: bool = False
        self.last_error: str = ""
        self.last_board: Dict[str, Any] = {"chapter": "", "section": "", "points": [], "progress": "0/0"}

    # ------------------------------------------------------------ 基础
    def update_config(self, cfg: Dict[str, Any]) -> None:
        self.cfg = dict(cfg or {})

    def parse_markdown(self, md_text: str, max_len: Optional[int] = None):
        """实例方法版本（tests/test_smoke.py 调用）：返回 (chunks, faqs, chapters)。"""
        return parse_markdown(md_text, int(max_len or self.cfg.get("chunk_max_chars", 100)))

    def list_docs(self) -> List[str]:
        docs: List[str] = []
        for p in sorted(self.kb_dir.rglob("*.md")):
            rel = p.relative_to(self.kb_dir).as_posix()
            if rel.startswith(".") or "/." in rel:
                continue
            docs.append(rel)
        return docs

    def doc_path(self, doc_name: str) -> Path:
        raw = str(doc_name or "").replace("\\", "/").lstrip("/")
        cand = (self.kb_dir / raw).resolve()
        root = self.kb_dir.resolve()
        try:
            cand.relative_to(root)
        except ValueError:
            cand = self.kb_dir / Path(raw).name
        if cand.is_file():
            return cand
        fname = Path(raw).name
        if fname:
            for hit in self.kb_dir.rglob(fname):
                if hit.is_file() and ".indexes" not in hit.parts:
                    return hit
        return cand

    def _index_file_for(self, doc_name: str) -> Path:
        safe = hashlib.md5(doc_name.encode("utf-8")).hexdigest()[:10]
        return self.index_dir / f"{safe}.idx.json"

    def _load_cursors(self) -> Dict[str, int]:
        try:
            return {k: int(v) for k, v in json.loads(self.cursor_path.read_text(encoding="utf-8")).items()}
        except Exception:
            return {}

    def save_cursors(self) -> None:
        if self.active_doc:
            self.doc_cursors[self.active_doc] = self.cursor
        try:
            tmp = self.cursor_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.doc_cursors, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.cursor_path)
        except Exception:
            pass

    def embedding_available(self) -> bool:
        emb = self.cfg.get("embedding", {}) or {}
        return bool(emb.get("enabled", True)) and not is_placeholder_key(emb.get("api_key"))

    def _compute_idf(self) -> None:
        n = max(1, len(self.chunks))
        self.chunk_tokens = [
            set(tokenize_mixed(f"{c['chapter']} {c['section']} {' '.join(c.get('board_points', []))} {c['text']}"))
            for c in self.chunks
        ]
        df: Dict[str, int] = {}
        for toks in self.chunk_tokens:
            for t in toks:
                df[t] = df.get(t, 0) + 1
        self.idf_map = {}
        for t, cnt in df.items():
            w = math.log((n + 1.0) / (cnt + 0.5)) + 1.0
            if cnt / n > 0.5:          # 几乎每段都有的词（如“AI”“咱们”）大幅降权
                w *= 0.3
            self.idf_map[t] = w
        # 讲稿里没出现过的词按“平均权重”计入分母，跑题问题得分自然偏低
        self.max_idf = (sum(self.idf_map.values()) / len(self.idf_map)) if self.idf_map else 1.0

    def _board_for(self, idx: int) -> Dict[str, Any]:
        if not self.chunks:
            return {"chapter": "", "section": "", "points": [], "progress": "0/0"}
        idx = max(0, min(idx, len(self.chunks) - 1))
        ch = self.chunks[idx]
        ci = ch.get("chapter_idx", 0)
        return {
            "chapter": ch["chapter"],
            "section": ch["section"],
            "bg_video": ch.get("bg_video", ""),
            "points": ch.get("board_points", []),
            "progress": f"{ci + 1}/{max(1, len(self.chapters))}",
            "beat": f"{idx + 1}/{len(self.chunks)}",
        }

    # ------------------------------------------------------------ 索引
    async def _embed_texts(self, client: httpx.AsyncClient, texts: List[str]) -> List[List[float]]:
        emb = self.cfg.get("embedding", {}) or {}
        if not self.embedding_available() or not texts:
            return []
        url = emb.get("base_url", "https://api.siliconflow.cn/v1").rstrip("/") + "/embeddings"
        headers = {"Authorization": f"Bearer {emb['api_key']}", "Content-Type": "application/json"}
        model = emb.get("model", "BAAI/bge-m3")
        timeout = float(emb.get("timeout", 12))
        out: List[List[float]] = []
        for i in range(0, len(texts), 16):
            batch = texts[i:i + 16]
            try:
                resp = await client.post(url, headers=headers, timeout=timeout,
                                         json={"model": model, "input": batch, "encoding_format": "float"})
                if resp.status_code != 200:
                    self.last_error = f"向量接口 HTTP {resp.status_code}: {resp.text[:120]}"
                    return []
                items = sorted(resp.json().get("data", []), key=lambda x: x.get("index", 0))
                out.extend(it["embedding"] for it in items)
            except Exception as exc:
                self.last_error = f"向量接口异常: {exc}"
                return []
        return out

    def _read_index(self, doc_name: str) -> Optional[Dict[str, Any]]:
        p = self._index_file_for(doc_name)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _activate(self, doc_name: str, data: Dict[str, Any], reset_cursor: bool) -> None:
        if self.active_doc and self.active_doc != doc_name:
            self.doc_cursors[self.active_doc] = self.cursor
        self.active_doc = doc_name
        self.chunks = data.get("chunks", [])
        self.faqs = data.get("faqs", [])
        self.chapters = data.get("chapters", [])
        self.doc_hash = data.get("doc_hash", "")
        self.vectorized = bool(data.get("vectorized"))
        self._compute_idf()
        saved = self.doc_cursors.get(doc_name, 0)
        self.cursor = 0 if reset_cursor else min(max(0, saved), max(0, len(self.chunks) - 1))
        self.interrupted_by_qa = False
        self.last_board = self._board_for(self.cursor)
        self.save_cursors()

    async def build_index(self, client: httpx.AsyncClient, doc_name: Optional[str] = None, force: bool = False,
                          reset_cursor: bool = True, activate: bool = True) -> Dict[str, Any]:
        target = Path(str(doc_name or self.active_doc or "")).name
        if not target:
            return {"code": 1, "msg": "未指定讲稿文件"}
        path = self.doc_path(target)
        if not path.exists():
            return {"code": 1, "msg": f"找不到讲稿文件: {target}"}
        md_text = path.read_text(encoding="utf-8", errors="ignore")
        max_len = int(self.cfg.get("chunk_max_chars", 100))
        model = (self.cfg.get("embedding", {}) or {}).get("model", "BAAI/bge-m3")
        new_hash = hashlib.md5(f"{md_text}|{max_len}".encode("utf-8")).hexdigest()

        cached = None if force else self._read_index(target)
        if cached and cached.get("doc_hash") == new_hash and cached.get("chunks") is not None:
            need_vec = self.embedding_available() and (not cached.get("vectorized") or cached.get("model") != model)
            if not need_vec:
                if activate:
                    self._activate(target, cached, reset_cursor)
                return {"code": 0, "msg": "命中本地索引缓存（秒级切换）", "doc": target,
                        "chunks": len(cached["chunks"]), "faqs": len(cached.get("faqs", [])),
                        "chapters": len(cached.get("chapters", [])),
                        "cursor": self.cursor if activate else 0, "vectorized": bool(cached.get("vectorized"))}

        self.building = True
        try:
            chunks, faqs, chapters = parse_markdown(md_text, max_len)
            vectorized = False
            if chunks and self.embedding_available():
                vecs = await self._embed_texts(client, [f"{c['chapter']} {c['section']} {c['text']}" for c in chunks])
                if len(vecs) == len(chunks):
                    for c, v in zip(chunks, vecs):
                        c["embedding"] = v
                    vectorized = True
            data = {"doc_name": target, "doc_hash": new_hash, "model": model if vectorized else "",
                    "vectorized": vectorized, "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "chapters": chapters, "faqs": faqs, "chunks": chunks}
            tmp = self._index_file_for(target).with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self._index_file_for(target))
            if activate:
                # 讲稿内容改过：旧进度可能错位，超出范围时自动回到开头
                self._activate(target, data, reset_cursor)
        finally:
            self.building = False
        msg = "新索引构建完成" + ("" if vectorized or not self.embedding_available() else f"（向量失败已降级词法：{self.last_error}）")
        return {"code": 0, "msg": msg, "doc": target, "chunks": len(chunks), "faqs": len(faqs),
                "chapters": len(chapters), "cursor": self.cursor if activate else 0, "vectorized": vectorized}

    # ------------------------------------------------------------ 检索
    def match_direct_faq(self, query: str) -> Optional[Dict[str, Any]]:
        """L1：触发词命中即直出（规则见 match_faq_list）。"""
        return match_faq_list(self.faqs, query)

    def lexical_scores(self, query: str) -> List[float]:
        q_tokens = set(tokenize_mixed(query))
        default = getattr(self, "max_idf", 1.0)
        total = sum(self.idf_map.get(t, default) for t in q_tokens) or 1.0
        return [min(1.0, sum(self.idf_map.get(t, default) for t in q_tokens if t in toks) / total)
                for toks in self.chunk_tokens]

    async def hybrid_search(self, client: httpx.AsyncClient, query: str, top_k: int = 3) -> Dict[str, Any]:
        direct = self.match_direct_faq(query)
        if direct:
            return {"mode": "L1_faq_direct", "direct_answer": direct["answer"], "keyword": direct["keyword"], "hits": []}
        if not self.chunks:
            return {"mode": "empty", "direct_answer": None, "hits": []}
        lex = self.lexical_scores(query)
        dense = [0.0] * len(self.chunks)
        used_dense = False
        if self.vectorized and self.embedding_available():
            qv = await self._embed_texts(client, [query])
            if qv:
                dense = [cosine_sim(qv[0], c.get("embedding", [])) for c in self.chunks]
                used_dense = True
        th = float(self.cfg.get("min_score_dense", 0.42)) if used_dense else float(self.cfg.get("min_score", 0.18))
        scored = []
        for i, c in enumerate(self.chunks):
            s = (0.65 * dense[i] + 0.35 * lex[i]) if used_dense else lex[i]
            if s >= th:
                scored.append({"id": c["id"], "chapter": c["chapter"], "section": c["section"],
                               "text": c["text"], "score": round(s, 3)})
        scored.sort(key=lambda x: x["score"], reverse=True)
        return {"mode": "L2_hybrid_bge_m3" if used_dense else "L3_cpu_lexical", "direct_answer": None,
                "hits": scored[:max(1, int(top_k))]}

    # ------------------------------------------------------------ 连播
    def mark_interrupted(self) -> None:
        if self.chunks and 0 < self.cursor < len(self.chunks):
            self.interrupted_by_qa = True

    def jump_to(self, index: int) -> None:
        if not self.chunks:
            self.cursor = 0
            return
        self.cursor = max(0, min(len(self.chunks) - 1, int(index)))
        self.interrupted_by_qa = False
        self.last_board = self._board_for(self.cursor)
        self.save_cursors()

    def peek_beat(self) -> Optional[Dict[str, Any]]:
        """取下一段讲稿但不推进游标；播报成功后再 commit_beat，失败不丢段。"""
        if self.paused or self.building or not self.chunks:
            return None
        idx = self.cursor
        restart = False
        if idx >= len(self.chunks):
            if not self.cfg.get("loop_course", True):
                return None
            idx = 0
            restart = True
        ch = self.chunks[idx]
        return {"idx": idx, "text": ch["text"], "chapter": ch["chapter"], "section": ch["section"],
                "resumed": bool(self.interrupted_by_qa and not restart), "course_restart": restart,
                "board": self._board_for(idx)}

    def commit_beat(self, beat: Dict[str, Any]) -> None:
        self.cursor = beat["idx"] + 1
        self.interrupted_by_qa = False
        self.last_board = beat["board"]
        self.doc_cursors[self.active_doc] = self.cursor

    def snapshot(self) -> Dict[str, Any]:
        return {
            "paused": self.paused,
            "building": self.building,
            "active_doc": self.active_doc,
            "docs": self.list_docs(),
            "cursor": self.cursor,
            "total_chunks": len(self.chunks),
            "total_faqs": len(self.faqs),
            "vectorized": self.vectorized,
            "chapters": [{"title": c["title"], "start_idx": c["start_idx"]} for c in self.chapters],
            "board": self.last_board,
            "last_error": self.last_error,
        }
