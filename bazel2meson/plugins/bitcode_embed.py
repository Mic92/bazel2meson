"""Write a header holding a file as a std::string.

Stands in for XLA's embed_bitcode tool, which needs LLVM and parts of XLA to
build and puts the data into an object file; a string literal does the same.

Usage: bitcode_embed.py INPUT OUT_H VARIABLE NAMESPACE
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    source, header, variable, namespace = sys.argv[1:5]
    data = Path(source).read_bytes()
    # Octal escapes have a fixed length, so no digit after one can extend it.
    lines = ["".join(f"\\{byte:03o}" for byte in data[i : i + 32]) for i in range(0, len(data), 32)]
    literal = "\n".join(f'  "{line}"' for line in lines) or '  ""'
    Path(header).write_text(
        "#pragma once\n\n#include <string>\n\n"
        f"namespace {namespace} {{\n\n"
        f"inline const std::string {variable}(\n{literal},\n  {len(data)});\n\n"
        f"}}  // namespace {namespace}\n"
    )


if __name__ == "__main__":
    main()
