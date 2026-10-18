"""把实际浏览器截图编码为报告 GIF；只裁切统一区域，不拉伸或合成页面内容。

输入清单：{"crop": [left, top, right, bottom], "scale_to": 1200, "trim": true,
           "frames": [{"file": "00-overview.png", "duration_ms": 3200}, ...]}

- `scale_to`：等比缩放到该宽度（LANCZOS），用于把 2 倍像素密度的截图变成清晰帧；
- `trim`：按所有帧的内容下边界统一下移画布底部，只去掉空白，不改变页面比例；
- 量化使用 256 色并开启抖动，避免文字边缘出现色带。

截图路径相对清单所在目录；需要 Pillow，运行时产品不依赖此工具。
"""

import argparse
import json
from pathlib import Path

from PIL import Image


def load_frames(manifest: Path) -> tuple[list[Image.Image], list[int], dict]:
    data = json.loads(manifest.read_text(encoding="utf-8"))
    crop = tuple(data["crop"])
    frames, durations = [], []
    for entry in data["frames"]:
        with Image.open(manifest.parent / entry["file"]) as original:
            left, top, right, bottom = crop
            if not (0 <= left < right <= original.width and 0 <= top < bottom <= original.height):
                raise ValueError("crop_outside_screenshot")
            frames.append(original.convert("RGB").crop(crop))
        durations.append(int(entry["duration_ms"]))
    return frames, durations, data


def content_bottom(frame: Image.Image, threshold: int = 250) -> int:
    """返回最后一个非空白行之后的位置，用于统一裁掉底部空白。"""

    grey = frame.convert("L")
    width, height = grey.size
    for y in range(height - 1, 0, -1):
        if grey.crop((0, y, width, y + 1)).getextrema()[0] < threshold:
            return min(height, y + 1 + 24)
    return height


def assemble(manifest: Path, output: Path, poster: Path | None = None):
    frames, durations, data = load_frames(manifest)
    if not frames:
        raise ValueError("no_screenshots")

    scale_to = data.get("scale_to")
    if scale_to:
        for index, frame in enumerate(frames):
            height = round(frame.height * scale_to / frame.width)
            frames[index] = frame.resize((int(scale_to), height), Image.LANCZOS)

    if data.get("trim"):
        keep = max(content_bottom(frame) for frame in frames)
        frames = [frame.crop((0, 0, frame.width, min(frame.height, keep))) for frame in frames]

    share = int(data.get("colors", 256))
    palette = [frame.quantize(colors=share, method=Image.Quantize.MEDIANCUT,
                              dither=Image.Dither.FLOYDSTEINBERG) for frame in frames]
    if poster:
        poster.parent.mkdir(parents=True, exist_ok=True)
        frames[0].save(poster, format="PNG")

    output.parent.mkdir(parents=True, exist_ok=True)
    palette[0].save(output, format="GIF", save_all=True, append_images=palette[1:],
                    duration=durations, loop=0, optimize=True, disposal=2)
    with Image.open(output) as result:
        return {"output": str(output), "size": result.size, "frames": result.n_frames,
                "duration_ms": sum(durations), "bytes": output.stat().st_size,
                "colors": share, "scale_to": scale_to, "trimmed": bool(data.get("trim"))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--poster", type=Path)
    args = parser.parse_args()
    print(json.dumps(assemble(args.manifest, args.output, args.poster), ensure_ascii=False))


if __name__ == "__main__":
    main()
