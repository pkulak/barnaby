"""Generate or edit images through OpenRouter and print the saved PNG paths."""

import argparse
import base64
import mimetypes
import os
import sys
import time
from pathlib import Path

import requests

API = "https://openrouter.ai/api/v1/images"
MODEL = "openai/gpt-image-2.5-sunburst"
OUT_DIR = Path("/tmp/images")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt")
    parser.add_argument("--image", action="append", default=[], help="input image to edit or reference; repeatable")
    parser.add_argument("--size", default="1024x1024")
    parser.add_argument("--quality", default="high")
    parser.add_argument("-n", type=int, default=1, choices=range(1, 5))
    args = parser.parse_args()

    params = {"model": MODEL, "prompt": args.prompt, "size": args.size, "quality": args.quality, "n": args.n}
    if args.image:
        params["input_references"] = [{"type": "image_url", "image_url": {"url": data_url(p)}} for p in args.image]
    headers = {"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"}

    response = requests.post(API, headers=headers, json=params, timeout=300)
    if not response.ok:
        sys.exit(f"OpenRouter error {response.status_code}: {response.text}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for i, item in enumerate(response.json()["data"], 1):
        path = OUT_DIR / f"{stamp}-{i}.png"
        path.write_bytes(base64.b64decode(item["b64_json"]))
        print(path)


def data_url(path):
    mime = mimetypes.guess_type(path)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(Path(path).read_bytes()).decode()}"


if __name__ == "__main__":
    main()
