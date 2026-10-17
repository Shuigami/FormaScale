"""Optional integration test using exact projections of an existing model.

These synthetic observations test software wiring only, not capture accuracy.
Set FORMASCALE_TEST_MODEL to a COLMAP model directory to enable this test.
"""
import json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
import numpy as np
import pycolmap as p
from formascale.geometry import triangulate

class ScaleIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('FORMASCALE_TEST_MODEL'),'set FORMASCALE_TEST_MODEL for integration test')
    def test_exact_projected_landmarks(self):
        path=Path(os.environ['FORMASCALE_TEST_MODEL']).resolve()
        model=p.Reconstruction(path)
        landmarks=[]
        for point in model.points3D.values():
            observations=[];normalized=[];poses=[]
            for entry in point.track.elements:
                image=model.images[entry.image_id];camera=model.cameras[image.camera_id]
                pose=image.cam_from_world().matrix();camera_point=pose[:,:3]@point.xyz+pose[:,3]
                pixel=camera.img_from_cam(camera_point)
                if pixel is None or not np.isfinite(pixel).all() or not(0<=pixel[0]<camera.width and 0<=pixel[1]<camera.height):continue
                observations.append({'image':image.name,'xy':pixel.tolist()})
                normalized.append(camera.cam_from_img(pixel));poses.append(pose)
            if len(observations)<3:continue
            try:triangulate(normalized,poses)
            except ValueError:continue
            landmarks.append((point.xyz.copy(),observations))
            if len(landmarks)==4:break
        self.assertEqual(len(landmarks),4,'test fixture needs four suitable tracks')
        factor=100/np.linalg.norm(landmarks[0][0]-landmarks[1][0])
        expected=float(np.linalg.norm(landmarks[2][0]-landmarks[3][0])*factor)
        annotation={'coordinate_space':'prepared_images_pixels','reference':{'a':'A','b':'B','length_mm':100},'points':{label:pair[1] for label,pair in zip('ABCD',landmarks)},'measurements':[{'a':'C','b':'D','reference_mm':expected}]}
        with tempfile.TemporaryDirectory(prefix='formascale-test-') as tmp:
            folder=Path(tmp);source=folder/'synthetic.json';source.write_text(json.dumps(annotation))
            script=Path(__file__).resolve().parents[1]/'scripts/scale_model.py'
            proc=subprocess.run([sys.executable,str(script),str(path),str(source),'--output',str(folder/'output')],capture_output=True,text=True)
            self.assertEqual(proc.returncode,0,proc.stderr)
            result=json.loads((folder/'output/measurements.json').read_text())
            self.assertAlmostEqual(result['scale_mm_per_unit'],factor,places=6)
            self.assertLess(result['measurements'][0]['relative_error_percent'],1e-6)
            self.assertTrue((folder/'output/points_mm.ply').is_file())
            self.assertFalse(result['metric_accuracy_validated'])
