#!/usr/bin/env python3
"""
Batch import JPG samples into Orthanc.

Supported layouts under --input-root:

1) Legacy CSV layout:
   to_load/<dataset>/<group>/selected_reports.csv
   to_load/<dataset>/<group>/<relative image path from CSV>.jpg

2) Per-study TXT layout:
   to_load/<dataset>/<group>/<study_id>/<image>.jpg
   to_load/<dataset>/<group>/<study_id>/report.txt
   or
   to_load/<dataset>/<group>/<study_id>/<image>.txt

Only datasets Healthy, Edema and Pneumo are imported, and only groups A/B.
"""

from __future__ import annotations

import argparse
import array
import json
import time
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Optional
from urllib import error, request

EMPTY_GENERATIVE_STUDY_UID = "1.2.826.0.1.3680043.8.498.92334923612841918328708913924036869452"


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
    pixels_8bit = list(image.getdata(band=0))
    # Preserve the JPEG appearance as much as possible instead of stretching the
    # full range aggressively; these files are already display-ready images.
    pixel_buffer = array.array("H", pixels_8bit)

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
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.PixelRepresentation = 0
    ds.WindowWidth = 255
    ds.WindowCenter = 127
    ds.InstanceNumber = 1
    ds.SeriesNumber = 1
    ds.BodyPartExamined = "CHEST"
    ds.ViewPosition = row.get("ViewPosition") or ""
    ds.ImageType = ["ORIGINAL", "PRIMARY"]
    report = (row.get("report") or "").strip()
    impression = (row.get("impression") or "").strip()
    same_text = report.casefold() == impression.casefold() if report and impression else False
    findings_value = "" if same_text else report
    impressions_value = impression or report

    ds.StudyComments = findings_value
    ds.ImageComments = f"Group={row.get('group') or ''}"
    ds.add_new((0x0011, 0x0010), "LO", "GenAIForEducation")
    ds.add_new((0x0011, 0x1001), "LT", findings_value)
    ds.add_new((0x0011, 0x1002), "LO", row.get("group") or "")
    ds.add_new((0x0011, 0x1003), "LO", row.get("study_id") or "")
    ds.add_new((0x0011, 0x1004), "LT", impressions_value)
    ds.PixelData = pixel_buffer.tobytes()
    ds.save_as(str(out_path), write_like_original=False)
    return out_path


def _create_empty_xray_placeholder_dicom(output_dir: Path) -> Path:
    from pydicom.uid import generate_uid

    study_name = "GenerativeAI Placeholder (Hidden)"
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "generation_empty_file.dcm"
    ds = _make_base_dataset(
        out_path=out_path,
        sop_instance_uid=generate_uid(),
        study_uid=EMPTY_GENERATIVE_STUDY_UID,
        series_uid=generate_uid(),
        patient_name="GENAI_PLACEHOLDER",
        patient_id="GENAI0001",
        study_name=study_name,
    )

    pixels = array.array("H", [0] * (512 * 512))
    ds.AccessionNumber = "GENAIEMPTY"
    ds.StudyID = "GENAI_EMPTY"
    ds.PatientName = "GENAI^PLACEHOLDER"
    ds.Modality = "DX"
    ds.SeriesDescription = "GenerativeAI Placeholder"
    ds.StudyDescription = study_name
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.Rows = 512
    ds.Columns = 512
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0
    ds.WindowWidth = 2000
    ds.WindowCenter = 1000
    ds.ImageType = ["ORIGINAL", "PRIMARY"]
    ds.PixelData = pixels.tobytes()
    ds.save_as(str(out_path), write_like_original=False)
    return out_path


def _ensure_empty_generative_study(
    orthanc_url: str,
    metadata_url: str,
    work_dir: Path,
) -> None:
    existing = _resolve_study_id_from_uid(metadata_url, EMPTY_GENERATIVE_STUDY_UID)
    if existing:
        print(f"Placeholder study already present: {EMPTY_GENERATIVE_STUDY_UID}")
        return

    placeholder = _create_empty_xray_placeholder_dicom(work_dir / "bootstrap")
    _upload_dicom(orthanc_url, placeholder)
    resolved = _resolve_study_id_from_uid(metadata_url, EMPTY_GENERATIVE_STUDY_UID)
    if not resolved:
        raise RuntimeError("Unable to resolve placeholder study after upload")
    print(f"Created placeholder study: {EMPTY_GENERATIVE_STUDY_UID}")


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


def _resolve_study_id_from_uid(metadata_url: str, study_instance_uid: str, retries: int = 5) -> Optional[str]:
    query = json.dumps(
        {
            "Level": "Study",
            "Expand": True,
            "Query": {"StudyInstanceUID": study_instance_uid},
        }
    ).encode("utf-8")

    for attempt in range(retries):
        response = _json_request(
            f"{metadata_url.rstrip('/')}/tools/find",
            method="POST",
            data=query,
            headers={"Content-Type": "application/json"},
        )
        study_id = response[0]["ID"] if response else None
        if study_id:
            return study_id
        time.sleep(0.4 * (attempt + 1))
    return None


