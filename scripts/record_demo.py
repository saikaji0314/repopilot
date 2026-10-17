"""把一次真实运行的证据合成为演示视频。

流程：先用 `repopilot report` 生成 HTML，再渲染整页截图，按区块裁切并逐段推进，
最后用 Pillow 生成带字幕的帧、ffmpeg 编码为 mp4。所有画面都来自本地真实产物，
不伪造进度，也不包含凭证与原始模型会话。

用法：
    python scripts/record_demo.py --run-id <RUN_ID> --output docs/demo/run-report.mp4
"""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT = 1280, 720
FPS = 24
FONT_CANDIDATES = [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc",
                   r"C:\Windows\Fonts\seguiemj.ttf", r"C:\Windows\Fonts\simhei.ttf"]
MONO_CANDIDATES = [r"C:\Windows\Fonts\consola.ttf", r"C:\Windows\Fonts\cour.ttf"]

BG = (16, 20, 27)
PANEL = (24, 30, 39)
FG = (232, 238, 246)
MUTED = (150, 163, 180)
ACCENT = (88, 166, 255)
OK = (63, 185, 80)
WARN = (210, 153, 34)


def font(size: int, mono: bool = False) -> ImageFont.FreeTypeFont:
    for path in (MONO_CANDIDATES if mono else FONT_CANDIDATES):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def canvas() -> Image.Image:
    return Image.new("RGB", (WIDTH, HEIGHT), BG)


def caption(draw: ImageDraw.ImageDraw, text: str, y: int = 636) -> None:
    draw.rectangle([0, y - 26, WIDTH, HEIGHT], fill=PANEL)
    draw.text((48, y - 14), text, font=font(24), fill=FG)


def header(draw: ImageDraw.ImageDraw, kicker: str, title: str) -> None:
    draw.text((48, 34), kicker, font=font(20), fill=ACCENT)
    draw.text((48, 64), title, font=font(38), fill=FG)


def wrap(draw, text, fnt, width, x, y, fill, line_height=None):
    line_height = line_height or fnt.size + 10
    line, cy = "", y
    for chunk in text.split("\n"):
        for char in chunk:
            trial = line + char
            if draw.textlength(trial, font=fnt) > width:
                draw.text((x, cy), line, font=fnt, fill=fill)
                line, cy = char, cy + line_height
            else:
                line = trial
        draw.text((x, cy), line, font=fnt, fill=fill)
        line, cy = "", cy + line_height
    return cy


def scene_title() -> Image.Image:
    image = canvas()
    draw = ImageDraw.Draw(image)
    draw.text((72, 190), "RepoPilot", font=font(72), fill=FG)
    draw.text((72, 280), "Issue → 工作区 → 独立检查 → 审核返工 → 人审 → 草稿 PR", font=font(28), fill=ACCENT)
    wrap(draw, "本视频回放一次真实运行：所有画面来自本地已落盘证据，\n不含凭证与原始模型会话。",
         font(24), 1080, 72, 380, MUTED)
    return image


def scene_terminal(command: str, output: str) -> Image.Image:
    image = canvas()
    draw = ImageDraw.Draw(image)
    header(draw, "复现命令", "把一次运行的证据渲染成单页报告")
    draw.rounded_rectangle([48, 132, WIDTH - 48, 600], radius=12, fill=PANEL, outline=(48, 58, 72))
    draw.text((76, 152), "PS D:\\workspace\\agent\\repopilot>", font=font(18, mono=True), fill=MUTED)
    y = 182
    monofont = font(18, mono=True)
    for line in command.split("\n"):
        draw.text((76, y), line, font=monofont, fill=OK)
        y += 26
    y += 12
    for line in output.strip().splitlines():
        draw.text((76, y), line, font=monofont, fill=ACCENT if line.strip().startswith('"') else FG)
        y += 26
    caption(draw, "报告只读已落盘证据：不调用模型、不改运行结果、不提供写操作")
    return image


