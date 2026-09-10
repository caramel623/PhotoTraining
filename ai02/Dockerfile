# Supply an official NVIDIA CUDA + cuDNN runtime tag compatible with AI02.
ARG NVIDIA_BASE_IMAGE
FROM ${NVIDIA_BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/vdm-venv
ENV PATH="/opt/vdm-venv/bin:${PATH}"
WORKDIR /opt/vdm

COPY ai02/requirements-onnx.txt /tmp/requirements-onnx.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r /tmp/requirements-onnx.txt

COPY vehicle_dataset_manager/ vehicle_dataset_manager/
COPY scripts/build_reid_index.py scripts/build_reid_index.py

ENTRYPOINT ["python", "scripts/build_reid_index.py"]
