"""Embed files in a C++ source, like XLA's filewrapper tool.

Usage: file_embed.py NAME INCLUDE_PATH NAMESPACE OUT_CC OUT_H FILE...

Writes NAME_create(), returning a table of the files ended by an entry
without name, and NAME_size(). File names lose their directory.
"""

import hashlib
import sys
from pathlib import Path


def literal(data: bytes) -> str:
    """A C string literal split over lines."""
    lines = []
    for start in range(0, len(data), 64):
        chunk = "".join(
            chr(c) if 32 <= c < 127 and c not in b'"?\\' else f"\\{c:03o}" for c in data[start : start + 64]
        )
        lines.append(f'"{chunk}"')
    return "\n".join(lines) or '""'


def main() -> None:
    name, include_path, namespace, out_cc, out_h, *files = sys.argv[1:]
    spaces = namespace.split("::") if namespace else []
    intro = "".join(f"namespace {ns} {{\n" for ns in spaces)
    outro = "".join(f"}}  // namespace {ns}\n" for ns in reversed(spaces))
    guard = "__STRUCT_FILE_TOC_" + "_".join([*spaces, name]) + "_"
    Path(out_h).write_text(
        f'#include "xla/tsl/util/file_toc.h"\n#ifndef {guard}\n#define {guard}\n\n{intro}'
        f"const struct FileToc* {name}_create();\nsize_t {name}_size();\n{outro}\n#endif  // {guard}\n"
    )
    data = [f'#include "{include_path}/{name}.h"\n\nnamespace {{']
    toc = []
    for index, file in enumerate(files):
        content = Path(file).read_bytes()
        digest = ", ".join(f"0x{b:02x}" for b in hashlib.md5(content).digest())
        data.append(f"alignas(16) const char data{index}[] =\n{literal(content)};")
        toc.append(f'  {{ "{Path(file).name}", data{index}, {len(content)}, {{ {digest} }} }},')
    data += [
        f"const struct FileToc toc[{len(files) + 1}] = {{",
        *toc,
        "  { nullptr, nullptr, 0, {} }",
        "};",
        "}  // namespace",
        "",
        f"{intro}const struct FileToc* {name}_create() {{ return toc; }}",
        f"size_t {name}_size() {{ return {len(files)}; }}",
        outro,
    ]
    Path(out_cc).write_text("\n".join(data))


if __name__ == "__main__":
    main()
