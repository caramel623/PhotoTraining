import sys
from vehicle_dataset_manager.detection import cuda_env

target = sys.argv[1]
print("Installing CUDA torch into: " + target, flush=True)
res = cuda_env.install_cuda_torch("cu126", python=target, line_cb=lambda l: print(l, flush=True))
print("RESULT success=%s code=%s" % (res.success, res.returncode), flush=True)
if not res.success:
    print("TAIL>>" + res.output_tail, flush=True)