"""Sanity tests for the RabbitClassifier using the real ONNX model."""
import os
import unittest

import numpy as np

from rabbit_recognition.classifier import RabbitClassifier

MODEL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "models",
    "mobilenet_v2_rabbit.onnx",
)


class TestRabbitClassifier(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(MODEL_PATH):
            raise unittest.SkipTest("Model file not found")
        cls.classifier = RabbitClassifier(model_path=MODEL_PATH)

    def make_frame(self, seed=0):
        rng = np.random.RandomState(seed)
        return rng.randint(0, 255, (240, 320, 3), dtype=np.uint8)

    def test_metadata(self):
        self.assertEqual(self.classifier.name, "mobilenet_v2_rabbit")
        self.assertEqual(self.classifier.default_threshold, 0.5)
        self.assertEqual(self.classifier.input_size, 224)
        self.assertEqual(len(self.classifier.mean), 3)
        self.assertEqual(len(self.classifier.std), 3)

    def test_preprocess_shape_and_normalization(self):
        out = self.classifier.preprocess(self.make_frame())
        self.assertEqual(out.shape, (1, 3, 224, 224))
        self.assertEqual(out.dtype, np.float32)
        self.assertTrue(np.isfinite(out).all())
        # Undo the manifest normalization; the recovered input must be the
        # pixel values in [0, 1] (uniform gray image, mean ~127.5/255).
        mean = self.classifier.mean[None, :, None, None]
        std = self.classifier.std[None, :, None, None]
        recovered = out * std + mean
        self.assertGreaterEqual(float(recovered.min()), -0.01)
        self.assertLessEqual(float(recovered.max()), 1.01)
        self.assertAlmostEqual(float(recovered.mean()), 127.5 / 255.0, delta=0.01)

    def test_predict_proba_range_and_determinism(self):
        frame = self.make_frame()
        p1 = self.classifier.predict_proba(frame)
        p2 = self.classifier.predict_proba(frame)
        self.assertGreaterEqual(p1, 0.0)
        self.assertLessEqual(p1, 1.0)
        self.assertEqual(p1, p2)

    def test_different_inputs_give_different_probabilities(self):
        p0 = self.classifier.predict_proba(self.make_frame(seed=0))
        p1 = self.classifier.predict_proba(self.make_frame(seed=1))
        self.assertNotAlmostEqual(p0, p1, places=4)

    def test_classify_threshold_semantics(self):
        frame = self.make_frame()
        p = self.classifier.predict_proba(frame)
        for threshold in (0.0, 1.0, 0.5):
            flag, proba, used = self.classifier.classify(frame, threshold=threshold)
            self.assertEqual(proba, p)
            self.assertEqual(used, threshold)
            self.assertIs(flag, bool(proba >= threshold))
        flag_default, _, used_default = self.classifier.classify(frame)
        self.assertEqual(used_default, self.classifier.default_threshold)
        self.assertIs(flag_default, bool(p >= self.classifier.default_threshold))


if __name__ == "__main__":
    unittest.main()
