from datetime import datetime
from pathlib import Path
import copy
from contextlib import nullcontext
from contextlib import contextmanager
import importlib
import inspect
import os
import sys
import threading
import time
import traceback
from typing import List
import json
import base64
from urllib import request as urlrequest
from urllib import error as urlerror

import numpy as np
import pydicom
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import (
    CTImageStorage,
    DigitalXRayImageStorageForPresentation,
    ExplicitVRLittleEndian,
    generate_uid,
)


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

OUTPUT_ROOT = Path(__file__).resolve().parent / "generated_files"
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
ASSETS_ROOT = Path(__file__).resolve().parent / "simulation_assets"
ORTHANC_INSTANCES_URL = "http://localhost:8042/instances"
ORTHANC_BASIC_AUTH = base64.b64encode(b"orthanc:orthanc").decode("ascii")
EMPTY_GENERATIVE_STUDY_UID = "1.2.826.0.1.3680043.8.498.92334923612841918328708913924036869452"
SERVICE_MODE = "ct-only"

_process_is_running = False
_progress_text = "Idle"
_state_lock = threading.Lock()
_bootstrap_lock = threading.Lock()
_text2ct_cache_lock = threading.Lock()
_text2ct_model_cache: dict = {"key": None, "bundle": None}

try:
    import nibabel as nib
except Exception:
    nib = None

try:
    from PIL import Image
except Exception:
    Image = None

TEXT2CT_ROOT = Path(__file__).resolve().parent / "Text2CT"
TEXT2CT_CONFIG_DIR = TEXT2CT_ROOT / "configs"
PROJECT_ROOT = Path(__file__).resolve().parent.parent

_TEXT2CT_IMPORT_ERROR: str | None = None
if str(TEXT2CT_ROOT) not in sys.path:
    sys.path.append(str(TEXT2CT_ROOT))

try:
    import torch
    import torch.distributed as dist
    from monai.inferers.inferer import SlidingWindowInferer
    from monai.networks.schedulers import RFlowScheduler
    from core.cfg_helper import model_cfg_bank
    from core.models.common.get_model import get_model
    from scripts.diff_model_demo import (
        load_models as text2ct_load_models,
        prepare_tensors as text2ct_prepare_tensors,
        set_random_seed as text2ct_set_random_seed,
    )
    from scripts.diff_model_setting import (
        initialize_distributed as text2ct_initialize_distributed,
        load_config as text2ct_load_config,
        setup_logging as text2ct_setup_logging,
    )
    from scripts.sample import ReconModel
    from scripts.utils import define_instance, dynamic_infer
except Exception as exc:
    _TEXT2CT_IMPORT_ERROR = str(exc)
    torch = None
    dist = None


def _set_process_state(value: bool) -> None:
    global _process_is_running
    with _state_lock:
        _process_is_running = value


def _get_process_state() -> bool:
    with _state_lock:
        return _process_is_running


def _set_progress(message: str) -> None:
    global _progress_text
    with _state_lock:
        _progress_text = message


def _get_progress() -> str:
    with _state_lock:
        return _progress_text


def _normalize_to_uint16(arr: np.ndarray) -> np.ndarray:
    arr = arr.astype(np.float32)
    arr = arr - float(np.min(arr))
    maxv = float(np.max(arr))
    if maxv > 0:
        arr = arr / maxv
    return (arr * 4095).astype(np.uint16)


def _find_ct_asset() -> Path | None:
    candidates = [
        ASSETS_ROOT / "valid_403_b_2.nii",
        ASSETS_ROOT / "ct.nii",
        ASSETS_ROOT / "ct.nii.gz",
    ]
    return next((p for p in candidates if p.exists()), None)


def _find_xray_asset() -> Path | None:
    candidates = [
        ASSETS_ROOT / "xray.dcm",
        ASSETS_ROOT / "xray.png",
        ASSETS_ROOT / "xray.jpg",
        ASSETS_ROOT / "xray.jpeg",
    ]
    return next((p for p in candidates if p.exists()), None)


