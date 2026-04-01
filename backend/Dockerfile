FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV EASYOCR_MODEL_DIR=/app/.easyocr

ARG TORCH_WHEEL_INDEX=https://download.pytorch.org/whl/cpu
ARG TORCH_PACKAGES="torch torchvision"

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --index-url ${TORCH_WHEEL_INDEX} ${TORCH_PACKAGES}

COPY . .

RUN python -c "from ocr_service import prefetch_reader; prefetch_reader()"

ENV PORT=8080

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
