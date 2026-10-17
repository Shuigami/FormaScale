import unittest
import numpy as np
from formascale.geometry import triangulate,scale_from_reference,distance_error


class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.poses=np.array([np.column_stack([np.eye(3),-np.array(c)]) for c in [[-1,0,0],[1,0,0],[0,1,0]]])
        self.point=np.array([.2,.1,5.])
        camera=np.einsum('nij,j->ni',self.poses,np.r_[self.point,1])
        self.xy=camera[:,:2]/camera[:,2,None]

    def test_triangulates_known_point(self):
        point,angle=triangulate(self.xy,self.poses)
        np.testing.assert_allclose(point,self.point,atol=1e-10)
        self.assertGreater(angle,10)

    def test_small_pixel_noise_is_bounded(self):
        noise=np.array([[.0001,-.0002],[-.0001,.0001],[.0002,.0001]])
        point,_=triangulate(self.xy+noise,self.poses)
        self.assertLess(np.linalg.norm(point-self.point),.01)

    def test_parallel_views_rejected(self):
        with self.assertRaises(ValueError):
            triangulate(np.tile(self.xy[0],(3,1)),np.tile(self.poses[0],(3,1,1)))

    def test_tiny_baseline_rejected(self):
        poses=self.poses.copy();poses[:,:,3]*=1e-5
        cam=np.einsum('nij,j->ni',poses,np.r_[self.point,1])
        with self.assertRaises(ValueError):triangulate(cam[:,:2]/cam[:,2,None],poses)

    def test_behind_camera_rejected(self):
        cam=np.einsum('nij,j->ni',self.poses,np.array([.2,.1,-5,1]))
        with self.assertRaises(ValueError):triangulate(cam[:,:2]/cam[:,2,None],self.poses)

    def test_invalid_rotation_rejected(self):
        poses=self.poses.copy();poses[0,0,0]=2
        with self.assertRaises(ValueError):triangulate(self.xy,poses)

    def test_scale(self):
        self.assertEqual(scale_from_reference([0,0,0],[2,0,0],100),50)

    def test_invalid_references(self):
        for length in [0,-1,float('nan')]:
            with self.assertRaises(ValueError):scale_from_reference([0,0,0],[2,0,0],length)
        with self.assertRaises(ValueError):scale_from_reference([0,0,0],[0,0,0],100)

    def test_error_metric(self):
        self.assertEqual(distance_error(105,100),{'absolute_error_mm':5.,'relative_error_percent':5.})

if __name__=='__main__':unittest.main()
