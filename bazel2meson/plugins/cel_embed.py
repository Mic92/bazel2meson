"""Write the bytes of a file as a C array initializer: cel_embed.py IN OUT."""

import sys
from pathlib import Path

data = Path(sys.argv[1]).read_bytes()
Path(sys.argv[2]).write_text(", ".join(f"0x{byte:02x}" for byte in data) + "\n")
