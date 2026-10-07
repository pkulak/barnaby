---
name: image
description: Generate or edit images, only when explicitly asked to draw, create, or edit one.
---

# Image

Only make images when someone explicitly asks for one. Don't illustrate other replies on your own initiative.

## Generate

```bash
python3 "$BARNABY_PI_SKILLS_DIR/image/scripts/image.py" "a corgi astronaut floating above the moon" --size 1536x1024
```

The script prints the path of each saved PNG. A nonzero exit means the request failed; report the useful error rather than retrying repeatedly.

Requests can take a minute or two. Pass the user's request through as the prompt, adding detail only where it clearly helps (style, composition, text to render).

## Edit

Pass one or more input images with `--image`. The same command handles all of these:

- Edit an attached photo: use the attachment's local path. For LibreChat requests, fetch the file first with the librechat-files skill.
- Combine reference images ("put my dog in this scene"): repeat `--image` for each, and say in the prompt what to take from which image.
- Follow up on an image you just made ("make it bluer"): pass the path of your previous output.

```bash
python3 "$BARNABY_PI_SKILLS_DIR/image/scripts/image.py" "make it night time, keep everything else the same" \
  --image /tmp/images/20261001-142233-1.png
```

## Options

- `--size`: choose from the prompt: `1024x1024` (square, default), `1024x1536` (portrait), or `1536x1024` (landscape). Only when someone asks for a large or printable image, use 2K or 4K such as `2048x2048`, `2560x1440`, or `3840x2160`.
- `--quality`: `high` by default. Use `xhigh` or `max` only when asked for higher quality, and `medium` or `low` when asked for something quick.
- `-n`: 1 by default. Up to 4 only when someone asks for options or variations.

## Send

Send each image with the sendfile tag and keep any commentary short:

```text
<sendfile>/tmp/images/20261001-142233-1.png</sendfile>
```
