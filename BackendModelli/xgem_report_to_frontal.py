import argparse
import os
import sys
from pathlib import Path
import torch


def _run_report_to_frontal(model, report: str, output_dir: Path, steps: int) -> None:
    # Local import to reuse XGeM task helpers while allowing configurable sampling steps.
    from inference.tasks import decode_image, encode_text
    import tifffile
    import numpy as np

    sampler = model.sampler
    shape_img = [1, 4, 32, 32]
    scale = 7.5
    conditioning = [encode_text(report, model)]
    condition_types = ["text"]
    xtype = ["frontal"]
    shapes = [shape_img]

    z, _ = sampler.sample(
        steps=steps,
        shape=shapes,
        condition=conditioning,
        unconditional_guidance_scale=scale,
        xtype=xtype,
        condition_types=condition_types,
        eta=1,
        verbose=False,
        mix_weight={"lateral": 1, "text": 1, "frontal": 1},
    )
    img = decode_image(z[0], model)
    tifffile.imwrite(str(output_dir / "output_frontal.tiff"), (img * 65535).astype(np.uint16))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run XGeM report->frontal in an isolated process.")
    parser.add_argument("--xgem-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--device", default=None)
    parser.add_argument("--model-path", default=None)
    parser.add_argument("--weights-dir", default=None)
    parser.add_argument("--steps", type=int, default=50)
    args = parser.parse_args()

    xgem_root = Path(args.xgem_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not xgem_root.exists():
        raise RuntimeError(f"XGeM root not found: {xgem_root}")

    os.chdir(str(xgem_root))
    if str(xgem_root) not in sys.path:
        sys.path.insert(0, str(xgem_root))

    if args.model_path:
        os.environ["XGEM_MODEL_PATH"] = args.model_path
    if args.weights_dir:
        os.environ["XGEM_WEIGHTS_DIR"] = args.weights_dir

    from inference.model_loader import load_model

    # Keep CPU subprocess memory pressure lower/more stable.
    if args.device and args.device.lower() == "cpu":
        torch.set_num_threads(1)
        torch.set_num_interop_threads(1)
    model = load_model(device=args.device)
    _run_report_to_frontal(model=model, report=args.prompt, output_dir=output_dir, steps=max(1, args.steps))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
