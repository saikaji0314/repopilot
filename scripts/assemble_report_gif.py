"""把实际浏览器截图编码为报告 GIF；只裁切统一区域，不拉伸或合成页面内容。

输入清单：{"crop": [left, top, right, bottom], "frames": [
    {"file": "00-overview.jpg", "duration_ms": 3200}, ...]}
截图路径相对清单所在目录；需要 Pillow，运行时本身不依赖此工具。
"""

import argparse
import json
from pathlib import Path

from PIL import Image


def assemble(manifest: Path, output: Path, poster: Path | None = None):
    data = json.loads(manifest.read_text(encoding="utf-8"))
    crop = tuple(data["crop"])
    frames, durations = [], []
    for entry in data["frames"]:
        with Image.open(manifest.parent / entry["file"]) as original:
            left, top, right, bottom = crop
            if not (0 <= left < right <= original.width and 0 <= top < bottom <= original.height):
                raise ValueError("crop_outside_screenshot")
            frame = original.convert("RGB").crop(crop)
        if not frames and poster:
            poster.parent.mkdir(parents=True, exist_ok=True)
            frame.save(poster, format="PNG")
        frames.append(frame.quantize(colors=128))
        durations.append(int(entry["duration_ms"]))
    if not frames:
        raise ValueError("no_screenshots")
    output.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:],
                   duration=durations, loop=0, optimize=True, disposal=2)
    with Image.open(output) as result:
        return {"output": str(output), "size": result.size, "frames": result.n_frames,
                "duration_ms": sum(durations), "bytes": output.stat().st_size}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--poster", type=Path)
    args = parser.parse_args()
    print(json.dumps(assemble(args.manifest, args.output, args.poster)))


if __name__ == "__main__":
    main()
