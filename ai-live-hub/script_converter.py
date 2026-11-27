# -*- coding: utf-8 -*-
"""
script_converter.py —— 长文章 / 网页 / 文档一键拆解为标准直播讲稿
  输入：网页 URL、本地 .txt/.md/.docx、或直接粘贴长文
  另有 import_markdown_document()：把任意普通 Markdown 直接整理成标准讲稿（纯规则、不调大模型、不改写原文）
  输出：kb_data/*.md 标准讲稿（# 章节 + > [板书] 要点 + ## 小节 + 60~95 字口播段 + Q/A 精准问答）
  调用：config.json 里的免费大模型容灾池（按顺序遇错自动换下一个）
两种用法：
  1. WebUI「一键拆稿」（server.py 的 /api/kb/convert 复用本模块）
  2. 命令行：
     python script_converter.py --url https://example.com/a --out kb_data/ai_news.md --style douyin --lang zh
     python script_converter.py --file D:\\资料\\讲义.docx --out kb_data/jiangyi.md --style wechat
     python script_converter.py --file D:\\资料\\笔记.md --out kb_data/biji.md --local   （直接导入，不调大模型）
"""
import argparse
import asyncio
import html
import io
import json
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import httpx

STYLE_GUIDES = {
    "douyin": "抖音风格：节奏明快、开头一句就抓人、大白话接地气，每段可以自然带一点互动感，禁止书面化长句。",
    "bilibili": "B站风格：逻辑清楚、有干货又不枯燥，像懂行的朋友聊天，可以有轻微幽默和生活类比。",
    "wechat": "视频号风格：语气温和、条理清晰、通俗易懂、节奏偏慢，适合中老年观众收听，不用网络梗和英文缩写。",
    "youtube": "YouTube 风格：结构完整、观点鲜明、口语自然，兼顾信息密度与可听性。",
    "tiktok": "TikTok style: energetic, punchy, short spoken sentences, friendly hooks, plain words for beginners.",
}
LANG_RULES = {
    "zh": "全部使用自然流畅的中文口语。",
    "en": "Write the entire script, board bullet points and Q/A in natural spoken English.",
    "bilingual": "以中文口语为主，关键术语第一次出现时附上英文，Q/A 中英文各提供一部分。",
}
Progress = Optional[Callable[[str], Awaitable[None]]]


def _is_placeholder(key: Any) -> bool:
    k = str(key or "").strip()
    return (not k) or k.startswith("填入")


# ---------------------------------------------------------------- 原文提取
class _SimpleHTMLTextExtractor(HTMLParser):
    """未安装 BeautifulSoup 时的纯标准库正文提取兜底。"""
    SKIP = ("script", "style", "noscript", "nav", "footer", "header", "aside", "form")
    BLOCK = ("p", "div", "br", "li", "h1", "h2", "h3", "h4", "article", "section", "tr")

    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self._chunks: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag in self.BLOCK:
            self._chunks.append("\n")

    def handle_data(self, data):
        if not self._skip and data.strip():
            self._chunks.append(data.strip())

    def get_text(self) -> str:
        raw = " ".join(self._chunks)
        return re.sub(r"\n\s*\n+", "\n\n", raw).strip()


def _decode_html(resp: httpx.Response) -> str:
    raw = resp.content
    encs: List[str] = []
    ct = resp.headers.get("content-type", "")
    m = re.search(r"charset=([\w\-]+)", ct, re.I)
    if m:
        encs.append(m.group(1))
    m = re.search(rb"<meta[^>]+charset=[\"']?([\w\-]+)", raw[:4096], re.I)
    if m:
        encs.append(m.group(1).decode("ascii", "ignore"))
    encs += ["utf-8", "gb18030"]
    for enc in encs:
        try:
            return raw.decode("gb18030" if enc.lower() in ("gb2312", "gbk") else enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="ignore")


async def fetch_webpage_text(url: str, timeout: float = 20.0) -> Tuple[str, str]:
    """抓取网页，返回 (标题, 正文纯文本)。需要登录或由 JS 渲染的页面可能抓不到正文。"""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                             "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
               "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        html = _decode_html(resp)
    title = ""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside", "form", "iframe"]):
            tag.decompose()
        main = soup.find("article") or soup.find("main") or soup.body or soup
        lines = [ln.strip() for ln in main.get_text("\n").splitlines()]
        text = "\n".join(ln for ln in lines if len(ln) >= 6)
    except ImportError:
        m = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.I | re.S)
        if m:
            title = re.sub(r"\s+", " ", m.group(1)).strip()
        p = _SimpleHTMLTextExtractor()
        p.feed(html)
        text = p.get_text()
    title = re.split(r"\s*[|_\-–—]\s*", title)[0].strip() if title else ""
    return title, text