def _load_ct_volume() -> tuple[np.ndarray, tuple[float, float, float]]:
    if nib is None:
        raise RuntimeError("nibabel non installato")

    ct_file = _find_ct_asset()
    if ct_file is None:
        raise RuntimeError(f"Asset CT non trovato in {ASSETS_ROOT}")

    nii = nib.load(str(ct_file))
    volume = nii.get_fdata().astype(np.float32)
    zooms = nii.header.get_zooms()
    if volume.ndim == 2:
        volume = volume[:, :, np.newaxis]
    if volume.ndim < 3:
        raise RuntimeError("Volume CT non valido")

    low, high = np.percentile(volume, [1, 99])
    if high <= low:
        raise RuntimeError("Range CT non valido")

    volume = np.clip(volume, low, high)
    volume = (volume - low) / (high - low)
    volume = (volume * 1624.0) - 1024.0
    volume = volume.astype(np.int16)

    # Allineamento orientamento stile MedSyn per immagini AI.
    volume = np.rot90(volume, k=1, axes=(0, 1))
    # Keep spacing consistent with the rotated in-plane axes.
    row_spacing = float(abs(zooms[1])) if len(zooms) > 1 else 1.0
    col_spacing = float(abs(zooms[0])) if len(zooms) > 0 else 1.0
    slice_spacing = float(abs(zooms[2])) if len(zooms) > 2 else 1.0
    return volume, (row_spacing, col_spacing, slice_spacing)


def _resolve_existing_path(candidates: List[Path]) -> Path | None:
    return next((p for p in candidates if p and p.exists()), None)


def _resolve_text2ct_asset_paths(payload: dict) -> dict:
    autoencoder_override = payload.get("text2ctAutoencoderPath")
    unet_override = payload.get("text2ctUnetPath")
    clip_override = payload.get("text2ctClipPath")

    autoencoder = _resolve_existing_path(
        [
            Path(autoencoder_override) if autoencoder_override else None,
            Path(os.environ["TEXT2CT_AUTOENCODER_PATH"]) if os.environ.get("TEXT2CT_AUTOENCODER_PATH") else None,
            PROJECT_ROOT / "models" / "autoencoder_epoch273.pt",
            TEXT2CT_ROOT / "models" / "autoencoder_epoch273.pt",
        ]
    )
    unet = _resolve_existing_path(
        [
            Path(unet_override) if unet_override else None,
            Path(os.environ["TEXT2CT_UNET_PATH"]) if os.environ.get("TEXT2CT_UNET_PATH") else None,
            PROJECT_ROOT / "models" / "unet_rflow_200ep.pt",
            TEXT2CT_ROOT / "models" / "unet_rflow_200ep.pt",
        ]
    )
    clip = _resolve_existing_path(
        [
            Path(clip_override) if clip_override else None,
            Path(os.environ["TEXT2CT_CLIP_PATH"]) if os.environ.get("TEXT2CT_CLIP_PATH") else None,
            PROJECT_ROOT / "models" / "CLIP3D_Finding_Impression_30ep.pt",
            TEXT2CT_ROOT / "models" / "CLIP3D_Finding_Impression_30ep.pt",
        ]
    )
    return {"autoencoder": autoencoder, "unet": unet, "clip": clip}


def _validate_text2ct_requirements(paths: dict) -> None:
    if _TEXT2CT_IMPORT_ERROR is not None:
        raise RuntimeError(
            f"Text2CT import failed: {_TEXT2CT_IMPORT_ERROR}. Install Text2CT requirements in this environment."
        )
    if torch is None:
        raise RuntimeError("PyTorch is not available in this runtime.")
    missing = [name for name, path in paths.items() if path is None]
    if missing:
        raise RuntimeError(
            "Missing Text2CT weights: "
            + ", ".join(missing)
            + ". Provide payload paths or TEXT2CT_* env vars. Expected files include "
            + "autoencoder_epoch273.pt, unet_rflow_200ep.pt, CLIP3D_Finding_Impression_30ep.pt."
        )


@contextmanager
def _pushd(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _sanitize_model_definition(definition: dict, device, logger, label: str) -> dict:
    sanitized = dict(definition)
    target = sanitized.get("_target_")
    if not target:
        return sanitized

    module_name, class_name = target.rsplit(".", 1)
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    signature = inspect.signature(cls.__init__)
    accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values())
    allowed = {name for name in signature.parameters if name != "self"}

    if not accepts_kwargs:
        removed = []
        for key in list(sanitized.keys()):
            if key in ("_target_",) or key.startswith("@"):
                continue
            if key not in allowed:
                removed.append(key)
                sanitized.pop(key, None)
        if removed:
            logger.warning("Text2CT %s: removed unsupported config keys: %s", label, removed)

    if device.type != "cuda" and sanitized.get("use_flash_attention", False):
        sanitized["use_flash_attention"] = False
        logger.warning("Text2CT %s: set use_flash_attention=False for CPU runtime", label)

    return sanitized


