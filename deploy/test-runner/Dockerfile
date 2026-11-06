# 平台受控测试镜像：预装通用 web 栈 + platform 常用大件(ML/RAG/队列)，避免测试时从网络重下大依赖。
# 被测仓库额外小依赖仍由 runner 自动安装(PIP_INDEX_URL 已指到国内加速源)。
FROM python:3.12
ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
RUN pip install --no-cache-dir \
    pytest pytest-cov pytest-asyncio \
    fastapi uvicorn pydantic pydantic-settings python-dotenv python-multipart httpx \
    SQLAlchemy PyMySQL PyMuPDF PyJWT \
    celery redis \
    langchain langchain-openai langgraph llama-index pymilvus deepagents
WORKDIR /workspace
