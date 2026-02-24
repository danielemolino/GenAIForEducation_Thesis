from datetime import datetime
from pathlib import Path
import threading
import time
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

_process_is_running = False
_progress_text = "Idle"
_state_lock = threading.Lock()

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
    volume, spacing = _load_ct_volume()
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
        _set_progress("Running fake generation")

        folder.mkdir(parents=True, exist_ok=True)

        generation_type = (payload.get("generationType") or "ct").lower()
        study_instance_uid = payload.get("studyInstanceUID") or generate_uid()
        if generation_type == "ct":
            _set_progress("Converting NIfTI to DICOM (CT)")
            out_files = _write_ct_dicoms(folder, payload, series_instance_uid, study_instance_uid)
        else:
            _set_progress("Converting image to DICOM (Xray)")
            out_files = _write_xray_dicom(folder, payload, series_instance_uid, study_instance_uid)

        summary = {
            "fileID": file_id,
            "generationType": generation_type,
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
    return {"status": "ok"}


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
        "message": "Fake generation started",
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