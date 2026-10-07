# Skills

Pi supports skills — markdown files that extend the agent's capabilities by
providing instructions and examples for specific tasks. Each skill is a directory
containing a `SKILL.md` file with a YAML frontmatter (`name`, `description`) and
the skill's instructions.

Barnaby does not enable any skills by default. Add only the skills you want the
agent to see.

## Bundled skills

Barnaby ships a few skills in `skills/`:

| Skill | What it does | Needs |
|---|---|---|
| `image` | Generates or edits images (`openai/gpt-image-2.5-sunburst`) | `OPENROUTER_API_KEY` |
| `transcribe` | Transcribes voice messages and other audio (`openai/gpt-4o-transcribe`) | `OPENROUTER_API_KEY` |
| `sports-scores` | Scores, schedules, standings, and rankings from ESPN, in the local timezone (`TZ`) | `OPENROUTER_API_KEY` for team search only |
| `sports-monitor` | Watches a live game and sends one alert | The `sports-scores` skill and the `reminders` extension |

`sports-scores` matches team names with Jev, so it has no list of favorite teams. If
"Ducks" should mean Oregon rather than Anaheim, say which teams the family follows in
the soul.

## NixOS module

The NixOS module provides a declarative `skills` option — an attrset mapping
skill names to directories:

```nix
services.barnaby.instances.barnaby.skills = {
  image = true;
  kagi-search = "${mics-skills}/skills/kagi-search";
  my-custom-skill = ./skills/my-custom-skill;
};
```

`true` enables a bundled skill and adds the packages it runs (Python, curl, and
so on) to the end of the service's `PATH`. All entries are assembled into a
single directory via `linkFarm` and passed to pi through `BARNABY_PI_SKILLS_DIR`.
The attrset is mergeable, so skills can be added from multiple NixOS module files.

## Environment variables

When not using the NixOS module, point `BARNABY_PI_SKILLS_DIR` at a directory
whose subdirectories are scanned for `SKILL.md` files:

```
BARNABY_PI_SKILLS_DIR=/path/to/skills-directory
```

## Writing a skill

Create a directory with a `SKILL.md`:

```markdown
---
name: my-skill
description: What this skill does and when to use it
---

Instructions for the agent on how to use this skill...
```
