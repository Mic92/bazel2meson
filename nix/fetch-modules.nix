# Fetch the repositories pinned by `bazel2meson-lock` and prepare them the
# way Bazel would: unpack, patch, add BUILD files.
{
  lib,
  stdenvNoCC,
  fetchurl,
  unzip,
}:

{
  # Path of the sources.json written by bazel2meson-lock.
  sources,
  # Source of the project itself; patches and BUILD files for a dependency
  # may live there.
  src,
  pname,
}:

let
  # A patch or BUILD file is either downloaded or part of another repository.
  file =
    spec:
    if spec ? url then
      fetchurl { inherit (spec) url hash; }
    else
      "${if spec.repo == "" then src else modules.${spec.repo}}/${spec.file}";

  fetchModule =
    name: spec:
    stdenvNoCC.mkDerivation {
      pname = "${pname}-module-${name}";
      inherit (spec) version;
      src = fetchurl { inherit (spec) urls hash; };
      nativeBuildInputs = [ unzip ];
      unpackPhase = ''
        runHook preUnpack
        mkdir unpacked
        cd unpacked
        unpackFile "$src"
        cd ${lib.escapeShellArg "./${spec.stripPrefix}"}
        runHook postUnpack
      '';
      patches = map file spec.patches;
      patchFlags = [ "-p${toString spec.patchStrip}" ];
      postPatch =
        lib.optionalString (spec ? moduleFile) ''
          install -Dm644 ${fetchurl { inherit (spec.moduleFile) url sha256; }} MODULE.bazel
        ''
        + lib.concatMapStrings (o: ''
          install -Dm644 ${file o} ${lib.escapeShellArg o.path}
        '') spec.overlay;
      dontConfigure = true;
      dontBuild = true;
      dontFixup = true;
      installPhase = ''
        cp -r . $out
      '';
    };

  modules = lib.mapAttrs fetchModule (lib.importJSON sources);
in
modules
