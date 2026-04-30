# YOLOv8n Mobile-SAM Training Overall Results

## Overall Metrics

| Overall Metric | Value |
|---|---:|
| Total Epochs | 50 |
| Best Epoch (by mAP50-95) | 34 |
| Best Precision | 0.89832 |
| Best Recall | 0.82540 |
| Best mAP@50 | 0.89402 |
| Best mAP@50-95 | 0.59637 |
| Final Precision (Epoch 50) | 0.96055 |
| Final Recall (Epoch 50) | 0.77307 |
| Final mAP@50 (Epoch 50) | 0.88049 |
| Final mAP@50-95 (Epoch 50) | 0.57337 |
| Average Precision (50 epochs) | 0.92564 |
| Average Recall (50 epochs) | 0.78087 |
| Average mAP@50 (50 epochs) | 0.87301 |
| Average mAP@50-95 (50 epochs) | 0.53878 |

## Chapter 4 Interpretation Table

| Metric | Value | Interpretation |
|---|---:|---|
| Precision | 0.961 | Excellent; very few false positive face detections |
| Recall | 0.773 | Good; most faces were detected, though some may still be missed |
| mAP@50 | 0.880 | Strong detection performance at IoU 0.50 |
| mAP@50-95 | 0.573 | Strong overall localization performance across stricter IoU thresholds |
| Validation Images | 23 | Moderate validation set; results are promising but still benefit from broader testing |
| Validation Instances | 63 | Total labeled face instances used during validation |

## Evaluation Metrics and Remarks

| Evaluation Metric | Result | Remarks |
|---|---:|---|
| Precision | 96.1% | Indicates high accuracy in predicted face detections |
| Recall | 77.3% | Indicates that most faces were detected by the model |
| mAP@50 | 88.0% | Shows strong detection quality at standard IoU threshold |
| mAP@50-95 | 57.3% | Shows good bounding-box quality across multiple IoU thresholds |

## Copy-Ready CSV

```csv
metric,value
Total Epochs,50
Best Epoch (by mAP50-95),34
Best Precision,0.89832
Best Recall,0.82540
Best mAP@50,0.89402
Best mAP@50-95,0.59637
Final Precision (Epoch 50),0.96055
Final Recall (Epoch 50),0.77307
Final mAP@50 (Epoch 50),0.88049
Final mAP@50-95 (Epoch 50),0.57337
Average Precision (50 epochs),0.92564
Average Recall (50 epochs),0.78087
Average mAP@50 (50 epochs),0.87301
Average mAP@50-95 (50 epochs),0.53878
Validation Images,23
Validation Instances,63
```
