# AI02 Re-ID hand-off

Target: Ubuntu 26.04 host, NVIDIA RTX A5000 24 GB, Docker. The Windows app
exports a portable Dataset; AI02 performs training, embedding and optional
FAISS conversion. No REST API or automatic network sync is enabled.

## Inputs

Copy only these user-approved artifacts to AI02:

- the completed Phase 5 `Dataset/` directory;
- a trained Re-ID ONNX model whose input and normalization match
  `PreprocessConfig`;
- this repository's code or a release archive.

Do not copy the original photo archive, workspace database, logs or OCR cache.
The Re-ID index metadata intentionally excludes plate text.

## GPU container

First install/configure NVIDIA Container Toolkit on the host and verify that a
CUDA container can run `nvidia-smi`. Select an official NVIDIA CUDA + cuDNN
runtime image tag compatible with the installed driver; the Dockerfile does not
guess or pin one.

~~~bash
docker build -f ai02/Dockerfile \
  --build-arg NVIDIA_BASE_IMAGE=<official-compatible-cuda-cudnn-runtime-tag> \
  -t vdm-reid:local .

docker run --rm --gpus all \
  -v /srv/vdm/Dataset:/data/Dataset:rw \
  -v /srv/vdm/models:/models:ro \
  vdm-reid:local /data/Dataset /models/reid.onnx --cuda
~~~

The command writes only `Dataset/features/reid_embeddings.npz` and
`index_state.json`. Re-running with the same model resumes and reuses indexed
items. A different model ID is rejected to prevent mixed embeddings.

## FAISS

FAISS upstream recommends Conda packages for supported installs. Install the
CPU or GPU package in an isolated AI02 environment after selecting the CUDA
variant compatible with that host, then convert:

~~~bash
python ai02/convert_to_faiss.py \
  /srv/vdm/Dataset/features/reid_embeddings.npz \
  /srv/vdm/Dataset/features/reid.faiss --gpu
~~~

The saved FAISS index is converted back to CPU form for portability. Its JSON
sidecar preserves the ordered image IDs and model identity.

## Model contract

- Input: one BGR image supplied by OpenCV; the engine converts it to RGB.
- Shape: NCHW float32, default `1x3x256x256`.
- Scaling: `[0,255] -> [0,1]`.
- Normalization: ImageNet mean `(0.485, 0.456, 0.406)`, std
  `(0.229, 0.224, 0.225)`.
- Output: first model output, shape `[1,D]`; Windows/AI02 code applies L2
  normalization before indexing.
- Training/evaluation images must use `reid_crops/`, where plates are masked.

If the trained model uses different preprocessing, change the explicit
`PreprocessConfig` on both sides and create a new output index.

## Official references

- NVIDIA Container Toolkit:
  https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html
- ONNX Runtime CUDA Execution Provider:
  https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html
- FAISS supported installation paths:
  https://github.com/facebookresearch/faiss/blob/main/INSTALL.md
