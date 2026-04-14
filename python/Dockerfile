FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

# 创建图片目录
RUN mkdir -p temp_images

EXPOSE 3333

CMD ["python", "server.py"]