def _sanitize_text2ct_defs_for_runtime(args, device, logger) -> None:
    args.diffusion_unet_def = _sanitize_model_definition(args.diffusion_unet_def, device, logger, "diffusion_unet_def")
    args.autoencoder_def = _sanitize_model_definition(args.autoencoder_def, device, logger, "autoencoder_def")
    if device.type == "cpu" and args.autoencoder_def.get("norm_float16", False):
        args.autoencoder_def["norm_float16"] = False
        logger.warning("Text2CT autoencoder_def: set norm_float16=False for CPU runtime")


def _run_text2ct_inference_device_aware(
    args,
    device,
    autoencoder,
    unet,
    clip_model,
    scale_factor,
    top_region_index_tensor,
    bottom_region_index_tensor,
    spacing_tensor,
    modality_tensor,
    output_size,
    divisor,
    logger,
    prompt,
) -> np.ndarray:
    model_dtype = torch.float32 if device.type == "cpu" else next(unet.parameters()).dtype
    include_body_region = unet.include_top_region_index_input
    include_modality = unet.num_class_embeds is not None
    use_cfg = args.use_cfg
    guidance_scale = args.guidance_scale if use_cfg else 1.0

    noise = torch.randn(
        (
            1,
            args.latent_channels,
            output_size[0] // divisor,
            output_size[1] // divisor,
            output_size[2] // divisor,
        ),
        device=device,
        dtype=model_dtype,
    )

    if not args.diffusion_unet_def["include_top_region_index_input"]:
        top_region_index_tensor = None
        bottom_region_index_tensor = None
        spacing_tensor = None
    else:
        top_region_index_tensor = top_region_index_tensor.to(device=device, dtype=model_dtype)
        bottom_region_index_tensor = bottom_region_index_tensor.to(device=device, dtype=model_dtype)
        spacing_tensor = spacing_tensor.to(device=device, dtype=model_dtype)

    noise_scheduler = define_instance(args, "noise_scheduler")

    if isinstance(noise_scheduler, RFlowScheduler):
        noise_scheduler.set_timesteps(
            num_inference_steps=args.diffusion_unet_inference["num_inference_steps"],
            input_img_size_numel=torch.prod(torch.tensor(noise.shape[2:])),
        )
    else:
        noise_scheduler.set_timesteps(num_inference_steps=args.diffusion_unet_inference["num_inference_steps"])

    with torch.no_grad():
        impression = clip_model([prompt], "encode_text").to(device=device, dtype=model_dtype)

    recon_model = ReconModel(autoencoder=autoencoder, scale_factor=scale_factor).to(device)
    autoencoder.eval()
    unet.eval()

    all_timesteps = noise_scheduler.timesteps
    all_next_timesteps = torch.cat((all_timesteps[1:], torch.tensor([0], dtype=all_timesteps.dtype)))
    autocast_ctx = torch.amp.autocast("cuda", enabled=device.type == "cuda") if device.type == "cuda" else nullcontext()

    image = noise
    with autocast_ctx:
        for t, next_t in zip(all_timesteps, all_next_timesteps):
            unet_inputs = {
                "x": image,
                "timesteps": torch.tensor((t,), device=device, dtype=model_dtype),
                "spacing_tensor": spacing_tensor,
            }

            if include_body_region:
                unet_inputs.update(
                    {
                        "top_region_index_tensor": top_region_index_tensor,
                        "bottom_region_index_tensor": bottom_region_index_tensor,
                    }
                )

            if include_modality:
                unet_inputs.update({"class_labels": modality_tensor})

            if use_cfg:
                unet_inputs_no_text = unet_inputs.copy()
                unet_inputs.update({"context": impression})
                unet_inputs_no_text.update({"context": torch.zeros_like(impression, device=device)})

                model_output_uncond = unet(**unet_inputs_no_text)
                model_output_cond = unet(**unet_inputs)
                model_output = model_output_uncond + guidance_scale * (model_output_cond - model_output_uncond)
            else:
                unet_inputs.update({"context": impression})
                model_output = unet(**unet_inputs)

            if not isinstance(noise_scheduler, RFlowScheduler):
                image, _ = noise_scheduler.step(model_output, t, image)
            else:
                image, _ = noise_scheduler.step(model_output, t, image, next_t)
            if device.type == "cpu":
                image = image.float()

        inferer = SlidingWindowInferer(
            roi_size=[80, 80, 80],
            sw_batch_size=1,
            progress=False,
            mode="gaussian",
            overlap=0.4,
            sw_device=device,
            device=device,
        )
        synthetic_images = dynamic_infer(inferer, recon_model, image)
        data = synthetic_images.squeeze().cpu().detach().numpy()
        data = (data - 0.0) / (1.0 - 0.0) * (1000 - (-1000)) + (-1000)
        data = np.clip(data, -1000, 1000)
    logger.info("Text2CT inference done on device=%s", device)
    return np.int16(data)


