FROM python:3.13-slim

# 设置工作目录
WORKDIR /app

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 复制项目文件（uv.lock 必须带上：否则镜像内 uv sync 会解析最新版依赖，
# 与本地锁定验证过的版本不一致，导致行为漂移）
COPY pyproject.toml uv.lock ./
COPY server.py .
COPY utils/ ./utils/
COPY README.md .

# 创建虚拟环境并安装依赖（严格按 uv.lock 锁定版本）
RUN uv venv /root/.venv --clear && \
    export PATH="/root/.venv/bin:$PATH" && \
    cd /app && uv sync --frozen

# 设置环境变量
ENV PATH="/root/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV UV_PROJECT_ENVIRONMENT=/root/.venv

EXPOSE 8000

CMD ["uv", "run", "server.py"]
