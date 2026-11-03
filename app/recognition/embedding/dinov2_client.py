# ruff: noqa: E501
"""Cliente de DINOv2-small: lanza `dinov2_worker.py` con el Python del entorno ML y habla con él por pipes.

- Carga DIFERIDA: no se crea ningún proceso hasta la primera llamada a `embed`.
- Concurrencia: un cerrojo serializa las peticiones (una inferencia a la vez en la GPU de 2 GB).
- Se descarga solo: tras `idle_seconds` sin uso el proceso termina y se libera la VRAM; se relanza en la siguiente petición.
- Cualquier fallo (ejecutable ausente, pesos ausentes, revisión distinta, tiempo agotado, proceso caído) se informa como `ExtractorUnavailable`.
"""

import atexit
import json
import logging
import struct
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

import numpy as np

from app.recognition.embedding.base import EmbeddingSpec, ExtractorUnavailable, validate_card_image

log = logging.getLogger("vision.embedding")

DINOV2_MODEL_NAME = "dinov2-small"
DINOV2_REVISION = "ed25f3a31f01632728cabb09d1542f84ab7b0056"  # pesos auditados (ver docs/recognition/06)
# versión = revisión de pesos + pooling + tamaño de entrada + preprocesado (p1: OpenCV INTER_AREA, normalización ImageNet)
DINOV2_SPEC = EmbeddingSpec(DINOV2_MODEL_NAME, f"{DINOV2_REVISION[:7]}-clsmean-224x308-p1", 768)
WORKER_SCRIPT = Path(__file__).with_name("dinov2_worker.py")


def default_ml_python() -> Path:
    return Path.home() / "vision-ml-lab" / "venv" / "Scripts" / "python.exe"


def default_ml_home() -> Path:
    return Path.home() / "vision-ml-lab" / "hf-home"


