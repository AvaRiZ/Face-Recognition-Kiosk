# DeepFace Tuning Results

Generated: 2026-04-30T23:27:54

## Dataset

- Images: 765
- Identities: 85
- Positive pairs: 3060
- Negative pairs: 5000

## Best Results

| Model | Threshold | Accuracy | Precision | Recall | F1-score | FP | FN |
|---|---:|---:|---:|---:|---:|---:|---:|
| ArcFace | 0.86 | 0.731 | 0.605 | 0.842 | 0.704 | 1685 | 484 |
| Facenet | 0.8 | 0.967 | 0.949 | 0.964 | 0.957 | 157 | 110 |
| ArcFace + Facenet | ArcFace=0.78, Facenet=0.74 | 0.941 | 0.906 | 0.942 | 0.923 | 300 | 179 |

## Single-Model Threshold Sweep

| Model | Threshold | Accuracy | Precision | Recall | F1-score | FP | FN |
|---|---:|---:|---:|---:|---:|---:|---:|
| ArcFace | 0.7 | 0.627 | 0.505 | 0.989 | 0.668 | 2972 | 34 |
| ArcFace | 0.72 | 0.639 | 0.513 | 0.983 | 0.674 | 2861 | 52 |
| ArcFace | 0.74 | 0.650 | 0.521 | 0.976 | 0.679 | 2745 | 74 |
| ArcFace | 0.76 | 0.662 | 0.530 | 0.967 | 0.685 | 2623 | 101 |
| ArcFace | 0.78 | 0.672 | 0.538 | 0.952 | 0.688 | 2499 | 146 |
| ArcFace | 0.8 | 0.686 | 0.551 | 0.934 | 0.693 | 2328 | 203 |
| ArcFace | 0.82 | 0.700 | 0.566 | 0.908 | 0.697 | 2133 | 281 |
| ArcFace | 0.84 | 0.716 | 0.584 | 0.877 | 0.701 | 1912 | 376 |
| ArcFace | 0.86 | 0.731 | 0.605 | 0.842 | 0.704 | 1685 | 484 |
| ArcFace | 0.88 | 0.742 | 0.625 | 0.801 | 0.702 | 1471 | 610 |
| ArcFace | 0.9 | 0.754 | 0.655 | 0.745 | 0.697 | 1202 | 781 |
| Facenet | 0.66 | 0.888 | 0.773 | 0.999 | 0.871 | 899 | 4 |
| Facenet | 0.68 | 0.905 | 0.801 | 0.998 | 0.889 | 759 | 5 |
| Facenet | 0.7 | 0.922 | 0.831 | 0.997 | 0.907 | 619 | 8 |
| Facenet | 0.72 | 0.934 | 0.856 | 0.993 | 0.920 | 510 | 21 |
| Facenet | 0.74 | 0.946 | 0.882 | 0.989 | 0.933 | 405 | 33 |
| Facenet | 0.76 | 0.955 | 0.905 | 0.985 | 0.943 | 315 | 46 |
| Facenet | 0.78 | 0.964 | 0.932 | 0.977 | 0.954 | 218 | 71 |
| Facenet | 0.8 | 0.967 | 0.949 | 0.964 | 0.957 | 157 | 110 |
| Facenet | 0.82 | 0.967 | 0.965 | 0.946 | 0.956 | 104 | 164 |
| Facenet | 0.84 | 0.962 | 0.978 | 0.919 | 0.948 | 62 | 248 |
| Facenet | 0.86 | 0.947 | 0.986 | 0.872 | 0.925 | 38 | 393 |

## Ensemble Threshold Sweep

| Model | Threshold | Accuracy | Precision | Recall | F1-score | FP | FN |
|---|---:|---:|---:|---:|---:|---:|---:|
| ArcFace + Facenet | ArcFace=0.78, Facenet=0.74 | 0.941 | 0.906 | 0.942 | 0.923 | 300 | 179 |
| ArcFace + Facenet | ArcFace=0.80, Facenet=0.76 | 0.941 | 0.925 | 0.919 | 0.922 | 227 | 248 |
| ArcFace + Facenet | ArcFace=0.82, Facenet=0.78 | 0.937 | 0.946 | 0.886 | 0.915 | 155 | 349 |
| ArcFace + Facenet | ArcFace=0.84, Facenet=0.80 | 0.928 | 0.961 | 0.845 | 0.899 | 106 | 475 |
| ArcFace + Facenet | ArcFace=0.86, Facenet=0.82 | 0.915 | 0.975 | 0.798 | 0.877 | 63 | 619 |
