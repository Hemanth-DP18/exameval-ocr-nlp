# Build the web interface first so the finished image runs without internet access.
FROM node:22-alpine AS frontend-build
WORKDIR /web
COPY frontend/package.json ./package.json
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr poppler-utils && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
# Download semantic and handwriting models at build time for offline CPU inference.
RUN HF_HOME=/opt/huggingface python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2').save('/opt/exameval-model')"
RUN HF_HOME=/opt/huggingface python -c "from transformers import TrOCRProcessor, VisionEncoderDecoderModel; p=TrOCRProcessor.from_pretrained('microsoft/trocr-small-handwritten'); m=VisionEncoderDecoderModel.from_pretrained('microsoft/trocr-small-handwritten'); p.save_pretrained('/opt/exameval-htr'); m.save_pretrained('/opt/exameval-htr', safe_serialization=True)"
ENV EXAMEVAL_MODEL_PATH=/opt/exameval-model EXAMEVAL_HTR_MODEL_PATH=/opt/exameval-htr HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HOME=/opt/huggingface EXAMEVAL_TORCH_THREADS=2
COPY backend/ .
COPY --from=frontend-build /web/dist ./static
ENV PORT=8000
CMD ["sh","-c","uvicorn main:app --host 0.0.0.0 --port $PORT"]
