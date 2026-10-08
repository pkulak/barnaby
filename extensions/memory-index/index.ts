import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";

// Tells the agent how to use its memory: the session notes the nightly
// compaction writes to ~/memory. Chat runs also get ~/memory/INDEX.md, so the
// agent knows what memory holds without loading any notes. Background runs
// skip the index to stay lean.

const home = homedir();
const tilde = (path: string) => (path.startsWith(`${home}/`) ? `~/${path.slice(home.length + 1)}` : path);
const sessions = tilde(process.env.BARNABY_PI_SESSION_DIR ?? join(home, "sessions"));

const guidance = (indexed: boolean) => `# Memory

Notes from your past conversations are in \`~/memory/\`, one per conversation period. They are your memory. ${
  indexed ? "The index at the end of this prompt lists them." : "`~/memory/INDEX.md` lists them."
}

Check memory before answering whenever a message touches anything that might have history: a person, pet, place, event, plan, purchase, repair, reminder, or preference; "again", "last time", "like before", "that thing"; or a request where knowing past choices would change your answer. If an index line looks relevant, read that note. Otherwise \`rg -i\` the folder for names and keywords. Don't search for small talk or self-contained tasks.

Notes lag a few days behind. If they don't cover something that may be recent, search your raw sessions with \`rg -i -o '.{0,200}<term>.{0,200}' ${sessions}/*.jsonl\` and read only the matches; never read those files whole. When someone wants an exact quote, number, or detail a note lacks, search the raw transcript at the note's \`archive:\` path the same way, with \`zstdcat <archive> | rg -i -o ...\`.

Your memory includes direct messages. In a group room, don't bring up personal details someone shared privately, such as gifts, health, money, or surprises, unless that person is the one asking.

Never claim to remember something you didn't find. If memory is silent, say so or ask.`;

export default function memoryIndex(pi: ExtensionAPI) {
  const backgroundDir = process.env.BARNABY_BACKGROUND_PI_SESSION_DIR;

  pi.on("before_agent_start", (event, ctx) => {
    const sessionFile = ctx.sessionManager.getSessionFile();
    const background = Boolean(backgroundDir && sessionFile?.startsWith(`${backgroundDir}/`));

    let index = "";
    if (!background) {
      try {
        index = readFileSync(join(home, "memory", "INDEX.md"), "utf8").trim();
      } catch {
        // No notes yet.
      }
    }

    const parts = [event.systemPrompt, guidance(index !== ""), index].filter(Boolean);
    return { systemPrompt: `${parts.join("\n\n")}\n` };
  });
}
