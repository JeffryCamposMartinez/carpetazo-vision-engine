# ruff: noqa: E501
"""Worker FALSO que habla el mismo protocolo que app/recognition/embedding/dinov2_worker.py, sin torch. Solo para pruebas.

Modos (--mode): ok | bad_revision | crash_on_request | hang_on_request | error_on_request | wrong_dim
"""

import json
import struct
import sys

import numpy as np

REVISION = "ed25f3a31f01632728cabb09d1542f84ab7b0056"


def main() -> int:
    out = sys.stdout.buffer
    mode = sys.argv[sys.argv.index("--mode") + 1] if "--mode" in sys.argv else "ok"
    dim = 384 if mode == "wrong_dim" else 768
    hello = json.dumps({"model_id": "facebook/dinov2-small", "revision": "0" * 40 if mode == "bad_revision" else REVISION, "device": "cpu", "dim": dim, "torch": "fake", "transformers": "fake", "vram_free_mb": None}).encode()
    out.write(struct.pack("<I", len(hello)) + hello)
    out.flush()
    stdin = sys.stdin.buffer
    while True:
        head = stdin.read(12)
        if len(head) < 12:
            return 0
        h, w, c = struct.unpack("<III", head)
        if h == 0:
            return 0
        data = np.frombuffer(stdin.read(h * w * c), np.uint8)
        if mode == "crash_on_request":
            return 3
        if mode == "hang_on_request":
            import time

            time.sleep(600)
        if mode == "error_on_request":
            msg = b"RuntimeError: fallo simulado"
            out.write(struct.pack("<II", 1, len(msg)) + msg)
            out.flush()
            continue
        rng = np.random.default_rng(int(data.astype(np.uint64).sum() % (2**32)))
        v = rng.normal(size=dim).astype(np.float32)
        v /= np.linalg.norm(v)
        out.write(struct.pack("<II", 0, 1 + 4 * dim) + bytes([0]) + v.tobytes())
        out.flush()


if __name__ == "__main__":
    sys.exit(main())
