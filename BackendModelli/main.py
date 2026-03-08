from datetime import datetime
from pathlib import Path
import os
import threading
import time
import io
import re
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
BACKEND_MODE = (os.getenv("BACKEND_MODE") or "full").strip().lower()
VALID_BACKEND_MODES = {"api-only", "ct-only", "xgem-only", "full"}
GENERATION_ENGINE = (os.getenv("GENERATION_ENGINE") or "real").strip().lower()
CT_REMOTE_URL = (os.getenv("CT_REMOTE_URL") or "").strip().rstrip("/")
XRAY_REMOTE_URL = (os.getenv("XRAY_REMOTE_URL") or "").strip().rstrip("/")
REMOTE_INFERENCE_URL = (os.getenv("REMOTE_INFERENCE_URL") or "").strip().rstrip("/")
REMOTE_TIMEOUT_SECONDS = float(os.getenv("REMOTE_TIMEOUT_SECONDS", "300"))
ENABLE_LEXICAL_RETRIEVAL_FALLBACK = (
    os.getenv("ENABLE_LEXICAL_RETRIEVAL_FALLBACK", "1").strip().lower() not in {"0", "false", "no", "off"}
)

_process_is_running = False
_progress_text = "Idle"
_state_lock = threading.Lock()
_bootstrap_lock = threading.Lock()

try:
    import nibabel as nib
except Exception:
    nib = None

try:
    from PIL import Image
except Exception:
    Image = None


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


def _normalize_generation_type(raw_generation_type: str | None) -> str:
    generation_type = (raw_generation_type or "ct").lower()
    if generation_type == "xrays":
        generation_type = "xray"
    return generation_type


def _allowed_generation_types_for_mode(mode: str) -> set[str]:
    if mode == "api-only":
        return set()
    if mode == "ct-only":
        return {"ct"}
    if mode == "xgem-only":
        return {"xray"}
    return {"ct", "xray"}


def _post_json(url: str, payload: dict, timeout: float = REMOTE_TIMEOUT_SECONDS) -> dict:
    req = urlrequest.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urlrequest.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_json(url: str, timeout: float = 5.0) -> dict:
    req = urlrequest.Request(url, method="GET")
    with urlrequest.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _service_url_for_generation_type(generation_type: str) -> str:
    if generation_type == "ct":
        return CT_REMOTE_URL or REMOTE_INFERENCE_URL or XRAY_REMOTE_URL
    return XRAY_REMOTE_URL or REMOTE_INFERENCE_URL or CT_REMOTE_URL


def _check_remote_service(remote_url: str, generation_type: str | None = None) -> tuple[bool, str]:
    if not remote_url:
        return False, "missing-url"
    try:
        health_path = f"/health/{generation_type}" if generation_type in {"ct", "xray"} else "/health"
        data = _get_json(f"{remote_url}{health_path}", timeout=3.0)
        ok = str(data.get("status", "")).lower() == "ok"
        return ok, "ok" if ok else "unhealthy"
    except Exception as exc:
        return False, str(exc)


def _remote_services_status() -> dict:
    ct_url = _service_url_for_generation_type("ct")
    xray_url = _service_url_for_generation_type("xray")
    ct_ok, ct_reason = _check_remote_service(ct_url, "ct")
    xray_ok, xray_reason = _check_remote_service(xray_url, "xray")
    return {
        "ct": {"url": ct_url, "available": ct_ok, "reason": ct_reason},
        "xray": {"url": xray_url, "available": xray_ok, "reason": xray_reason},
    }


