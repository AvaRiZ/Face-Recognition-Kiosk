import unittest

from scripts.static_image_benchmark import BenchmarkRecord, STAGE_LABELS, summarize_benchmark


class StaticImageBenchmarkSummaryTests(unittest.TestCase):
    def test_summary_averages_requested_processing_stages(self):
        records = [
            BenchmarkRecord(
                image_path="a.jpg",
                face_detection_yolov8_ms=10.0,
                arcface_embedding_ms=20.0,
                facenet_embedding_ms=30.0,
                database_query_comparison_ms=40.0,
                total_end_to_end_ms=100.0,
                matched_user_id=1,
                matched_name="A",
                confidence=0.91,
            ),
            BenchmarkRecord(
                image_path="b.jpg",
                face_detection_yolov8_ms=20.0,
                arcface_embedding_ms=40.0,
                facenet_embedding_ms=60.0,
                database_query_comparison_ms=80.0,
                total_end_to_end_ms=200.0,
                matched_user_id=None,
                matched_name=None,
                confidence=None,
            ),
        ]

        summary = summarize_benchmark(
            records,
            scanned_count=3,
            skipped={"no_face_detected": 1},
            users_loaded=12,
            embeddings_loaded=48,
        )

        self.assertEqual(summary["scanned_images"], 3)
        self.assertEqual(summary["processed_images"], 2)
        self.assertEqual(summary["skipped_images"], 1)
        self.assertEqual(summary["users_loaded"], 12)
        self.assertEqual(summary["embeddings_loaded"], 48)
        self.assertEqual(set(summary["averages"]), set(STAGE_LABELS))
        self.assertEqual(summary["averages"]["face_detection_yolov8_ms"]["average_ms"], 15.0)
        self.assertEqual(summary["averages"]["arcface_embedding_ms"]["average_ms"], 30.0)
        self.assertEqual(summary["averages"]["facenet_embedding_ms"]["average_ms"], 45.0)
        self.assertEqual(summary["averages"]["database_query_comparison_ms"]["average_ms"], 60.0)
        self.assertEqual(summary["averages"]["total_end_to_end_ms"]["average_ms"], 150.0)


if __name__ == "__main__":
    unittest.main()