def _clear_orthanc(orthanc_url: str) -> int:
    studies = _json_request(f"{orthanc_url.rstrip('/')}/studies")
    deleted = 0
    for study_id in studies:
        _http_request(f"{orthanc_url.rstrip('/')}/studies/{study_id}", method="DELETE")
        deleted += 1
    return deleted


DEFAULT_ALLOWED_DATASETS = {"Healthy", "Edema", "Pneumo"}
ALLOWED_GROUPS = {"A", "B"}


def _study_name_from_dir(study_dir: Path) -> str:
    raw = study_dir.name
    if raw.startswith("s") and raw[1:].isdigit():
        return raw[1:]
    return raw


def _resolve_report_text(study_dir: Path, image_path: Path) -> str:
    candidates = [
        image_path.with_suffix(".txt"),
        study_dir / "report.txt",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.read_text(encoding="utf-8").strip()
    raise FileNotFoundError(f"Report text not found for image {image_path}")


def _iter_txt_rows(input_root: Path, allowed_datasets: set[str]) -> Iterable[tuple[Path, str, Dict[str, str]]]:
    for dataset_dir in sorted(p for p in input_root.iterdir() if p.is_dir()):
        if dataset_dir.name not in allowed_datasets:
            continue
        for group_dir in sorted(p for p in dataset_dir.iterdir() if p.is_dir()):
            if group_dir.name not in ALLOWED_GROUPS:
                continue
            for study_dir in sorted(p for p in group_dir.iterdir() if p.is_dir()):
                jpgs = sorted(study_dir.glob("*.jpg"))
                if not jpgs:
                    continue
                image_path = jpgs[0]
                report_text = _resolve_report_text(study_dir, image_path)
                study_name = _study_name_from_dir(study_dir)
                yield study_dir, group_dir.name, {
                    "study_id": study_name,
                    "subject_id": study_name,
                    "dicom_id": image_path.stem,
                    "image_path": str(image_path),
                    "report": report_text,
                    "impression": report_text,
                    "ViewPosition": "",
                }


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
    parser.add_argument(
        "--group-map-output",
        type=Path,
        default=Path("Viewer/platform/app/public/study_groups.generated.json"),
        help="JSON file where StudyInstanceUID -> Group mappings are written for the worklist.",
    )
    parser.add_argument(
        "--datasets",
        default="Healthy,Edema,Pneumo",
        help="Comma-separated dataset names to import, e.g. Other or Healthy,Edema,Pneumo.",
    )
    args = parser.parse_args()
    allowed_datasets = {item.strip() for item in args.datasets.split(",") if item.strip()}
    if not allowed_datasets:
        allowed_datasets = set(DEFAULT_ALLOWED_DATASETS)

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
    generated_group_map: Dict[str, str] = {}

    for source_path, group, row in _iter_txt_rows(args.input_root, allowed_datasets):
        if args.limit and imported >= args.limit:
            break

        try:
            row["group"] = group
            image_path = Path(row["image_path"])
            study_name = row["study_id"]
            report = row.get("report") or ""
            impression = row.get("impression") or ""
            same_text = report.strip().casefold() == impression.strip().casefold() if report and impression else False
            findings_value = "" if same_text else report
            impressions_value = impression or report
            work_dir = args.work_dir / source_path.parent.parent.name / group / study_name

            if args.dry_run:
                print(
                    f"[dry-run] {study_name} group={group} image={image_path} "
                    f"report={report[:80]!r}"
                )
                imported += 1
                continue

            dicom_path = _build_dicom_from_jpg(row, image_path, work_dir)
            _upload_dicom(args.orthanc_url, dicom_path)
            # Resolve through /tools/find using the DICOM StudyInstanceUID, not the upload ParentStudy ID.
            import pydicom

            ds = pydicom.dcmread(str(dicom_path), stop_before_pixels=True)
            generated_group_map[str(ds.StudyInstanceUID)] = group
            orthanc_study_id = _resolve_study_id_from_uid(args.metadata_url, str(ds.StudyInstanceUID))

            if not args.skip_metadata:
                if not orthanc_study_id:
                    raise RuntimeError("Unable to resolve Orthanc study ID from StudyInstanceUID after upload")
                try:
                    if findings_value.strip():
                        _put_study_metadata(args.metadata_url, orthanc_study_id, "Findings", findings_value)
                        _put_study_metadata(args.metadata_url, orthanc_study_id, "1024", findings_value)
                    _put_study_metadata(args.metadata_url, orthanc_study_id, "Impressions", impressions_value)
                    _put_study_metadata(args.metadata_url, orthanc_study_id, "1025", impressions_value)
                    try:
                        _put_study_metadata(args.metadata_url, orthanc_study_id, "Group", group)
                    except Exception:
                        _put_study_metadata(args.metadata_url, orthanc_study_id, "1027", group)
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
                f"(source={source_path}, group={group}, image={row.get('dicom_id')}): {exc}",
                file=sys.stderr,
            )

    if not args.dry_run:
        args.group_map_output.parent.mkdir(parents=True, exist_ok=True)
        with args.group_map_output.open("w", encoding="utf-8") as f:
            json.dump(generated_group_map, f, indent=2, sort_keys=True)
        print(f"Wrote group map: {args.group_map_output}")
        _ensure_empty_generative_study(args.orthanc_url, args.metadata_url, args.work_dir)

    print(f"Done. imported={imported} failures={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