def extract_file_bytes(filename: str, raw_bytes: bytes) -> str:
    """从 .txt / .md / .docx 提取纯文本。"""
    ext = Path(filename).suffix.lower()
    if ext == ".docx":
        try:
            from docx import Document
        except ImportError as exc:
            raise RuntimeError("解析 Word (.docx) 需要 python-docx：pip install python-docx") from exc
        doc = Document(io.BytesIO(raw_bytes))
        paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    paras.append("，".join(cells))
        return "\n\n".join(paras)
    if ext == ".doc":
        raise RuntimeError("不支持旧版 .doc，请在 Word 里另存为 .docx 后再上传")
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw_bytes.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("utf-8", errors="ignore")


def split_source_segments(text: str, max_chars: int = 3200) -> List[str]:
    """按自然段把长文切块，防止超出免费 7B 模型的安全输出窗口。"""
    clean = re.sub(r"\r\n?", "\n", text).strip()
    if len(clean) <= max_chars:
        return [clean]
    paras = [p.strip() for p in re.split(r"\n+", clean) if p.strip()]
    segs: List[str] = []
    buf = ""
    for p in paras:
        if len(buf) + len(p) + 1 <= max_chars:
            buf = f"{buf}\n{p}" if buf else p
            continue
        if buf:
            segs.append(buf)
        while len(p) > max_chars:
            segs.append(p[:max_chars])
            p = p[max_chars:]
        buf = p
    if buf:
        segs.append(buf)
    return segs


