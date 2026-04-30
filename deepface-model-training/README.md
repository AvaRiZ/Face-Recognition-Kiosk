# DeepFace Model Experiments

This folder is for recognition-model experiments, not for enrolling real kiosk users.

## Current Dataset Shape

`df_images/` uses one folder per identity label. Each folder currently has one original image and eight light augmented variants.

```text
df_images/
  image1/
    image1.jpg
    aug_image1_01.jpg
    ...
```

## Workflow

1. Generate or refresh augmentations:

```powershell
python .\deepface-model-training\augment_recognition_dataset.py --variants 8 --overwrite
```

2. Evaluate ArcFace and Facenet thresholds:

```powershell
python .\deepface-model-training\evaluate_deepface_models.py
```

For a quick smoke test:

```powershell
python .\deepface-model-training\evaluate_deepface_models.py --max-identities 3
```

## Notes

ArcFace and Facenet in DeepFace are pretrained embedding models. The practical "hyperparameters" for this project are the inference settings and decision thresholds:

- `detector_backend`
- `align`
- `normalization`
- cosine-confidence threshold per model
- two-model ensemble rule

The kiosk app compares L2-normalized embeddings with cosine similarity, so this experiment script reports confidence thresholds in the same style.