def _tokenize_for_overlap(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{2,}", (text or "").lower()))


def _list_retrieval_candidates(generation_type: str, exclude_folder: Path | None = None) -> list[dict]:
    candidates: list[dict] = []
    if not OUTPUT_ROOT.exists():
        return candidates

    for folder in OUTPUT_ROOT.iterdir():
        if not folder.is_dir():
            continue
        if exclude_folder is not None and folder.resolve() == exclude_folder.resolve():
            continue
        summary_path = folder / "summary.json"
        if not summary_path.exists():
            continue
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if summary.get("status") != "completed":
            continue
        if _normalize_generation_type(summary.get("generationType")) != generation_type:
            continue
        dicoms = sorted([p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".dcm"])
        if not dicoms:
            continue

        request_prompt = ""
        request_path = folder / "request.json"
        if request_path.exists():
            try:
                request_data = json.loads(request_path.read_text(encoding="utf-8"))
                request_prompt = str(request_data.get("prompt") or "")
            except Exception:
                request_prompt = ""

        searchable_text = " ".join(
            [
                str(summary.get("prompt") or ""),
                str(summary.get("description") or ""),
                request_prompt,
                str(summary.get("fileID") or ""),
            ]
        ).strip()
        candidates.append(
            {
                "folder": folder,
                "dicoms": dicoms,
                "summary": summary,
                "searchable_text": searchable_text,
            }
        )
    return candidates


def _has_retrieval_candidates(generation_type: str) -> bool:
    return bool(_list_retrieval_candidates(generation_type))


def _pick_best_retrieval_case(
    payload: dict, generation_type: str, exclude_folder: Path | None = None
) -> tuple[dict, float]:
    candidates = _list_retrieval_candidates(generation_type, exclude_folder=exclude_folder)
    if not candidates:
        raise RuntimeError(f"No retrieval candidates available for generationType='{generation_type}'")

    query_tokens = _tokenize_for_overlap((payload.get("prompt") or "").strip())
    for candidate in candidates:
        text_tokens = _tokenize_for_overlap(candidate["searchable_text"])
        if not query_tokens:
            score = 0.0
        elif not text_tokens:
            score = 0.0
        else:
            score = len(query_tokens.intersection(text_tokens)) / float(len(query_tokens))
        candidate["score"] = score

        created_at = str(candidate["summary"].get("createdAt") or "")
        try:
            candidate["created_at_sort"] = datetime.fromisoformat(created_at)
        except Exception:
            candidate["created_at_sort"] = datetime.min

    candidates.sort(key=lambda item: (item.get("score", 0.0), item.get("created_at_sort", datetime.min)), reverse=True)
    best = candidates[0]
    return best, float(best.get("score", 0.0))


def _copy_case_dicoms_with_new_uids(
    source_dicoms: list[Path],
    output_dir: Path,
    payload: dict,
    series_instance_uid: str,
    study_instance_uid: str,
    generation_type: str,
) -> List[Path]:
    if not source_dicoms:
        raise RuntimeError("Retrieval source has no DICOM files")

    out_files: List[Path] = []
    for idx, src in enumerate(source_dicoms, start=1):
        ds = pydicom.dcmread(str(src))
        ds.StudyInstanceUID = study_instance_uid
        ds.SeriesInstanceUID = series_instance_uid
        ds.SOPInstanceUID = generate_uid()
        if getattr(ds, "file_meta", None) is not None:
            ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
        ds.SeriesDate = datetime.now().strftime("%Y%m%d")
        ds.SeriesTime = datetime.now().strftime("%H%M%S")
        ds.InstanceCreationDate = ds.SeriesDate
        ds.InstanceCreationTime = ds.SeriesTime
        ds.AccessionNumber = (payload.get("filename") or f"RETR-{generation_type}")[:16]
        ds.InstanceNumber = idx
        ds.PatientName = "Retrieved CT" if generation_type == "ct" else "Retrieved XRAY"
        ds.SeriesDescription = payload.get("description") or f"Retrieved {generation_type.upper()} case"

        if generation_type == "ct":
            out_path = output_dir / f"slice_{idx:03d}.dcm"
        else:
            out_path = output_dir / f"xray_{idx:03d}.dcm"
        ds.save_as(str(out_path), write_like_original=False)
        out_files.append(out_path)
    return out_files


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

    generation_type = (payload.get("generationType") or "ct").lower()
    if generation_type == "xrays":
        generation_type = "xray"
    patient_name = "Generated CT" if generation_type == "ct" else "Generated XRAY"
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
    volume, spacing = _load_ct_volume()
    return _write_ct_dicoms_from_volume(
        output_dir=output_dir,
        payload=payload,
        series_instance_uid=series_instance_uid,
        study_instance_uid=study_instance_uid,
        volume=volume,
        spacing=spacing,
    )


def _write_ct_dicoms_from_volume(
    output_dir: Path,
    payload: dict,
    series_instance_uid: str,
    study_instance_uid: str,
    volume: np.ndarray,
    spacing: tuple[float, float, float],
) -> List[Path]:
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
    return _write_xray_dicom_from_pixels(
        output_dir=output_dir,
        payload=payload,
        series_instance_uid=series_instance_uid,
        study_instance_uid=study_instance_uid,
        pixels=pixels,
    )


def _write_xray_dicom_from_pixels(
    output_dir: Path,
    payload: dict,
    series_instance_uid: str,
    study_instance_uid: str,
    pixels: np.ndarray,
) -> List[Path]:
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


def _decode_numpy_from_base64(encoded_array: str) -> np.ndarray:
    arr_bytes = base64.b64decode(encoded_array.encode("ascii"))
    return np.load(io.BytesIO(arr_bytes), allow_pickle=False)


def _generate_ct_via_remote(payload: dict) -> tuple[np.ndarray, tuple[float, float, float]]:
    remote_url = _service_url_for_generation_type("ct")
    if not remote_url:
        raise RuntimeError("CT remote URL not configured (set CT_REMOTE_URL or REMOTE_INFERENCE_URL)")
    response = _post_json(f"{remote_url}/infer/ct", payload)
    if not response.get("ok"):
        raise RuntimeError(response.get("error") or "CT remote inference failed")
    spacing_raw = response.get("spacing") or [1.0, 1.0, 1.0]
    spacing = (float(spacing_raw[0]), float(spacing_raw[1]), float(spacing_raw[2]))
    volume = _decode_numpy_from_base64(response["volume_npy_b64"]).astype(np.int16)
    if volume.ndim != 3:
        raise RuntimeError(f"CT remote volume shape invalid: {volume.shape}")
    return volume, spacing


def _generate_xray_via_remote(payload: dict) -> np.ndarray:
    remote_url = _service_url_for_generation_type("xray")
    if not remote_url:
        raise RuntimeError("XRay remote URL not configured (set XRAY_REMOTE_URL or REMOTE_INFERENCE_URL)")
    response = _post_json(f"{remote_url}/infer/xray", payload)
    if not response.get("ok"):
        raise RuntimeError(response.get("error") or "XRay remote inference failed")
    pixels = _decode_numpy_from_base64(response["pixels_npy_b64"])
    if pixels.ndim != 2:
        raise RuntimeError(f"XRay remote pixels shape invalid: {pixels.shape}")
    return _normalize_to_uint16(pixels)


def _simulate_generation_job(file_id: str, payload: dict, series_instance_uid: str) -> None:
    folder = OUTPUT_ROOT / file_id
    summary_path = folder / "summary.json"
    try:
        _set_progress("Queued")
        time.sleep(1)
        _set_progress("Running generation")

        folder.mkdir(parents=True, exist_ok=True)
        request_path = folder / "request.json"
        request_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        generation_type = _normalize_generation_type(payload.get("generationType"))
        prompt = (payload.get("prompt") or "").strip()
        study_instance_uid = payload.get("studyInstanceUID") or generate_uid()
        use_real_engine = GENERATION_ENGINE == "real"
        engine_used = GENERATION_ENGINE
        retrieval_used = False
        retrieval_meta: dict | None = None
        if generation_type == "ct":
            if use_real_engine:
                try:
                    _set_progress("Generating CT via remote service")
                    volume, spacing = _generate_ct_via_remote(payload)
                    out_files = _write_ct_dicoms_from_volume(
                        output_dir=folder,
                        payload=payload,
                        series_instance_uid=series_instance_uid,
                        study_instance_uid=study_instance_uid,
                        volume=volume,
                        spacing=spacing,
                    )
                except Exception as remote_exc:
                    if not ENABLE_LEXICAL_RETRIEVAL_FALLBACK:
                        raise
                    _set_progress("CT service down: retrieving lexical match from local DB")
                    best, score = _pick_best_retrieval_case(payload, "ct", exclude_folder=folder)
                    out_files = _copy_case_dicoms_with_new_uids(
                        source_dicoms=best["dicoms"],
                        output_dir=folder,
                        payload=payload,
                        series_instance_uid=series_instance_uid,
                        study_instance_uid=study_instance_uid,
                        generation_type="ct",
                    )
                    retrieval_used = True
                    engine_used = "retrieval"
                    retrieval_meta = {
                        "score": score,
                        "sourceFileID": best["summary"].get("fileID"),
                        "sourceFolder": best["folder"].name,
                        "remoteError": str(remote_exc),
                    }
            else:
                _set_progress("Converting NIfTI to DICOM (CT asset fallback)")
                out_files = _write_ct_dicoms(folder, payload, series_instance_uid, study_instance_uid)
        else:
            if use_real_engine:
                try:
                    _set_progress("Generating XRay via remote service")
                    pixels = _generate_xray_via_remote(payload)
                    out_files = _write_xray_dicom_from_pixels(
                        output_dir=folder,
                        payload=payload,
                        series_instance_uid=series_instance_uid,
                        study_instance_uid=study_instance_uid,
                        pixels=pixels,
                    )
                except Exception as remote_exc:
                    if not ENABLE_LEXICAL_RETRIEVAL_FALLBACK:
                        raise
                    _set_progress("XRay service down: retrieving lexical match from local DB")
                    best, score = _pick_best_retrieval_case(payload, "xray", exclude_folder=folder)
                    out_files = _copy_case_dicoms_with_new_uids(
                        source_dicoms=best["dicoms"],
                        output_dir=folder,
                        payload=payload,
                        series_instance_uid=series_instance_uid,
                        study_instance_uid=study_instance_uid,
                        generation_type="xray",
                    )
                    retrieval_used = True
                    engine_used = "retrieval"
                    retrieval_meta = {
                        "score": score,
                        "sourceFileID": best["summary"].get("fileID"),
                        "sourceFolder": best["folder"].name,
                        "remoteError": str(remote_exc),
                    }
            else:
                _set_progress("Converting image to DICOM (Xray asset fallback)")
                out_files = _write_xray_dicom(folder, payload, series_instance_uid, study_instance_uid)

        summary = {
            "fileID": file_id,
            "generationType": generation_type,
            "engine": engine_used,
            "prompt": prompt,
            "retrievalFallbackUsed": retrieval_used,
            "studyInstanceUID": study_instance_uid,
            "seriesInstanceUID": series_instance_uid,
            "dicomCount": len(out_files),
            "firstFile": out_files[0].name if out_files else None,
            "lastFile": out_files[-1].name if out_files else None,
            "createdAt": datetime.now().isoformat(),
            "status": "completed",
        }
        if retrieval_meta is not None:
            summary["retrieval"] = retrieval_meta
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
            "createdAt": datetime.now().isoformat(),
        }
        folder.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
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
    mode_ok = BACKEND_MODE in VALID_BACKEND_MODES
    services = _remote_services_status() if GENERATION_ENGINE == "real" else None
    return {
        "status": "ok" if mode_ok else "misconfigured",
        "backendMode": BACKEND_MODE,
        "generationEngine": GENERATION_ENGINE,
        "validModes": sorted(VALID_BACKEND_MODES),
        "services": services,
    }


