FROM python:3.12-slim

WORKDIR /app

# 零第三方依赖：复制全部 Python 模块，避免运行期漏模块。
COPY *.py ./
COPY data/ ./data/
COPY static/ ./static/
COPY sql/ ./sql/

# 容器内监听 0.0.0.0，平台会把 PORT 注入进来
ENV HOST=0.0.0.0 \
    PORT=8080 \
    SEED=1 \
    READONLY=1

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:%s/healthz'%os.environ['PORT']).read()"

CMD ["python", "demo.py"]
