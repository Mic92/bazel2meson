# perf_to_profile built by Meson from a build description generated out of
# its Bazel files. Shows protobuf code generation.
{
  lib,
  stdenv,
  fetchFromGitHub,
  meson,
  ninja,
  pkg-config,
  bazel2meson,
  protobuf,
  abseil-cpp,
  boringssl,
  gflags,
  zlib,
  elfutils,
}:

stdenv.mkDerivation (finalAttrs: {
  pname = "perf_data_converter";
  version = "0-unstable-2026-11-12";

  src = fetchFromGitHub {
    owner = "google";
    repo = "perf_data_converter";
    rev = "2e7cb63c9939dcd69080baa4de706db9f2385313";
    hash = "sha256-K1Ak06UmMfQ4VUZFXvwTOJJ4Z8ndlYc/kB/U8Lmt3WQ=";
  };

  postPatch = ''
    bazel2meson \
      --config ${./perf_data_converter.toml} \
      --source . \
      --cpu ${stdenv.hostPlatform.parsed.cpu.name} \
      --version ${finalAttrs.version}
  '';

  nativeBuildInputs = [
    meson
    ninja
    pkg-config
    bazel2meson
    protobuf
  ];

  buildInputs = [
    abseil-cpp
    protobuf
    boringssl
    gflags
    zlib
    elfutils
  ];

  doInstallCheck = true;
  installCheckPhase = ''
    $out/bin/perf_to_profile --help > help.txt 2>&1 || true
    cat help.txt
    test -s help.txt
  '';

  meta = {
    description = "Tool to convert Linux perf files to the profile.proto format used by pprof, built with Meson";
    homepage = "https://github.com/google/perf_data_converter";
    license = lib.licenses.bsd3;
    mainProgram = "perf_to_profile";
    platforms = lib.platforms.linux;
  };
})
