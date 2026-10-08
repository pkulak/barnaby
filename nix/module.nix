{ self }:
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.barnaby;

  jsonFormat = pkgs.formats.json { };

  # Each instance's container is named after it.
  stateDirOf = name: "/var/lib/${name}";

  mkInstanceOptions =
    name:
    let
      stateDir = stateDirOf name;
    in
    {
      package = lib.mkOption {
        type = lib.types.package;
        default = self.packages.${pkgs.stdenv.hostPlatform.system}.barnaby;
        defaultText = lib.literalExpression "barnaby.packages.\${system}.barnaby";
        description = "The barnaby package to use.";
      };

      piPackage = lib.mkOption {
        type = lib.types.package;
        description = "The pi coding agent package. Required — typically from llm-agents.nix or similar.";
        example = lib.literalExpression "llm-agents.packages.\${system}.pi";
      };

      skills = lib.mkOption {
        type = lib.types.attrsOf (lib.types.either lib.types.bool lib.types.path);
        default = { };
        description = ''
          Skills to make available to pi, keyed by name. Each value can be:
          - `true` to enable a skill shipped with barnaby (from its
            `skills` directory), along with the packages it needs
          - `false` to explicitly disable a skill
          - A path to a directory containing a SKILL.md file

          All skills are assembled into a single directory and passed via
          BARNABY_PI_SKILLS_DIR.

          Bundled skills: `calendar`, `image`, `transcribe`, `sports-scores`,
          `sports-monitor`, `weather`, and `web-search`. See docs/skills.md for what each one needs.
        '';
        example = lib.literalExpression ''
          {
            image = true;
            my-custom-skill = ./my-custom-skill;
            kagi-search = "''${pkgs.fetchFromGitHub { owner = "someone"; repo = "pi-skills"; rev = "main"; hash = "..."; }}/kagi-search";
          }
        '';
      };

      extensions = lib.mkOption {
        type = lib.types.attrsOf (lib.types.either lib.types.bool lib.types.path);
        default = { };
        description = ''
          Pi extension files or directories to make available, keyed by
          name. Each value can be:
          - `true` to enable a packaged extension shipped with barnaby
            (resolved from the flake's `extension-''${name}` package output)
          - `false` to explicitly disable an extension
          - A path to a .ts file or directory containing an index.ts

          All enabled extensions are written into a generated settings.json
          that pi reads from PI_CODING_AGENT_DIR.

          Bundled extension: `reminders` (remind_at/list/cancel tools backed
          by barnaby.db).
        '';
        example = lib.literalExpression ''
          {
            # Enable the bundled reminders extension
            reminders = true;
            # Custom extension from a local path
            my-ext = ./extensions/my-ext.ts;
          }
        '';
      };

      piSettings = lib.mkOption {
        type = jsonFormat.type;
        default = { };
        description = ''
          Extra keys to include in the generated pi settings.json.
          The `extensions` key is automatically populated from the
          `extensions` option and should not be set here.
        '';
        example = lib.literalExpression ''
          {
            packages = [ "npm:@foo/bar@1.0.0" ];
            compaction = { enabled = true; };
          }
        '';
      };

      piModels = lib.mkOption {
        type = jsonFormat.type;
        default = { };
        description = ''
          Contents of pi's models.json. Use this to add custom
          providers or override properties of built-in models via
          `modelOverrides` — most commonly `contextWindow` when the
          configured API tier is narrower than pi's published value
          (e.g. Anthropic's long-context requires separate usage
          credits). Without the override pi's auto-compaction never
          triggers and every turn bounces off a 429.
        '';
        example = lib.literalExpression ''
          {
            providers.anthropic.modelOverrides."claude-sonnet-4-6".contextWindow = 200000;
          }
        '';
      };

      environmentFiles = lib.mkOption {
        type = lib.types.listOf lib.types.path;
        default = [ ];
        description = ''
          List of environment files containing secrets (on the host).
          Bind-mounted read-only into the container.
          Must define at minimum (across all files):
          - BARNABY_MATRIX_ACCESS_TOKEN
          - BARNABY_MATRIX_USER_ID
          - ANTHROPIC_API_KEY (or the appropriate key for your provider)
        '';
      };

      credentialFiles = lib.mkOption {
        type = lib.types.attrsOf lib.types.path;
        default = { };
        description = ''
          Credential files to pass into the container via systemd-nspawn's
          --load-credential. Keys are credential names, values are host paths.
          Inside the container, the barnaby service imports them via
          ImportCredential and they are available under
          $CREDENTIALS_DIRECTORY/<name>.
        '';
        example = lib.literalExpression ''
          { "custom-secret" = /run/secrets/custom-secret; }
        '';
      };

      extraPackages = lib.mkOption {
        type = lib.types.listOf lib.types.package;
        default = [ ];
        description = "Extra packages available inside the container and on the service PATH.";
        example = lib.literalExpression "[ pkgs.curl pkgs.jq ]";
      };

      extraBindMounts = lib.mkOption {
        type = lib.types.attrsOf (
          lib.types.submodule {
            options = {
              hostPath = lib.mkOption { type = lib.types.str; };
              isReadOnly = lib.mkOption {
                type = lib.types.bool;
                default = false;
              };
            };
          }
        );
        default = { };
        description = "Additional bind mounts into the container.";
      };

      memory = {
        enable = lib.mkEnableOption ''
          long-term memory. Every night at 03:00, up to 10 sessions that have
          been idle for 3 days become markdown notes in `~/memory`, and their
          raw transcripts are compressed into `~/session-archive`. The agent's
          prompt gets instructions for searching the notes, and chat runs get
          their index'';

        directory = lib.mkOption {
          type = lib.types.str;
          default = "${stateDir}/memory";
          description = ''
            Host directory for the notes. The agent always sees it at
            `~/memory`. A directory outside the state directory must already
            exist and be writable by the container's `barnaby` user.
          '';
        };

        model = lib.mkOption {
          type = lib.types.nullOr lib.types.str;
          default = null;
          description = ''
            The pi model that writes the notes, as `provider/id` with an
            optional `:thinking` suffix. Null uses the chat model.
          '';
          example = "openrouter/anthropic/claude-sonnet-4.5:medium";
        };
      };

      environment = lib.mkOption {
        type = lib.types.submodule {
          freeformType = lib.types.attrsOf lib.types.str;

          options = {
            BARNABY_MATRIX_HOMESERVER = lib.mkOption {
              type = lib.types.str;
              default = "";
              description = "Matrix homeserver URL. Required.";
              example = "https://matrix.example.com";
            };

            BARNABY_MATRIX_DEVICE_ID = lib.mkOption {
              type = lib.types.str;
              default = "";
              description = "Matrix device ID.";
            };

            BARNABY_PI_PROVIDER = lib.mkOption {
              type = lib.types.str;
              default = "anthropic";
              description = "LLM provider for pi (anthropic, openai, google, etc.).";
            };

            BARNABY_PI_MODEL = lib.mkOption {
              type = lib.types.str;
              default = "claude-opus-4-6";
              description = "Model ID for the chat pi session.";
            };

            BARNABY_BACKGROUND_PI_PROVIDER = lib.mkOption {
              type = lib.types.str;
              default = "";
              description = "Optional provider override for the background pi session.";
            };

            BARNABY_BACKGROUND_PI_MODEL = lib.mkOption {
              type = lib.types.str;
              default = "";
              description = "Optional model override for reminders and external triggers.";
            };

            BARNABY_BACKGROUND_FALLBACK_PI_PROVIDER = lib.mkOption {
              type = lib.types.str;
              default = "";
              description = "Provider for the background fallback model. Defaults to the background provider.";
            };

            BARNABY_BACKGROUND_FALLBACK_PI_MODEL = lib.mkOption {
              type = lib.types.str;
              default = "";
              description = "Optional model that finishes background runs after a provider error.";
            };

            BARNABY_BACKGROUND_FALLBACK_COOLDOWN = lib.mkOption {
              type = lib.types.str;
              default = "1h";
              description = "How long background runs stay on the fallback model after a failure.";
            };

            BARNABY_PI_SESSION_DIR = lib.mkOption {
              type = lib.types.str;
              default = "${stateDir}/sessions";
              description = "Directory for pi session storage.";
            };

            BARNABY_PI_IDLE_TIMEOUT = lib.mkOption {
              type = lib.types.str;
              default = "30m";
              description = "Idle timeout for pi processes (Go duration format, e.g. 30m, 1h).";
            };

            BARNABY_PI_WORKING_DIR = lib.mkOption {
              type = lib.types.str;
              default = stateDir;
              description = "Working directory for pi subprocesses.";
            };

            BARNABY_PI_SKILLS_DIR = lib.mkOption {
              type = lib.types.str;
              # A short, stable link to skillsDir: pi lists every skill's path in
              # the system prompt, and store paths cost many tokens and change
              # with each deploy.
              default = "${stateDir}/skills";
              description = "Directory to scan for skill subdirectories (each must contain SKILL.md). By default a link to the skills from the `skills` option.";
            };

            BARNABY_SOUL_FILE = lib.mkOption {
              type = lib.types.str;
              default = "";
              description = "Path to a file containing the system prompt. Empty uses the built-in SOUL.md.";
            };

            PI_CODING_AGENT_DIR = lib.mkOption {
              type = lib.types.str;
              default = "${stateDir}/pi-agent";
              description = "Directory where pi stores its agent configuration and data.";
            };

            BARNABY_LOG_LEVEL = lib.mkOption {
              type = lib.types.enum [
                "debug"
                "info"
                "warn"
                "error"
              ];
              default = "info";
              description = "Log verbosity. Set to 'debug' to log full conversation content.";
            };
          };
        };
        default = { };
        description = ''
          Environment variables passed to the barnaby service.
          Known options have defaults and descriptions. Extra variables
          (e.g. provider-specific settings) can be added freely.
        '';
      };
    };

  instanceModule =
    { name, ... }:
    {
      options = {
        enable = lib.mkEnableOption "Barnaby messaging bot instance '${name}'";
      }
      // mkInstanceOptions name;
    };

  enabledInstances = lib.filterAttrs (_: i: i.enable) cfg.instances;

  mkInstanceConfig =
    name: icfg:
    let
      barnabyPkg = icfg.package;

      containerName = name;
      stateDir = stateDirOf name;

      # Resolve extension values: `true` means use the corresponding
      # extension package from the barnaby flake, a path is used as-is.
      resolvedExtensions =
        lib.mapAttrs (
          ename: value:
          if value == true then
            self.packages.${pkgs.stdenv.hostPlatform.system}."extension-${ename}"
          else
            value
        ) (lib.filterAttrs (_: v: v != false) icfg.extensions)
        // lib.optionalAttrs icfg.memory.enable { memory-index = "${../extensions/memory-index}"; };

      # Inside the container, notes are always at ~/memory.
      memoryDir = "${stateDir}/memory";
      memoryModel =
        if icfg.memory.model != null then
          icfg.memory.model
        else
          "${icfg.environment.BARNABY_PI_PROVIDER}/${icfg.environment.BARNABY_PI_MODEL}";

      serviceEnvironment = {
        HOME = stateDir;
      }
      // lib.filterAttrs (_: v: v != "") icfg.environment;
      serviceEnvironmentFiles = lib.imap0 (
        i: _: "/run/secrets/${containerName}-envfile-${toString i}"
      ) icfg.environmentFiles;

      # Generate a settings.json for pi that lists declared extensions.
      # Installed into PI_CODING_AGENT_DIR at service startup so pi
      # auto-discovers them.
      piSettingsJson = jsonFormat.generate "pi-settings-${name}.json" (
        icfg.piSettings // { extensions = lib.attrValues resolvedExtensions; }
      );

      piModelsJson = jsonFormat.generate "pi-models-${name}.json" icfg.piModels;

      enabledSkills = lib.filterAttrs (_: v: v != false) icfg.skills;

      skillsDir = pkgs.linkFarm "barnaby-skills-${name}" (
        lib.mapAttrsToList (sname: value: {
          name = sname;
          path = if value == true then ../skills + "/${sname}" else value;
        }) enabledSkills
      );

      # Packages the bundled skills run. They go last on the service's PATH,
      # so anything in extraPackages (a Python with more modules, say) wins.
      bundledSkillPackages = {
        calendar = [ (pkgs.python3.withPackages (ps: [ ps.caldav ])) ];
        image = [ (pkgs.python3.withPackages (ps: [ ps.requests ])) ];
        transcribe = [
          pkgs.curl
          pkgs.jq
        ];
        sports-scores = [ pkgs.python3 ];
        weather = [ pkgs.python3 ];
        web-search = [ pkgs.python3 ];
      };

      skillPackages = lib.concatLists (
        lib.mapAttrsToList (sname: _: bundledSkillPackages.${sname} or [ ]) (
          lib.filterAttrs (_: v: v == true) icfg.skills
        )
      );

      # Host-side wrapper to interact with pi inside the container as the barnaby user.
      barnabyPi = pkgs.writeShellScriptBin "${containerName}-pi" ''
        exec machinectl shell barnaby@${containerName} \
          /run/current-system/sw/bin/env \
            HOME=${stateDir} \
            PI_CODING_AGENT_DIR=${icfg.environment.PI_CODING_AGENT_DIR} \
          ${lib.getExe icfg.piPackage} "$@"
      '';

    in
    {
      assertions = [
        {
          assertion = icfg.environment.BARNABY_MATRIX_HOMESERVER != "";
          message = "services.barnaby (${name}): BARNABY_MATRIX_HOMESERVER is required.";
        }
      ]
      ++ lib.mapAttrsToList (sname: _: {
        assertion = builtins.pathExists (../skills + "/${sname}");
        message = "services.barnaby (${name}): barnaby has no bundled skill named ${sname}.";
      }) (lib.filterAttrs (_: v: v == true) icfg.skills)
      ++ lib.optional ((icfg.skills.sports-monitor or false) == true) {
        assertion = enabledSkills ? sports-scores && (icfg.extensions.reminders or false) != false;
        message = "services.barnaby (${name}): the sports-monitor skill needs the sports-scores skill and the reminders extension.";
      };

      systemPackages = [ barnabyPi ];

      # The barnaby user only exists inside the container, not on the host,
      # so we cannot reference it in host-side tmpfiles rules (systemd-tmpfiles
      # would fail to resolve the name and skip the rule, leaving the bind
      # mount source missing). Create the directory as root here; the
      # container's own tmpfiles rules fix up ownership from the inside
      # where the barnaby UID is known.
      tmpfilesRules = [
        "d ${stateDir} 0750 - - -"
      ];

      containerPreStart = ''
        ${pkgs.systemd}/bin/busctl call org.freedesktop.machine1 \
          /org/freedesktop/machine1 \
          org.freedesktop.machine1.Manager \
          UnregisterMachine s ${containerName} 2>/dev/null || true
      '';

      container = {
        autoStart = true;
        privateNetwork = false;

        bindMounts = {
          "${stateDir}" = {
            hostPath = stateDir;
            isReadOnly = false;
          };
        }
        // lib.listToAttrs (
          lib.imap0 (i: path: {
            name = "/run/secrets/${containerName}-envfile-${toString i}";
            value = {
              hostPath = toString path;
              isReadOnly = true;
            };
          }) icfg.environmentFiles
        )
        // lib.optionalAttrs (icfg.memory.enable && icfg.memory.directory != memoryDir) {
          ${memoryDir} = {
            hostPath = icfg.memory.directory;
            isReadOnly = false;
          };
        }
        // icfg.extraBindMounts;

        extraFlags = lib.mapAttrsToList (
          cname: path: "--load-credential=${cname}:${toString path}"
        ) icfg.credentialFiles;

        config =
          { pkgs, ... }:
          {
            system.stateVersion = "25.05";

            users.users.barnaby = {
              isSystemUser = true;
              group = "barnaby";
              home = stateDir;
            };
            users.groups.barnaby = { };

            # Place the generated settings.json into PI_CODING_AGENT_DIR
            # so pi discovers declared extensions and packages.
            systemd.tmpfiles.rules = [
              # Fix up ownership of the bind-mounted state dir. The host side
              # created it as root because the barnaby user does not exist
              # there; inside the container we know the UID and can chown it.
              "d ${stateDir} 0750 barnaby barnaby -"
              "d ${icfg.environment.PI_CODING_AGENT_DIR} 0750 barnaby barnaby -"
              "L+ ${icfg.environment.PI_CODING_AGENT_DIR}/settings.json - - - - ${piSettingsJson}"
              "L+ ${stateDir}/skills - - - - ${skillsDir}"
            ]
            ++ lib.optional (
              icfg.piModels != { }
            ) "L+ ${icfg.environment.PI_CODING_AGENT_DIR}/models.json - - - - ${piModelsJson}"
            ++ lib.optional (
              icfg.memory.enable && icfg.memory.directory == memoryDir
            ) "d ${memoryDir} 0750 barnaby barnaby -";

            systemd.services.barnaby = {
              description = "Barnaby Matrix Bot (${name})";
              wantedBy = [ "multi-user.target" ];
              after = [ "network-online.target" ];
              wants = [ "network-online.target" ];

              path = [
                barnabyPkg
                icfg.piPackage
                pkgs.bash
                pkgs.coreutils
                pkgs.ffmpeg
              ]
              ++ icfg.extraPackages
              ++ skillPackages;

              environment = serviceEnvironment;

              serviceConfig = {
                EnvironmentFile = serviceEnvironmentFiles;
                ImportCredential = lib.attrNames icfg.credentialFiles;
                ExecStart = lib.getExe barnabyPkg;
                Restart = "on-failure";
                RestartSec = 10;
                User = "barnaby";
                Group = "barnaby";
                WorkingDirectory = stateDir;
                StateDirectory = containerName;
                StateDirectoryMode = "0750";
              };
            };

            systemd.services.barnaby-memory = lib.mkIf icfg.memory.enable {
              description = "Turn idle Barnaby sessions into memory notes (${name})";
              path = [
                icfg.piPackage
                pkgs.zstd
              ];
              environment = serviceEnvironment;
              serviceConfig = {
                Type = "oneshot";
                EnvironmentFile = serviceEnvironmentFiles;
                ExecStart = lib.escapeShellArgs [
                  "${pkgs.python3}/bin/python3"
                  "${../memory}/session-compact.py"
                  "--source"
                  "${name}:chat:${icfg.environment.BARNABY_PI_SESSION_DIR}:${memoryDir}"
                  "--archive"
                  "${stateDir}/session-archive"
                  "--model"
                  memoryModel
                  "run"
                  "--limit"
                  "10"
                ];
                User = "barnaby";
                Group = "barnaby";
                WorkingDirectory = stateDir;
              };
            };

            systemd.timers.barnaby-memory = lib.mkIf icfg.memory.enable {
              wantedBy = [ "timers.target" ];
              timerConfig = {
                OnCalendar = "03:00";
                Persistent = true;
              };
            };

            environment.systemPackages = [
              barnabyPkg
              icfg.piPackage
            ]
            ++ icfg.extraPackages;
          };
      };

    };

  instanceConfigs = lib.mapAttrs mkInstanceConfig enabledInstances;