def _generate_ct_volume_with_text2ct(payload: dict, output_dir: Path) -> tuple[np.ndarray, tuple[float, float, float]]:
    if nib is None:
        raise RuntimeError("nibabel non installato")

    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        raise RuntimeError("Prompt is required for Text2CT generation")

    paths = _resolve_text2ct_asset_paths(payload)
    _validate_text2ct_requirements(paths)

    env_config = Path(payload.get("text2ctEnvConfigPath") or (TEXT2CT_CONFIG_DIR / "environment_diff_model_eval.json"))
    model_config = Path(payload.get("text2ctModelConfigPath") or (TEXT2CT_CONFIG_DIR / "config_diff_model.json"))
    model_def = Path(payload.get("text2ctModelDefPath") or (TEXT2CT_CONFIG_DIR / "config_rflow.json"))
    for cfg in (env_config, model_config, model_def):
        if not cfg.exists():
            raise RuntimeError(f"Text2CT config not found: {cfg}")

    device_mode = "cuda" if torch.cuda.is_available() else "cpu"
    cache_key = (
        str(env_config.resolve()),
        str(model_config.resolve()),
        str(model_def.resolve()),
        str(paths["autoencoder"].resolve()),
        str(paths["unet"].resolve()),
        str(paths["clip"].resolve()),
        device_mode,
    )

    with _text2ct_cache_lock:
        cached = _text2ct_model_cache.get("bundle")
        if _text2ct_model_cache.get("key") != cache_key or cached is None:
            _set_progress("Text2CT: loading models")
            args_base = text2ct_load_config(str(env_config), str(model_config), str(model_def))
            args_base.model_dir = str(paths["unet"].parent)
            args_base.model_filename = paths["unet"].name
            args_base.trained_autoencoder_path = str(paths["autoencoder"])
            args_base.existing_ckpt_filepath = str(paths["unet"])

            num_gpus = int(payload.get("text2ctNumGpus") or 1)
            if not torch.cuda.is_available():
                num_gpus = 0
            local_rank, _, device = text2ct_initialize_distributed(num_gpus)
            logger = text2ct_setup_logging("text2ct_inference")
            seed = text2ct_set_random_seed(
                args_base.diffusion_unet_inference["random_seed"] + local_rank
                if args_base.diffusion_unet_inference["random_seed"]
                else None
            )
            logger.info("Text2CT seed=%s device=%s", seed, device)
            _sanitize_text2ct_defs_for_runtime(args_base, device, logger)

            autoencoder, unet, scale_factor = text2ct_load_models(args_base, device, logger)
            with _pushd(TEXT2CT_ROOT):
                cfgm = model_cfg_bank()("clip_3D")
            clip = get_model()(cfgm).to(device)
            clip.load_state_dict(torch.load(paths["clip"], map_location=device), strict=True)
            if device.type == "cpu":
                autoencoder = autoencoder.float()
                unet = unet.float()
                clip = clip.float()
            clip.eval()

            _text2ct_model_cache["key"] = cache_key
            _text2ct_model_cache["bundle"] = {
                "args_base": args_base,
                "device": device,
                "logger": logger,
                "autoencoder": autoencoder,
                "unet": unet,
                "clip": clip,
                "scale_factor": scale_factor,
            }
            cached = _text2ct_model_cache["bundle"]

    args = copy.deepcopy(cached["args_base"])
    device = cached["device"]
    logger = cached["logger"]
    autoencoder = cached["autoencoder"]
    unet = cached["unet"]
    clip = cached["clip"]
    scale_factor = cached["scale_factor"]

    if "text2ctNumInferenceSteps" in payload:
        args.diffusion_unet_inference["num_inference_steps"] = int(payload["text2ctNumInferenceSteps"])
    if "text2ctDim" in payload:
        args.diffusion_unet_inference["dim"] = list(payload["text2ctDim"])
    elif device.type == "cuda":
        # Default GPU target volume unless explicitly overridden by payload.
        args.diffusion_unet_inference["dim"] = [512, 512, 128]
        logger.info("GPU mode detected, defaulting text2ct dim to %s", args.diffusion_unet_inference["dim"])
    elif device.type == "cpu":
        args.diffusion_unet_inference["dim"] = [128, 128, 32]
        logger.info("CPU mode detected, defaulting text2ct dim to %s", args.diffusion_unet_inference["dim"])
    if "text2ctSpacing" in payload:
        args.diffusion_unet_inference["spacing"] = list(payload["text2ctSpacing"])

    num_downsample_level = max(
        1,
        (
            len(args.diffusion_unet_def["num_channels"])
            if isinstance(args.diffusion_unet_def["num_channels"], list)
            else len(args.diffusion_unet_def["attention_levels"])
        ),
    )
    divisor = 2 ** (num_downsample_level - 2)
    output_size = tuple(args.diffusion_unet_inference["dim"])
    spacing = tuple(args.diffusion_unet_inference["spacing"])
    top_region_index_tensor, bottom_region_index_tensor, spacing_tensor, modality_tensor = text2ct_prepare_tensors(
        args, device
    )

    if device.type == "cpu" and "text2ctNumInferenceSteps" not in payload:
        args.diffusion_unet_inference["num_inference_steps"] = 2
        logger.info("CPU mode detected, defaulting num_inference_steps=%s", args.diffusion_unet_inference["num_inference_steps"])
    _set_progress(f"Text2CT: running diffusion inference on {device.type.upper()}")
    data = _run_text2ct_inference_device_aware(
        args=args,
        device=device,
        autoencoder=autoencoder,
        unet=unet,
        clip_model=clip,
        scale_factor=scale_factor,
        top_region_index_tensor=top_region_index_tensor,
        bottom_region_index_tensor=bottom_region_index_tensor,
        spacing_tensor=spacing_tensor,
        modality_tensor=modality_tensor,
        output_size=output_size,
        divisor=divisor,
        logger=logger,
        prompt=prompt,
    ).astype(np.int16)

    generated_nii = output_dir / "generated_text2ct.nii.gz"
    affine = np.eye(4)
    affine[0, 0] = float(spacing[0])
    affine[1, 1] = float(spacing[1])
    affine[2, 2] = float(spacing[2])
    nib.save(nib.Nifti1Image(data, affine=affine), str(generated_nii))

    if dist is not None and dist.is_initialized():
        dist.destroy_process_group()

    return data, (float(spacing[0]), float(spacing[1]), float(spacing[2]))