class Dinov2WorkerExtractor:
    spec = DINOV2_SPEC

    def __init__(
        self,
        python: str | Path | None = None,
        hf_home: str | Path | None = None,
        device: str = "auto",
        min_free_vram_mb: int = 700,
        idle_seconds: float = 120.0,
        startup_timeout: float = 120.0,
        request_timeout: float = 60.0,
        command: list[str] | None = None,  # solo pruebas: sustituye el comando del worker
        expected_revision: str | None = DINOV2_REVISION,
    ):
        self.python = Path(python) if python else default_ml_python()
        self.hf_home = Path(hf_home) if hf_home else default_ml_home()
        self.device_pref, self.min_free_vram_mb = device, min_free_vram_mb
        self.idle_seconds, self.startup_timeout, self.request_timeout = idle_seconds, startup_timeout, request_timeout
        self._command, self._expected_revision = command, expected_revision
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._stderr = None
        self._timer: threading.Timer | None = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dinov2-read")
        self.hello: dict | None = None
        self.last_device: str | None = None
        self.starts = 0
        atexit.register(self.close)

    # ---- ciclo de vida
    @property
    def loaded(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _build_command(self) -> list[str]:
        if self._command:
            return self._command
        if not self.python.exists():
            raise ExtractorUnavailable(f"no existe el Python del entorno ML: {self.python}")
        if not (self.hf_home / "hub").exists():
            raise ExtractorUnavailable(f"no existe la caché de pesos: {self.hf_home}")
        return [str(self.python), str(WORKER_SCRIPT), "--device", self.device_pref, "--min-free-vram-mb", str(self.min_free_vram_mb), "--hf-home", str(self.hf_home)]

    def _read(self, n: int, timeout: float) -> bytes:
        assert self._proc is not None and self._proc.stdout is not None
        fut = self._pool.submit(self._proc.stdout.read, n)
        try:
            data = fut.result(timeout=timeout)
        except FutureTimeout:
            self._stop()
            raise ExtractorUnavailable(f"el worker no respondió en {timeout:.0f} s") from None
        if len(data) != n:
            raise ExtractorUnavailable("el worker terminó inesperadamente" + self._stderr_tail())
        return data

    def _stderr_tail(self) -> str:
        try:
            self._stderr.flush()
            self._stderr.seek(0)
            tail = self._stderr.read().decode("utf-8", "replace").strip().splitlines()[-3:]
            return ": " + " | ".join(tail) if tail else ""
        except Exception:  # noqa: BLE001
            return ""

    def _start(self) -> None:
        cmd = self._build_command()
        self._stderr = tempfile.TemporaryFile()
        env = {"HF_HUB_OFFLINE": "1", "PYTHONIOENCODING": "utf-8", "SYSTEMROOT": _env("SYSTEMROOT"), "PATH": _env("PATH"), "TEMP": _env("TEMP")}
        try:
            self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._stderr, env=env)  # noqa: S603
        except OSError as exc:
            raise ExtractorUnavailable(f"no se pudo lanzar el worker: {exc}") from None
        self.starts += 1
        try:
            (n,) = struct.unpack("<I", self._read(4, self.startup_timeout))
            self.hello = json.loads(self._read(n, self.startup_timeout))
        except ExtractorUnavailable:
            self._stop()
            raise
        if self.hello.get("dim") != self.spec.dim:
            self._stop()
            raise ExtractorUnavailable(f"dimensión inesperada del worker: {self.hello.get('dim')} (se esperaba {self.spec.dim})")
        if self._expected_revision and self.hello.get("revision") != self._expected_revision:
            got = self.hello.get("revision")
            self._stop()
            raise ExtractorUnavailable(f"revisión de pesos {got} distinta de la auditada {self._expected_revision[:7]}: otra versión de embeddings")
        log.info("worker DINOv2 listo device=%s vram_libre_mb=%s", self.hello.get("device"), self.hello.get("vram_free_mb"))

    def _stop(self) -> None:
        proc, self._proc = self._proc, None
        if self._timer:
            self._timer.cancel()
            self._timer = None
        if proc is not None:
            try:
                if proc.stdin:
                    proc.stdin.write(struct.pack("<III", 0, 0, 0))
                    proc.stdin.flush()
            except OSError:
                pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            for stream in (proc.stdin, proc.stdout):
                try:
                    stream and stream.close()
                except OSError:
                    pass

    def _arm_idle_timer(self) -> None:
        if self._timer:
            self._timer.cancel()
        if self.idle_seconds > 0:
            self._timer = threading.Timer(self.idle_seconds, self._idle_unload)
            self._timer.daemon = True
            self._timer.start()

    def _idle_unload(self) -> None:
        if self._lock.acquire(blocking=False):  # si hay una petición en curso, no se descarga
            try:
                log.info("worker DINOv2 inactivo: se descarga para liberar VRAM")
                self._stop()
            finally:
                self._lock.release()

    def close(self) -> None:
        with self._lock:
            self._stop()

    # ---- inferencia
    def embed(self, card_bgr: np.ndarray) -> np.ndarray:
        validate_card_image(card_bgr)
        with self._lock:
            if not self.loaded:
                self._start()
            assert self._proc is not None and self._proc.stdin is not None
            h, w, c = card_bgr.shape
            try:
                self._proc.stdin.write(struct.pack("<III", h, w, c) + np.ascontiguousarray(card_bgr).tobytes())
                self._proc.stdin.flush()
            except OSError:
                self._stop()
                raise ExtractorUnavailable("el worker cerró la conexión") from None
            status, n = struct.unpack("<II", self._read(8, self.request_timeout))
            payload = self._read(n, self.request_timeout)
            if status != 0:
                raise ExtractorUnavailable("error del worker: " + payload.decode("utf-8", "replace"))
            self.last_device = "cuda" if payload[0] == 1 else "cpu"
            vec = np.frombuffer(payload[1:], np.float32).copy()
            self._arm_idle_timer()
            if vec.shape != (self.spec.dim,) or not np.isfinite(vec).all():
                raise ExtractorUnavailable("el worker devolvió un vector inválido")
            return vec


def _env(name: str) -> str:
    import os

    return os.environ.get(name, "")


def worker_command_for_tests(script: Path) -> list[str]:
    return [sys.executable, str(script)]