in
{
  options.services.barnaby.instances = lib.mkOption {
    type = lib.types.attrsOf (lib.types.submodule instanceModule);
    default = { };
    description = "Barnaby Matrix bot instances. Each instance runs in its own container.";
    example = lib.literalExpression ''
      {
        mybot = {
          enable = true;
          piPackage = llm-agents.packages.''${system}.pi;
          environment.BARNABY_MATRIX_HOMESERVER = "https://matrix.example.com";
        };
      }
    '';
  };

  # Aggregate host-level config from all instances.
  config = lib.mkIf (instanceConfigs != { }) {
    assertions = lib.concatLists (lib.mapAttrsToList (_: ic: ic.assertions) instanceConfigs);

    environment.systemPackages = lib.concatLists (
      lib.mapAttrsToList (_: ic: ic.systemPackages) instanceConfigs
    );

    systemd.tmpfiles.rules = lib.concatLists (
      lib.mapAttrsToList (_: ic: ic.tmpfilesRules) instanceConfigs
    );

    # Work around stale machined registration after unclean shutdown.
    systemd.services = lib.mapAttrs' (
      name: ic:
      lib.nameValuePair "container@${name}" {
        preStart = lib.mkBefore ic.containerPreStart;
      }
    ) instanceConfigs;

    containers = lib.mapAttrs (_: ic: ic.container) instanceConfigs;
  };
}
