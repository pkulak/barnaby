{
  description = "Barnaby - Personal AI assistent connecting via Matrix";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

    treefmt-nix.url = "github:numtide/treefmt-nix";
    treefmt-nix.inputs.nixpkgs.follows = "nixpkgs";
  };

  outputs =
    {
      self,
      nixpkgs,
      treefmt-nix,
    }:
    let
      forAllSystems = nixpkgs.lib.genAttrs [
        "x86_64-linux"
        "aarch64-linux"
      ];
    in
    {
      packages = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        {
          barnaby = pkgs.callPackage ./nix/package.nix { };
          extension-reminders = pkgs.callPackage ./nix/extension-reminders.nix { };
          default = self.packages.${system}.barnaby;
        }
      );

      formatter = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        (treefmt-nix.lib.evalModule pkgs ./nix/treefmt.nix).config.build.wrapper
      );

      devShells = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        {
          default = pkgs.mkShell {
            inputsFrom = [ self.packages.${system}.barnaby ];
            packages = [
              pkgs.golangci-lint
              pkgs.sqlc
            ];
          };
        }
      );

      checks = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        {
          formatting = (treefmt-nix.lib.evalModule pkgs ./nix/treefmt.nix).config.build.check self;

          golangci-lint = self.packages.${system}.barnaby.overrideAttrs (old: {
            nativeBuildInputs = old.nativeBuildInputs ++ [ pkgs.golangci-lint ];
            outputs = [ "out" ];
            buildPhase = ''
              HOME=$TMPDIR
              golangci-lint run --build-tags goolm
            '';
            installPhase = ''
              touch $out
            '';
          });

          weather = pkgs.runCommand "weather-tests" { nativeBuildInputs = [ pkgs.python3 ]; } ''
            cd ${./skills/weather/scripts}
            python3 -B -m unittest -v test_weather
            touch $out
          '';
        }
      );

      nixosModules.default = nixpkgs.lib.modules.importApply ./nix/module.nix { inherit self; };
    };
}