def _load_ct_volume_for_generation(payload: dict, output_dir: Path) -> tuple[np.ndarray, tuple[float, float, float]]:
    ct_source = (payload.get("ctSource") or "text2ct").lower()
    if ct_source == "asset":
        payload["_ctEffectiveSource"] = "asset"
        return _load_ct_volume()
    payload["_ctEffectiveSource"] = "text2ct"
    return _generate_ct_volume_with_text2ct(payload, output_dir)


def _load_xray_pixels() -> np.ndarray:
    xray_file = _find_xray_asset()
    if xray_file is None:
        raise RuntimeError(f"Asset Xray non trovato in {ASSETS_ROOT}")

    if xray_file.suffix.lower() == ".dcm":
        ds = pydicom.dcmread(str(xray_file))
        return _normalize_to_uint16(ds.pixel_array)

    if Image is None:
        raise RuntimeError("Pillow non installato")

    img = Image.open(xray_file).convert("L")
    return _normalize_to_uint16(np.array(img))


def _create_base_dataset(
    output_file: Path,
    payload: dict,
    series_instance_uid: str,
    study_instance_uid: str,
    sop_class_uid: str,
    sop_instance_uid: str,
) -> FileDataset:
    now = datetime.now()

    file_meta = Dataset()
    file_meta.MediaStorageSOPClassUID = sop_class_uid
    file_meta.MediaStorageSOPInstanceUID = sop_instance_uid
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    file_meta.ImplementationClassUID = generate_uid()

    ds = FileDataset(str(output_file), {}, file_meta=file_meta, preamble=b"\0" * 128)

    patient_name = payload.get("patient_name") or "Generated^Patient"
    patient_id = payload.get("patient_id") or "GEN-001"

    ds.SOPClassUID = sop_class_uid
    ds.SOPInstanceUID = sop_instance_uid
    ds.PatientName = patient_name
    ds.PatientID = patient_id
    ds.StudyInstanceUID = study_instance_uid
    ds.SeriesInstanceUID = series_instance_uid
    ds.AccessionNumber = payload.get("filename", "").replace(".npy", "")[:16]
    ds.StudyID = "1"

    ds.StudyDate = now.strftime("%Y%m%d")
    ds.StudyTime = now.strftime("%H%M%S")
    ds.SeriesDate = ds.StudyDate
    ds.SeriesTime = ds.StudyTime
    ds.ContentDate = ds.StudyDate
    ds.ContentTime = ds.StudyTime
    ds.InstanceCreationDate = ds.StudyDate
    ds.InstanceCreationTime = ds.StudyTime

    ds.Manufacturer = "MedSyn-style-Simulator"
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    return ds


