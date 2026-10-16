---
name: transcribe
description: Transcribe voice messages and audio attachments to text. Use automatically on any audio.
---

# Transcribe

When a user sends an audio or video file, transcribe it and treat the result as their message.

## How to transcribe

The model accepts WAV, MP3, Ogg/Opus, and FLAC, and rejects M4A, AAC, MP4, and WebM with `Provider returned 400`. Convert any other file to Opus first:

```bash
ffmpeg -i <FILE_PATH> -vn -c:a libopus -b:a 32k /tmp/transcribe.ogg
```

```bash
curl -s https://openrouter.ai/api/v1/audio/transcriptions \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" \
  -F "file=@<FILE_PATH>" \
  -F "model=microsoft/mai-transcribe-2" | jq -r .text
```
