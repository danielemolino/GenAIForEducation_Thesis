from datetime import datetime
from pathlib import Path
import threading
import time
import subprocess
import os
import sys
from typing import List
import json
import base64
import traceback
from urllib import request as urlrequest
from urllib import error as urlerror

import numpy as np
import pydicom
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import (
    DigitalXRayImageStorageForPresentation,
    ExplicitVRLittleEndian,
    generate_uid,
)

try:
    import tifffile
except Exception:
    tifffile = None

try:
    from PIL import Image
except Exception:
    Image = None


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

XGEM_DEFAULT_ROOT = Path("/mnt/c/Users/danie/OneDrive/Desktop/Codice/XGeM_API")
XGEM_DEFAULT_ROOT_WINDOWS = Path(r"C:\Users\danie\OneDrive\Desktop\Codice\XGeM_API")
SERVICE_MODE = "xgem-only"

_process_is_running = False
_progress_text = "Idle"
_state_lock = threading.Lock()


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


def _find_xray_asset() -> Path | None:
    candidates = [
        ASSETS_ROOT / "xray.dcm",
        ASSETS_ROOT / "xray.png",
        ASSETS_ROOT / "xray.jpg",
        ASSETS_ROOT / "xray.jpeg",
    ]
    return next((p for p in candidates if p.exists()), None)


def _load_xray_pixels_from_asset() -> np.ndarray:
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


def _resolve_xgem_root(payload: dict) -> Path | None:
    userprofile = os.environ.get("USERPROFILE")
    onedrive = os.environ.get("OneDrive")
    dynamic_windows_candidates = []
    if onedrive:
        dynamic_windows_candidates.append(Path(onedrive) / "Desktop" / "Codice" / "XGeM_API")
    if userprofile:
        dynamic_windows_candidates.append(Path(userprofile) / "OneDrive" / "Desktop" / "Codice" / "XGeM_API")
        dynamic_windows_candidates.append(Path(userprofile) / "Desktop" / "Codice" / "XGeM_API")

    candidates = [
        Path(payload["xgemApiRoot"]) if payload.get("xgemApiRoot") else None,
        Path(os.environ["XGEM_API_ROOT"]) if os.environ.get("XGEM_API_ROOT") else None,
        Path(__file__).resolve().parent / "XGeM_API",
        XGEM_DEFAULT_ROOT,
        XGEM_DEFAULT_ROOT_WINDOWS,
        *dynamic_windows_candidates,
    ]
    return next((p for p in candidates if p and p.exists()), None)


def _generate_xray_pixels_with_xgem(payload: dict, output_dir: Path) -> np.ndarray:
    if tifffile is None:
        raise RuntimeError("tifffile non installato")

    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        raise RuntimeError("Prompt is required for XRay generation")

    xgem_root = _resolve_xgem_root(payload)
    if xgem_root is None:
        raise RuntimeError("XGeM_API root not found. Set payload.xgemApiRoot or env XGEM_API_ROOT.")

    bridge_script = Path(__file__).resolve().parent / "xgem_report_to_frontal.py"
    xgem_device = payload.get("xgemDevice") or os.environ.get("XGEM_DEVICE")
    xgem_model_path = payload.get("xgemModelPath") or os.environ.get("XGEM_MODEL_PATH")
    xgem_weights_dir = payload.get("xgemWeightsDir") or os.environ.get("XGEM_WEIGHTS_DIR")
    requested_steps = payload.get("xgemSteps")
    xgem_steps = int(requested_steps) if requested_steps is not None else (20 if (xgem_device or "").lower() == "cpu" else 50)
    xgem_python = (
        payload.get("xgemPythonExecutable")
        or os.environ.get("XGEM_PYTHON_EXECUTABLE")
        or sys.executable
    )

    cmd = [
        xgem_python,
        str(bridge_script),
        "--xgem-root",
        str(xgem_root),
        "--output-dir",
        str(output_dir),
        "--prompt",
        prompt,
        "--steps",
        str(max(1, xgem_steps)),
    ]
    if xgem_device:
        cmd.extend(["--device", xgem_device])
    if xgem_model_path:
        cmd.extend(["--model-path", xgem_model_path])
    if xgem_weights_dir:
        cmd.extend(["--weights-dir", xgem_weights_dir])

    child_env = os.environ.copy()
    child_env["PYTHONUNBUFFERED"] = "1"
    child_env.setdefault("OMP_NUM_THREADS", "1")
    child_env.setdefault("MKL_NUM_THREADS", "1")
    child_env.setdefault("OPENBLAS_NUM_THREADS", "1")
    proc = subprocess.run(cmd, capture_output=True, text=True, env=child_env)
    if proc.returncode != 0:
        stdout_log = output_dir / "xgem_subprocess_stdout.log"
        stderr_log = output_dir / "xgem_subprocess_stderr.log"
        stdout_log.write_text(proc.stdout or "", encoding="utf-8", errors="ignore")
        stderr_log.write_text(proc.stderr or "", encoding="utf-8", errors="ignore")
        stderr = (proc.stderr or "").strip()
        stdout = (proc.stdout or "").strip()

        stderr_tail = "\n".join(stderr.splitlines()[-8:]) if stderr else ""
        stdout_tail = "\n".join(stdout.splitlines()[-8:]) if stdout else ""
        detail_parts = [f"returncode={proc.returncode}"]
        if proc.returncode == 3221225477:
            detail_parts.append(
                "Windows access violation (0xC0000005): likely native crash/OOM in torch extension or runtime mismatch"
            )
        if stderr_tail:
            detail_parts.append(f"stderr_tail: {stderr_tail}")
        if stdout_tail:
            detail_parts.append(f"stdout_tail: {stdout_tail}")
        detail_parts.append(f"logs: {stdout_log.name}, {stderr_log.name}")
        detail = " | ".join(detail_parts)
        raise RuntimeError(f"XGeM subprocess failed: {detail}")

    out_path = output_dir / "output_frontal.tiff"
    if not out_path.exists():
        raise RuntimeError(f"XGeM inference produced no frontal image: {out_path}")

    xray = tifffile.imread(str(out_path)).astype(np.float32)
    return _normalize_to_uint16(xray)


