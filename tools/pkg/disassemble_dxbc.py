"""Disassemble a DXBC blob with d3dcompiler_47 (Windows).

Default path is the live chest dye PS dumped by hook v10.
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

DEFAULT = Path(__file__).with_name("objs") / "live_chest_ps.bin"


def disassemble(data: bytes) -> str:
    d3d = ctypes.WinDLL("d3dcompiler_47.dll")
    fn = d3d.D3DDisassemble
    fn.argtypes = [
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_uint,
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    fn.restype = ctypes.c_long
    buf = ctypes.create_string_buffer(data)
    blob = ctypes.c_void_p()
    hr = fn(ctypes.cast(buf, ctypes.c_void_p), len(data), 0, None, ctypes.byref(blob))
    if hr < 0:
        raise SystemExit(f"D3DDisassemble failed HRESULT=0x{hr & 0xFFFFFFFF:08X}")
    obj = ctypes.cast(blob.value, ctypes.POINTER(ctypes.c_void_p))
    vtbl = ctypes.cast(obj[0], ctypes.POINTER(ctypes.c_void_p))
    get_ptr = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p)(vtbl[3])
    get_size = ctypes.CFUNCTYPE(ctypes.c_size_t, ctypes.c_void_p)(vtbl[4])
    return ctypes.string_at(get_ptr(blob.value), get_size(blob.value)).decode(
        "utf-8", "replace"
    )


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    text = disassemble(path.read_bytes())
    out = path.with_suffix(".asm")
    out.write_text(text, encoding="utf-8")
    print(f"{path} -> {out}  {len(text)} chars")


if __name__ == "__main__":
    main()
