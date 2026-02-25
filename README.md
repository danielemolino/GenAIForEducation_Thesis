# GenAIforEdu

Progetto per generazione di immagini medicali da testo e visualizzazione DICOM in OHIF.

## A cosa serve

- Generare una **CT sintetica** da prompt testuale (Text2CT).
- Generare una **X-Ray frontale** da report testuale (XGeM).
- Convertire/salvare output in DICOM e caricarlo in Orthanc.
- Visualizzare tutto nel viewer web (OHIF).

## Blocchi principali

- `Viewer/`: interfaccia web (OHIF + pannello GenAI).
- `BackendModelli/main.py`: backend principale (router API) che inoltra CT/XRay ai modelli.
- `BackendModelli/main_text2ct.py`: logica di generazione CT (usata dal backend principale).
- `BackendModelli/main_xgem.py`: logica di generazione XRay (usata dal backend principale).
- `Viewer` + Docker Orthanc: PACS locale per archiviazione e query DICOM.
- `BackendModelli/generated_files/`: output delle generazioni.
- `BackendModelli/simulation_assets/`: asset fallback quando non si usa inferenza reale.

## Come attivare ogni blocco

Aprire 3 terminali.

### 1) Orthanc (Docker)

```bash
cd /mnt/d/GenAIforEdu/Viewer
yarn orthanc:up
```

### 2) Viewer (OHIF)

```bash
cd /mnt/d/GenAIforEdu/Viewer
yarn dev:orthanc
```

### 3) Backend principale (PowerShell)

```powershell
cd /mnt/d/GenAIforEdu/BackendModelli
$env:BACKEND_MODE="full"          # api-only | ct-only | xgem-only | full
$env:GENERATION_ENGINE="real"     # real | asset
uvicorn main:app --host 0.0.0.0 --port 8000 --access-log
```

## Modalita backend

- `BACKEND_MODE=api-only`: API online, generazione disabilitata.
- `BACKEND_MODE=ct-only`: abilita solo CT.
- `BACKEND_MODE=xgem-only`: abilita solo X-Ray.
- `BACKEND_MODE=full`: abilita CT + X-Ray.

- `GENERATION_ENGINE=real`: usa Text2CT/XGeM reali.
- `GENERATION_ENGINE=asset`: usa asset locali (debug/fallback).

## Check rapido

- Health backend: `GET http://localhost:8000/health`
- Modalita backend: `GET http://localhost:8000/mode`