# ---------------------------------------------------------------- LLM
async def _call_free_llm(client: httpx.AsyncClient, providers: List[Dict[str, Any]], system_prompt: str,
                         user_prompt: str, max_tokens: int = 2200) -> Tuple[str, str]:
    valid = [p for p in providers if p.get("enabled") and not _is_placeholder(p.get("api_key"))]
    if not valid:
        raise RuntimeError("config.json 里没有可用的大模型 Key（llm_providers 至少填一个）")
    last_err = ""
    for prov in valid:
        url = prov["base_url"].rstrip("/") + "/chat/completions"
        payload = {"model": prov["model"], "temperature": 0.4, "max_tokens": max_tokens,
                   "messages": [{"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_prompt}]}
        if isinstance(prov.get("extra_body"), dict):
            payload.update(prov["extra_body"])          # 例：DeepSeek {"thinking": {"type": "disabled"}}
        try:
            resp = await client.post(url, json=payload, timeout=max(60.0, float(prov.get("timeout", 12))),
                                     headers={"Authorization": f"Bearer {prov['api_key']}",
                                              "Content-Type": "application/json"})
            if resp.status_code == 200:
                content = (resp.json()["choices"][0]["message"].get("content") or "").strip()
                content = re.sub(r"^```(?:markdown|md)?\s*", "", content, flags=re.I)
                content = re.sub(r"\s*```\s*$", "", content)
                if content.strip():
                    return content.strip(), prov["name"]
                last_err = f"{prov['name']} 返回空内容"
            else:
                last_err = f"{prov['name']} HTTP {resp.status_code}: {resp.text[:160]}"
        except Exception as exc:
            last_err = f"{prov['name']} 异常: {exc}"
    raise RuntimeError(f"所有大模型节点都调用失败：{last_err}")


# ---------------------------------------------------------------- 规整
_CHAPTER_PREFIX = re.compile(
    r"^(第\s*[0-9一二三四五六七八九十百]+\s*[章节讲部分]+|chapter\s*\d+|part\s*\d+)\s*[:：、.\-]?\s*", re.I)


def _is_faq_header(title: str) -> bool:
    return any(w in title for w in ("常见高频问答", "精准直出", "FAQ", "常见问题"))


def normalize_script(md: str, lang: str = "zh") -> str:
    """清掉模型常见的格式偏差：列表符号、加粗、板书写法不统一、章节编号重复。"""
    out: List[str] = []
    n = 0
    for raw in md.replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        s = line.strip()
        if s.startswith("```") or re.fullmatch(r"[-=*_]{3,}", s or "x"):
            continue
        if re.match(r"^#\s+", s):
            title = re.sub(r"^#\s+", "", s).strip()
            if _is_faq_header(title):
                out += ["", "# 常见高频问答（精准直出库）"]
                continue
            title = _CHAPTER_PREFIX.sub("", title).strip() or "专题讲解"
            n += 1
            out += ["", f"# 第{n}章 {title}" if lang != "en" else f"# Chapter {n}: {title}"]
            continue
        if re.match(r"^#{2,6}\s+", s):
            out += ["", "## " + re.sub(r"^#{2,6}\s+", "", s).strip()]
            continue
        if re.match(r"^>\s*(?:\[背景\]|【背景】)", s):
            out.append(s)
            continue
        if s.startswith(">"):
            pt = re.sub(r"\*\*|__", "", s)
            pt = re.sub(r"^>\s*(\[板书\]|【板书】|板书\s*[:：]|\[board\])?\s*", "", pt, flags=re.I).strip()
            if pt:
                out.append(f"> [板书] {pt}")
            continue
        m = re.match(r"^(?:Q|q|问)\s*\d*\s*[:：]\s*(.*)$", s)
        if m:
            kws = [k.strip() for k in re.split(r"[|｜/、,，]", m.group(1)) if k.strip()]
            out += ["", "Q: " + "|".join(kws)]
            continue
        m = re.match(r"^(?:A|a|答)\s*\d*\s*[:：]\s*(.*)$", s)
        if m:
            out.append("A: " + re.sub(r"\*\*|__", "", m.group(1)).strip())
            continue
        s = re.sub(r"^\s*(?:[-*+•]|\d+[.、)])\s+", "", s)
        s = re.sub(r"\*\*|__|`", "", s)
        out.append(s)
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text + "\n"


async def convert_article_to_live_script(client: httpx.AsyncClient, providers: List[Dict[str, Any]], raw_text: str,
                                         title: str = "", style: str = "douyin", language: str = "zh",
                                         faq_count: int = 6, audience: str = "", max_chars: int = 30000,
                                         progress: Progress = None, source: str = "") -> Dict[str, Any]:
    """核心入口：把任意长文改写成 ai-live-hub 标准讲稿（章节 + 板书 + 口播段 + 精准 Q/A）。"""
    clean = re.sub(r"[ \t\u3000]+", " ", raw_text or "").strip()
    if len(clean) < 40:
        raise ValueError("原文内容太短（少于 40 字），没法拆成直播讲稿；如果是网页，可能需要登录或由脚本渲染，建议复制正文粘贴")
    truncated = len(clean) > max_chars
    clean = clean[:max_chars]
    style_desc = STYLE_GUIDES.get(style, STYLE_GUIDES["douyin"])
    lang_rule = LANG_RULES.get(language, LANG_RULES["zh"])
    audience_rule = f"目标听众：{audience}，第一次出现专业词时要用大白话解释。\n" if audience else ""
    segments = split_source_segments(clean, 3200)

    chapter_sys = (
        "你是知识类直播的金牌编导。请把用户给的原文改写成 AI 数字人口播用的 Markdown 直播讲稿。\n"
        f"风格要求：{style_desc}\n语言要求：{lang_rule}\n{audience_rule}"
        "【严格格式】\n"
        "1. 按主题拆成 1 到 3 个一级章节，格式：# 第X章 章节标题\n"
        "2. 每个一级章节标题下面紧跟 2 条屏幕板书要点，格式严格为：> [板书] 12 到 22 字的核心要点\n"
        "3. 每个章节下拆 1 到 3 个二级小节：## 小节标题\n"
        "4. 正文写成口播自然段，每段 60 到 95 字，纯口语叙述，段与段之间空一行；"
        "禁止列表符号、加粗星号、括号舞台动作、表情符号、表格、链接，保证语音可以直接流畅念出。\n"
        "5. 严禁编造原文中没有的数据、价格、日期或事实；原文里的数字保持原样。\n"
        "6. 需要自称时只用“我”，不要写主播名字；不要出现任何平台名称、二维码、联系方式、购买或报名引导。\n"
        "7. 本直播间只面向成年观众（家长与中老年朋友）：禁止直接对小朋友喊话，禁止引导未成年人观看、点赞、评论或打赏；"
        "涉及孩子的内容一律写成“家长如何引导和陪伴孩子”；不推荐任何付费课程、训练营或收费 App；不鼓励代写作业。\n"
        "8. 只输出 Markdown 正文，不要任何开场说明或结束语。"
    )
    blocks: List[str] = []
    used = ""
    for idx, seg in enumerate(segments, start=1):
        if progress:
            await progress(f"拆稿进度：正在改写第 {idx}/{len(segments)} 部分原文（{len(seg)} 字）...")
        u = (f"文章标题：{title or '专题知识讲解'}\n"
             f"这是全文的第 {idx}/{len(segments)} 部分，请只改写这一部分：\n\n{seg}")
        md, used = await _call_free_llm(client, providers, chapter_sys, u, max_tokens=2200)
        blocks.append(md)
    body = normalize_script("\n\n".join(blocks), language)
    if "# " not in body:
        body = normalize_script(f"# {title or '专题讲解'}\n\n{body}", language)

    if progress:
        await progress(f"拆稿进度：正在提炼 {faq_count} 组高频精准问答...")
    faq_sys = (
        "你是知识直播间的运营专家。请根据讲稿提炼观众在公屏最可能追问的高频问题，输出成精准直出词条。\n"
        f"语言要求：{lang_rule}\n{audience_rule}"
        "【严格格式】只输出下面的格式，不要任何其他说明：\n"
        "# 常见高频问答（精准直出库）\n\n"
        "Q: 触发词1|触发词2|触发词3\n"
        "A: 35 到 65 字的纯口语标准回答\n\n"
        "触发词要求：是观众口语问法里的关键片段，每个 3 到 10 个字，例如“会不会犯错”“怎么防骗”；"
        "不要用“AI”“是什么”这种过于宽泛的词。回答严禁编造讲稿里没有的事实，不出现 Markdown 符号和表情。"
        "回答只面向成年观众，不直接对小朋友喊话，不推荐付费课程、训练营或收费 App。"
    )
    faq_user = f"请提炼 {faq_count} 组问答：\n\n{body[:5000]}"
    faq_md, _ = await _call_free_llm(client, providers, faq_sys, faq_user, max_tokens=1200)
    faq_md = normalize_script(faq_md, language)
    if "常见高频问答" not in faq_md:
        faq_md = "# 常见高频问答（精准直出库）\n\n" + faq_md
    header = (f"<!-- 由 script_converter 自动生成 {time.strftime('%Y-%m-%d %H:%M')} | 风格 {style} | 语言 {language}"
              f"{' | 来源 ' + source if source else ''}{' | 原文过长已截取前 ' + str(max_chars) + ' 字' if truncated else ''} -->\n"
              "<!-- 播出前请人工通读一遍：核对事实、删改不合适的说法。改完在 WebUI 点「热重载 JSON」即可重建索引 -->\n\n")
    final_md = header + body.strip() + "\n\n" + faq_md.strip() + "\n"
    return {"markdown": final_md, "segments_processed": len(segments), "provider": used,
            "char_count": len(final_md), "truncated": truncated}


# ---------------------------------------------------------------- Markdown 直接导入（纯规则，不调大模型、不花额度）
MINOR_PHRASES = ("小朋友们好", "小朋友记住", "小朋友请注意", "小朋友们记住", "小朋友，", "小朋友可不能",
                 "小朋友们，", "小朋友们注意", "同学们好")
IMPORT_EXTS = (".md", ".markdown", ".txt")
_IMP_CJK = re.compile("[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_IMP_FAQ_WORDS = ("常见高频问答", "精准直出", "常见问题", "问答", "FAQ", "Q&A")
_IMP_EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF\uFE0F\u200d\u20e3]")
_IMP_BULLET = re.compile(r"^(?:[-*+•·▪●○◆]\s+|\d{1,3}[.)]\s+|\d{1,3}[、．]\s*|[（(]\d{1,3}[)）]\s*|[①-⑳]\s*)(?:\[[ xX]\]\s*)?")
_IMP_BOARD = re.compile(r"^(?:\[板书\]|【板书】|板书\s*[:：]|\[board\])\s*", re.I)
_IMP_Q = re.compile(r"^(?:Q|q|问)\s*\d*\s*[:：]\s*(.+)$")
_IMP_A = re.compile(r"^(?:A|a|答)\s*\d*\s*[:：]\s*(.*)$")
_IMP_CN_CHAPTER = re.compile(r"^(?:第\s*[0-9一二三四五六七八九十百]+\s*[章讲篇]|第\s*[0-9一二三四五六七八九十百]+\s*部分|[一二三四五六七八九十]+\s*[、.．])\s*\S")
_IMP_CN_SECTION = re.compile(r"^(?:第\s*[0-9一二三四五六七八九十百]+\s*节|[（(][一二三四五六七八九十]+[)）])\s*\S")
_IMP_TABLE_SEP = re.compile(r"^\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)+\|?$|^\|\s*:?-{2,}:?\s*\|$")
_IMP_END = "。！？!?.；;…：:”\"）)」』"


def _imp_inline(text: str, stats: Optional[Dict[str, int]] = None) -> str:
    """去掉 Markdown 行内语法，只留能念出来的文字（链接留文字、去网址；图片、代码标记、表情去掉）。"""
    st = stats if stats is not None else {}
    t = str(text or "")
    st["images"] = st.get("images", 0) + len(re.findall(r"!\[[^\]]*\]\([^)]*\)", t))
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)|!\[[^\]]*\]\[[^\]]*\]", "", t)
    st["links"] = st.get("links", 0) + len(re.findall(r"\[[^\]]+\]\([^)]*\)", t))
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"\[([^\]]+)\]\[[^\]]*\]", r"\1", t)
    t = re.sub(r"\[\^[^\]]+\]", "", t)
    t = re.sub(r"<(?:https?://|mailto:)[^>]+>", "\x00", t)
    t = re.sub(r"(?:https?://|www\.)[^\s，。！？、；）)\]」]+", "\x00", t)
    t = re.sub(r"(?:详细说明见|详情见|详见|参见|请见|原文见|见|地址|链接|网址|来源)?\s*[:：]?\s*\x00", "", t).replace("\x00", "")
    t = re.sub(r"</?[a-zA-Z][^>]*>", "", t)
    t = html.unescape(t)
    t = re.sub(r"`+([^`]+)`+", r"\1", t)
    t = re.sub(r"(\*\*|__)(.+?)\1", r"\2", t)
    t = re.sub(r"(?<![*\\A-Za-z0-9])\*(?![\s*])([^*\n]+?)(?<![\s\\])\*(?![*A-Za-z0-9])", r"\1", t)
    t = re.sub(r"(?<![_\\A-Za-z0-9])_(?![\s_])([^_\n]+?)(?<![\s\\])_(?![_A-Za-z0-9])", r"\1", t)
    t = re.sub(r"~~(.+?)~~", r"\1", t)
    t = re.sub(r"==(.+?)==", r"\1", t)
    t = re.sub(r"\\([\\`*_{}\[\]()#+\-.!>|~])", r"\1", t)
    t = t.replace("**", "").replace("__", "")
    t = _IMP_EMOJI.sub("", t)
    t = re.sub(r"[ \t\u3000]+", " ", t)
    t = re.sub(r"(?<=[\u3400-\u9fff，。！？；：、（）“”])\s+(?=[\u3400-\u9fff，。！？；：、（）“”])", "", t)
    t = re.sub(r"\s+(?=[，。！？；：、）])", "", t)
    return t.strip()