def _write_ct_dicoms(
    output_dir: Path,
    payload: dict,
    series_instance_uid: str,
    study_instance_uid: str,
) -> List[Path]:
    volume, spacing = _load_ct_volume_for_generation(payload, output_dir)
    series_description = payload.get("description") or "Generated CT"
    row_spacing, col_spacing, slice_spacing = spacing
    num_slices = int(volume.shape[2])

    out_files: List[Path] = []
    for idx in range(1, num_slices + 1):
        z = idx - 1
        out_path = output_dir / f"slice_{idx:03d}.dcm"
        sop_instance_uid = generate_uid()

        ds = _create_base_dataset(
            output_file=out_path,
            payload=payload,
            series_instance_uid=series_instance_uid,
            study_instance_uid=study_instance_uid,
            sop_class_uid=CTImageStorage,
            sop_instance_uid=sop_instance_uid,
        )

        pixels = np.fliplr(volume[:, :, z]).astype(np.int16)

        ds.Modality = "CT"
        ds.SeriesDescription = series_description
        ds.SeriesNumber = 1
        ds.InstanceNumber = idx
        ds.ImagePositionPatient = [0.0, 0.0, float(z * slice_spacing)]
        ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        ds.SliceThickness = float(slice_spacing)
        ds.PixelSpacing = [float(row_spacing), float(col_spacing)]
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.Rows = int(pixels.shape[0])
        ds.Columns = int(pixels.shape[1])
        ds.BitsAllocated = 16
        ds.BitsStored = 16
        ds.HighBit = 15
        ds.PixelRepresentation = 1
        ds.RescaleIntercept = 0
        ds.RescaleSlope = 1
        ds.WindowWidth = 1500
        ds.WindowCenter = 0
        ds.PixelData = pixels.tobytes()

        ds.save_as(str(out_path), write_like_original=False)
        out_files.append(out_path)

    return out_files


def _write_xray_dicom(
    output_dir: Path,
    payload: dict,
    series_instance_uid: str,
    study_instance_uid: str,
) -> List[Path]:
    pixels = _load_xray_pixels()
    out_path = output_dir / "xray_001.dcm"
    sop_instance_uid = generate_uid()

    ds = _create_base_dataset(
        output_file=out_path,
        payload=payload,
        series_instance_uid=series_instance_uid,
        study_instance_uid=study_instance_uid,
        sop_class_uid=DigitalXRayImageStorageForPresentation,
        sop_instance_uid=sop_instance_uid,
    )

    ds.Modality = "DX"
    ds.SeriesDescription = payload.get("description") or "Generated Xray"
    ds.SeriesNumber = 1
    ds.InstanceNumber = 1
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.Rows = int(pixels.shape[0])
    ds.Columns = int(pixels.shape[1])
    ds.BitsAllocated = 16
    ds.BitsStored = 12
    ds.HighBit = 11
    ds.PixelRepresentation = 0
    ds.WindowWidth = 2000
    ds.WindowCenter = 1000
    ds.PixelData = pixels.tobytes()

    ds.save_as(str(out_path), write_like_original=False)
    return [out_path]


