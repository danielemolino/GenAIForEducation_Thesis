#!/usr/bin/env python3
"""
Batch import JPG samples described by selected_reports.csv files into Orthanc.

Expected layout under --input-root:
  to_load/<dataset>/<group>/selected_reports.csv
  to_load/<dataset>/<group>/<relative image path from CSV>.jpg

For each CSV row this script:
  - builds a single-frame DX DICOM from the JPG
  - uses the study_id as the visible study name/description
  - uploads the DICOM to Orthanc
  - stores Orthanc study metadata:
      Report -> CSV report
      Group -> A or B
      StudyName -> CSV study_id
"""

from __future__ import annotations

import argparse
import array
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Optional
from urllib import error, request


def _http_request(
    url: str,
    method: str = "GET",
    data: Optional[bytes] = None,
    headers: Optional[Dict[str, str]] = None,
) -> bytes:
    req = request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with request.urlopen(req) as resp:
            return resp.read()
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} -> HTTP {exc.code}: {detail}") from exc


def _json_request(
    url: str,
    method: str = "GET",
    data: Optional[bytes] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Dict:
    raw = _http_request(url, method=method, data=data, headers=headers)
    return json.loads(raw.decode("utf-8"))


def _now_strings() -> tuple[str, str]:
    now = datetime.now()
    return now.strftime("%Y%m%d"), now.strftime("%H%M%S")


def _sanitize_patient_name(raw: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {" ", "_", "-"} else " " for ch in raw).strip()
    cleaned = "_".join(cleaned.split())
    return cleaned or "Uploaded_Study"


def _make_base_dataset(
    out_path: Path,
    sop_instance_uid: str,
    study_uid: str,
    series_uid: str,
    patient_name: str,
    patient_id: str,
    study_name: str,
) -> "FileDataset":
    from pydicom.dataset import Dataset, FileDataset
    from pydicom.uid import (
        DigitalXRayImageStorageForPresentation,
        ExplicitVRLittleEndian,
        PYDICOM_IMPLEMENTATION_UID,
    )

    study_date, study_time = _now_strings()

    file_meta = Dataset()
    file_meta.MediaStorageSOPClassUID = DigitalXRayImageStorageForPresentation
    file_meta.MediaStorageSOPInstanceUID = sop_instance_uid
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    file_meta.ImplementationClassUID = PYDICOM_IMPLEMENTATION_UID

    ds = FileDataset(str(out_path), {}, file_meta=file_meta, preamble=b"\0" * 128)
    ds.is_little_endian = True
    ds.is_implicit_VR = False

    ds.SOPClassUID = DigitalXRayImageStorageForPresentation
    ds.SOPInstanceUID = sop_instance_uid
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid
    ds.Modality = "DX"

    ds.PatientName = patient_name
    ds.PatientID = patient_id
    ds.StudyDescription = study_name
    ds.SeriesDescription = study_name
    ds.AccessionNumber = str(study_name)[:16]
    ds.StudyID = str(study_name)[:16]

    ds.StudyDate = study_date
    ds.StudyTime = study_time
    ds.SeriesDate = study_date
    ds.SeriesTime = study_time
    ds.ContentDate = study_date
    ds.ContentTime = study_time
    ds.InstanceCreationDate = study_date
    ds.InstanceCreationTime = study_time
    ds.Manufacturer = "import_to_orthanc.py"
    return ds


def _build_dicom_from_jpg(row: Dict[str, str], image_path: Path, output_dir: Path) -> Path:
    from PIL import Image
    from pydicom.uid import generate_uid

    image = Image.open(image_path).convert("L")
    pixels_8bit = list(image.getdata())
    max_pixel = max(pixels_8bit) if pixels_8bit else 0
    scale = 4095.0 / max(1.0, float(max_pixel))
    pixels_12bit = [int(round(value * scale)) for value in pixels_8bit]
    pixel_buffer = array.array("H", pixels_12bit)

    study_uid = generate_uid()
    series_uid = generate_uid()
    sop_uid = generate_uid()
    study_name = row["study_id"]
    patient_name = _sanitize_patient_name(study_name)
    patient_id = row.get("subject_id") or study_name

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{row['dicom_id']}.dcm"
    ds = _make_base_dataset(
        out_path=out_path,
        sop_instance_uid=sop_uid,
        study_uid=study_uid,
        series_uid=series_uid,
        patient_name=patient_name,
        patient_id=patient_id,
        study_name=study_name,
    )

    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.Rows = int(image.height)
    ds.Columns = int(image.width)
    ds.BitsAllocated = 16
    ds.BitsStored = 12
    ds.HighBit = 11
    ds.PixelRepresentation = 0
    ds.WindowWidth = 2000
    ds.WindowCenter = 1000
    ds.InstanceNumber = 1
    ds.SeriesNumber = 1
    ds.BodyPartExamined = "CHEST"
    ds.ViewPosition = row.get("ViewPosition") or ""
    ds.ImageType = ["ORIGINAL", "PRIMARY"]
    ds.StudyComments = row.get("report") or ""
    ds.ImageComments = f"Group={row.get('group') or ''}"
    ds.add_new((0x0011, 0x0010), "LO", "GenAIForEducation")
    ds.add_new((0x0011, 0x1001), "LT", row.get("report") or "")
    ds.add_new((0x0011, 0x1002), "LO", row.get("group") or "")
    ds.add_new((0x0011, 0x1003), "LO", row.get("study_id") or "")
    ds.PixelData = pixel_buffer.tobytes()
    ds.save_as(str(out_path), write_like_original=False)
    return out_path


def _upload_dicom(orthanc_url: str, dicom_path: Path) -> Dict:
    with dicom_path.open("rb") as f:
        payload = f.read()
    return _json_request(
        f"{orthanc_url.rstrip('/')}/instances",
        method="POST",
        data=payload,
        headers={"Content-Type": "application/dicom"},
    )


def _put_study_metadata(orthanc_url: str, orthanc_study_id: str, key: str, value: str) -> None:
    _http_request(
        f"{orthanc_url.rstrip('/')}/studies/{orthanc_study_id}/metadata/{key}",
        method="PUT",
        data=(value or "").encode("utf-8"),
        headers={"Content-Type": "text/plain; charset=utf-8"},
    )


def _clear_orthanc(orthanc_url: str) -> int:
    studies = _json_request(f"{orthanc_url.rstrip('/')}/studies")
    deleted = 0
    for study_id in studies:
        _http_request(f"{orthanc_url.rstrip('/')}/studies/{study_id}", method="DELETE")
        deleted += 1
    return deleted


def _iter_csv_rows(input_root: Path) -> Iterable[tuple[Path, str, Dict[str, str]]]:
    for csv_path in sorted(input_root.rglob("selected_reports.csv")):
        group = csv_path.parent.name
        if group not in {"A", "B"}:
            continue
        with csv_path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                yield csv_path, group, row


def _resolve_local_image(csv_path: Path, row: Dict[str, str]) -> Path:
    relative_dicom = Path(row["path"])
    relative_image = relative_dicom.with_suffix(".jpg")
    local_image = csv_path.parent / relative_image
    if local_image.exists():
        return local_image

    fallback = csv_path.parent.rglob(f"{row['dicom_id']}.jpg")
    for candidate in fallback:
        return candidate

    raise FileNotFoundError(f"Image not found for study {row.get('study_id')} ({row.get('dicom_id')})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Import JPG studies with CSV metadata into Orthanc.")
    parser.add_argument("--input-root", type=Path, default=Path("to_load"))
    parser.add_argument("--orthanc-url", default="http://localhost:8042")
    parser.add_argument(
        "--metadata-url",
        default="http://localhost/pacs",
        help="Base URL used to write study metadata (defaults to the local /pacs proxy).",
    )
    parser.add_argument("--work-dir", type=Path, default=Path("/tmp/import_to_orthanc"))
    parser.add_argument("--clear-db", action="store_true", help="Delete all existing Orthanc studies before import.")
    parser.add_argument("--limit", type=int, default=0, help="Import at most N rows (0 = no limit).")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--skip-metadata",
        action="store_true",
        help="Upload DICOMs without writing Orthanc study metadata.",
    )
    args = parser.parse_args()

    if not args.input_root.exists():
        print(f"Input root not found: {args.input_root}", file=sys.stderr)
        return 1

    if args.clear_db:
        if args.dry_run:
            print("[dry-run] Would clear existing Orthanc studies")
        else:
            deleted = _clear_orthanc(args.orthanc_url)
            print(f"Deleted {deleted} existing Orthanc studies")

    imported = 0
    failures = 0

    for csv_path, group, row in _iter_csv_rows(args.input_root):
        if args.limit and imported >= args.limit:
            break

        try:
            row["group"] = group
            image_path = _resolve_local_image(csv_path, row)
            study_name = row["study_id"]
            report = row.get("report") or ""
            work_dir = args.work_dir / csv_path.parent.parent.name / group / study_name

            if args.dry_run:
                print(
                    f"[dry-run] {study_name} group={group} image={image_path} "
                    f"report={report[:80]!r}"
                )
                imported += 1
                continue

            dicom_path = _build_dicom_from_jpg(row, image_path, work_dir)
            upload_result = _upload_dicom(args.orthanc_url, dicom_path)
            orthanc_study_id = upload_result["ParentStudy"]

            if not args.skip_metadata:
                try:
                    _put_study_metadata(args.metadata_url, orthanc_study_id, "Report", report)
                    _put_study_metadata(args.metadata_url, orthanc_study_id, "Group", group)
                    _put_study_metadata(args.metadata_url, orthanc_study_id, "StudyName", study_name)
                except Exception as exc:
                    print(
                        f"WARNING {study_name}: Orthanc metadata write failed, "
                        f"but DICOM import succeeded: {exc}",
                        file=sys.stderr,
                    )

            print(f"Imported study={study_name} group={group} image={image_path.name}")
            imported += 1
        except Exception as exc:
            failures += 1
            print(
                f"FAILED {row.get('study_id', '<unknown>')} "
                f"(csv={csv_path}, group={group}, image={row.get('dicom_id')}): {exc}",
                file=sys.stderr,
            )

    print(f"Done. imported={imported} failures={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