def _imp_join(pieces: List[str]) -> str:
    out = ""
    for seg in pieces:
        seg = seg.strip()
        if not seg:
            continue
        if out and not _IMP_CJK.search(out[-1:]) and not _IMP_CJK.search(seg[:1]):
            out += " "
        out += seg
    return out


def _imp_end(s: str) -> str:
    s = s.strip()
    if not s or s[-1] in _IMP_END:
        return s
    return s + ("." if not _IMP_CJK.search(s) else "。")


def _imp_is_faq_title(s: str) -> bool:
    t = s.replace(" ", "")
    return any(w.lower() in t.lower() for w in _IMP_FAQ_WORDS) or re.search(r"\bQ\s*&?\s*A\b", s) is not None


def _imp_looks_like_question(s: str) -> bool:
    return bool(re.search(r"[？?]\s*$", s) or re.search(r"(吗|么|怎么|怎样|如何|为什么|为啥|是什么|什么|哪|能不能|可不可以|要不要)", s))


def _imp_format_q(q: str) -> str:
    q = q.strip()
    if "|" in q or "｜" in q:
        return q
    if len(q) <= 12 and not re.search(r"[？?]$", q):
        return q                                    # 短的触发词列表（用 、/，分隔）保持原样
    return re.sub(r"[、,，/]", "", q).rstrip("？?。.!！ ")   # 整句问题：合成一个触发词，避免被拆成「我该管吗」这种泛词


