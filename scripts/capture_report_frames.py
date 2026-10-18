"""用本机 Chrome 无头模式把离线报告截成高清帧，并写出 GIF 清单。

这一步以前靠人工在浏览器里翻页截图，既不可复现，也会因为拿不到浏览器画面而卡住。
现在改成命令驱动：概览帧截完整页头，其余帧各自生成一份只显示目标区块的临时页面，
因此**不依赖滚动定位**（无头截屏在锚点滚动后不会重绘，实测会得到空白画面）；
截图按 2 倍像素密度输出，交给 `assemble_report_gif.py` 编码。

只读已落盘的报告 HTML，不调用模型、不访问 GitHub、不含凭证。
"""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

# 报告 article 里的区块按 evidence-N 编号（第 N 个 h2），对应 sections.py 的顺序。
TOUR = [
    ("01-overview", None, 3600, "交付概览"),
    ("02-changes", 5, 3200, "候选改动"),
    ("03-checks", 6, 3000, "独立复核"),
    ("04-review", 7, 3400, "审核与返工"),
    ("05-approval", 8, 3000, "人工决定"),
    ("06-remote", 9, 3400, "远端产物"),
]

ISOLATION = """<style id="capture-isolation">
.report-content > .handoff {{ display: none !important; }}
article > * {{ display: none !important; }}
article > h2:nth-of-type({index}), article > h2:nth-of-type({index}) ~ * {{ display: block !important; }}
article > h2:nth-of-type({index}) ~ h2,
article > h2:nth-of-type({index}) ~ h2 ~ * {{ display: none !important; }}
article pre, article .diff {{ max-height: {diff_cap}px !important; overflow: hidden !important; }}
.evidence-tabs a[href="#evidence-{index}"] {{ border-bottom: 2px solid #fd8c73; font-weight: 600; }}
</style>"""


def find_chrome(explicit: str | None) -> Path:
    for candidate in [explicit, *CHROME_CANDIDATES]:
        if candidate and Path(candidate).exists():
            return Path(candidate)
    raise SystemExit("chrome_not_found: 用 --chrome 指定浏览器可执行文件")


def capture(chrome: Path, url: str, destination: Path, *, width: int, height: int, scale: int,
            timeout: float = 60.0) -> Path:
    """截一张图；`--screenshot` 是异步落盘，这里等文件尺寸稳定后再返回。

    路径必须是绝对路径，相对路径在部分 Chrome 版本上会被静默忽略。
    """

    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    result = subprocess.run([str(chrome), "--headless", "--disable-gpu", "--hide-scrollbars",
                             "--no-first-run", "--no-default-browser-check",
                             f"--force-device-scale-factor={scale}",
                             f"--window-size={width},{height}",
                             f"--screenshot={destination}", url],
                            check=False, capture_output=True, timeout=timeout)
    deadline = time.monotonic() + 15
    size = -1
    while time.monotonic() < deadline:
        if destination.exists():
            current = destination.stat().st_size
            if current > 0 and current == size:
                return destination
            size = current
        time.sleep(0.2)
    detail = result.stderr.decode("utf-8", errors="replace").strip()[-300:]
    raise SystemExit(f"screenshot_timeout: {destination.name} {detail}")


def isolated_page(html: str, index: int, diff_cap: int) -> str:
    style = ISOLATION.format(index=index, diff_cap=diff_cap)
    return html.replace("</head>", style + "</head>", 1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--html", type=Path, help="已生成的报告 HTML；默认先用 CLI 生成")
    parser.add_argument("--work", type=Path, default=Path(".local/demo/frames"))
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=880)
    parser.add_argument("--scale", type=int, default=2)
    parser.add_argument("--scale-to", type=int, default=1200, help="GIF 帧宽度")
    parser.add_argument("--diff-cap", type=int, default=150, help="特写帧里差异块的最大高度")
    parser.add_argument("--chrome")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    html_path = args.html or (args.work / "generated.html")
    if not html_path.exists():
        subprocess.run([str(root / ".venv" / "Scripts" / "repopilot.exe"), "report", args.run_id,
                        "--html", str(html_path)], check=True, capture_output=True)
    html = html_path.resolve().read_text(encoding="utf-8")

    if args.work.exists():
        shutil.rmtree(args.work)
    work = args.work.resolve()
    work.mkdir(parents=True)
    plain = work / "report.html"
    plain.write_text(html, encoding="utf-8")
    chrome = find_chrome(args.chrome)

    frames = []
    for name, index, duration, label in TOUR:
        if index is None:
            page = plain
        else:
            page = work / f"{name}.html"
            page.write_text(isolated_page(html, index, args.diff_cap), encoding="utf-8")
        target = work / f"{name}.png"
        capture(chrome, f"file:///{page.as_posix()}", target,
                width=args.width, height=args.height, scale=args.scale)
        frames.append({"file": target.name, "duration_ms": duration, "block": index, "label": label})
        print(json.dumps({"frame": name, "block": index, "label": label}, ensure_ascii=False))

    from PIL import Image  # 只有本工具需要 Pillow；运行时产品不依赖它。

    with Image.open(work / frames[0]["file"]) as sample:
        width, height = sample.size
    manifest = {
        "description": f"运行 {args.run_id} 的离线报告浏览；2 倍像素密度截图后缩放到 {args.scale_to}px",
        "run_id": args.run_id,
        "crop": [0, 0, width, height],
        "scale_to": args.scale_to,
        "trim": True,
        "frames": frames,
    }
    (work / "frames.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"manifest": str(work / "frames.json"), "native": [width, height],
                      "scale_to": args.scale_to, "frames": len(frames)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
