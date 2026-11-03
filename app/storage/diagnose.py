"""Diagnóstico de solo lectura de un almacenamiento: estructura, extensiones, tamaños y patrones de nombres."""

import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.storage.base import ObjectInfo

_SAMPLES_PER_PREFIX = 5


def name_pattern(filename: str) -> str:
    """Firma del nombre: letras -> a, dígitos -> 9, se colapsan repeticiones. 'base1-58.webp' -> 'a9-9.a'."""
    stem, dot, ext = filename.rpartition(".")
    if not dot:
        stem, ext = filename, ""
    sig = re.sub(r"[A-Za-z]+", "a", stem)
    sig = re.sub(r"[0-9]+", "9", sig)
    return f"{sig}.{ext.lower()}" if ext else sig


@dataclass
class DiagnosisReport:
    scanned: int = 0
    truncated: bool = False
    total_bytes: int = 0
    extensions: Counter = field(default_factory=Counter)
    top_prefixes: Counter = field(default_factory=Counter)
    depths: Counter = field(default_factory=Counter)
    patterns: Counter = field(default_factory=Counter)
    pattern_examples: dict = field(default_factory=dict)
    samples: dict = field(default_factory=lambda: defaultdict(list))
    size_stats: dict = field(default_factory=dict)
    duplicate_etags: int = 0

    def to_dict(self) -> dict:
        return {
            "objects": self.scanned,
            "truncated_by_limit": self.truncated,
            "total_bytes": self.total_bytes,
            "extensions": dict(self.extensions.most_common()),
            "prefixes": dict(self.top_prefixes.most_common(50)),
            "depth_distribution": dict(sorted(self.depths.items())),
            "size_bytes": self.size_stats,
            "name_patterns": [
                {"pattern": p, "count": n, "example": self.pattern_examples[p]}
                for p, n in self.patterns.most_common(15)
            ],
            "sample_keys_by_prefix": dict(self.samples),
            "objects_sharing_etag_with_another": self.duplicate_etags,
        }


def diagnose(objects: Iterable[ObjectInfo], max_objects: int = 500_000) -> DiagnosisReport:
    report = DiagnosisReport()
    sizes: list[int] = []
    etags: Counter = Counter()
    for obj in objects:
        if report.scanned >= max_objects:
            report.truncated = True
            break
        report.scanned += 1
        report.total_bytes += obj.size
        sizes.append(obj.size)
        parts = obj.key.split("/")
        filename = parts[-1]
        prefix = parts[0] + "/" if len(parts) > 1 else "(raíz)"
        ext = filename.rpartition(".")[2].lower() if "." in filename else "(sin extensión)"
        report.extensions[ext] += 1
        report.top_prefixes[prefix] += 1
        report.depths[len(parts) - 1] += 1
        pattern = name_pattern(filename)
        report.patterns[pattern] += 1
        report.pattern_examples.setdefault(pattern, obj.key)
        if len(report.samples[prefix]) < _SAMPLES_PER_PREFIX:
            report.samples[prefix].append(obj.key)
        if obj.etag:
            etags[obj.etag] += 1
    if sizes:
        report.size_stats = {
            "min": min(sizes),
            "median": int(statistics.median(sizes)),
            "p95": sorted(sizes)[int(len(sizes) * 0.95) - 1] if len(sizes) >= 20 else max(sizes),
            "max": max(sizes),
        }
    report.duplicate_etags = sum(n for n in etags.values() if n > 1)
    return report


def render_text(report: DiagnosisReport, bucket: str) -> str:
    d = report.to_dict()
    lines = [
        f"Bucket/raíz: {bucket}",
        f"Objetos analizados: {d['objects']}" + (" (cortado por --max-objects)" if d["truncated_by_limit"] else ""),
        f"Tamaño total: {d['total_bytes'] / 1_048_576:.1f} MiB",
    ]
    if d["size_bytes"]:
        s = d["size_bytes"]
        lines.append(f"Tamaños (bytes): min {s['min']} · mediana {s['median']} · p95 {s['p95']} · máx {s['max']}")
    lines.append("\nExtensiones:")
    lines += [f"  .{ext:<16} {n}" for ext, n in d["extensions"].items()]
    lines.append("\nPrefijos de primer nivel (hasta 50):")
    lines += [f"  {p:<40} {n}" for p, n in d["prefixes"].items()]
    lines.append(f"\nProfundidad (nº de carpetas -> objetos): {d['depth_distribution']}")
    lines.append("\nPatrones de nombre (a=letras, 9=dígitos):")
    lines += [f"  {p['pattern']:<28} {p['count']:>7}  ej: {p['example']}" for p in d["name_patterns"]]
    lines.append("\nEjemplos de keys por prefijo:")
    for prefix, keys in list(d["sample_keys_by_prefix"].items())[:20]:
        lines.append(f"  {prefix}")
        lines += [f"    {k}" for k in keys]
    lines.append(
        f"\nObjetos que comparten ETag con otro (posibles duplicados): {d['objects_sharing_etag_with_another']}"
    )
    return "\n".join(lines)
