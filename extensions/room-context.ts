/**
 * Room Context Extension — built into barnaby
 *
 * Barnaby sends `/room-context <text>` for each room message that doesn't
 * start a turn: unaddressed group chat and the background worker's posts.
 * The command records the text in the session as a custom message without
 * calling the model, so the agent sees it on its next turn and the session
 * file keeps it. Barnaby embeds this file and loads it with --extension.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export default function roomContext(pi: ExtensionAPI) {
  pi.registerCommand("room-context", {
    description: "Record a room message without starting a turn",
    handler: async (text) => {
      pi.sendMessage(
        { customType: "room-context", content: text, display: true },
        { triggerTurn: false },
      );
    },
  });
}
