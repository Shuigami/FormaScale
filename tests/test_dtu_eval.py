import unittest

import numpy as np

from formascale.dtu_eval import camera_center_from_projection, reduce_points, umeyama_similarity


class DtuEvalTests(unittest.TestCase):
    def test_umeyama_recovers_similarity(self):
        source = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
        rotation = np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
        scale = 2.5
        translation = np.array([10.0, -3.0, 4.0])
        target = scale * (source @ rotation.T) + translation
        estimated_scale, estimated_rotation, estimated_translation = umeyama_similarity(source, target)
        self.assertAlmostEqual(estimated_scale, scale, places=8)
        np.testing.assert_allclose(estimated_rotation, rotation, atol=1e-8)
        np.testing.assert_allclose(estimated_translation, translation, atol=1e-8)

    def test_camera_center_from_projection(self):
        rotation = np.eye(3)
        center = np.array([1.0, 2.0, 3.0])
        translation = -rotation @ center
        intrinsic = np.diag([1000.0, 1000.0, 1.0])
        projection = intrinsic @ np.column_stack([rotation, translation])
        np.testing.assert_allclose(camera_center_from_projection(projection), center, atol=1e-8)

    def test_reduce_points_keeps_one_per_voxel(self):
        points = np.array([[0.0, 0.0, 0.0], [0.05, 0.05, 0.05], [0.3, 0.0, 0.0]])
        reduced = reduce_points(points, spacing_mm=0.2)
        self.assertEqual(len(reduced), 2)


if __name__ == "__main__":
    unittest.main()
