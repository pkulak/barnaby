---
name: transcribe
description: Transcribe voice messages and audio attachments to text. Use automatically on any audio.
---

# Transcribe

When a user sends an audio or video file, transcribe it and treat the result as their message.

## How to transcribe

```bash
curl -s https://openrouter.ai/api/v1/audio/transcriptions \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" \
  -F "file=@<FILE_PATH>" \
  -F "model=openai/gpt-4o-transcribe" | jq -r .text
```
