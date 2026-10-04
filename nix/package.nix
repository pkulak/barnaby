{
  lib,
  buildGoModule,
}:
buildGoModule (finalAttrs: {
  pname = "barnaby";
  version = "0.3.0";
  ldflags = [ "-X main.version=${finalAttrs.version}" ];
  src = lib.fileset.toSource {
    root = ./..;
    fileset = lib.fileset.unions [
      ./../go.mod
      ./../go.sum
      (lib.fileset.fileFilter (file: file.hasExt "go") ./..)
      (lib.fileset.fileFilter (file: file.hasExt "sql") ./..)
      ./../.golangci.yml
      ./../testdata
      ./../SOUL.md
    ];
  };
  vendorHash = "sha256-JZNOOJH1bkABgzLKq7c19b9cdQ4nBXUMRiHn/tNbluE=";
  subPackages = [ "." ];
  tags = [ "goolm" ];

  # buildGoModule only passes `tags` to `go install`, not to GOFLAGS,
  # so `nix develop` doesn't get them. Also replace -mod=vendor with
  # -mod=mod since we don't vendor locally.
  shellHook = ''
    export GOFLAGS="-mod=mod -trimpath -tags=goolm"
  '';

  postInstall = ''
    mkdir -p $out/share/barnaby
    cp SOUL.md $out/share/barnaby/SOUL.md
  '';

  meta = {
    description = "Matrix bot bridging messages to an AI coding agent via pi RPC";
    homepage = "https://github.com/pkulak/barnaby";
    mainProgram = "barnaby";
  };
})
