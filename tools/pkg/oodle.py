"""
Binds the game's installed Oodle codec so package blocks can be decoded outside the game.

The DLL used is the game's own `oo2core_3_win64.dll`, not a redistributed copy, so no Oodle binary
is vendored into this repo. Calls mirror
`Sunrise/src/middleware/compression/oodle/oodle_runtime.cpp` exactly, including the thread phase and
the disabled fuzz-safety and CRC checks, so the results agree with what the game itself produces.

A block does not record its decompressed size. Sunrise recovers it by asking for progressively
smaller multiples of 0x4000 until a decode reports exactly the size requested; the same search runs
here.
"""
from __future__ import annotations

import ctypes
from pathlib import Path

BLOCK_SIZE = 0x40000
DECODE_STEP = 0x4000
KRAKEN = 8
NORMAL_LEVEL = 4
COMPLETE_THREAD_PHASE = 3

DEFAULT_DLL = Path(r"C:\Sunrise\bin\x64\oo2core_3_win64.dll")


class OodleError(Exception):
    """Raised when the codec is unavailable or refuses a buffer."""


class Oodle:
    """One loaded Oodle module and the three exports the package pipeline needs."""

    def __init__(self, dll_path: str | Path = DEFAULT_DLL) -> None:
        self.path = Path(dll_path)
        if not self.path.is_file():
            raise OodleError(f"no Oodle codec at {self.path}")
        self._lib = ctypes.WinDLL(str(self.path))

        self._decompress = self._lib.OodleLZ_Decompress
        self._decompress.restype = ctypes.c_int64
        self._decompress.argtypes = [
            ctypes.c_void_p, ctypes.c_int64,   # input, input size
            ctypes.c_void_p, ctypes.c_int64,   # output, output size
            ctypes.c_int, ctypes.c_int,        # fuzz safe, check CRC
            ctypes.c_int64,                    # verbosity
            ctypes.c_void_p, ctypes.c_int64,   # decode buffer
            ctypes.c_void_p, ctypes.c_void_p,  # callback, callback context
            ctypes.c_void_p, ctypes.c_int64,   # decoder memory
            ctypes.c_int,                      # thread phase
        ]

        self._capacity = self._lib.OodleLZ_GetCompressedBufferSizeNeeded
        self._capacity.restype = ctypes.c_int64
        self._capacity.argtypes = [ctypes.c_int64]

        self._compress = self._lib.OodleLZ_Compress
        self._compress.restype = ctypes.c_int64
        self._compress.argtypes = [
            ctypes.c_int,                      # compressor
            ctypes.c_void_p, ctypes.c_int64,   # input, input size
            ctypes.c_void_p,                   # output
            ctypes.c_int,                      # level
            ctypes.c_void_p, ctypes.c_void_p,  # options, dictionary
            ctypes.c_void_p,                   # long range matcher
            ctypes.c_void_p, ctypes.c_int64,   # scratch
        ]

    def decompress_exact(self, data: bytes, size: int) -> bytes:
        """@return `size` plaintext bytes, or raises when the codec will not produce exactly that."""
        out = ctypes.create_string_buffer(size)
        produced = self._decompress(
            data, len(data), out, size, 0, 0, 0, None, 0, None, None, None, 0, COMPLETE_THREAD_PHASE
        )
        if produced != size:
            raise OodleError(f"decode returned {produced}, wanted {size}")
        return out.raw[:size]

    def decompress_block(self, data: bytes, cap: int = BLOCK_SIZE) -> bytes:
        """
        Decodes one package block, whose plaintext size the format does not record.

        @param data Stored block body.
        @param cap Largest plaintext the block can hold.
        @return The block plaintext.
        """
        size = (cap // DECODE_STEP) * DECODE_STEP
        while size >= DECODE_STEP:
            try:
                return self.decompress_exact(data, size)
            except OodleError:
                size -= DECODE_STEP
        raise OodleError(f"no decode size fit a {len(data)}-byte block")

    def compress(self, data: bytes) -> bytes:
        """@return `data` Kraken-compressed at the level the game's own encoder uses."""
        required = self._capacity(len(data))
        if required <= 0:
            raise OodleError(f"codec refused a capacity query for {len(data)} bytes")
        out = ctypes.create_string_buffer(required)
        produced = self._compress(
            KRAKEN, data, len(data), out, NORMAL_LEVEL, None, None, None, None, 0
        )
        if produced <= 0:
            raise OodleError(f"compression failed for {len(data)} bytes")
        return out.raw[:produced]