def import_markdown_document(raw_text: str, title: str = "", source: str = "") -> Dict[str, Any]:
    """把任意 Markdown（或纯文本）整理成 kb_engine 标准讲稿，原文措辞不改。
    规则：最上层标题 → # 章节（只有一个最上层标题时视为文档标题，下一级才是章节）；更深的标题 → ## 小节；
    列表 / 表格 → 口播句子；> [板书] 保留，其它引用当正文；Q:/A: 或 FAQ 区的问句标题 → 精准问答；
    没有板书的章节自动用小节标题 / 列表要点 / 首句补板书；图片、代码块、网址、HTML、表情去掉。
    返回 {markdown, title, stats, warnings, minor_hits}；minor_hits 非空说明含直接对小朋友喊话的句子，调用方应拒绝导入。"""
    stats: Dict[str, int] = {"images": 0, "links": 0, "code_blocks": 0, "tables": 0, "list_items": 0, "auto_points": 0}
    warnings: List[str] = []
    fm_title = False
    text = str(raw_text or "").replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    fm = re.match(r"^\s*(---|\+\+\+)[ \t]*\n(.*?)\n\1[ \t]*(?:\n|$)", text, re.S)
    if fm:
        mt = re.search(r"^title\s*[:=]\s*[\"']?(.+?)[\"']?\s*$", fm.group(2), re.M | re.I)
        if mt and mt.group(1).strip():
            title = mt.group(1).strip()
            fm_title = True
        text = text[fm.end():]
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", text, flags=re.S | re.I)

    def _drop_fence(m: "re.Match[str]") -> str:
        stats["code_blocks"] += 1
        return "\n"
    text = re.sub(r"^[ \t]*(```|~~~)[^\n]*\n.*?^[ \t]*\1[ \t]*$", _drop_fence, text, flags=re.S | re.M)
    lines = text.split("\n")

    # ---- 1. 逐行切成记号
    toks: List[Tuple[Any, ...]] = []
    probe_stats: Dict[str, int] = {}
    in_table = False
    i = 0
    while i < len(lines):
        raw = lines[i].rstrip()
        line = raw.strip()
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        i += 1
        if not line:
            in_table = False
            toks.append(("blank",))
            continue
        if re.fullmatch(r"(?:[-*_]\s*){3,}", line):
            toks.append(("blank",))
            continue
        if "|" in line and _IMP_TABLE_SEP.match(line):
            in_table = True
            toks.append(("tsep",))
            continue
        if line.startswith("|") or in_table or ("|" in line and "|" in nxt and _IMP_TABLE_SEP.match(nxt)):
            if not _IMP_Q.match(line):
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                toks.append(("trow", cells))
                continue
        if nxt and re.fullmatch(r"=+", nxt) and not line.startswith(("#", ">", "|")):
            toks.append(("h", 1, line))
            i += 1
            continue
        if nxt and re.fullmatch(r"-{2,}", nxt) and not line.startswith(("#", ">", "|")) and not _IMP_BULLET.match(line):
            toks.append(("h", 2, line))
            i += 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if m:
            toks.append(("h", len(m.group(1)), m.group(2)))
            continue
        if line.startswith(">"):
            body = re.sub(r"^(?:>\s*)+", "", line)
            body = re.sub(r"^\[!\w+\]\s*", "", body)
            bg = re.match(r"^(?:\[背景\]|【背景】)\s*(.+)$", body)
            if bg:
                toks.append(("bg", bg[1].strip()))
                continue
            probe = _imp_inline(body, probe_stats)
            if _IMP_BOARD.match(probe):
                toks.append(("board", _IMP_BOARD.sub("", probe)))
            elif body:
                toks.append(("p", body))
            else:
                toks.append(("blank",))
            continue
        bm = _IMP_BULLET.match(line)
        body = line[bm.end():] if bm else line
        probe = _imp_inline(body, probe_stats)
        mq, ma = _IMP_Q.match(probe), _IMP_A.match(probe)
        if mq:
            toks.append(("q", mq.group(1)))
        elif ma:
            toks.append(("a", ma.group(1)))
        elif bm:
            toks.append(("li", body))
        else:
            toks.append(("p", line))

    # ---- 2. 没有任何 Markdown 标题时，识别「一、」「第X章」「（一）」这类中文标题
    had_md_heading = any(t[0] == "h" for t in toks)
    if not had_md_heading:
        for k, t in enumerate(toks):
            if t[0] != "p":
                continue
            s = _imp_inline(t[1], probe_stats)
            if len(s) <= 30 and not re.search(r"[。！？!?；;]$", s):
                if _IMP_CN_CHAPTER.match(s):
                    toks[k] = ("h", 1, s)
                elif _IMP_CN_SECTION.match(s):
                    toks[k] = ("h", 2, s)
    heads = [(t[1], _imp_inline(t[2], probe_stats)) for t in toks if t[0] == "h"]
    levels = sorted({lv for lv, s in heads if not _imp_is_faq_title(s)})
    chapter_level = levels[0] if levels else 1
    if len(levels) >= 2:
        top = sum(1 for lv, s in heads if lv == levels[0] and not _imp_is_faq_title(s))
        second = sum(1 for lv, s in heads if lv == levels[1] and not _imp_is_faq_title(s))
        if top == 1 and second >= 2:
            chapter_level = levels[1]                # 唯一的最上层标题 = 文档标题

    # ---- 3. 组装章节结构
    doc_title = _imp_inline(title, probe_stats) if title else ""
    chapters: List[Dict[str, Any]] = []
    faqs: List[List[Any]] = []
    cur: Optional[Dict[str, Any]] = None
    para: List[str] = []
    items: List[str] = []
    table_rows: List[List[str]] = []
    table_header: Optional[List[str]] = None
    in_faq = False
    pending: Optional[List[Any]] = None
    ans_open: Optional[List[str]] = None
    ans_until_heading = False
    faq_orphans = 0

    def new_chapter(t: str) -> Dict[str, Any]:
        ch = {"title": t, "points": [], "blocks": [], "sections": [], "items": []}
        chapters.append(ch)
        return ch

    def ensure_chapter() -> Dict[str, Any]:
        nonlocal cur
        if cur is None:
            cur = new_chapter(doc_title or "讲稿正文")
        return cur

    def add_paragraph(txt: str) -> None:
        nonlocal faq_orphans
        txt = re.sub(r"([。！？；，!?;])\s*[。，；,;]+", r"\1", txt.strip()).lstrip("。，；,; ")
        if not re.search(r"\w", txt):
            return
        txt = _imp_end(txt)
        if in_faq:
            faq_orphans += 1
            return
        ensure_chapter()["blocks"].append(("p", txt))

    def flush_para() -> None:
        nonlocal para
        if para:
            add_paragraph(_imp_join(para))
        para = []

    def flush_list() -> None:
        nonlocal items
        if items:
            stats["list_items"] += len(items)
            if not in_faq:
                ensure_chapter()["items"].extend(items)
            add_paragraph(_imp_join([_imp_end(x) for x in items]))
        items = []

    def flush_table() -> None:
        nonlocal table_rows, table_header
        if table_rows or table_header:
            stats["tables"] += 1
            sents = []
            for row in table_rows:
                row = [_imp_inline(c, stats) for c in row]
                if table_header and len(table_header) == len(row):
                    parts = [(f"{h}：{c}" if h else c) for h, c in zip(table_header, row) if c]
                    if row[0] and not in_faq:
                        ensure_chapter()["items"].append(row[0])      # 第一列（如「场景」）适合做板书要点
                else:
                    parts = [c for c in row if c]
                if parts:
                    sents.append(_imp_end("，".join(parts)))
            add_paragraph(_imp_join(sents))
        table_rows, table_header = [], None

    for t in toks:
        kind = t[0]
        if kind != "li":
            flush_list()
        if kind not in ("trow", "tsep"):
            flush_table()
        if kind == "blank":
            flush_para()
            if not ans_until_heading:
                ans_open = None
        elif kind == "h":
            flush_para()
            ans_open, ans_until_heading = None, False
            lv, txt = t[1], _imp_inline(t[2], stats)
            if not txt:
                continue
            if _imp_is_faq_title(txt):
                in_faq = True
                continue
            if lv < chapter_level:
                if not fm_title:
                    doc_title = txt                  # 文内标题优先于文件名
                in_faq, cur = False, None
                continue
            if lv == chapter_level:
                in_faq = False
                cur = new_chapter(txt)
                continue
            if in_faq:
                if _imp_looks_like_question(txt):
                    faqs.append([txt, []])
                    pending, ans_open, ans_until_heading = faqs[-1], faqs[-1][1], True
                continue
            ch = ensure_chapter()
            ch["blocks"].append(("s", txt))
            ch["sections"].append(txt)
        elif kind == "bg":
            ensure_chapter()["background"] = t[1]
        elif kind == "board":
            pt = _imp_inline(t[1], stats)
            if pt and not in_faq:
                ensure_chapter()["points"].append(pt)
        elif kind == "q":
            flush_para()
            faqs.append([_imp_inline(t[1], stats), []])
            pending, ans_open, ans_until_heading = faqs[-1], None, False
        elif kind == "a":
            flush_para()
            if pending is not None and not pending[1]:
                ans_open = pending[1]
                a = _imp_inline(t[1], stats)
                if a:
                    ans_open.append(a)
            else:
                para.append(_imp_inline(t[1], stats))
        elif kind == "li":
            txt = _imp_inline(t[1], stats)
            if ans_open is not None:
                ans_open.append(_imp_end(txt))
            elif txt:
                flush_para()
                items.append(txt)
        elif kind == "trow":
            flush_para()
            table_rows.append(t[1])
        elif kind == "tsep":
            if len(table_rows) == 1 and table_header is None:
                table_header = [_imp_inline(c, stats) for c in table_rows.pop()]
        else:   # p
            txt = _imp_inline(t[1], stats)
            if not txt:
                continue
            if ans_open is not None:
                ans_open.append(txt)
            else:
                para.append(txt)
    flush_list()
    flush_table()
    flush_para()

    # ---- 4. 没有板书的章节自动补
    for ch in chapters:
        if ch["points"]:
            continue
        cands = [s for s in ch["sections"] if len(s) <= 22][:3]
        if not cands:
            cands = [x.rstrip("。.；;：:") for x in ch["items"] if 2 <= len(x) <= 22][:3]
        if not cands:
            first = next((b[1] for b in ch["blocks"] if b[0] == "p"), "")
            head = re.split(r"[。！？!?；;]", first)[0].strip()
            if len(head) > 22:
                head = re.split(r"[，,：:]", head)[0].strip()
            if 4 <= len(head) <= 22:
                cands = [head]
        ch["points"] = cands
        stats["auto_points"] += len(cands)

    # ---- 5. 输出标准讲稿
    out: List[str] = [
        f"<!-- 由 Markdown 直接导入 {time.strftime('%Y-%m-%d %H:%M')}{' | 来源 ' + source if source else ''} | 原文措辞未经大模型改写 -->",
        "<!-- 播出前请通读一遍：核对事实、删改不合适的说法；改完在控制台点「重建索引」 -->",
        "",
    ]
    empty_chapters = 0
    for ch in chapters:
        if not any(b[0] == "p" for b in ch["blocks"]):
            empty_chapters += 1
            continue
        out.append(f"# {ch['title']}")
        if ch.get("background"):
            out.append("> [背景] " + ch["background"])
        out.extend(f"> [板书] {p}" for p in ch["points"])
        out.append("")
        for k, (kind, val) in enumerate(ch["blocks"]):
            if kind == "s":
                if any(b[0] == "p" for b in ch["blocks"][k + 1:k + 2]):
                    out.extend([f"## {val}", ""])
            else:
                out.extend([val, ""])
    good_faqs = [(q, _imp_join(a)) for q, a in faqs if q and _imp_join(a)]
    if good_faqs:
        out.extend(["# 常见高频问答（精准直出库）", ""])
        for q, a in good_faqs:
            out.extend([f"Q: {_imp_format_q(q)}", f"A: {a}", ""])
    md = "\n".join(out).rstrip() + "\n"

    # ---- 6. 统计、提示、成人合规检查
    try:
        from kb_engine import parse_markdown
        chunks, faq_list, chs = parse_markdown(md)
        stats.update(chapters=len(chs), chunks=len(chunks), faqs=len(faq_list))
    except Exception:
        stats.update(chapters=sum(1 for ln in out if ln.startswith("# ")), chunks=-1, faqs=len(good_faqs))
    if not had_md_heading and not any(t[0] == "h" for t in toks):
        warnings.append(f"原文没有标题层级，整篇作为一章「{doc_title or '讲稿正文'}」；想分章节板书，在原文用 # 标出章节")
    if not good_faqs:
        warnings.append("没有问答（Q:/A:），观众提问会走检索 + 大模型；想让常见问题秒答，可在文末加几组 Q:/A:")
    dropped_q = sum(1 for q, a in faqs if not _imp_join(a))
    if dropped_q:
        warnings.append(f"{dropped_q} 个问题没有答案，已跳过")
    if faq_orphans:
        warnings.append(f"问答区有 {faq_orphans} 段文字不是 Q/A 格式，已跳过")
    if empty_chapters:
        warnings.append(f"{empty_chapters} 个标题下面没有正文，已跳过")
    removed = [f"{n} {w}" for n, w in ((stats["images"], "张图片"), (stats["code_blocks"], "个代码块")) if n]
    if removed:
        warnings.append("念不出来的内容已去掉：" + "、".join(removed) + (f"；{stats['links']} 个链接只保留了文字" if stats["links"] else ""))
    elif stats["links"]:
        warnings.append(f"{stats['links']} 个链接只保留了文字，网址不念")
    if stats["tables"]:
        warnings.append(f"{stats['tables']} 个表格已改成逐行口播句子，建议看一眼是否通顺")
    if stats["auto_points"]:
        warnings.append(f"自动补了 {stats['auto_points']} 条板书要点（OBS 上显示），可在讲稿里改成 > [板书] 自己写的要点")
    minor_hits = []
    for ln in md.splitlines():
        for ph in MINOR_PHRASES:
            if ph in ln:
                minor_hits.append({"phrase": ph, "text": ln.strip()[:40]})
    return {"markdown": md, "title": doc_title, "stats": stats, "warnings": warnings, "minor_hits": minor_hits}


