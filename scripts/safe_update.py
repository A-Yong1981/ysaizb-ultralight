# -*- coding: utf-8 -*-
"""
safe_update.py —— 一键安全更新（06_update.bat 调用）

白话说明：以前直接 git pull 经常失败，因为桌面程序每次启动都会改写 env.bat 和 OBS配置/ 里的文件，
git 发现“本地有改动”就拒绝更新，结果重新打包出来的 EXE 还是旧代码。这个脚本按下面顺序做：

  1. 检查桌面程序是否还开着（开着会导致 EXE 打包失败）
  2. 把你在 env.bat 里的个人设置（主播、声线、引擎参数……）搬到 env.local.bat（不进 git，永远不冲突）
  3. 拉取最新代码清单（git fetch）
  4. 你自己改过的文件（比如自带讲稿）先备份到 git stash；程序自动生成的 OBS配置/ 和 .bat 不备份（会自动重新生成）
  5. 把代码切到最新版（git reset --hard 到远端分支；avatars/ models/ config.json 等未纳入 git 的文件完全不动）
  6. 把第 4 步备份的个人改动放回来
  7. （加 --build 时）重新打包桌面 EXE，并显示新版本号

用法：python scripts/safe_update.py [--build] [--force]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import re
import subprocess
import sys
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
HUB_DIR = ROOT / "ai-live-hub"
EXE_NAME = "泳昇Ultralight直播平台.exe"
GENERATED_PREFIXES = ("OBS配置/",)     # 每次启动按代码重新生成，不需要备份


def say(msg: str) -> None:
    print(msg, flush=True)


def git(*args: str, check: bool = True, quiet: bool = False) -> subprocess.CompletedProcess:
    cmd = ["git", "-c", "core.quotepath=false", *args]
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败：\n{(r.stderr or r.stdout).strip()}")
    if not quiet and r.stdout.strip() and args[0] not in ("show", "diff", "rev-parse", "rev-list", "status"):
        say(r.stdout.strip())
    return r


def exe_running() -> bool:
    if sys.platform != "win32":
        return False
    try:
        r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {EXE_NAME}", "/FO", "CSV", "/NH"], capture_output=True)
        out = (r.stdout or b"").decode("utf-8", errors="replace") + (r.stdout or b"").decode("mbcs", errors="replace")
        # 只靠文件名里的英文部分判断，不受控制台编码影响；没运行时 tasklist 只输出一行“没有任务”提示
        return "ultralight" in out.lower()
    except Exception:
        return False


def read_version() -> str:
    try:
        m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', (HUB_DIR / "app_version.py").read_text(encoding="utf-8"))
        return m.group(1) if m else "未知"
    except OSError:
        return "未知"


def migrate_env_customizations() -> List[str]:
    """把 env.bat 里与当前提交不同的 set "K=V"（你的个人设置）写入 env.local.bat。"""
    sys.path.insert(0, str(HUB_DIR))
    import config_guide as cg  # noqa: E402

    env_p = ROOT / "env.bat"
    head = git("show", "HEAD:env.bat", check=False, quiet=True)
    if head.returncode != 0 or not env_p.is_file():
        return []
    set_re = re.compile(r'^\s*set\s+"([A-Za-z0-9_]+)=(.*)"\s*$', re.I)
    base = {m.group(1): m.group(2) for m in (set_re.match(l) for l in head.stdout.splitlines()) if m}
    cur = {m.group(1): m.group(2) for m in (set_re.match(l) for l in env_p.read_text(encoding="utf-8", errors="ignore").splitlines()) if m}
    diff = {k: v for k, v in cur.items() if base.get(k) != v}
    moved: List[str] = []
    for k, v in diff.items():
        try:
            cg.update_env_bat(env_p, {k: v})
            moved.append(f"{k}={v}")
        except ValueError as exc:
            say(f"[提示] 个人设置 {k} 含特殊字符没有自动搬运，请更新后在傻瓜配置里重新设置：{exc}")
    return moved


def main() -> int:
    ap = argparse.ArgumentParser(description="一键安全更新到最新版")
    ap.add_argument("--build", action="store_true", help="更新后重新打包桌面 EXE")
    ap.add_argument("--force", action="store_true", help="桌面程序还开着也继续（不推荐）")
    args = ap.parse_args()

    say(f"== 泳昇 Ultralight 一键更新 ==  当前版本：{read_version()}")
    try:
        git("rev-parse", "--show-toplevel", quiet=True)
    except Exception:
        say("[错误] 没找到 git，或当前目录不是 git 仓库。请先安装 Git for Windows，或用 git clone 重新下载项目。")
        return 1

    if exe_running() and not args.force:
        say(f"[请先关闭] 桌面程序 {EXE_NAME} 还在运行（任务栏/托盘里），关闭后再双击 06_update.bat。")
        say("          不关的话新 EXE 打包会失败，看到的还是旧版。")
        return 2

    # 1. 个人设置搬到 env.local.bat
    moved = migrate_env_customizations()
    if moved:
        say("[1/6] 已把你的个人设置搬到 env.local.bat：" + "，".join(moved))
    else:
        say("[1/6] env.bat 没有个人改动需要搬运")

    # 2. 拉取远端
    say("[2/6] 正在从 GitHub 获取最新代码清单...")
    git("fetch", "origin")
    up = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False, quiet=True).stdout.strip() or "origin/main"
    ahead = int((git("rev-list", "--count", f"{up}..HEAD", check=False, quiet=True).stdout or "0").strip() or 0)
    if ahead > 0:
        say(f"[停止] 你本地有 {ahead} 个没推送的提交，为避免丢失，这里不自动更新。请先推送或联系开发者。")
        return 3
    old_head = git("rev-parse", "--short", "HEAD", quiet=True).stdout.strip()
    new_head = git("rev-parse", "--short", up, quiet=True).stdout.strip()

    # 3. 备份你自己改过、且和新版不同的文件（跳过 .bat 与自动生成的 OBS配置/）
    names = [n for n in git("diff", "--name-only", "-z", "HEAD", quiet=True).stdout.split("\0") if n]
    to_stash: List[str] = []
    for n in names:
        if n.lower().endswith((".bat", ".cmd")) or n.startswith(GENERATED_PREFIXES):
            continue
        same_as_new = git("diff", "--quiet", up, "--", n, check=False, quiet=True).returncode == 0
        if not same_as_new:
            to_stash.append(n)
    stash_msg = f"safe_update {_dt.datetime.now():%Y-%m-%d %H:%M:%S}"
    if to_stash:
        say(f"[3/6] 备份你改过的 {len(to_stash)} 个文件到 git stash（{stash_msg}）：")
        for n in to_stash[:20]:
            say(f"      - {n}")
        git("stash", "push", "-m", stash_msg, "--", *to_stash)
    else:
        say("[3/6] 没有需要备份的个人改动")

    # 4. 切到最新版（未纳入 git 的 avatars/ models/ config.json 等完全不受影响）
    if old_head == new_head:
        say(f"[4/6] 已经是最新代码（{new_head}），同时修复了本地被改动的生成文件")
    else:
        say(f"[4/6] 更新代码：{old_head} → {new_head}")
    git("reset", "--hard", up)

    # 5. 放回个人改动
    if to_stash:
        r = git("stash", "pop", check=False)
        if r.returncode != 0:
            say("[注意] 你改过的文件和新版有冲突，已保留在 git stash 里没有丢。")
            say(f"       运行 git stash list 能看到「{stash_msg}」；把冲突截图发给开发者处理。")
            return 4
        say("[5/6] 已放回你改过的文件")
    else:
        say("[5/6] 无需放回")

    say(f"[6/6] 更新完成，当前版本：{read_version()}（{new_head}）")
    if args.build:
        say("==> 正在重新打包桌面 EXE（约 1~3 分钟）...")
        rc = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_exe.py")], cwd=str(ROOT)).returncode
        if rc != 0:
            say("[错误] EXE 打包失败，请把上面的报错截图发给开发者。")
            return rc
        say(f"[完成] 新 EXE 已生成：{ROOT / EXE_NAME}")
        say(f"       启动后窗口标题应显示「v{read_version()}」，看到这个就是新版。")
    say("下一步：重新开播（数字人引擎和中控都要重启），OBS 里右键浏览器源 →「刷新」。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
