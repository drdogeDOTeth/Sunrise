"""Print OSGN / ISGN / OSG5 from a dumped DXBC blob."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

CHUNKS = {b"ISGN", b"OSGN", b"OSG5", b"PCSG", b"SHEX", b"SHDR"}


def chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    if data[:4] != b"DXBC":
        raise SystemExit("not DXBC")
    # DXBC: magic[4] + digest[16] + version[4] + fileSize[4] + chunkCount[4] + offsets[]
    count = struct.unpack_from("<I", data, 28)[0]
    out: list[tuple[bytes, bytes]] = []
    for i in range(count):
        offset = struct.unpack_from("<I", data, 32 + i * 4)[0]
        fourcc = data[offset : offset + 4]
        size = struct.unpack_from("<I", data, offset + 4)[0]
        out.append((fourcc, data[offset + 8 : offset + 8 + size]))
    return out


def mask_text(mask: int) -> str:
    return "".join("xyzw"[i] if mask & (1 << i) else "_" for i in range(4))


def parse_signature(body: bytes) -> list[str]:
    if len(body) < 8:
        return []
    count = struct.unpack_from("<I", body, 0)[0]
    lines: list[str] = []
    for i in range(count):
        at = 8 + i * 24
        if at + 24 > len(body):
            break
        name_off, semantic_index, sysval, comp, register, mask = struct.unpack_from(
            "<IIIII B", body, at
        )
        name = body[name_off : body.find(b"\x00", name_off)].decode("ascii", "replace")
        lines.append(
            f"  {name}{semantic_index} reg={register} mask={mask_text(mask)} "
            f"sys={sysval} type={comp}"
        )
    return lines


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name(
        "objs"
    ) / "live_chest_ps.bin"
    data = path.read_bytes()
    print(f"{path}  {len(data)} B")
    for fourcc, body in chunks(data):
        print(fourcc.decode("ascii", "replace"), len(body), "B")
        if fourcc in {b"ISGN", b"OSGN", b"OSG5", b"PCSG"}:
            print("\n".join(parse_signature(body)))


if __name__ == "__main__":
    main()