def _create_base_dataset(
    output_file: Path,
    payload: dict,
    series_instance_uid: str,
    study_instance_uid: str,
    sop_instance_uid: str,
) -> FileDataset:
    now = datetime.now()

    file_meta = Dataset()
    file_meta.MediaStorageSOPClassUID = DigitalXRayImageStorageForPresentation
    file_meta.MediaStorageSOPInstanceUID = sop_instance_uid
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    file_meta.ImplementationClassUID = generate_uid()

    ds = FileDataset(str(output_file), {}, file_meta=file_meta, preamble=b"\0" * 128)

    patient_name = payload.get("patient_name") or "Generated^Patient"
    patient_id = payload.get("patient_id") or "GEN-001"

    ds.SOPClassUID = DigitalXRayImageStorageForPresentation
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

    ds.Manufacturer = "XGeM"
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    return ds


def _write_xray_dicom(
    output_dir: Path,
    payload: dict,
    series_instance_uid: str,
    study_instance_uid: str,
) -> List[Path]:
    source = (payload.get("xraySource") or "xgem").lower()
    if source == "asset":
        pixels = _load_xray_pixels_from_asset()
        payload["_xrayEffectiveSource"] = "asset"
    else:
        _set_progress("XGeM: running report->frontal inference")
        pixels = _generate_xray_pixels_with_xgem(payload, output_dir)
        payload["_xrayEffectiveSource"] = "xgem"

    out_path = output_dir / "xray_001.dcm"
    sop_instance_uid = generate_uid()

    ds = _create_base_dataset(
        output_file=out_path,
        payload=payload,
        series_instance_uid=series_instance_uid,
        study_instance_uid=study_instance_uid,
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

        generation_type = (payload.get("generationType") or "xray").lower()
        if generation_type == "xrays":
            generation_type = "xray"
        if generation_type != "xray":
            raise RuntimeError("main_xgem supports only generationType='xray'")

        study_instance_uid = payload.get("studyInstanceUID") or generate_uid()
        out_files = _write_xray_dicom(folder, payload, series_instance_uid, study_instance_uid)

        summary = {
            "fileID": file_id,
            "generationType": generation_type,
            "xrayMode": payload.get("_xrayEffectiveSource", "n/a"),
            "studyInstanceUID": study_instance_uid,
            "seriesInstanceUID": series_instance_uid,
            "dicomCount": len(out_files),
            "firstFile": out_files[0].name if out_files else None,
            "lastFile": out_files[-1].name if out_files else None,
            "createdAt": datetime.now().isoformat(),
            "status": "completed",
        }
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
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


@app.get("/health")
def health():
    return {"status": "ok", "serviceMode": SERVICE_MODE}


@app.get("/mode")
def mode():
    return {"serviceMode": SERVICE_MODE, "allowedGenerationTypes": ["xray"]}


@app.get("/status")
def status():
    return {"process_is_running": _get_process_state()}


@app.get("/progress")
def progress():
    return _get_progress()


@app.post("/files/{file_id}")
def start_generation(file_id: str, payload: dict):
    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        return JSONResponse(status_code=400, content={"error": "Prompt is required"})

    if _get_process_state():
        return JSONResponse(status_code=409, content={"error": "A generation is already running"})

    generation_type = (payload.get("generationType") or "xray").lower()
    if generation_type == "xrays":
        generation_type = "xray"
    if generation_type != "xray":
        return JSONResponse(status_code=400, content={"error": "main_xgem supports only generationType='xray'"})

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
