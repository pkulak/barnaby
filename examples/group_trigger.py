#!/usr/bin/env python3
"""Ask an LLM whether a group message is meant for the bot.

Needs OPENROUTER_API_KEY. See docs/configuration.md#group-message-routing for
the input format and exit codes.
"""

import json
import os
import sys
import urllib.request

NAME = "Crow"
ALIASES = ["crow", "crowbot"]

URL = "https://openrouter.ai/api/alpha/decisions"
# THRESHOLD is tuned for this model; retune it if you change models.
MODEL = "typesafe/jev-1.13"
THRESHOLD = 0.5
TIMEOUT = 5

ALIAS_TEXT = " or ".join(ALIASES)

QUESTION = {
    "type": "noul",
    "instructions": (
        f"{NAME} (also called {ALIAS_TEXT}) is an AI assistant in a group chat. "
        f"Should {NAME} respond to `message`? `history` holds earlier messages, "
        "oldest first; `ago` is how long before `message` each was sent, and "
        f"`is_bot` marks {NAME}'s own messages."
    ),
    "criteria": {
        "true": (
            f"The message speaks to {NAME} by name, or continues a recent exchange "
            f"with {NAME}, such as a follow-up, correction, or answer to {NAME}'s question."
        ),
        "false": (
            f"The message is between humans, talks about {NAME} rather than to it, "
            f"uses {ALIAS_TEXT} with another meaning, or is unrelated to any recent "
            f"exchange with {NAME}."
        ),
    },
}


def probability(state):
    body = {"model": MODEL, "state": state, "questions": {"respond": QUESTION}}
    request = urllib.request.Request(
        URL,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.load(response)["answers"]["respond"]["noul"]


def main():
    p = probability(json.load(sys.stdin))
    print(f"p={p:.2f} T={THRESHOLD}")
    return 0 if p >= THRESHOLD else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        # Uncaught exceptions exit 1, which means "skip"; errors must be distinct.
        print(f"error: {e}")
        sys.exit(2)
