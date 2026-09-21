"""Download the local sentence encoder into the Semantic Models directory.

Run once after installing, or as a build step before deploying:

    python scripts/fetch_model.py

The quantised ONNX export is chosen deliberately. It is roughly 23 MB against
90 MB for the float weights and about 1 GB for the PyTorch stack, which is what
lets deduplication run with no API key inside a serverless bundle. Accuracy
loss at this task, judging whether two headlines describe the same event, is
not measurable against the float version.

The repository also holds TensorFlow, Rust, OpenVINO and unquantised variants,
about 1 GB in total. Only the four files below are fetched.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = "sentence-transformers/all-MiniLM-L6-v2"
BASE = f"https://huggingface.co/{REPO}/resolve/main"

# The weights are tried in order and the first that downloads wins, so a
# renamed quantised export degrades to the float one instead of failing.
WEIGHT_CANDIDATES = (
    "onnx/model_qint8_avx512.onnx",
    "onnx/model_quantized.onnx",
    "onnx/model.onnx",
)

# tokenizer.json is the only one the runtime strictly needs. The rest are small
# and make the directory usable by sentence-transformers as well.
SUPPORT_FILES = (
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "config.json",
    "vocab.txt",
)


def _target_dir(override: str = "") -> Path:
    if override:
        return Path(override)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from server import settings

    return Path(settings.LOCAL_EMBED_MODEL_DIR)


def _download(url: str, destination: Path, *, quiet: bool = False) -> bool:
    import requests

    destination.parent.mkdir(parents=True, exist_ok=True)
    # Written to a temporary name and moved into place, so an interrupted run
    # cannot leave a half file that looks valid to the runtime's is_available
    # check and then fails to load.
    staging = destination.with_suffix(destination.suffix + ".part")
    try:
        with requests.get(url, stream=True, timeout=120) as response:
            if response.status_code != 200:
                return False
            total = int(response.headers.get("content-length") or 0)
            written = 0
            with staging.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1 << 20):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    written += len(chunk)
                    if not quiet and total:
                        percent = 100 * written / total
                        print(
                            f"\r  {destination.name}: {percent:5.1f}% "
                            f"({written / 1e6:.1f} MB)",
                            end="",
                            flush=True,
                        )
        staging.replace(destination)
        if not quiet:
            print(f"\r  {destination.name}: done ({written / 1e6:.1f} MB)      ")
        return True
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        if not quiet:
            print(f"\r  {destination.name}: failed ({exc})")
        staging.unlink(missing_ok=True)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default="", help="Where to put the model.")
    parser.add_argument("--force", action="store_true", help="Re-download existing files.")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    target = _target_dir(args.dir)
    target.mkdir(parents=True, exist_ok=True)
    if not args.quiet:
        print(f"Downloading {REPO} into {target}")

    weights_present = any(target.glob("*.onnx"))
    if weights_present and not args.force:
        if not args.quiet:
            print("  weights already present, skipping (use --force to replace)")
    else:
        for candidate in WEIGHT_CANDIDATES:
            name = candidate.split("/")[-1]
            if _download(f"{BASE}/{candidate}", target / name, quiet=args.quiet):
                break
        else:
            print("Could not download any ONNX weights file.", file=sys.stderr)
            return 1

    for name in SUPPORT_FILES:
        destination = target / name
        if destination.exists() and not args.force:
            continue
        # Several of these are optional in the repository, so a miss is fine.
        _download(f"{BASE}/{name}", destination, quiet=args.quiet)

    if not (target / "tokenizer.json").is_file():
        print("tokenizer.json is missing, the local embedder cannot run.", file=sys.stderr)
        return 1

    if not args.quiet:
        total = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
        print(f"Ready. {target} holds {total / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
