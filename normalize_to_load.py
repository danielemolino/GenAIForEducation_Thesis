#!/usr/bin/env python3
"""
Normalize and convert studies for OHIF/Orthanc loading.

Input folder default:   D:\\Tesi_Codici\\to_load
Output folder default:  D:\\Tesi_Codici\\normalized_to_load

Supported inputs:
- DICOM files (.dcm or DICOM without extension): normalized to Explicit VR Little Endian.
- X-ray image files (.png, .jpg, .jpeg, .bmp, .tif, .tiff): converted to single-frame DX DICOM.
- CT volumes (.nii, .nii.gz): converted to CT DICOM series (one slice per file).
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional, Tuple

import numpy as np
import pydicom
from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import (
    CTImageStorage,
    DigitalXRayImageStorageForPresentation,
    ExplicitVRLittleEndian,
    PYDICOM_IMPLEMENTATION_UID,
    generate_uid,
)


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
NIFTI_EXTS = {".nii", ".nii.gz"}


def _now_strings() -> Tuple[str, str]:
    now = datetime.now()
    return now.strftime("%Y%m%d"), now.strftime("%H%M%S")


def _sanitize_patient_name(raw: str) -> str:
    # DICOM PN commonly uses "Family^Given"
    cleaned = "".join(ch if ch.isalnum() or ch in {" ", "_", "-"} else " " for ch in raw).strip()
    cleaned = "_".join(cleaned.split())
    return cleaned or "Uploaded^Patient"


def _detect_nifti(path: Path) -> bool:
    lower = path.name.lower()
    return lower.endswith(".nii") or lower.endswith(".nii.gz")


def _is_probably_dicom(path: Path) -> bool:
    if path.suffix.lower() == ".dcm":
        return True
    try:
        pydicom.dcmread(str(path), stop_before_pixels=True, force=False)
        return True
    except Exception:
        return False


def _iter_files(root: Path) -> Iterable[Path]:
    for p in root.rglob("*"):
        if p.is_file():
            yield p


def _safe_folder_name(filename: str) -> str:
    cleaned = []
    for ch in filename:
        if ch.isalnum() or ch in {"-", "_"}:
            cleaned.append(ch)
        else:
            cleaned.append("_")
    name = "".join(cleaned).strip("_")
    return name or "study"


def _make_base_dataset(
    out_path: Path,
    sop_class_uid: str,
    sop_instance_uid: str,
    study_uid: str,
    series_uid: str,
    modality: str,
    patient_name: str,
    patient_id: str,
    study_description: str,
    series_description: str,
    accession_number: str,
) -> FileDataset:
    study_date, study_time = _now_strings()

    file_meta = Dataset()
    file_meta.MediaStorageSOPClassUID = sop_class_uid
    file_meta.MediaStorageSOPInstanceUID = sop_instance_uid
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    file_meta.ImplementationClassUID = PYDICOM_IMPLEMENTATION_UID

    ds = FileDataset(str(out_path), {}, file_meta=file_meta, preamble=b"\0" * 128)
    ds.is_little_endian = True
    ds.is_implicit_VR = False

    ds.SOPClassUID = sop_class_uid
    ds.SOPInstanceUID = sop_instance_uid
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid
    ds.Modality = modality

    ds.PatientName = patient_name
    ds.PatientID = patient_id
    ds.StudyDescription = study_description
    ds.SeriesDescription = series_description
    ds.AccessionNumber = accession_number
    ds.StudyID = "1"

    ds.StudyDate = study_date
    ds.StudyTime = study_time
    ds.SeriesDate = study_date
    ds.SeriesTime = study_time
    ds.ContentDate = study_date
    ds.ContentTime = study_time
    ds.InstanceCreationDate = study_date
    ds.InstanceCreationTime = study_time
    ds.Manufacturer = "normalize_to_load.py"
    return ds


def _validate_pixel_compatibility(ds: FileDataset, src: Path) -> None:
    required = [
        "SOPClassUID",
        "Modality",
        "Rows",
        "Columns",
        "SamplesPerPixel",
        "PhotometricInterpretation",
        "BitsAllocated",
        "BitsStored",
        "HighBit",
        "PixelRepresentation",
        "PixelData",
    ]
    missing = [k for k in required if not hasattr(ds, k)]
    if missing:
        raise RuntimeError(f"Missing required pixel tags {missing} for {src}")


def _normalize_dicom_file(src: Path, dst: Path) -> None:
    ds = pydicom.dcmread(str(src), force=True)

    # Best effort decompression if compressed.
    decompressed_successfully = True
    if ds.file_meta.get("TransferSyntaxUID") and ds.file_meta.TransferSyntaxUID.is_compressed:
        try:
            ds.decompress()
        except Exception:
            decompressed_successfully = False

    if not decompressed_successfully:
        raise RuntimeError(
            "Compressed DICOM cannot be normalized without decoder plugins "
            "(gdcm/pylibjpeg). Install one, or provide already-uncompressed DICOM."
        )

    # Ensure required list fields exist.
    stem = src.stem
    patient_name = ds.get("PatientName") or _sanitize_patient_name(stem)
    patient_id = ds.get("PatientID") or "UPL0001"
    study_uid = ds.get("StudyInstanceUID") or generate_uid()
    series_uid = ds.get("SeriesInstanceUID") or generate_uid()
    modality = ds.get("Modality") or "DX"

    study_date, study_time = _now_strings()
    ds.PatientName = patient_name
    ds.PatientID = patient_id
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid
    ds.Modality = modality
    ds.StudyDate = ds.get("StudyDate") or study_date
    ds.StudyTime = ds.get("StudyTime") or study_time
    ds.StudyDescription = ds.get("StudyDescription") or ("Uploaded CT" if modality == "CT" else "Uploaded Xray")
    ds.SeriesDescription = ds.get("SeriesDescription") or ds.StudyDescription
    ds.AccessionNumber = ds.get("AccessionNumber") or datetime.now().strftime("%Y%m%d%H%M%S")[:16]

    # Normalize transfer syntax for broad browser compatibility.
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    _validate_pixel_compatibility(ds, src)

    dst.parent.mkdir(parents=True, exist_ok=True)
    ds.save_as(str(dst), write_like_original=False)


def _convert_xray_image(src: Path, out_dir: Path) -> None:
    try:
        from PIL import Image
    except Exception as exc:
        raise RuntimeError("Pillow is required to convert image files. Install with: pip install pillow") from exc

    img = Image.open(src).convert("L")
    arr = np.asarray(img, dtype=np.uint16)
    arr = ((arr.astype(np.float32) / max(1.0, float(arr.max()))) * 4095.0).astype(np.uint16)

    study_uid = generate_uid()
    series_uid = generate_uid()
    sop_uid = generate_uid()
    name = _sanitize_patient_name(src.stem)
    accession = datetime.now().strftime("%Y%m%d%H%M%S")[:16]
    out_path = out_dir / f"{src.stem}_dx.dcm"

    ds = _make_base_dataset(
        out_path=out_path,
        sop_class_uid=DigitalXRayImageStorageForPresentation,
        sop_instance_uid=sop_uid,
        study_uid=study_uid,
        series_uid=series_uid,
        modality="DX",
        patient_name=name,
        patient_id="UPL0001",
        study_description="Uploaded Xray",
        series_description="Uploaded Xray",
        accession_number=accession,
    )

    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.Rows = int(arr.shape[0])
    ds.Columns = int(arr.shape[1])
    ds.BitsAllocated = 16
    ds.BitsStored = 12
    ds.HighBit = 11
    ds.PixelRepresentation = 0
    ds.WindowWidth = 2000
    ds.WindowCenter = 1000
    ds.InstanceNumber = 1
    ds.SeriesNumber = 1
    ds.PixelData = arr.tobytes()
    _validate_pixel_compatibility(ds, src)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    ds.save_as(str(out_path), write_like_original=False)


def _apply_ct_rotation(pixels: np.ndarray, mode: str) -> np.ndarray:
    if mode == "ccw":
        return np.rot90(pixels, k=1)
    if mode == "cw":
        return np.rot90(pixels, k=3)
    return pixels


def _convert_nifti_ct(src: Path, out_dir: Path, ct_rotation: str = "ccw") -> int:
    try:
        import nibabel as nib
    except Exception as exc:
        raise RuntimeError("nibabel is required for CT volume conversion. Install with: pip install nibabel") from exc

    nii = nib.load(str(src))
    vol = nii.get_fdata().astype(np.float32)
    if vol.ndim == 4:
        vol = vol[..., 0]
    if vol.ndim != 3:
        raise RuntimeError(f"NIfTI volume shape not supported: {vol.shape}")

    zooms = nii.header.get_zooms()
    row_spacing = float(abs(zooms[0])) if len(zooms) > 0 else 1.0
    col_spacing = float(abs(zooms[1])) if len(zooms) > 1 else 1.0
    slice_spacing = float(abs(zooms[2])) if len(zooms) > 2 else 1.0

    low, high = np.percentile(vol, [1, 99])
    if high <= low:
        high = low + 1.0
    vol = np.clip(vol, low, high)
    vol = ((vol - low) / (high - low) * 1624.0 - 1024.0).astype(np.int16)

    study_uid = generate_uid()
    series_uid = generate_uid()
    accession = datetime.now().strftime("%Y%m%d%H%M%S")[:16]
    name = _sanitize_patient_name(src.stem.replace(".nii", ""))
    created = 0

    # one DICOM per slice
    for i in range(vol.shape[2]):
        sop_uid = generate_uid()
        out_path = out_dir / f"{src.stem.replace('.nii.gz', '').replace('.nii', '')}_ct_{i+1:03d}.dcm"
        ds = _make_base_dataset(
            out_path=out_path,
            sop_class_uid=CTImageStorage,
            sop_instance_uid=sop_uid,
            study_uid=study_uid,
            series_uid=series_uid,
            modality="CT",
            patient_name=name,
            patient_id="UPL0001",
            study_description="Uploaded CT",
            series_description="Uploaded CT",
            accession_number=accession,
        )

        pixels = vol[:, :, i]
        pixels = _apply_ct_rotation(pixels, ct_rotation)
        ds.SeriesNumber = 1
        ds.InstanceNumber = i + 1
        ds.ImagePositionPatient = [0.0, 0.0, float(i * slice_spacing)]
        ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        ds.SliceThickness = slice_spacing
        ds.PixelSpacing = [row_spacing, col_spacing]
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
        ds.PixelData = np.ascontiguousarray(pixels).tobytes()
        _validate_pixel_compatibility(ds, src)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        ds.save_as(str(out_path), write_like_original=False)
        created += 1

    return created


def _route_file(
    src: Path, input_root: Path, output_root: Path, ct_rotation: str = "ccw"
) -> Tuple[str, int]:
    rel = src.relative_to(input_root)
    # One output folder per input file.
    parent_out = output_root / rel.parent / _safe_folder_name(src.name)
    suffix = src.suffix.lower()

    if _detect_nifti(src):
        count = _convert_nifti_ct(src, parent_out, ct_rotation=ct_rotation)
        return ("ct_from_nifti", count)

    if suffix in IMAGE_EXTS:
        _convert_xray_image(src, parent_out)
        return ("xray_from_image", 1)

    if _is_probably_dicom(src):
        dst_name = "normalized.dcm"
        _normalize_dicom_file(src, parent_out / dst_name)
        return ("dicom_normalized", 1)

    return ("skipped_unsupported", 0)


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize/convert studies for loading into OHIF/Orthanc.")
    parser.add_argument(
        "--input",
        default=r".\to_load",
        help="Input folder containing DICOM/images/NIfTI.",
    )
    parser.add_argument(
        "--output",
        default=r".\normalized_to_load",
        help="Output folder for normalized DICOM files.",
    )
    parser.add_argument(
        "--ct-rotation",
        default="ccw",
        choices=["none", "cw", "ccw"],
        help="Rotation applied to CT slices converted from NIfTI.",
    )
    args = parser.parse_args()

    input_root = Path(args.input)
    output_root = Path(args.output)

    if not input_root.exists() or not input_root.is_dir():
        print(f"[ERROR] Input folder not found: {input_root}")
        return 1

    output_root.mkdir(parents=True, exist_ok=True)

    stats = {
        "dicom_normalized": 0,
        "xray_from_image": 0,
        "ct_from_nifti": 0,
        "skipped_unsupported": 0,
        "errors": 0,
    }

    files = list(_iter_files(input_root))
    if not files:
        print(f"[INFO] No files found in: {input_root}")
        return 0

    for src in files:
        try:
            kind, count = _route_file(
                src, input_root, output_root, ct_rotation=args.ct_rotation
            )
            stats[kind] += count if kind != "skipped_unsupported" else 1
            print(f"[OK] {src} -> {kind} ({count})")
        except Exception as exc:
            stats["errors"] += 1
            print(f"[ERROR] {src}: {exc}")

    print("\n=== SUMMARY ===")
    for k, v in stats.items():
        print(f"{k}: {v}")
    print(f"Output folder: {output_root}")
    return 0 if stats["errors"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
