import unittest

from scripts.render_benchmark_visuals import stage_rows


class BenchmarkVisualRowsTests(unittest.TestCase):
    def test_stage_rows_preserve_requested_order(self):
        summary = {
            "averages": {
                "total_end_to_end_ms": {"label": "Total", "average_ms": 50, "sample_count": 2},
                "face_detection_yolov8_ms": {"label": "Detection", "average_ms": 10, "sample_count": 2},
                "database_query_comparison_ms": {"label": "Comparison", "average_ms": 4, "sample_count": 2},
                "facenet_embedding_ms": {"label": "FaceNet", "average_ms": 20, "sample_count": 2},
                "arcface_embedding_ms": {"label": "ArcFace", "average_ms": 15, "sample_count": 2},
            }
        }

        labels = [row["label"] for row in stage_rows(summary)]

        self.assertEqual(labels, ["Detection", "ArcFace", "FaceNet", "Comparison", "Total"])


if __name__ == "__main__":
    unittest.main()
