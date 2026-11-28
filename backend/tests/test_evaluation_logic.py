import os
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["DB_PATH"] = os.path.join(tempfile.gettempdir(), "exameval-tests.db")
os.environ["EXAMEVAL_ADMIN_PASSWORD"] = "test-only-password"

import main

class EvaluationLogicTests(unittest.TestCase):
    def test_rubric_pipe_marks_format(self):
        rows = main.rubric("Q1: photosynthesis converts light energy | marks: 4\nQ2: chlorophyll absorbs light (2 marks)")
        self.assertEqual([r["criterion"] for r in rows], ["Q1", "Q2"])
        self.assertEqual([r["max_marks"] for r in rows], [4.0, 2.0])

    def test_rubric_unstructured_is_explicit_placeholder(self):
        rows = main.rubric("First expected point\n\nSecond expected point")
        self.assertEqual(len(rows), 2)
        self.assertIn("rubric_warning", rows[0])

    def test_question_answer_segmentation(self):
        text = "1. photosynthesis uses sunlight\n2. respiration releases energy"
        self.assertIn("photosynthesis", main.answer_for_question(text, "Q1"))
        self.assertNotIn("respiration", main.answer_for_question(text, "Q1"))

    def test_question_answer_segmentation_q_prefix(self):
        text = "Q1: force equals mass times acceleration\\nQ2: energy is conserved"
        self.assertIn("force equals", main.answer_for_question(text, "Q1"))
        self.assertNotIn("energy is conserved", main.answer_for_question(text, "Q1"))

    def test_question_answer_segmentation_question_prefix(self):
        text = "Question 1: first response\\nQuestion 2: second response"
        self.assertEqual(main.answer_for_question(text, "Q1"), "first response")

    def test_similarity_identical_terms(self):
        class FakeModel:
            def encode(self, texts, **kwargs):
                import numpy as np
                return np.array([[1.0, 0.0], [1.0, 0.0]])
        with patch.object(main, "_SEMANTIC_MODEL", FakeModel()):
            self.assertAlmostEqual(main.score("Cell membrane controls entry", "Cell membrane controls entry"), 1.0)

    def test_similarity_no_overlap(self):
        class FakeModel:
            def encode(self, texts, **kwargs):
                import numpy as np
                return np.array([[1.0, 0.0], [0.0, 1.0]])
        with patch.object(main, "_SEMANTIC_MODEL", FakeModel()):
            self.assertEqual(main.score("algebra geometry", "photosynthesis chlorophyll"), 0.0)

if __name__ == "__main__":
    unittest.main()