# ---------------------------------------------------------------- CLI
async def _cli_main() -> None:
    ap = argparse.ArgumentParser(description="长文章 / 网页一键拆解为 ai-live-hub 标准直播讲稿")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", help="要抓取并拆解的网页链接")
    src.add_argument("--file", help="本地 .txt / .md / .docx 文件路径")
    ap.add_argument("--out", required=True, help="输出 Markdown 路径，如 kb_data/ai_news.md")
    ap.add_argument("--style", default="douyin", choices=list(STYLE_GUIDES.keys()))
    ap.add_argument("--lang", default="zh", choices=list(LANG_RULES.keys()))
    ap.add_argument("--faqs", type=int, default=6)
    ap.add_argument("--local", action="store_true", help="只对 .md/.markdown/.txt 生效：直接整理成讲稿，不调大模型、不改写原文")
    ap.add_argument("--config", default=str(Path(__file__).resolve().parent / "config.json"))
    a = ap.parse_args()
    cfg = json.loads(Path(a.config).read_text(encoding="utf-8"))
    kb = cfg.get("knowledge_base", {})
    if a.local:
        if not a.file or Path(a.file).suffix.lower() not in IMPORT_EXTS:
            raise SystemExit("[Converter] --local 只支持 --file 指向 .md / .markdown / .txt")
        fp = Path(a.file)
        imp = import_markdown_document(extract_file_bytes(fp.name, fp.read_bytes()), title=fp.stem, source=fp.name)
        if imp["minor_hits"]:
            raise SystemExit(f"[Converter] 没导入：含直接对小朋友喊话的句子「{imp['minor_hits'][0]['text']}」，请改成对家长说的话")
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(imp["markdown"], encoding="utf-8")
        st = imp["stats"]
        print(f"[Converter] 已导入 {out}：{st['chapters']} 章 / {st['chunks']} 段 / 问答 {st['faqs']} 条")
        for w in imp["warnings"]:
            print("  -", w)
        return
    if a.url:
        print(f"[Converter] 正在抓取网页: {a.url}")
        title, text = await fetch_webpage_text(a.url)
        source = a.url
    else:
        fp = Path(a.file)
        title, text, source = fp.stem, extract_file_bytes(fp.name, fp.read_bytes()), fp.name
    print(f"[Converter] 原文提取完成（{len(text)} 字），开始调用免费大模型拆解...")

    async def _p(msg: str) -> None:
        print("[Converter]", msg)

    async with httpx.AsyncClient() as client:
        res = await convert_article_to_live_script(
            client, cfg.get("llm_providers", []), text, title=title, style=a.style, language=a.lang,
            faq_count=a.faqs, audience=kb.get("converter_audience", ""),
            max_chars=int(kb.get("converter_max_chars", 30000)), progress=_p, source=source)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(res["markdown"], encoding="utf-8")
    print(f"[Converter] 完成！节点 {res['provider']}，已保存 {out}（{res['char_count']} 字）")


if __name__ == "__main__":
    asyncio.run(_cli_main())