def _simulate_generation_job(file_id: str, payload: dict, series_instance_uid: str) -> None:
    folder = OUTPUT_ROOT / file_id
    summary_path = folder / "summary.json"
    try:
        _set_progress("Queued")
        time.sleep(1)
        _set_progress("Running generation")

        folder.mkdir(parents=True, exist_ok=True)

        generation_type = (payload.get("generationType") or "ct").lower()
        study_instance_uid = payload.get("studyInstanceUID") or generate_uid()
        if generation_type == "ct":
            _set_progress("Generating CT with Text2CT")
            out_files = _write_ct_dicoms(folder, payload, series_instance_uid, study_instance_uid)
        else:
            _set_progress("Converting image to DICOM (Xray)")
            out_files = _write_xray_dicom(folder, payload, series_instance_uid, study_instance_uid)

        ct_mode = payload.get("_ctEffectiveSource", "n/a") if generation_type == "ct" else "n/a"

        summary = {
            "fileID": file_id,
            "generationType": generation_type,
            "ctMode": ct_mode,
            "studyInstanceUID": study_instance_uid,
            "seriesInstanceUID": series_instance_uid,
            "dicomCount": len(out_files),
            "firstFile": out_files[0].name if out_files else None,
            "lastFile": out_files[-1].name if out_files else None,
            "createdAt": datetime.now().isoformat(),
            "status": "completed",
        }
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(
            f"[GENERATION] fileID={file_id} type={generation_type} "
            f"studyUID={study_instance_uid} seriesUID={series_instance_uid} dicomCount={len(out_files)}"
        )
        _set_progress(
            f"Completed: {len(out_files)} DICOM file(s) | study={study_instance_uid} | series={series_instance_uid}"
        )
    except Exception as exc:
        summary = {
            "fileID": file_id,
            "seriesInstanceUID": series_instance_uid,
            "status": "error",
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "createdAt": datetime.now().isoformat(),
        }
        folder.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(traceback.format_exc())
        _set_progress(f"Error: {exc}")
    finally:
        _set_process_state(False)


def _upload_folder_to_orthanc(folder: Path) -> List[dict]:
    files = sorted([p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".dcm"])
    upload_results: List[dict] = []

    for dcm_file in files:
        try:
            dicom_bytes = dcm_file.read_bytes()
            req = urlrequest.Request(
                ORTHANC_INSTANCES_URL,
                data=dicom_bytes,
                method="POST",
                headers={
                    "Content-Type": "application/dicom",
                    "Authorization": f"Basic {ORTHANC_BASIC_AUTH}",
                },
            )
            with urlrequest.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                upload_results.append(payload)
        except urlerror.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Orthanc upload failed for {dcm_file.name}: {exc.code} {detail}") from exc
        except Exception as exc:
            raise RuntimeError(f"Orthanc upload failed for {dcm_file.name}: {exc}") from exc

    return upload_results


def _orthanc_find_study_by_uid(study_instance_uid: str) -> dict | None:
    payload = {
        "Level": "Study",
        "Expand": True,
        "Query": {
            "StudyInstanceUID": study_instance_uid,
        },
    }
    req = urlrequest.Request(
        "http://localhost:8042/tools/find",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Basic {ORTHANC_BASIC_AUTH}",
        },
    )
    with urlrequest.urlopen(req, timeout=15) as resp:
        found = json.loads(resp.read().decode("utf-8"))
        if isinstance(found, list) and found:
            return found[0]
    return None


def _create_empty_xray_placeholder_dicom(output_file: Path, study_instance_uid: str) -> Path:
    now = datetime.now()
    file_meta = Dataset()
    file_meta.MediaStorageSOPClassUID = DigitalXRayImageStorageForPresentation
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    file_meta.ImplementationClassUID = generate_uid()

    ds = FileDataset(str(output_file), {}, file_meta=file_meta, preamble=b"\0" * 128)
    ds.is_little_endian = True
    ds.is_implicit_VR = False

    pixels = np.zeros((512, 512), dtype=np.uint16)
    ds.SOPClassUID = DigitalXRayImageStorageForPresentation
    ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
    ds.PatientName = "GENAI^PLACEHOLDER"
    ds.PatientID = "GENAI0001"
    ds.StudyInstanceUID = study_instance_uid
    ds.SeriesInstanceUID = generate_uid()
    ds.StudyID = "GENAI_EMPTY"
    ds.Modality = "DX"
    ds.SeriesDescription = "GenerativeAI Placeholder"
    ds.StudyDescription = "GenerativeAI Placeholder (Hidden)"
    ds.AccessionNumber = "GENAIEMPTY"
    ds.StudyDate = now.strftime("%Y%m%d")
    ds.StudyTime = now.strftime("%H%M%S")
    ds.SeriesDate = ds.StudyDate
    ds.SeriesTime = ds.StudyTime
    ds.ContentDate = ds.StudyDate
    ds.ContentTime = ds.StudyTime
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.Rows = int(pixels.shape[0])
    ds.Columns = int(pixels.shape[1])
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0
    ds.WindowWidth = 2000
    ds.WindowCenter = 1000
    ds.PixelData = pixels.tobytes()

    output_file.parent.mkdir(parents=True, exist_ok=True)
    ds.save_as(str(output_file), write_like_original=False)
    return output_file


