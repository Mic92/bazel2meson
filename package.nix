{
  lib,
  buildPythonApplication,
  uv-build,
}:

buildPythonApplication {
  pname = "bazel2meson";
  version = "0.1.0";
  pyproject = true;

  src = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.unions [
      ./bazel2meson
      ./pyproject.toml
      ./README.md
    ];
  };

  build-system = [ uv-build ];

  pythonImportsCheck = [ "bazel2meson" ];

  meta = {
    description = "Generate a Meson build from a project's Bazel build files";
    homepage = "https://github.com/Mic92/bazel2meson";
    mainProgram = "bazel2meson";
  };
}
