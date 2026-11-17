#!/usr/bin/env python3
"""Ask an LLM for a one-line status of what the bot is working on.

Needs OPENROUTER_API_KEY. See docs/configuration.md#progress-updates for the
input format.
"""

import json
import os
import sys
import urllib.request

URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "openai/gpt-6.1-sol"
TIMEOUT = 8

PROMPT = (
    "An AI assistant is working on a chat request, and the user is waiting on "
    "their phone. Write the status line they see. You get the request, the "
    "previous status, and the assistant's messages and tool calls since then. "
    "Say what it's doing now in a few plain words, like \"Checking the "
    "calendar\" or \"Comparing flight prices\". No tool names, file paths, or "
    "commands. Reply with the status line only."
)


def main():
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": sys.stdin.read()},
        ],
        "reasoning": {"effort": "low"},
    }
    request = urllib.request.Request(
        URL,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        status = json.load(response)["choices"][0]["message"]["content"].strip()
    print(status.rstrip(".…") + "…")


if __name__ == "__main__":
    main()