def _upload_single_dicom_to_orthanc(dcm_file: Path) -> dict:
    dicom_bytes = dcm_file.read_bytes()
    req = urlrequest.Request(
        ORTHANC_INSTANCES_URL,
        data=dicom_bytes,
        method="POST",
        headers={
            "Content-Type": "application/dicom",
            "Authorization": f"Basic {ORTHANC_BASIC_AUTH}",
        },
    )
    with urlrequest.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


@app.get("/health")
def health():
    return {"status": "ok", "serviceMode": SERVICE_MODE}


@app.get("/mode")
def mode():
    return {"serviceMode": SERVICE_MODE, "allowedGenerationTypes": ["ct", "xray"]}


@app.get("/status")
def status():
    return {"process_is_running": _get_process_state()}


@app.get("/progress")
def progress():
    return _get_progress()


@app.post("/bootstrap/generative-ai-empty-study")
def ensure_empty_generative_study():
    try:
        with _bootstrap_lock:
            existing = _orthanc_find_study_by_uid(EMPTY_GENERATIVE_STUDY_UID)
            if existing:
                return {
                    "status": "exists",
                    "studyInstanceUID": EMPTY_GENERATIVE_STUDY_UID,
                    "orthancStudyID": existing.get("ID"),
                }

            out_file = OUTPUT_ROOT / "bootstrap" / "generation_empty_file.dcm"
            generated = _create_empty_xray_placeholder_dicom(out_file, EMPTY_GENERATIVE_STUDY_UID)
            uploaded = _upload_single_dicom_to_orthanc(generated)
            return {
                "status": "created",
                "studyInstanceUID": EMPTY_GENERATIVE_STUDY_UID,
                "uploadResult": uploaded,
                "filePath": str(generated),
            }
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        return JSONResponse(
            status_code=500,
            content={"error": f"Orthanc bootstrap failed: {exc.code} {detail}"},
        )
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.post("/files/{file_id}")
def start_generation(file_id: str, payload: dict):
    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        return JSONResponse(status_code=400, content={"error": "Prompt is required"})

    if _get_process_state():
        return JSONResponse(status_code=409, content={"error": "A generation is already running"})

    generation_type = (payload.get("generationType") or "ct").lower()
    if generation_type == "xrays":
        generation_type = "xray"
    if generation_type not in ("ct", "xray"):
        return JSONResponse(status_code=400, content={"error": "generationType must be 'ct' or 'xray'/'xrays'"})

    series_instance_uid = generate_uid()
    _set_process_state(True)

    worker = threading.Thread(
        target=_simulate_generation_job,
        args=(file_id, payload, series_instance_uid),
        daemon=True,
    )
    worker.start()

    return {
        "message": "Generation started",
        "prompt": prompt,
        "seriesInstanceUID": series_instance_uid,
        "generationType": generation_type,
        "fileID": file_id,
    }


@app.get("/files/{folder_name}")
def list_generated_files(folder_name: str):
    folder = OUTPUT_ROOT / folder_name
    if not folder.exists() or not folder.is_dir():
        return JSONResponse(status_code=404, content={"error": "Folder not found"})

    files = sorted([p.name for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".dcm"])
    return files


@app.get("/files/{folder_name}/summary")
def get_generation_summary(folder_name: str):
    folder = OUTPUT_ROOT / folder_name
    summary_path = folder / "summary.json"
    if not summary_path.exists() or not summary_path.is_file():
        return JSONResponse(status_code=404, content={"error": "Summary not found"})

    try:
        return json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": f"Invalid summary.json: {exc}"})


@app.post("/files/{folder_name}/upload-orthanc")
def upload_generated_folder_to_orthanc(folder_name: str):
    folder = OUTPUT_ROOT / folder_name
    if not folder.exists() or not folder.is_dir():
        return JSONResponse(status_code=404, content={"error": "Folder not found"})

    try:
        upload_results = _upload_folder_to_orthanc(folder)
        return {
            "folderName": folder_name,
            "uploadedCount": len(upload_results),
            "uploadResults": upload_results,
        }
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.post("/files/{folder_name}/{filename}")
def get_generated_file(folder_name: str, filename: str):
    file_path = OUTPUT_ROOT / folder_name / filename
    if not file_path.exists() or not file_path.is_file():
        return JSONResponse(status_code=404, content={"error": "DICOM file not found"})

    return Response(content=file_path.read_bytes(), media_type="application/dicom")


#ciao ciao 
