"""用本机 Chrome 无头模式把离线报告截成高清帧，并写出 GIF 清单。

概览帧截页头，其余帧按章节标题定位并生成单独的临时页面，避免章节增删后截错内容。
截图按 2 倍像素密度输出，交给 `assemble_report_gif.py` 编码；只覆盖本工具的命名输出，
不会清空工作目录。

只读已落盘的报告 HTML，不调用模型、不访问 GitHub、不含凭证。
"""

import argparse
from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
import sys
import time

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

# 标题匹配后再解析当前 evidence-N；不把展示脚本绑定到某一版报告的章节序号。
TOUR = [
    ("01-overview", None, 3600, "交付概览"),
    ("02-changes", "候选改动", 3200, "候选改动"),
    ("03-checks", "独立复核", 3000, "独立复核"),
    ("04-review", "审核与返工", 3400, "审核与返工"),
    ("05-approval", "人工决定", 3000, "人工决定"),
    ("06-remote", "远端产物", 3000, "远端产物"),
    ("07-merge", "负责人批准后的合并", 4000, "原生批准与合并确认"),
]


class EvidenceHeadings(HTMLParser):
    """只索引报告已有的 evidence 编号，不执行 HTML 或脚本。"""

    def __init__(self):
        super().__init__()
        self.current = None
        self.headings = {}

    def handle_starttag(self, tag, attrs):
        identifier = dict(attrs).get("id", "")
        if tag == "h2" and identifier.startswith("evidence-") and identifier[9:].isdigit():
            self.current = (int(identifier[9:]), [])

    def handle_data(self, data):
        if self.current is not None:
            self.current[1].append(data)

    def handle_endtag(self, tag):
        if tag == "h2" and self.current is not None:
            index, parts = self.current
            self.headings["".join(parts).strip()] = index
            self.current = None

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
        subprocess.run([sys.executable, "-m", "repopilot.cli", "report", args.run_id,
                        "--html", str(html_path)], check=True, capture_output=True)
    html = html_path.resolve().read_text(encoding="utf-8")
    headings = EvidenceHeadings()
    headings.feed(html)
    missing = [title for _, title, _, _ in TOUR if title and title not in headings.headings]
    if missing:
        raise SystemExit("report_sections_missing: " + ", ".join(missing))

    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    plain = work / "report.html"
    plain.write_text(html, encoding="utf-8")
    chrome = find_chrome(args.chrome)

    frames = []
    for name, title, duration, label in TOUR:
        index = headings.headings[title] if title else None
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
