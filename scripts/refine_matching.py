"""Retry matching from a completed run without re-extracting image features."""
import argparse,json,resource,sqlite3,time
from pathlib import Path
import pycolmap as p

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('source',type=Path)
parser.add_argument('--run',required=True)
parser.add_argument('--reuse-matches',action='store_true')
args=parser.parse_args()
if Path(args.run).name!=args.run or args.run in {'.','..'}:parser.error('invalid run name')
source=args.source.resolve();output=source.parent/args.run
output.mkdir(exist_ok=False)
with sqlite3.connect(f'file:{source/"database.db"}?mode=ro',uri=True) as src,sqlite3.connect(output/'database.db') as dst:
    src.backup(dst)
    if not args.reuse_matches:
        dst.execute('delete from matches');dst.execute('delete from two_view_geometries');dst.commit()
config=json.loads((source/'config.json').read_text())
matching=p.FeatureMatchingOptions();matching.num_threads=2;matching.guided_matching=True;matching.max_num_matches=4096
mapping=p.IncrementalPipelineOptions();mapping.num_threads=2;mapping.random_seed=0;mapping.max_runtime_seconds=300;mapping.ba_min_num_residuals_for_cpu_multi_threading=1000000
config.update(source_run=source.name,matching=matching.todict(),mapping=mapping.todict(),resource_limit='6 GiB RAM, no swap, 600 s enforced externally')
(output/'config.json').write_text(json.dumps(config,default=str,indent=2))
p.logging.minloglevel=1
start=time.perf_counter()
(output/'status.json').write_text(json.dumps({'stage':'matching'}))
if not args.reuse_matches:
    p.match_exhaustive(output/'database.db',matching_options=matching,device=p.Device.cpu)
matched=time.perf_counter();print('matching complete',flush=True)
(output/'status.json').write_text(json.dumps({'stage':'mapping'}))
images=source.parents[1]/'prepared/images';sparse=output/'sparse';sparse.mkdir()
models=p.incremental_mapping(output/'database.db',images,sparse,options=mapping)
finished=time.perf_counter();results=[]
for index,model in models.items():
    directory=sparse/str(index);directory.mkdir(exist_ok=True);model.write(directory);model.export_PLY(str(directory/'points.ply'))
    results.append({'id':index,'registered_images':model.num_reg_images(),'points3D':model.num_points3D(),'mean_reprojection_error_px':model.compute_mean_reprojection_error(),'image_names':sorted(model.images[i].name for i in model.reg_image_ids())})
summary={'input_images':59,'models':results,'seconds':{'extraction':0,'matching':matched-start,'mapping':finished-matched,'total':finished-start},'extraction_reused_from':source.name,'matching_reused':args.reuse_matches,'peak_process_ram_mib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,'metric_scale_validated':False,'object_geometry_validated':False}
summary['input_images']=len(list(images.glob('*.jpg')))
(output/'summary.json').write_text(json.dumps(summary,indent=2));(output/'status.json').write_text(json.dumps({'stage':'complete'}))
print(json.dumps({**summary,'models':[{k:v for k,v in r.items() if k!='image_names'} for r in results]},indent=2))

