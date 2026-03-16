# Default Generative AI Prompt Examples

This file tracks the default prompt examples shown in the Generative AI UI, along with the source cases used to derive them.

## CT

These CT prompts use the `Findings: ... Impression: ...` style.

1. Source case: `valid_39_a_1.nii.gz` (case id `39`)
   Prompt: `Findings: Multiple bilateral peripheral ground-glass opacities, more evident in the lower lobes, without pleural effusion or significant lymphadenopathy. Impression: Multifocal atypical pneumonia pattern, including viral pneumonia.`

2. Source case: `valid_16_a_1.nii.gz` (case id `16`)
   Prompt: `Findings: Focal air-space consolidation is present in the anteromedial basal segment of the left lower lobe, without pleural effusion. Impression: Left lower lobe pneumonia.`

3. Source case: `valid_8_a_1.nii.gz` (case id `8`)
   Prompt: `Findings: Bilateral diffuse ground-glass opacities with smooth interlobular septal thickening and mild mosaic attenuation are present, with no focal lobar consolidation. Impression: Pulmonary edema, likely cardiogenic.`

4. Source case: `valid_2_a_1.nii.gz` (case id `2`)
   Prompt: `Findings: Multiple bilateral pulmonary nodules of varying size are seen in both lungs, with associated post-treatment pleural-parenchymal change in the left upper lobe and mild bilateral peribronchial thickening. Impression: Multiple pulmonary metastases with post-treatment change.`

5. Source case: `valid_106_a_1.nii.gz` (case id `106`)
   Prompt: `Findings: Endobronchial soft-tissue lesion is present in the lingular bronchus with an associated irregular left upper lobe pulmonary nodule, mediastinal lymphadenopathy, and a left adrenal mass. Impression: Primary lung malignancy with mediastinal nodal involvement and left adrenal metastasis.`

## XRay

These XRay prompts use the impression text only, without the `Impression:` prefix.

1. Source study: `52535468`, patient `10002013`, dicom `0c27f551-2cc6bf3e-a2e2cabd-97973771-40f1e6ce`
   Prompt: `No acute cardiopulmonary process.`

2. Source study: `58500109`, patient `10019593`, dicom `d8bb1eda-1acb9229-4531796f-3f4dd3da-ed32b7e2`
   Prompt: `Left lower lung opacity concerning for pneumonia.`

3. Source study: `53282957`, patient `10003502`, dicom `eb2fabb7-4bbc8aab-d7371282-08e5bcb5-de2e430a`
   Prompt: `Moderate pulmonary edema with moderate to large bilateral pleural effusions and bibasilar atelectasis.`

4. Source study: `58054149`, patient `10002013`, dicom `81bca127-0c416084-67f8033c-ecb26476-6d1ecf60`
   Prompt: `Moderate left pleural effusion with adjacent atelectasis in the left lung base.`

5. Source study: `56759094`, patient `10063856`, dicom `10cd06e9-5443fef9-9afbe903-e2ce1eb5-dcff1097`
   Prompt: `Left upper lobe collapse, with a similar appearing large left hilar mass and trace left pleural effusion. No pneumothorax identified.`
