"""Triangulate annotated landmarks and scale a saved model, with geometric checks.

Coordinates refer to the prepared JPEG images, not the original HEIC files.
At least three distinct registered views per landmark are required.
"""
import argparse
import json
from pathlib import Path
import sys
import numpy as np
import pycolmap as p
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from formascale.geometry import triangulate,scale_from_reference,distance_error


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('model',type=Path)
    parser.add_argument('annotations',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():parser.error('output must not exist; preserve earlier results')
    annotation=json.loads(args.annotations.read_text(encoding='utf-8-sig'))
    if annotation.get('coordinate_space', 'prepared_images_pixels') != 'prepared_images_pixels':
        raise ValueError('Annotations must use prepared-image pixel coordinates')
    if 'run' in annotation and annotation['run'] != args.model.resolve().parents[1].name:
        raise ValueError('Annotation run does not match the selected reconstruction')
    if 'model_id' in annotation and str(annotation['model_id']) != args.model.name:
        raise ValueError('Annotation model ID does not match the selected reconstruction')
    model=p.Reconstruction(args.model)
    lookup={model.images[k].name:model.images[k] for k in model.reg_image_ids()}
    landmarks={}
    for label,observations in annotation['points'].items():
        if len(observations)<3 or len({o['image'] for o in observations})!=len(observations):
            raise ValueError(f'{label}: at least three distinct views required')
        normalized=[];poses=[];cameras=[];pixels=[]
        for obs in observations:
            image=lookup[obs['image']]
            camera=model.cameras[image.camera_id]
            pixel=np.asarray(obs['xy'],float)
            if pixel.shape!=(2,) or not np.isfinite(pixel).all() or not(0<=pixel[0]<camera.width and 0<=pixel[1]<camera.height):
                raise ValueError(f'{label}: pixel outside prepared image')
            normalized.append(camera.cam_from_img(pixel));poses.append(image.cam_from_world().matrix())
            cameras.append(camera);pixels.append(pixel)
        point,angle=triangulate(normalized,poses)
        residuals=[float(np.linalg.norm(camera.img_from_cam(pose[:,:3]@point+pose[:,3])-pixel)) for camera,pose,pixel in zip(cameras,poses,pixels)]
        if max(residuals)>3:
            raise ValueError(f'{label}: reprojection error exceeds 3 pixels: {max(residuals):.2f}')
        landmarks[label]={'xyz':point.tolist(),'max_reprojection_error_px':max(residuals),
                          'best_triangulation_angle_deg':angle,'observations':observations}
    reference=annotation['reference']
    a,b=reference['a'],reference['b']
    factor=scale_from_reference(landmarks[a]['xyz'],landmarks[b]['xyz'],reference['length_mm'])
    measurements=[]
    for request in annotation.get('measurements',[]):
        x,y=request['a'],request['b']
        if {x,y}=={a,b}:raise ValueError('Do not use the scale reference as an independent validation measurement')
        measured=float(np.linalg.norm(np.array(landmarks[x]['xyz'])-landmarks[y]['xyz'])*factor)
        result={'a':x,'b':y,'distance_mm':measured}
        if 'reference_mm' in request:
            result.update(reference_mm=request['reference_mm'],**distance_error(measured,request['reference_mm']))
        measurements.append(result)
    args.output.mkdir(parents=True)
    report={'model':str(args.model.resolve()),'annotations':annotation,'scale_mm_per_unit':factor,
            'landmarks':landmarks,'measurements':measurements,'metric_accuracy_validated':False,
            'note':'Reference triangulation passed checks. Independent dimensions and repeated captures are required to validate metric accuracy.'}
    (args.output/'measurements.json').write_text(json.dumps(report,indent=2))
    with (args.output/'points_mm.ply').open('w') as out:
        out.write(f'ply\nformat ascii 1.0\nelement vertex {model.num_points3D()}\nproperty double x\nproperty double y\nproperty double z\nproperty uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n')
        for point in model.points3D.values():
            out.write(' '.join(map(str,[*(point.xyz*factor),*point.color]))+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()

