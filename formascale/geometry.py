"""Geometry independent of COLMAP, using world-to-camera [R|t] poses."""
import numpy as np


def triangulate(normalized_xy, poses, min_angle_deg=2.0):
    xy = np.asarray(normalized_xy, dtype=float)
    poses = np.asarray(poses, dtype=float)
    if xy.ndim != 2 or xy.shape[1] != 2 or len(xy) < 2:
        raise ValueError('At least two normalized 2D observations are required')
    if poses.shape != (len(xy), 3, 4) or not np.isfinite(xy).all() or not np.isfinite(poses).all():
        raise ValueError('Invalid camera poses or observations')
    rotations=poses[:,:,:3]
    if not np.allclose(rotations @ rotations.transpose(0,2,1), np.eye(3), atol=1e-5) or not np.allclose(np.linalg.det(rotations),1,atol=1e-5):
        raise ValueError('Camera rotations must be proper orthonormal matrices')
    rows=[]
    for (x,y), pose in zip(xy,poses):
        rows.extend([x*pose[2]-pose[0], y*pose[2]-pose[1]])
    _,_,vh=np.linalg.svd(np.asarray(rows))
    if abs(vh[-1,3]) < 1e-12:
        raise ValueError('Point at infinity or degenerate geometry')
    point=vh[-1,:3]/vh[-1,3]
    depths=np.einsum('nij,j->ni',poses,np.r_[point,1])[:,2]
    if np.any(depths<=0):
        raise ValueError('Triangulated point is behind a camera')
    centers=-np.einsum('nji,nj->ni',rotations,poses[:,:,3])
    rays=point-centers
    rays/=np.linalg.norm(rays,axis=1)[:,None]
    angles=np.rad2deg(np.arccos(np.clip(rays@rays.T,-1,1)))
    # Both nearly parallel and nearly antiparallel rays are ill-conditioned.
    angles=np.minimum(angles,180-angles)
    best=float(np.max(angles))
    if best < min_angle_deg:
        raise ValueError(f'Insufficient triangulation angle: {best:.3f} degrees')
    return point,best


def scale_from_reference(a,b,known_mm):
    if not np.isfinite(known_mm) or known_mm<=0:
        raise ValueError('Reference length must be finite and positive')
    distance=float(np.linalg.norm(np.asarray(a,float)-np.asarray(b,float)))
    if not np.isfinite(distance) or distance<=1e-10:
        raise ValueError('Reference endpoints must be distinct finite points')
    return float(known_mm/distance)


def distance_error(measured_mm, reference_mm):
    if not np.isfinite([measured_mm,reference_mm]).all() or measured_mm<0 or reference_mm<=0:
        raise ValueError('Invalid measurement or reference')
    absolute=abs(measured_mm-reference_mm)
    return {'absolute_error_mm':float(absolute),'relative_error_percent':float(100*absolute/reference_mm)}