@app.get("/mode")
def mode():
    allowed_generation_types = _allowed_generation_types_for_mode(BACKEND_MODE)
    services = _remote_services_status() if GENERATION_ENGINE == "real" else None
    if services:
        if not services["ct"]["available"] and not (ENABLE_LEXICAL_RETRIEVAL_FALLBACK and _has_retrieval_candidates("ct")):
            allowed_generation_types.discard("ct")
        if not services["xray"]["available"] and not (
            ENABLE_LEXICAL_RETRIEVAL_FALLBACK and _has_retrieval_candidates("xray")
        ):
            allowed_generation_types.discard("xray")
    return {
        "backendMode": BACKEND_MODE,
        "generationEngine": GENERATION_ENGINE,
        "validModes": sorted(VALID_BACKEND_MODES),
        "allowedGenerationTypes": sorted(allowed_generation_types),
        "services": services,
    }


@app.get("/services/status")
def services_status():
    services = _remote_services_status() if GENERATION_ENGINE == "real" else {
        "ct": {"url": "", "available": True, "reason": "asset-mode"},
        "xray": {"url": "", "available": True, "reason": "asset-mode"},
    }
    return {"generationEngine": GENERATION_ENGINE, "services": services}


@app.get("/status")
def status():
    return {"process_is_running": _get_process_state()}


