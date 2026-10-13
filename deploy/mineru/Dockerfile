FROM vllm/vllm-openai:v0.21.0

RUN apt-get update && \
    apt-get install -y libgl1 fonts-noto-core fonts-noto-cjk fontconfig && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install 'mineru[torch]==4.0.12' \
    -i https://mirrors.aliyun.com/pypi/simple --break-system-packages && \
    python3 -m pip cache purge

ENV MINERU_MODEL_SMALL_BACKEND=torch \
    MINERU_MODEL_VLM_ENGINE=vllm \
    MINERU_MODEL_SOURCE=local

RUN mineru-kit models download --tier standard --small-backend torch \
    --vlm-engine vllm --source modelscope && \
    mineru-kit models verify --tier standard --small-backend torch --vlm-engine vllm

ENTRYPOINT ["mineru-kit"]