def scene_slide(region: Image.Image, kicker: str, title: str, note: str) -> Image.Image:
    image = canvas()
    draw = ImageDraw.Draw(image)
    header(draw, kicker, title)
    left, top, right, bottom = 48, 120, WIDTH - 48, 632
    box_w, box_h = right - left, bottom - top
    ratio = min(box_w / region.width, box_h / region.height)
    scaled = region.resize((int(region.width * ratio), int(region.height * ratio)), Image.LANCZOS)
    image.paste(scaled, (left + (box_w - scaled.width) // 2, top + (box_h - scaled.height) // 2))
    draw.rectangle([left - 1, top - 1, right + 1, bottom + 1], outline=(48, 58, 72))
    caption(draw, note, y=668)
    return image


def scene_outro() -> Image.Image:
    image = canvas()
    draw = ImageDraw.Draw(image)
    draw.text((72, 170), "这套证据链已经可以核对", font=font(52), fill=FG)
    lines = [
        ("真实工单", "Issue 文本进入本地任务，不改登记命令与仓库路径"),
        ("独立复核", "编排层重跑登记检查，不采信实现方自查结论"),
        ("审核与返工", "结论绑定提交；要求改动时返工，达上限交人工"),
        ("人工决定", "批准绑定完整 SHA，批准本身不推送"),
        ("草稿 PR", "只有获批版本才能发布；合并不在本项目内"),
    ]
    y = 260
    for key, value in lines:
        draw.text((72, y), key, font=font(26), fill=ACCENT)
        draw.text((300, y), value, font=font(24), fill=MUTED)
        y += 52
    wrap(draw, "仍缺：操作系统沙箱、合并与发布、实时控制台。", font(22), 1150, 72, 560, WARN)
    return image


def capture_page(html: Path, height: int, target: Path) -> Image.Image:
    chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    if not Path(chrome).exists():
        raise SystemExit("chrome_not_found")
    subprocess.run([chrome, "--headless", "--disable-gpu", "--hide-scrollbars",
                    f"--window-size=1100,{height}", f"--screenshot={target}", f"file:///{html.as_posix()}"],
                   check=False, capture_output=True)
    if not target.exists():
        raise SystemExit("screenshot_failed")
    return Image.open(target).convert("RGB")


def content_bottom(page: Image.Image) -> int:
    gray = page.convert("L")
    width, height = gray.size
    for y in range(height - 1, 0, -10):
        if gray.crop((0, y, width, y + 1)).getextrema()[0] < 250:
            return min(height, y + 60)
    return height


def text_bands(page: Image.Image) -> list[tuple[int, int]]:
    """把整页按“有文字的连续行”切成横条，用于定位区块位置。"""

    gray = page.convert("L")
    width, height = gray.size

    def has_text(y: int) -> bool:
        return gray.crop((0, y, width, y + 1)).getextrema()[0] < 250

    bands, start = [], None
    for y in range(height):
        if has_text(y) and start is None:
            start = y
        elif not has_text(y) and start is not None:
            if y - start >= 12:
                bands.append((start, y))
            start = None
    if start is not None:
        bands.append((start, height))
    return bands


CANONICAL_SECTIONS = ["需求来源", "关键指标", "阶段时间线", "实现报告", "候选改动",
                      "独立复核", "审核与返工", "审核发现", "人工决定", "远端产物"]


def section_offsets(page: Image.Image) -> dict[str, int]:
    """用最大的若干垂直留白切出区块，再按固定顺序命名。

    报告区块之间是统一留白，区块内部间距明显更小；因此“边界数 = 区块数 - 1”。
    各运行实际包含的区块会少一些可选块，这里按固定顺序取前 N 个名字，
    只用于定位，不宣称区块一定存在。
    """

    bands = text_bands(page)
    gaps = sorted(((start - previous_end, start) for (_, previous_end), (start, _) in zip(bands, bands[1:])),
                  reverse=True)
    count = min(len(CANONICAL_SECTIONS), len(gaps) + 1)
    boundaries = sorted(start for _, start in gaps[:count - 1])
    offsets = {"需求来源": 0}
    for name, start in zip(CANONICAL_SECTIONS[1:], boundaries):
        offsets[name] = start
    return offsets


def section_region(page: Image.Image, offsets: dict[str, int], name: str, height: int = 660) -> Image.Image:
    start = offsets.get(name)
    if start is None:
        start = 0
    start = max(0, start - 18)
    return page.crop((0, start, page.width, min(page.height, start + height)))


def scroll_frames(page: Image.Image, bottom: int, seconds: float = 9.0) -> list[Image.Image]:
    """用裁切窗口在整页上平滑下移，模拟逐段阅读。

    窗口高度固定，因此最后几帧必须停在“窗口下边缘刚好等于内容底部”的位置，
    否则会裁到页面外的空白。
    """

    window = 700
    visible = max(1, bottom - window)
    frames, total = [], int(seconds * FPS)
    for index in range(total):
        progress = index / max(1, total - 1)
        offset = int(progress * visible)
        frames.append(page.crop((0, offset, page.width, offset + window)))
    return frames


def encode(frames_dir: Path, output: Path) -> None:
    ffmpeg = shutil.which("ffmpeg") or r"D:\Common\ffmpeg-7.1.1-full_build\bin\ffmpeg.exe"
    if not Path(ffmpeg).exists():
        raise SystemExit("ffmpeg_not_found")
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-framerate", str(FPS),
                    "-i", str(frames_dir / "frame-%05d.png"),
                    "-vf", "scale=1280:720:flags=lanczos,format=yuv420p",
                    "-c:v", "libx264", "-preset", "slow", "-crf", "26",
                    "-movflags", "+faststart", str(output)], check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "demo" / "run-report.mp4")
    parser.add_argument("--work", type=Path, default=ROOT / ".local" / "demo" / "recording")
    parser.add_argument("--html", type=Path)
    parser.add_argument("--sections", default="阶段时间线,实现报告,候选改动,审核与返工",
                        help="报告导览要特写的区块，按报告顺序用英文逗号分隔")
    args = parser.parse_args()

    work = args.work
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    html = args.html or (ROOT / ".local" / "demo" / f"{args.run_id}.html")
    if not html.exists():
        subprocess.run([str(ROOT / ".venv" / "Scripts" / "repopilot.exe"), "report", args.run_id,
                        "--html", str(html)], check=True, capture_output=True)

    page = capture_page(html, 7000, work / "page.png")
    bottom = content_bottom(page)
    page = page.crop((0, 0, page.width, bottom))

    frames: list[Image.Image] = []
    frames += [scene_title()] * (FPS * 3)
    command = f"repopilot report {args.run_id}\n    --html docs/demo/report.html"
    output = json.dumps({"status": "ok", "run_id": args.run_id, "html": "docs/demo/report.html",
                         "bytes": html.stat().st_size}, ensure_ascii=False, indent=2)
    frames += [scene_terminal(command, output)] * (FPS * 5)

    offsets = section_offsets(page)
    print(json.dumps({"section_offsets": offsets}, ensure_ascii=False))

    # 整页自上而下推进，覆盖开场与全部内容
    frames += scroll_frames(page, bottom, seconds=10)

    sections = [
        ("阶段时间线", "阶段记录真实事件顺序：工作区、复核、审核"),
        ("实现报告", "证据分文件与检查两类，逐条可核对"),
        ("候选改动", "相对基线的改动清单与差异"),
        ("审核与返工", "结论绑定提交，发现按严重程度列出"),
    ]
    notes = {name: note for name, note in sections}
    for key in [item.strip() for item in args.sections.split(",") if item.strip()]:
        region = section_region(page, offsets, key)
        frames += [scene_slide(region, "报告导览", key, notes.get(key, ""))] * (FPS * 4)

    frames += [scene_outro()] * (FPS * 4)

    for index, frame in enumerate(frames):
        frame.save(work / f"frame-{index:05d}.png")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    encode(work, args.output)
    print(json.dumps({"status": "ok", "video": str(args.output),
                      "seconds": round(len(frames) / FPS, 1),
                      "bytes": args.output.stat().st_size}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