@app.get("/progress")
def progress():
    return _get_progress()


@app.post("/bootstrap/generative-ai-empty-study")
def ensure_empty_generative_study():
    try:
        # Serialize check/create to avoid duplicate creation on repeated Start clicks.
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

    if BACKEND_MODE not in VALID_BACKEND_MODES:
        return JSONResponse(
            status_code=500,
            content={
                "error": f"Invalid BACKEND_MODE='{BACKEND_MODE}'. Valid values: {sorted(VALID_BACKEND_MODES)}"
            },
        )

    generation_type = _normalize_generation_type(payload.get("generationType"))
    if generation_type not in ("ct", "xray"):
        return JSONResponse(status_code=400, content={"error": "generationType must be 'ct' or 'xray'/'xrays'"})

    allowed_generation_types = _allowed_generation_types_for_mode(BACKEND_MODE)
    if GENERATION_ENGINE == "real":
        remote_url = _service_url_for_generation_type(generation_type)
        svc_ok, svc_reason = _check_remote_service(remote_url, generation_type)
        if not svc_ok:
            if not (ENABLE_LEXICAL_RETRIEVAL_FALLBACK and _has_retrieval_candidates(generation_type)):
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": (
                            f"{generation_type} service is not available "
                            f"(url='{remote_url}', reason='{svc_reason}') and retrieval fallback has no candidates"
                        )
                    },
                )
    if generation_type not in allowed_generation_types:
        if BACKEND_MODE == "api-only":
            return JSONResponse(
                status_code=503,
                content={"error": "Generation disabled in api-only mode"},
            )
        return JSONResponse(
            status_code=400,
            content={
                "error": (
                    f"generationType '{generation_type}' not allowed in mode '{BACKEND_MODE}'. "
                    f"Allowed: {sorted(allowed_generation_types)}"
                )
            },
        )

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
