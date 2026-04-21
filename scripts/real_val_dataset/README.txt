Real validation dataset scaffold

Use this dataset for hand-labeled validation on real frames.

Expected structure:
- images/val/*.jpg
- labels/val/*.txt

Each label file should use YOLO detection format:
<class_id> <x_center> <y_center> <width> <height>

For this project, use class_id 0 for face.
