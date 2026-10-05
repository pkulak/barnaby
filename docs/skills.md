# Skills

Pi supports skills — markdown files that extend the agent's capabilities by
providing instructions and examples for specific tasks. Each skill is a directory
containing a `SKILL.md` file with a YAML frontmatter (`name`, `description`) and
the skill's instructions.

Barnaby does not enable any skills by default. Add only the skill directories
you want the agent to see.

## NixOS module

The NixOS module provides a declarative `skills` option — an attrset mapping
skill names to directories:

```nix
services.barnaby.instances.barnaby.skills = {
  kagi-search = "${mics-skills}/skills/kagi-search";
  my-custom-skill = ./skills/my-custom-skill;
};
```

All entries are assembled into a single directory via `linkFarm` and passed to
pi through `BARNABY_PI_SKILLS_DIR`. The attrset is mergeable, so skills can be
added from multiple NixOS module files.

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
